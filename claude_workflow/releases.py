"""Validation and deterministic packaging helpers for Claude Workflow releases.

The lifecycle module deliberately depends on this module for all source
handling.  Keeping archive extraction here makes it easier to audit the two
places where untrusted paths enter the filesystem.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator


PLUGIN_NAME = "claude-workflow"
VERSION_RE = re.compile(
    r"^[vV]?(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)


class ReleaseError(ValueError):
    """Raised when a plugin source or release archive is invalid."""


@dataclass(frozen=True)
class ResolvedSource:
    """A validated plugin directory and the temporary directory that owns it."""

    root: Path
    kind: str
    version: str
    name: str
    _temporary: tempfile.TemporaryDirectory[str] | None = None

    def close(self) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()

    def __enter__(self) -> "ResolvedSource":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _normalise_plugin_root(root: Path) -> Path:
    """Find a plugin root in a directory without following symlinks."""

    root = Path(root)
    if not root.exists() or not root.is_dir() or root.is_symlink():
        raise ReleaseError(f"plugin source must be a real directory: {root}")
    candidates = [root]
    for nested in (root / "plugin", root / "claude_workflow" / "plugin"):
        if nested.exists() and nested.is_dir() and not nested.is_symlink():
            candidates.append(nested)
    for candidate in candidates:
        manifest_dir = candidate / ".claude-plugin"
        manifest = manifest_dir / "plugin.json"
        if manifest_dir.is_symlink() or manifest.is_symlink():
            raise ReleaseError(f"symlinks are not allowed in plugin sources: {manifest}")
        if manifest.is_file():
            return candidate.resolve()
    raise ReleaseError(
        f"plugin.json not found under {root} (expected .claude-plugin/plugin.json)"
    )


def _validate_relative_member(name: str) -> PurePosixPath:
    # ZIP names use POSIX separators.  Backslashes are rejected as well since
    # accepting them creates ambiguity when an archive is consumed on Windows.
    if not name or "\\" in name:
        raise ReleaseError(f"unsafe archive path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ReleaseError(f"unsafe archive path: {name!r}")
    if ":" in path.parts[0]:
        raise ReleaseError(f"unsafe archive path: {name!r}")
    return path


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def _extract_archive(archive: Path, destination: Path) -> None:
    try:
        zf = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseError(f"invalid release archive: {archive}") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > 10000:
            raise ReleaseError("release archive has too many entries")
        total_size = 0
        for info in infos:
            relative = _validate_relative_member(info.filename)
            if _is_zip_symlink(info):
                raise ReleaseError(f"symlinks are not allowed in release archives: {info.filename}")
            total_size += info.file_size
            if total_size > 100 * 1024 * 1024:
                raise ReleaseError("release archive is larger than 100 MiB")
            target = destination.joinpath(*relative.parts)
            try:
                target.resolve(strict=False).relative_to(destination.resolve())
            except ValueError as exc:
                raise ReleaseError(f"archive path escapes extraction directory: {info.filename}") from exc
            if info.is_dir() or info.filename.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # Do not use extractall: writing each checked member avoids path
            # traversal and allows us to reject pre-existing symlink parents.
            current = target.parent
            while current != destination:
                if current.is_symlink():
                    raise ReleaseError(f"symlink parent in release archive: {info.filename}")
                current = current.parent
            try:
                with zf.open(info, "r") as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
            except OSError as exc:
                raise ReleaseError(f"could not extract archive member: {info.filename}") from exc


def _parse_manifest(root: Path, *, require_complete: bool = False) -> dict[str, object]:
    manifest_path = root / ".claude-plugin" / "plugin.json"
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"invalid plugin manifest: {manifest_path}") from exc
    if not isinstance(manifest, dict):
        raise ReleaseError("plugin manifest must be a JSON object")
    if manifest.get("name") != PLUGIN_NAME:
        raise ReleaseError(f"plugin manifest name must be {PLUGIN_NAME!r}")
    components = manifest.get("components", {})
    if components is not None and not isinstance(components, dict):
        raise ReleaseError("plugin manifest components must be an object")
    if isinstance(components, dict):
        for component, entries in components.items():
            values = entries if isinstance(entries, list) else [entries]
            if not isinstance(component, str) or not all(isinstance(value, str) for value in values):
                raise ReleaseError("plugin manifest component paths must be strings")
            for value in values:
                if not value.startswith("./") or "\\" in value:
                    raise ReleaseError(f"plugin manifest component path is unsafe: {value!r}")
                _safe_manifest_path = PurePosixPath(value[2:])
                if _safe_manifest_path.is_absolute() or any(part in ("", ".", "..") for part in _safe_manifest_path.parts):
                    raise ReleaseError(f"plugin manifest component path is unsafe: {value!r}")
    version = manifest.get("version")
    if not isinstance(version, str):
        raise ReleaseError("plugin manifest version must be a string")
    parse_version(version)
    agents = sorted(
        path.relative_to(root).as_posix()
        for path in (root / "agents").rglob("*")
        if path.is_file() and not path.is_symlink() and path.suffix == ".md"
    ) if (root / "agents").is_dir() else []
    skills = sorted(
        path.relative_to(root).as_posix()
        for path in (root / "skills").rglob("SKILL.md")
        if path.is_file() and not path.is_symlink()
    ) if (root / "skills").is_dir() else []
    if not agents:
        raise ReleaseError("plugin must contain at least one agents/*.md definition")
    if not skills:
        raise ReleaseError("plugin must contain at least one skills/**/SKILL.md definition")
    if require_complete and len(agents) != 6:
        raise ReleaseError(f"complete plugin must contain six agent definitions (found {len(agents)})")
    # Walk the complete tree once here so validation rejects symlinked files or
    # directories even when they are outside agents/skills.
    _iter_files(root)
    return {
        "name": PLUGIN_NAME,
        "version": version,
        "agent_count": len(agents),
        "skill_count": len(skills),
        "agents": agents,
        "skills": skills,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    }


def parse_version(version: str) -> tuple[int, int, int, tuple[tuple[int, object], ...]]:
    """Parse a semver-like version and return a comparable tuple."""

    if not isinstance(version, str):
        raise ReleaseError("version must be a string")
    match = VERSION_RE.fullmatch(version.strip())
    if not match:
        raise ReleaseError(f"invalid semantic version: {version!r}")
    pre = match.group("pre")
    identifiers: list[tuple[int, object]] = []
    if pre:
        for identifier in pre.split("."):
            if identifier.isdigit():
                identifiers.append((0, int(identifier)))
            else:
                identifiers.append((1, identifier))
    return int(match.group("major")), int(match.group("minor")), int(match.group("patch")), tuple(identifiers)


def compare_versions(left: str, right: str) -> int:
    """Compare two semantic versions, returning -1, 0, or 1."""

    a = parse_version(left)
    b = parse_version(right)
    for i in range(3):
        if a[i] != b[i]:
            return -1 if a[i] < b[i] else 1
    apre, bpre = a[3], b[3]
    if not apre and not bpre:
        return 0
    if not apre:
        return 1
    if not bpre:
        return -1
    for av, bv in zip(apre, bpre):
        if av == bv:
            continue
        # Numeric identifiers sort before non-numeric identifiers.
        if av[0] != bv[0]:
            return -1 if av[0] < bv[0] else 1
        return -1 if av[1] < bv[1] else 1
    if len(apre) == len(bpre):
        return 0
    return -1 if len(apre) < len(bpre) else 1


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseError(f"cannot read source archive: {path}") from exc
    return digest.hexdigest()


def _validate_checksum(checksum: str | None, path: Path) -> str | None:
    if checksum is None:
        return None
    value = checksum.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ReleaseError("checksum must be a 64-character SHA256 hex digest")
    actual = sha256_file(path)
    if actual != value:
        raise ReleaseError(f"checksum mismatch for {path}: expected {value}, got {actual}")
    return actual


@contextlib.contextmanager
def resolve_source(source: str | os.PathLike[str], *, checksum: str | None = None) -> Iterator[ResolvedSource]:
    """Resolve a local directory/archive or explicit HTTP(S) source.

    Archives and URLs are extracted into a temporary directory and removed on
    exit.  URL use is intentionally opt-in through this function's explicit
    ``source`` argument; lifecycle has no implicit update endpoint.
    """

    raw = os.fspath(source)
    parsed = urllib.parse.urlparse(raw)
    temporary: tempfile.TemporaryDirectory[str] | None = None
    archive_path: Path
    kind: str
    if parsed.scheme in ("http", "https"):
        if checksum is None:
            raise ReleaseError("remote plugin sources require --sha256")
        temporary = tempfile.TemporaryDirectory(prefix="claude-workflow-download-")
        archive_path = Path(temporary.name) / "source.zip"
        try:
            with urllib.request.urlopen(raw, timeout=30) as response, archive_path.open("wb") as output:
                shutil.copyfileobj(response, output)
        except Exception as exc:  # urllib raises several concrete network errors
            temporary.cleanup()
            raise ReleaseError(f"could not download plugin source: {raw}") from exc
        if checksum is None:
            raise ReleaseError("release archives require an explicit --sha256 checksum")
        try:
            _validate_checksum(checksum, archive_path)
        except Exception:
            if temporary is not None:
                temporary.cleanup()
            raise
        kind = "remote-archive"
    else:
        archive_path = Path(raw).expanduser()
        if not archive_path.exists() or archive_path.is_symlink():
            raise ReleaseError(f"plugin source does not exist or is a symlink: {archive_path}")
        if archive_path.is_dir():
            if checksum is not None:
                raise ReleaseError("--sha256 is supported for release archives, not directories")
            root = _normalise_plugin_root(archive_path)
            manifest = _parse_manifest(root)
            yield ResolvedSource(root, "directory", str(manifest["version"]), PLUGIN_NAME)
            return
        if not archive_path.is_file():
            raise ReleaseError(f"plugin source must be a directory or ZIP archive: {archive_path}")
        if checksum is None:
            raise ReleaseError("release archives require an explicit --sha256 checksum")
        _validate_checksum(checksum, archive_path)
        kind = "archive"
    try:
        if temporary is None:
            temporary = tempfile.TemporaryDirectory(prefix="claude-workflow-archive-")
        extraction_root = Path(temporary.name) / "extracted"
        extraction_root.mkdir()
        _extract_archive(archive_path, extraction_root)
        root = _normalise_plugin_root(extraction_root)
        manifest = _parse_manifest(root)
        yield ResolvedSource(root, kind, str(manifest["version"]), PLUGIN_NAME, temporary)
    except Exception:
        if temporary is not None:
            temporary.cleanup()
        raise
    finally:
        if temporary is not None:
            temporary.cleanup()


def validate_plugin(
    source: str | os.PathLike[str],
    *,
    checksum: str | None = None,
    require_complete: bool = False,
) -> dict[str, object]:
    """Validate a plugin directory or checksum-verified ZIP release."""

    with resolve_source(source, checksum=checksum) as resolved:
        report = _parse_manifest(resolved.root, require_complete=require_complete)
        report.update({"source": str(source), "kind": resolved.kind})
        return report


def _iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ReleaseError(f"symlinks are not allowed in plugin sources: {relative}")
        if any(part in {"__pycache__", ".pytest_cache", ".mypy_cache"} for part in relative.parts) or path.suffix == ".pyc":
            continue
        if any(part in (".", "..") for part in relative.parts):
            raise ReleaseError(f"unsafe plugin path: {relative}")
        if path.is_file():
            files.append(path)
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def _release_inputs(source_value: Path, plugin_root: Path) -> tuple[Path, list[tuple[str, bytes, str]]]:
    """Build archive payload names from a plugin or project checkout."""

    # A checkout contains the plugin below claude_workflow/plugin plus package
    # metadata.  Flatten the plugin to ``plugin/`` so the resulting archive is
    # directly consumable by lifecycle updates while retaining useful source
    # metadata alongside it.
    source_root = source_value.resolve() if source_value.exists() else source_value.absolute()
    checkout = source_root.is_dir() and (source_root / "pyproject.toml").is_file() and plugin_root != source_root
    if not checkout:
        files = _iter_files(plugin_root)
        names = [(f"plugin/{path.relative_to(plugin_root).as_posix()}", path.read_bytes()) for path in files]
    else:
        files: list[tuple[str, bytes]] = []
        for path in sorted(source_root.rglob("*")):
            if path.is_symlink():
                raise ReleaseError(f"symlinks are not allowed in release sources: {path.relative_to(source_root)}")
            if not path.is_file():
                continue
            relative = path.relative_to(source_root).as_posix()
            relative_parts = relative.split("/")
            first = relative.split("/", 1)[0]
            if (
                relative.startswith(".git/")
                or relative == ".git"
                or relative.startswith("tests/")
                or first in {"build", "dist", ".pytest_cache", ".mypy_cache"}
                or "__pycache__" in relative_parts
                or path.suffix == ".pyc"
                or relative.endswith(".zip")
                or relative == "SHA256SUMS"
            ):
                continue
            plugin_prefix = f"{plugin_root.relative_to(source_root).as_posix()}/"
            if relative.startswith(plugin_prefix):
                continue
            # Runtime package files and project docs are useful for a source
            # release; plugin files are represented once below plugin/.
            namesafe = relative
            files.append((namesafe, path.read_bytes()))
        for path in _iter_files(plugin_root):
            # Keep a checkout release runnable after extraction: package code
            # expects its bundled native plugin at
            # ``claude_workflow/plugin``.  Plugin-only releases retain the
            # shorter ``plugin/`` layout handled by the branch above.
            namesafe = f"claude_workflow/plugin/{path.relative_to(plugin_root).as_posix()}"
            files.append((namesafe, path.read_bytes()))
        names = files
    payloads = [(name, data, hashlib.sha256(data).hexdigest()) for name, data in sorted(names)]
    return source_root, payloads


def build_release(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
) -> dict[str, object]:
    """Build a byte-for-byte deterministic ZIP release.

    Every plugin file is stored below ``plugin/``.  ``SHA256SUMS`` contains
    hashes for those payload files and therefore remains deterministic too.
    """

    output_path = Path(output).expanduser()
    source_value = Path(source).expanduser()
    with resolve_source(source) as resolved:
        report = _parse_manifest(resolved.root)
        _source_root, payloads = _release_inputs(source_value, resolved.root)
        checksums = "".join(f"{digest}  {name}\n" for name, _, digest in payloads).encode("ascii")
        output_is_directory = output_path.is_dir() or (
            not output_path.exists() and output_path.suffix.lower() != ".zip"
        )
        if output_is_directory:
            output_path.mkdir(parents=True, exist_ok=True)
            output_path = output_path / f"{PLUGIN_NAME}-{report['version']}.zip"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = output_path.with_name(output_path.name + ".tmp")
        try:
            with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for name, data, _digest in payloads:
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.create_system = 3
                    info.external_attr = 0o100644 << 16
                    archive.writestr(info, data)
                info = zipfile.ZipInfo("SHA256SUMS", date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, checksums)
            os.replace(temp_path, output_path)
        except Exception:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            raise
        archive_digest = sha256_file(output_path)
        sums_path = output_path.parent / "SHA256SUMS"
        sums_path.write_text(f"{archive_digest}  {output_path.name}\n", encoding="ascii")
        return {
            "path": str(output_path),
            "sha256": archive_digest,
            "checksums": str(sums_path),
            "name": report["name"],
            "version": report["version"],
            "file_count": len(payloads),
        }
