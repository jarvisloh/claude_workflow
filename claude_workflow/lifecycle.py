"""Safe, project-scoped lifecycle operations for Claude Workflow.

The module has no dependency on Claude Code.  It materializes a validated
plugin source into a project, records the exact bytes it owns, and uses a
per-project lock plus an in-memory transaction to make ordinary failures
recoverable.
"""

from __future__ import annotations

import base64
import contextlib
import copy
import errno
import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping

try:  # pragma: no cover - the CI host is POSIX; this keeps the package importable on Windows.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from .releases import ReleaseError, compare_versions, resolve_source, validate_plugin


STATE_SCHEMA = 1
PACKAGE_VERSION = "0.1.0"
STATE_DIR_NAME = ".claude-workflow"
STATE_FILE_NAME = "state.json"
CONFIG_FILE_NAME = "config.json"
LOCK_FILE_NAME = ".lock"
STABLE_LOCK_FILE_NAME = ".claude-workflow.lock"
CLAUDE_FILE_NAME = "CLAUDE.md"
CLAUDE_START = b"<!-- claude-workflow:begin -->"
CLAUDE_END = b"<!-- claude-workflow:end -->"
LEGACY_CLAUDE_START = b"<!-- claude-workflow:managed:start -->"
LEGACY_CLAUDE_END = b"<!-- claude-workflow:managed:end -->"
PLUGIN_ROOT_TOKEN = b"${CLAUDE_PLUGIN_ROOT}"
DEFAULT_CONFIG: dict[str, Any] = {
    "route": "light",
    "models": {
        "coordinator": "claude-opus-5",
        "worker": "claude-opus-5",
        "context": "claude-sonnet-5",
        "senior": "claude-fable-5-1",
    },
}
ROUTES = {"light", "medium", "heavy"}
MODEL_ROLES = {"coordinator", "worker", "context", "senior"}
AGENT_MODEL_ROLES = {
    "companion": "context",
    "investigator": "context",
    "executor": "worker",
    "tester": "worker",
    "archivist": "worker",
    "senior-executor": "senior",
}
# The project memory files are deliberately a small, documented namespace.
# They are durable user-facing documents, so a state record for any other
# project-root path must never become an authority to remove or create it.
MEMORY_DOCUMENTS = frozenset(
    {
        "latest_session_work.md",
        "project_core_tech.md",
        "project_diary.md",
        "project_overview.md",
        "project_progress.md",
        "project_structure.md",
    }
)
BOOTSTRAP_FRAMEWORK = tuple(f"agent_docs/{name}" for name in sorted(MEMORY_DOCUMENTS))


class LifecycleError(RuntimeError):
    """Base error raised for an unsafe or invalid lifecycle operation."""


class ConflictError(LifecycleError):
    """A managed file was changed or an unowned target would be overwritten."""


class UnsafePathError(LifecycleError):
    """A project, source, or managed path is unsafe to access."""


class StateError(LifecycleError):
    """The ownership state is absent, malformed, or internally inconsistent."""


class TransactionError(LifecycleError):
    """A mutation failed and its rollback could not be completed."""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(value: Any, label: str) -> bytes:
    if not isinstance(value, str):
        raise StateError(f"state {label} must be base64 text")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise StateError(f"state {label} is not valid base64") from exc


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _project_path(project: str | os.PathLike[str], *, create: bool = False) -> Path:
    raw = Path(project).expanduser()
    if raw.is_symlink():
        raise UnsafePathError(f"project path may not be a symlink: {raw}")
    if not raw.exists():
        # Inspect the nearest existing ancestor before optionally creating the
        # project.  This rejects a symlink hidden in a missing path prefix.
        ancestor = raw.parent
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        if ancestor.is_symlink():
            raise UnsafePathError(f"project parent may not be a symlink: {ancestor}")
        if not create:
            return raw.absolute()
        try:
            raw.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LifecycleError(f"could not create project directory: {raw}") from exc
    if not raw.is_dir():
        raise LifecycleError(f"project path must be a directory: {raw}")
    # Resolve only after checking the final path.  A regular project may live
    # under a system symlink such as /tmp -> /private/tmp.
    return raw.resolve()


def _safe_relative(relative: str | os.PathLike[str]) -> PurePosixPath:
    value = os.fspath(relative)
    if not value or "\\" in value:
        raise UnsafePathError(f"unsafe managed path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise UnsafePathError(f"unsafe managed path: {value!r}")
    # A drive-looking first component is not useful on POSIX and is dangerous
    # when a state file is later copied to Windows.
    if ":" in path.parts[0]:
        raise UnsafePathError(f"unsafe managed path: {value!r}")
    return path


def _safe_target(project: Path, relative: str | os.PathLike[str]) -> Path:
    safe = _safe_relative(relative)
    target = project.joinpath(*safe.parts)
    current = target
    while current != project:
        if current.is_symlink():
            raise UnsafePathError(f"managed path is a symlink: {target}")
        current = current.parent
    try:
        target.resolve(strict=False).relative_to(project.resolve())
    except ValueError as exc:
        raise UnsafePathError(f"managed path escapes project: {relative}") from exc
    return target


def _record_kind(relative: str) -> str:
    """Return the only ownership role allowed for *relative*.

    State is a user-editable JSON file and its hashes are therefore not an
    ownership proof.  Keep the accepted targets constrained to package-owned
    namespaces.  In particular, CLAUDE.md is represented by its separate
    marker metadata, and agent_docs is retained project memory rather than a
    deletion authority.
    """

    path = PurePosixPath(relative)
    parts = path.parts
    if parts == (STATE_DIR_NAME, CONFIG_FILE_NAME):
        return "config"
    if len(parts) >= 3 and parts[:2] == (STATE_DIR_NAME, "plugin"):
        return "runtime"
    if len(parts) == 3 and parts[:2] == (".claude", "agents"):
        name = parts[2]
        if name.startswith("cw-") and name.endswith(".md") and len(name) > len("cw-.md"):
            return "agent"
    if len(parts) >= 4 and parts[:2] == (".claude", "skills"):
        name = parts[2]
        if name.startswith("cw-") and len(name) > len("cw-"):
            return "skill"
    if len(parts) == 2 and parts[0] == "agent_docs" and parts[1] in MEMORY_DOCUMENTS:
        return "memory"
    raise StateError(f"lifecycle state path is outside owned namespaces: {relative}")


def _runtime_source_for_active(project: Path, relative: str, kind: str) -> Path:
    """Map a namespaced active target to its copied plugin source file."""

    path = PurePosixPath(relative)
    runtime_root = _safe_target(project, f"{STATE_DIR_NAME}/plugin")
    if kind == "agent":
        # An input plugin may use either `executor.md` or `cw-executor.md`.
        # Both materialize to `.claude/agents/cw-executor.md`.
        candidates = [runtime_root / "agents" / path.parts[2]]
        if path.parts[2].startswith("cw-"):
            candidates.append(runtime_root / "agents" / path.parts[2][3:])
        return next((candidate for candidate in candidates if candidate.is_file() and not candidate.is_symlink()), candidates[0])
    if kind == "skill":
        skill_name = path.parts[2]
        candidates = [runtime_root / "skills" / skill_name, runtime_root / "skills" / skill_name[3:]]
        suffix = Path(*path.parts[3:])
        for directory in candidates:
            candidate = directory / suffix
            if candidate.is_file() and not candidate.is_symlink():
                return candidate
        return candidates[0] / suffix
    raise StateError(f"active ownership role is invalid for {relative}")


def _validate_record_ownership(
    project: Path,
    relative: str,
    record: Mapping[str, Any],
    data: bytes,
    config: Mapping[str, Any],
) -> str:
    """Validate a record's path role and source-derived inventory boundary."""

    kind = _record_kind(relative)
    if record.get("kind") != kind:
        raise StateError(f"state record role for {relative!r} is invalid")
    if kind == "memory":
        # Project memory is never an active entry point and is never removed.
        if record.get("retain_when_disabled") is not True or record.get("active") is not True:
            raise StateError(f"project memory record must be retained: {relative}")
    elif kind == "runtime":
        if record.get("retain_when_disabled") is not True:
            raise StateError(f"runtime record must be retained: {relative}")
        source = _safe_target(project, relative)
        if source.is_symlink() or not source.is_file():
            raise StateError(f"runtime record is not in the installed source inventory: {relative}")
        if data != source.read_bytes():
            raise StateError(f"runtime record does not match its source inventory: {relative}")
    elif kind in {"agent", "skill"}:
        if record.get("retain_when_disabled") is not False:
            raise StateError(f"active record must not be retained: {relative}")
        source = _runtime_source_for_active(project, relative, kind)
        if source.is_symlink() or not source.is_file():
            raise StateError(f"active record is not in the installed source inventory: {relative}")
        runtime_root = _safe_target(project, f"{STATE_DIR_NAME}/plugin")
        expected = _render(source.read_bytes(), runtime_root)
        configured = _configured_agent_bytes(relative, expected, config) if kind == "agent" else expected
        if data not in {expected, configured}:
            raise StateError(f"active record does not match its source inventory: {relative}")
    return kind


def _state_dir(project: Path) -> Path:
    path = _safe_target(project, STATE_DIR_NAME)
    if path.exists() and not path.is_dir():
        raise UnsafePathError(f"state directory is not a directory: {path}")
    if path.is_symlink():
        raise UnsafePathError(f"state directory is a symlink: {path}")
    return path


def _state_path(project: Path) -> Path:
    return _safe_target(project, f"{STATE_DIR_NAME}/{STATE_FILE_NAME}")


def _config_path(project: Path) -> Path:
    return _safe_target(project, f"{STATE_DIR_NAME}/{CONFIG_FILE_NAME}")


def _lock_path(project: Path) -> Path:
    return _safe_target(project, f"{STATE_DIR_NAME}/{LOCK_FILE_NAME}")


def _stable_lock_path(project: Path) -> Path:
    """Return the lock inode that survives removal of the state directory."""

    return _safe_target(project, STABLE_LOCK_FILE_NAME)


@contextlib.contextmanager
def project_lock(project: str | os.PathLike[str]) -> Iterator[Path]:
    """Take an exclusive lock for one project lifecycle mutation."""

    root = _project_path(project, create=True)
    directory = _state_dir(root)
    legacy_lock = _lock_path(root)
    # Older installations placed the lock inside the removable state
    # directory.  Acquire it when it already exists so an in-flight/legacy
    # caller remains serialized, but do not create a second removable inode.
    legacy_exists = legacy_lock.exists()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LifecycleError(f"could not create lifecycle state directory: {directory}") from exc
    if fcntl is None:  # pragma: no cover - Windows fallback below
        raise LifecycleError("project locking is unavailable on this platform")
    handles: list[tuple[Path, Any]] = []
    try:
        # Every current process takes the stable root lock first.  The
        # optional legacy lock is second, avoiding a lock-order deadlock.
        lock_paths = [_stable_lock_path(root)]
        if legacy_exists:
            lock_paths.append(legacy_lock)
        for lock_path in lock_paths:
            try:
                handle = lock_path.open("a+")
            except OSError as exc:
                raise LifecycleError(f"could not open project lock: {lock_path}") from exc
            handles.append((lock_path, handle))
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                raise LifecycleError(f"could not lock project: {root}") from exc
        yield root
    finally:
        for _lock_path_value, handle in reversed(handles):
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            handle.close()


class MutationTransaction:
    """Track file bytes and newly created directories for rollback."""

    def __init__(self, project: Path):
        self.project = project
        self._snapshots: dict[Path, tuple[str, bytes | None, int | None]] = {}
        self._created_dirs: list[Path] = []
        self._temporary_files: list[Path] = []
        self._closed = False

    def _snapshot(self, path: Path) -> None:
        if path in self._snapshots:
            return
        if path.is_symlink():
            raise UnsafePathError(f"refusing to mutate symlink: {path}")
        if path.exists():
            if path.is_dir():
                self._snapshots[path] = ("dir", None, None)
            elif path.is_file():
                try:
                    data = path.read_bytes()
                    mode = path.stat().st_mode & 0o777
                except OSError as exc:
                    raise LifecycleError(f"could not snapshot {path}") from exc
                self._snapshots[path] = ("file", data, mode)
            else:
                raise UnsafePathError(f"unsupported managed filesystem entry: {path}")
        else:
            self._snapshots[path] = ("missing", None, None)

    def _parents(self, path: Path) -> None:
        missing: list[Path] = []
        current = path.parent
        while current != self.project and not current.exists():
            missing.append(current)
            current = current.parent
        if current.is_symlink():
            raise UnsafePathError(f"managed parent is a symlink: {current}")
        for directory in reversed(missing):
            directory.mkdir()
            self._created_dirs.append(directory)

    def write(self, path: Path, data: bytes, *, mode: int = 0o644) -> None:
        if self._closed:
            raise TransactionError("transaction is closed")
        self._snapshot(path)
        self._parents(path)
        temporary: Path | None = None
        try:
            fd, name = tempfile.mkstemp(prefix=".cw-write-", dir=str(path.parent))
            temporary = Path(name)
            self._temporary_files.append(temporary)
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
            self._temporary_files.remove(temporary)
        except Exception:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
            raise

    def delete(self, path: Path) -> None:
        if self._closed:
            raise TransactionError("transaction is closed")
        self._snapshot(path)
        if not path.exists():
            return
        if not path.is_file():
            raise UnsafePathError(f"managed target is not a regular file: {path}")
        path.unlink()

    def rollback(self) -> None:
        if self._closed:
            return
        rollback_errors: list[BaseException] = []
        for temporary in list(self._temporary_files):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                rollback_errors.append(exc)
        for path, (kind, data, mode) in reversed(list(self._snapshots.items())):
            try:
                if kind == "missing":
                    if path.exists() or path.is_symlink():
                        if path.is_dir() and not path.is_symlink():
                            path.rmdir()
                        else:
                            path.unlink()
                elif kind == "file" and data is not None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary_fd, temporary_name = tempfile.mkstemp(prefix=".cw-rollback-", dir=str(path.parent))
                    try:
                        with os.fdopen(temporary_fd, "wb") as output:
                            output.write(data)
                            output.flush()
                            os.fsync(output.fileno())
                        os.chmod(temporary_name, mode or 0o644)
                        os.replace(temporary_name, path)
                    finally:
                        try:
                            Path(temporary_name).unlink()
                        except FileNotFoundError:
                            pass
                elif kind == "dir":
                    path.mkdir(parents=True, exist_ok=True)
            except BaseException as exc:  # preserve the original mutation error
                rollback_errors.append(exc)
        for directory in reversed(self._created_dirs):
            try:
                directory.rmdir()
            except OSError as exc:
                if exc.errno not in (errno.ENOENT, errno.ENOTEMPTY, errno.EEXIST):
                    rollback_errors.append(exc)
        self._closed = True
        if rollback_errors:
            raise TransactionError("mutation failed and rollback was incomplete") from rollback_errors[0]

    def commit(self) -> None:
        self._closed = True
        for temporary in self._temporary_files:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def __enter__(self) -> "MutationTransaction":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.commit()
            return False
        try:
            self.rollback()
        except TransactionError:
            # A rollback failure is more actionable than the original error,
            # but keep the original exception chained for diagnostics.
            raise
        return False


def _read_state(project: Path) -> dict[str, Any] | None:
    path = _state_path(project)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise StateError(f"state file is not a regular file: {path}")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateError(f"malformed lifecycle state: {path}") from exc
    if not isinstance(state, dict):
        raise StateError("lifecycle state must be a JSON object")
    _validate_state(state, project)
    return state


def load_state(project: str | os.PathLike[str]) -> dict[str, Any] | None:
    """Read and validate ownership state without changing the project."""

    root = _project_path(project)
    if not root.exists():
        return None
    return _read_state(root)


def _validate_state(state: Mapping[str, Any], project: Path) -> None:
    if state.get("schema") != STATE_SCHEMA:
        raise StateError(f"unsupported lifecycle state schema: {state.get('schema')!r}")
    if state.get("package") != "claude-workflow":
        raise StateError("lifecycle state package is invalid")
    status_value = state.get("status")
    if status_value not in {"enabled", "disabled"}:
        raise StateError("lifecycle state status is invalid")
    if state.get("package_version") != PACKAGE_VERSION:
        raise StateError("lifecycle state package version is invalid")
    if state.get("project") != str(project):
        raise StateError("lifecycle state belongs to a different project")
    config = state.get("config")
    _validate_config(config if isinstance(config, dict) else {})
    try:
        version = state["version"]
        if not isinstance(version, str):
            raise TypeError
        # Import lazily to keep the public lifecycle import lightweight.
        from .releases import parse_version

        parse_version(version)
    except (KeyError, TypeError, ReleaseError) as exc:
        raise StateError("lifecycle state version is invalid") from exc
    files = state.get("files")
    if not isinstance(files, dict):
        raise StateError("lifecycle state files must be an object")
    for relative, record in files.items():
        if not isinstance(relative, str):
            raise StateError("lifecycle state file paths must be strings")
        _safe_relative(relative)
        if relative in {
            f"{STATE_DIR_NAME}/{STATE_FILE_NAME}",
            f"{STATE_DIR_NAME}/{LOCK_FILE_NAME}",
        }:
            raise StateError(f"state may not manage its own control file: {relative}")
        if not isinstance(record, dict):
            raise StateError(f"state record for {relative!r} is invalid")
        expected_keys = {"kind", "sha256", "data", "active", "retain_when_disabled"}
        if set(record) != expected_keys:
            raise StateError(f"state record for {relative!r} has unexpected fields")
        data = _unb64(record.get("data"), f"file {relative!r}")
        if record.get("sha256") != _digest(data):
            raise StateError(f"state hash for {relative!r} does not match its bytes")
        if not isinstance(record.get("active"), bool) or not isinstance(record.get("retain_when_disabled"), bool):
            raise StateError(f"state flags for {relative!r} are invalid")
        record_kind = _validate_record_ownership(project, relative, record, data, config)
        if record_kind == "memory" and record["active"] is not True:
            raise StateError(f"project memory record must remain active: {relative}")
        if status_value == "enabled" and not record["active"]:
            raise StateError(f"enabled state has an inactive file record: {relative}")
        if status_value == "disabled" and record["active"] != record["retain_when_disabled"]:
            raise StateError(f"disabled state file flags are inconsistent: {relative}")
    claude = state.get("claude")
    if not isinstance(claude, dict):
        raise StateError("lifecycle state CLAUDE metadata is invalid")
    expected_claude_keys = {
        "existed",
        "original",
        "before_separator",
        "after_separator",
        "block",
        "block_sha256",
    }
    if set(claude) != expected_claude_keys:
        raise StateError("lifecycle state CLAUDE metadata has unexpected fields")
    original = claude.get("original")
    if original is not None:
        _unb64(original, "CLAUDE original")
    existed = claude.get("existed")
    if not isinstance(existed, bool):
        raise StateError("lifecycle state CLAUDE existed flag is invalid")
    if existed != (original is not None):
        raise StateError("lifecycle state CLAUDE existence metadata is inconsistent")
    block = _unb64(claude.get("block"), "CLAUDE block")
    if claude.get("block_sha256") != _digest(block):
        raise StateError("lifecycle state CLAUDE block hash does not match")
    has_current_markers = CLAUDE_START in block and CLAUDE_END in block
    has_legacy_markers = LEGACY_CLAUDE_START in block and LEGACY_CLAUDE_END in block
    if not (has_current_markers or has_legacy_markers):
        raise StateError("lifecycle state CLAUDE block markers are missing")
    before_separator = _unb64(claude.get("before_separator"), "CLAUDE before separator")
    after_separator = _unb64(claude.get("after_separator"), "CLAUDE after separator")
    # These separators are generated by _claude_install and are only newline
    # framing.  Treating arbitrary state bytes as separators would allow a
    # forged manifest to strip user content while removing the marker block.
    if before_separator not in {b"", b"\n"} or after_separator != b"\n":
        raise StateError("lifecycle state CLAUDE separators are invalid")
    config_record = files.get(f"{STATE_DIR_NAME}/{CONFIG_FILE_NAME}")
    if not isinstance(config_record, dict):
        raise StateError("lifecycle state has no managed config record")
    try:
        recorded_config = json.loads(_unb64(config_record["data"], "config file").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, StateError) as exc:
        raise StateError("managed config bytes are malformed") from exc
    if recorded_config != config:
        raise StateError("managed config bytes do not match lifecycle state")


def _verify_file_records(project: Path, state: Mapping[str, Any]) -> None:
    status_value = state["status"]
    for relative, record in state["files"].items():
        target = _safe_target(project, relative)
        # agent_docs is durable project memory.  It may be edited or removed
        # by the project owner at any time, and lifecycle operations preserve
        # the current bytes rather than treating those edits as a conflict.
        if record["kind"] == "memory":
            continue
        present = target.exists()
        expected_present = bool(record["active"]) or bool(record["retain_when_disabled"])
        if not present:
            if expected_present:
                raise ConflictError(f"managed file is missing: {relative}")
            continue
        if target.is_symlink() or not target.is_file():
            raise ConflictError(f"managed file is not a regular file: {relative}")
        actual = _digest(target.read_bytes())
        if actual != record["sha256"]:
            raise ConflictError(f"managed file was modified: {relative}")
        if status_value == "disabled" and not record["retain_when_disabled"] and record["active"]:
            raise StateError(f"disabled state has active file record: {relative}")


def _claude_bytes(project: Path) -> bytes | None:
    path = _safe_target(project, CLAUDE_FILE_NAME)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ConflictError("CLAUDE.md is not a regular file")
    return path.read_bytes()


def _extract_claude_block(data: bytes) -> tuple[int, int, bytes] | None:
    markers = [(CLAUDE_START, CLAUDE_END), (LEGACY_CLAUDE_START, LEGACY_CLAUDE_END)]
    found = [(data.find(start), start, end) for start, end in markers if data.find(start) >= 0 or data.find(end) >= 0]
    if not found:
        return None
    # Only one marker style may occur in a document.  Mixed/repeated markers
    # are treated as a conflict instead of guessing which bytes we own.
    if len(found) != 1:
        raise ConflictError("CLAUDE.md contains mixed managed marker styles")
    start, start_marker, end_marker = found[0]
    end = data.find(end_marker)
    if start < 0 or end < 0 or data.find(start_marker, start + 1) >= 0 or data.find(end_marker, end + 1) >= 0:
        raise ConflictError("CLAUDE.md contains malformed or repeated managed markers")
    if end < start:
        raise ConflictError("CLAUDE.md managed markers are out of order")
    end += len(end_marker)
    return start, end, data[start:end]


def _verify_claude(project: Path, state: Mapping[str, Any]) -> None:
    data = _claude_bytes(project)
    metadata = state["claude"]
    block = _unb64(metadata["block"], "CLAUDE block")
    extracted = _extract_claude_block(data) if data is not None else None
    if state["status"] == "enabled":
        if extracted is None or extracted[2] != block:
            raise ConflictError("CLAUDE.md managed region is missing or modified")
    elif extracted is not None:
        raise ConflictError("disabled project still contains a managed CLAUDE.md region")
    elif metadata.get("existed") and data is None:
        raise ConflictError("CLAUDE.md was removed while the project was disabled")


def _verify_state(project: Path, state: Mapping[str, Any]) -> None:
    _verify_file_records(project, state)
    _verify_claude(project, state)


def _record(relative: str, data: bytes, *, active: bool, retain_when_disabled: bool) -> dict[str, Any]:
    return {
        "kind": _record_kind(relative),
        "sha256": _digest(data),
        "data": _b64(data),
        "active": active,
        "retain_when_disabled": retain_when_disabled,
    }


def _plugin_files(root: Path) -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise UnsafePathError(f"plugin source contains a symlink: {relative}")
        if any(part in {"__pycache__", ".pytest_cache", ".mypy_cache"} for part in relative.parts) or path.suffix == ".pyc":
            continue
        if path.is_file():
            _safe_relative(relative.as_posix())
            files.append((relative.as_posix(), path.read_bytes()))
    return sorted(files)


def _active_name(relative: str) -> str | None:
    path = PurePosixPath(relative)
    if path.parts[0] == "agents" and len(path.parts) == 2 and path.parts[1].endswith(".md"):
        name = path.parts[1] if path.parts[1].startswith("cw-") else "cw-" + path.parts[1]
        return PurePosixPath(".claude", "agents", name).as_posix()
    if path.parts[0] == "skills" and len(path.parts) >= 2:
        name = path.parts[1] if path.parts[1].startswith("cw-") else "cw-" + path.parts[1]
        return PurePosixPath(".claude", "skills", name, *path.parts[2:]).as_posix()
    return None


def _render(data: bytes, runtime_root: Path) -> bytes:
    # The native usage skill puts this token inside double quotes, while the
    # workflow skill also uses it in Markdown path references.  Preserve
    # spaces literally so the quoted shell example remains correct, and
    # reject characters that could become shell syntax in either context.
    rendered_root = _runtime_root_text(runtime_root)
    return data.replace(PLUGIN_ROOT_TOKEN, rendered_root.encode("utf-8"))


def _validate_model_id(value: Any, label: str = "model") -> str:
    if not isinstance(value, str) or not value or value != value.strip() or any(ord(character) < 0x20 for character in value):
        raise LifecycleError(f"{label} must be a non-empty model ID without control characters")
    return value


def _validate_config(config: Mapping[str, Any]) -> None:
    if not isinstance(config, dict) or config.get("route") not in ROUTES:
        raise StateError("lifecycle state config route is invalid")
    models = config.get("models")
    if not isinstance(models, dict) or set(models) != MODEL_ROLES:
        raise StateError("lifecycle state config models are invalid")
    for role in MODEL_ROLES:
        try:
            _validate_model_id(models[role], f"model for {role}")
        except LifecycleError as exc:
            raise StateError(str(exc)) from exc


def _agent_model_value(model: str) -> str:
    """Encode a model ID as one safe YAML frontmatter scalar."""

    # Keep common IDs readable while quoting values containing YAML syntax.
    if all(character.isalnum() or character in "._:+/@~-" for character in model):
        return model
    return "'" + model.replace("'", "''") + "'"


def _replace_agent_model(data: bytes, model: str) -> bytes:
    """Replace/add the first frontmatter ``model`` field while preserving body bytes."""

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LifecycleError("agent definition is not valid UTF-8") from exc
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise LifecycleError("agent definition has no YAML frontmatter")
    closing: int | None = None
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == "---":
            closing = index
            break
    if closing is None:
        raise LifecycleError("agent definition has unterminated YAML frontmatter")
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    replacement = f"model: {_agent_model_value(model)}{newline}"
    for index in range(1, closing):
        line = lines[index]
        content = line.rstrip("\r\n")
        if content.lstrip().startswith("model:"):
            indent = content[: len(content) - len(content.lstrip())]
            lines[index] = indent + replacement
            return "".join(lines).encode("utf-8")
    lines.insert(closing, replacement)
    return "".join(lines).encode("utf-8")


def _configured_agent_bytes(
    relative: str,
    data: bytes,
    config: Mapping[str, Any],
) -> bytes:
    path = PurePosixPath(relative)
    if len(path.parts) != 3 or path.parts[:2] != (".claude", "agents"):
        return data
    name = path.parts[2]
    if not name.startswith("cw-") or not name.endswith(".md"):
        return data
    role = AGENT_MODEL_ROLES.get(name[3:-3])
    if role is None:
        return data
    models = config.get("models")
    if not isinstance(models, Mapping) or role not in models:
        raise StateError(f"configured model for {role} is missing")
    model = _validate_model_id(models[role], f"model for {role}")
    return _replace_agent_model(data, model)


def _apply_agent_model_overrides(
    assets: dict[str, tuple[bytes, bool]],
    config: Mapping[str, Any],
) -> None:
    for relative, (data, retain) in list(assets.items()):
        if relative.startswith(".claude/agents/"):
            assets[relative] = (_configured_agent_bytes(relative, data, config), retain)


def _runtime_root_text(runtime_root: Path) -> str:
    value = str(runtime_root)
    unsafe = {
        "'",
        '"',
        "`",
        "$",
        "\\",
        ";",
        "&",
        "|",
        "<",
        ">",
        "*",
        "?",
        "[",
        "]",
        "{",
        "}",
        "!",
        "(",
        ")",
    }
    if any(character in unsafe or ord(character) < 0x20 for character in value):
        raise UnsafePathError(
            "project path contains shell-unsafe characters; choose a path without quotes, "
            "substitution syntax, separators, wildcards, or control characters"
        )
    return value


def _default_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_CONFIG)


def _config_bytes(config: Mapping[str, Any]) -> bytes:
    return (json.dumps(config, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _claude_install(
    data: bytes | None,
    project: Path,
    version: str,
    template: bytes | None = None,
) -> tuple[bytes, dict[str, Any]]:
    existing = data or b""
    if _extract_claude_block(existing) is not None:
        raise ConflictError("CLAUDE.md already contains a claude-workflow managed region")
    before_separator = b"" if not existing or existing.endswith(b"\n") else b"\n"
    after_separator = b"\n"
    block = None
    if template:
        template_block = _extract_claude_block(template)
        if template_block is None:
            raise ReleaseError("plugin CLAUDE.md template has no managed region")
        block = _render(template_block[2], _safe_target(project, f"{STATE_DIR_NAME}/plugin"))
    if block is None:
        runtime_text = _runtime_root_text(_safe_target(project, f"{STATE_DIR_NAME}/plugin"))
        block = (
            CLAUDE_START
            + b"\n"
            + b"Claude Workflow managed project entrypoint.\n"
            + b"Project-scoped assets are under .claude/ and .claude-workflow/.\n"
            + b"Runtime plugin root: "
            + runtime_text.encode("utf-8")
            + b"\n"
            + CLAUDE_END
        )
    installed = existing + before_separator + block + after_separator
    metadata = {
        "existed": data is not None,
        "original": _b64(data) if data is not None else None,
        "before_separator": _b64(before_separator),
        "after_separator": _b64(after_separator),
        "block": _b64(block),
        "block_sha256": _digest(block),
    }
    return installed, metadata


def _claude_remove(data: bytes | None, metadata: Mapping[str, Any]) -> bytes | None:
    if data is None:
        return None
    extracted = _extract_claude_block(data)
    if extracted is None:
        # A disabled project already had its managed block removed.  Preserve
        # any user edits made after that transition.
        return data
    expected = _unb64(metadata["block"], "CLAUDE block")
    if extracted[2] != expected:
        raise ConflictError("CLAUDE.md managed region was modified")
    original = metadata.get("original")
    original_bytes = _unb64(original, "CLAUDE original") if original is not None else None
    if original_bytes is not None:
        # This also preserves every byte of an existing document when the
        # surrounding user content was untouched.
        before = data[:extracted[0]]
        after = data[extracted[1]:]
        if before.endswith(_unb64(metadata["before_separator"], "CLAUDE separator")):
            before = before[: -len(_unb64(metadata["before_separator"], "CLAUDE separator"))] if _unb64(metadata["before_separator"], "CLAUDE separator") else before
        suffix_separator = _unb64(metadata["after_separator"], "CLAUDE separator")
        if suffix_separator and after.startswith(suffix_separator):
            after = after[len(suffix_separator):]
        if before + after == original_bytes:
            return original_bytes
    before_separator = _unb64(metadata["before_separator"], "CLAUDE separator")
    after_separator = _unb64(metadata["after_separator"], "CLAUDE separator")
    before = data[:extracted[0]]
    after = data[extracted[1]:]
    if before_separator and before.endswith(before_separator):
        before = before[: -len(before_separator)]
    if after_separator and after.startswith(after_separator):
        after = after[len(after_separator):]
    result = before + after
    if not result and not metadata.get("existed", False):
        return None
    return result


def _updated_claude(
    project: Path,
    state: Mapping[str, Any],
    source_root: Path,
) -> tuple[dict[str, Any], bytes | None]:
    """Return updated marker metadata and enabled-document bytes, if needed."""

    metadata = copy.deepcopy(state["claude"])
    template_path = source_root / "templates" / "CLAUDE.md"
    if not template_path.is_file() or template_path.is_symlink():
        return metadata, None
    template_block = _extract_claude_block(template_path.read_bytes())
    if template_block is None:
        raise ReleaseError("plugin CLAUDE.md template has no managed region")
    new_block = _render(template_block[2], _safe_target(project, f"{STATE_DIR_NAME}/plugin"))
    old_block = _unb64(metadata["block"], "CLAUDE block")
    if new_block == old_block:
        return metadata, None
    metadata["block"] = _b64(new_block)
    metadata["block_sha256"] = _digest(new_block)
    if state["status"] != "enabled":
        return metadata, None
    current = _claude_bytes(project)
    extracted = _extract_claude_block(current or b"")
    if current is None or extracted is None or extracted[2] != old_block:
        raise ConflictError("CLAUDE.md managed region is missing or modified")
    return metadata, current[: extracted[0]] + new_block + current[extracted[1] :]


def _build_assets(
    root: Path,
    project: Path,
    config: Mapping[str, Any],
    *,
    existing_owned: set[str] | None = None,
) -> dict[str, tuple[bytes, bool]]:
    """Return target relative path -> (bytes, retain when disabled)."""

    runtime_root = _safe_target(project, f"{STATE_DIR_NAME}/plugin")
    assets: dict[str, tuple[bytes, bool]] = {}
    plugin_files = _plugin_files(root)
    for relative, data in plugin_files:
        target = PurePosixPath(STATE_DIR_NAME, "plugin", relative).as_posix()
        assets[target] = (data, True)
        active = _active_name(relative)
        if active is not None:
            rendered = _render(data, runtime_root)
            if active in assets:
                raise ReleaseError(f"plugin sources collide at active path: {active}")
            assets[active] = (rendered, False)
        # Templates provide the six durable project-memory documents.  Only
        # materialize a missing document; an existing project document is
        # user-owned and remains outside our manifest.
        template_prefix = "templates/agent_docs/"
        if relative.startswith(template_prefix):
            memory_name = relative[len(template_prefix) :]
            memory_target = f"agent_docs/{memory_name}" if memory_name else ""
            if memory_target and not _safe_target(project, memory_target).exists():
                assets[memory_target] = (data, True)
    config_data = _config_bytes(config)
    assets[f"{STATE_DIR_NAME}/{CONFIG_FILE_NAME}"] = (config_data, True)
    return assets


def _state_summary(state: Mapping[str, Any], project: Path, *, idempotent: bool = False) -> dict[str, Any]:
    return {
        "project": str(project),
        "status": state["status"],
        "version": state["version"],
        "managed_count": len(state["files"]),
        "active_count": sum(1 for record in state["files"].values() if record["active"]),
        "config": copy.deepcopy(state["config"]),
        "source": copy.deepcopy(state["source"]),
        "idempotent": idempotent,
    }


def _state_for_assets(
    project: Path,
    assets: Mapping[str, tuple[bytes, bool]],
    *,
    version: str,
    source: Mapping[str, Any],
    config: Mapping[str, Any],
    claude: Mapping[str, Any],
    status_value: str = "enabled",
) -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "package": "claude-workflow",
        "package_version": PACKAGE_VERSION,
        "project": str(project),
        "status": status_value,
        "version": version,
        "source": dict(source),
        "config": copy.deepcopy(dict(config)),
        "claude": dict(claude),
        "files": {
            relative: _record(
                relative,
                data,
                active=(status_value == "enabled" or retain),
                retain_when_disabled=retain,
            )
            for relative, (data, retain) in sorted(assets.items())
        },
    }


def _save_state(transaction: MutationTransaction, project: Path, state: Mapping[str, Any]) -> None:
    payload = (json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    transaction.write(_state_path(project), payload)


def _assert_state_dir_clean(project: Path) -> None:
    directory = _state_dir(project)
    if not directory.exists():
        return
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise UnsafePathError(f"state directory contains a symlink: {path}")
        if path.name == LOCK_FILE_NAME and path.is_file():
            continue
        if path.is_file() or not path.is_dir():
            raise ConflictError(f"unowned lifecycle state file exists: {path}")


def _assert_unowned_targets(project: Path, assets: Mapping[str, tuple[bytes, bool]]) -> None:
    for relative in assets:
        target = _safe_target(project, relative)
        if target.exists():
            raise ConflictError(f"install would overwrite an unowned file: {relative}")


def _require_state(project: Path) -> dict[str, Any]:
    state = _read_state(project)
    if state is None:
        raise LifecycleError(f"claude-workflow is not installed in {project}")
    return state


def install(
    project: str | os.PathLike[str],
    *,
    source: str | os.PathLike[str] | None = None,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Install bundled or supplied plugin assets into one project."""

    root = _project_path(project, create=True)
    # Validate the eventual path before taking the lock, which would create
    # `.claude-workflow` as a side effect even when rendering must fail.
    _runtime_root_text(_safe_target(root, f"{STATE_DIR_NAME}/plugin"))
    source_value = source if source is not None else Path(__file__).parent / "plugin"
    with resolve_source(source_value, checksum=checksum) as resolved:
        with project_lock(root):
            current = _read_state(root)
            if current is not None:
                _verify_state(root, current)
                return _state_summary(current, root, idempotent=True)
            _assert_state_dir_clean(root)
            config = _default_config()
            assets = _build_assets(resolved.root, root, config)
            _assert_unowned_targets(root, assets)
            claude_path = _safe_target(root, CLAUDE_FILE_NAME)
            existing_claude = _claude_bytes(root)
            template_path = resolved.root / "templates" / "CLAUDE.md"
            template_data = template_path.read_bytes() if template_path.is_file() and not template_path.is_symlink() else None
            installed_claude, claude_metadata = _claude_install(existing_claude, root, resolved.version, template_data)
            state = _state_for_assets(
                root,
                assets,
                version=resolved.version,
                source={"kind": resolved.kind, "name": resolved.name, "manifest_sha256": _manifest_hash(resolved.root)},
                config=config,
                claude=claude_metadata,
            )
            with MutationTransaction(root) as transaction:
                transaction.write(claude_path, installed_claude)
                for relative, (data, _retain) in sorted(assets.items()):
                    transaction.write(_safe_target(root, relative), data)
                _save_state(transaction, root, state)
            return _state_summary(state, root)


def bootstrap(
    project: str | os.PathLike[str],
    *,
    package: str | os.PathLike[str],
    checksum: str,
) -> dict[str, Any]:
    """Install a complete workflow from one checksum-verified release ZIP.

    Bootstrap is intentionally project-scoped.  It never writes user-level
    Claude settings: Claude Code loads the materialized agents and skills from
    the project after the required documentation handoff is completed.
    """

    package_path = Path(package).expanduser()
    if package_path.is_symlink() or not package_path.is_file() or package_path.suffix.lower() != ".zip":
        raise LifecycleError("bootstrap requires a real local .zip release package")
    if not checksum or not checksum.strip():
        raise LifecycleError("bootstrap requires --sha256 for the release package")

    # Validate the complete release before creating the target project or its
    # stable lock file. A bad checksum must be a read-only failure.
    validation = validate_plugin(package_path, checksum=checksum, require_complete=True)
    root = _project_path(project, create=True)
    existing_framework = {
        relative for relative in BOOTSTRAP_FRAMEWORK if _safe_target(root, relative).exists()
    }
    result = install(root, source=package_path, checksum=checksum)
    state = _require_state(root)
    created_files = sorted(
        relative
        for relative in BOOTSTRAP_FRAMEWORK
        if relative not in existing_framework and relative in state["files"]
    )
    action = {
        "agent": "cw-archivist",
        "task_id": "bootstrap_docs",
        "required": True,
        "framework": list(BOOTSTRAP_FRAMEWORK),
        "files": created_files,
        "created_files": created_files,
        "recovery_files": [],
        "required_context_files": [
            relative
            for relative in (
                "agent_docs/project_overview.md",
                "agent_docs/project_structure.md",
                "agent_docs/project_core_tech.md",
            )
            if relative in created_files
        ],
        "guidance": (
            "Use the verified project evidence to initialize only files listed in files; "
            "preserve existing project documents and do not modify source, Git state, or user-level Claude settings."
        ),
    }
    result.update(
        {
            "bootstrapped": True,
            "package": str(package_path),
            "package_validation": validation,
            "agent_actions": [action],
        }
    )
    return result


def _manifest_hash(root: Path) -> str:
    return _digest((root / ".claude-plugin" / "plugin.json").read_bytes())


def status(project: str | os.PathLike[str]) -> dict[str, Any]:
    """Return status and verify all owned bytes before reporting healthy state."""

    root = _project_path(project)
    if not root.exists():
        return {"project": str(root), "status": "not-installed", "managed_count": 0, "active_count": 0}
    state = _read_state(root)
    if state is None:
        return {"project": str(root), "status": "not-installed", "managed_count": 0, "active_count": 0}
    _verify_state(root, state)
    return _state_summary(state, root)


def configure(
    project: str | os.PathLike[str],
    *,
    route: str | None = None,
    models: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Update route/model configuration while preserving all other settings."""

    if route is not None:
        route = route.lower()
        if route not in ROUTES:
            raise LifecycleError(f"route must be one of: {', '.join(sorted(ROUTES))}")
    supplied_models = dict(models or {})
    for role, model in supplied_models.items():
        if role not in MODEL_ROLES:
            raise LifecycleError(f"unknown model role: {role}")
        _validate_model_id(model, f"model for {role}")
    root = _project_path(project)
    with project_lock(root):
        state = _require_state(root)
        _verify_state(root, state)
        config = copy.deepcopy(state["config"])
        if route is not None:
            config["route"] = route
        config.setdefault("models", {}).update(supplied_models)
        _validate_config(config)
        config_data = _config_bytes(config)
        target = _config_path(root)
        new_state = copy.deepcopy(state)
        relative = f"{STATE_DIR_NAME}/{CONFIG_FILE_NAME}"
        new_state["config"] = config
        new_state["files"][relative] = _record(
            relative,
            config_data,
            active=True,
            retain_when_disabled=True,
        )
        for agent_relative, record in state["files"].items():
            if record["kind"] != "agent":
                continue
            source = _runtime_source_for_active(root, agent_relative, "agent")
            runtime_root = _safe_target(root, f"{STATE_DIR_NAME}/plugin")
            rendered = _render(source.read_bytes(), runtime_root)
            configured = _configured_agent_bytes(agent_relative, rendered, config)
            new_state["files"][agent_relative] = _record(
                agent_relative,
                configured,
                active=record["active"],
                retain_when_disabled=record["retain_when_disabled"],
            )
        with MutationTransaction(root) as transaction:
            transaction.write(target, config_data)
            if state["status"] == "enabled":
                for agent_relative, record in new_state["files"].items():
                    if record["kind"] == "agent":
                        transaction.write(
                            _safe_target(root, agent_relative),
                            _unb64(record["data"], f"file {agent_relative!r}"),
                        )
            _save_state(transaction, root, new_state)
        return _state_summary(new_state, root)


def disable(project: str | os.PathLike[str]) -> dict[str, Any]:
    """Remove active project entry points while retaining owned runtime state."""

    root = _project_path(project)
    with project_lock(root):
        state = _require_state(root)
        _verify_state(root, state)
        if state["status"] == "disabled":
            return _state_summary(state, root, idempotent=True)
        new_state = copy.deepcopy(state)
        new_state["status"] = "disabled"
        for record in new_state["files"].values():
            if not record["retain_when_disabled"]:
                record["active"] = False
        current_claude = _claude_bytes(root)
        removed_claude = _claude_remove(current_claude, state["claude"])
        with MutationTransaction(root) as transaction:
            for relative, record in state["files"].items():
                if not record["retain_when_disabled"]:
                    transaction.delete(_safe_target(root, relative))
            claude_path = _safe_target(root, CLAUDE_FILE_NAME)
            if removed_claude is None:
                transaction.delete(claude_path)
            else:
                transaction.write(claude_path, removed_claude)
            _save_state(transaction, root, new_state)
        _prune_empty_dirs(root)
        return _state_summary(new_state, root)


def enable(project: str | os.PathLike[str]) -> dict[str, Any]:
    """Restore active project entry points from recorded owned bytes."""

    root = _project_path(project)
    with project_lock(root):
        state = _require_state(root)
        _verify_state(root, state)
        if state["status"] == "enabled":
            return _state_summary(state, root, idempotent=True)
        new_state = copy.deepcopy(state)
        new_state["status"] = "enabled"
        for relative, record in new_state["files"].items():
            if not record["retain_when_disabled"]:
                record["active"] = True
        current_claude = _claude_bytes(root)
        if _extract_claude_block(current_claude or b"") is not None:
            raise ConflictError("CLAUDE.md already contains a managed region while disabled")
        block = _unb64(state["claude"]["block"], "CLAUDE block")
        before_separator = _unb64(state["claude"]["before_separator"], "CLAUDE separator")
        after_separator = _unb64(state["claude"]["after_separator"], "CLAUDE separator")
        existing = current_claude or b""
        installed_claude = existing + before_separator + block + after_separator
        with MutationTransaction(root) as transaction:
            for relative, record in state["files"].items():
                if not record["retain_when_disabled"]:
                    transaction.write(_safe_target(root, relative), _unb64(record["data"], f"file {relative!r}"))
            transaction.write(_safe_target(root, CLAUDE_FILE_NAME), installed_claude)
            _save_state(transaction, root, new_state)
        return _state_summary(new_state, root)


def _prune_empty_dirs(root: Path) -> None:
    # Only remove directories below roots owned by this package, and stop at
    # the project root.  Any unrelated file naturally prevents removal.
    for relative in (".claude", STATE_DIR_NAME):
        top = _safe_target(root, relative)
        if not top.is_dir() or top.is_symlink():
            continue
        directories = sorted(
            (path for path in top.rglob("*") if path.is_dir() and not path.is_symlink()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for path in [*directories, top]:
            try:
                path.rmdir()
            except (FileNotFoundError, OSError):
                pass


def remove(
    project: str | os.PathLike[str],
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Preview removal by default; apply only with an explicit flag."""

    root = _project_path(project)
    if not apply:
        state = _read_state(root) if root.exists() else None
        if state is None:
            return {"project": str(root), "status": "preview", "managed_count": 0, "paths": []}
        _verify_state(root, state)
        paths = sorted(relative for relative, record in state["files"].items() if record["kind"] != "memory")
        preserved_paths = sorted(relative for relative, record in state["files"].items() if record["kind"] == "memory")
        if state["status"] == "enabled":
            paths.append(CLAUDE_FILE_NAME)
        return {
            "project": str(root),
            "status": "preview",
            "paths": paths,
            "preserved_paths": preserved_paths,
            "managed_count": len(paths),
        }
    with project_lock(root):
        state = _require_state(root)
        _verify_state(root, state)
        with MutationTransaction(root) as transaction:
            for relative, record in sorted(state["files"].items()):
                if record["kind"] != "memory":
                    transaction.delete(_safe_target(root, relative))
            if state["status"] == "enabled":
                current_claude = _claude_bytes(root)
                restored = _claude_remove(current_claude, state["claude"])
                claude_path = _safe_target(root, CLAUDE_FILE_NAME)
                if restored is None:
                    transaction.delete(claude_path)
                else:
                    transaction.write(claude_path, restored)
            transaction.delete(_state_path(root))
            transaction.delete(_lock_path(root))
        _prune_empty_dirs(root)
        return {
            "project": str(root),
            "status": "removed",
            "managed_count": sum(1 for record in state["files"].values() if record["kind"] != "memory"),
            "preserved_count": sum(1 for record in state["files"].values() if record["kind"] == "memory"),
        }


def _current_config(state: Mapping[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(state["config"])


def check_update(
    project: str | os.PathLike[str],
    *,
    source: str | os.PathLike[str] | None = None,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Compare an explicitly supplied source; no remote default is used."""

    root = _project_path(project)
    state = _require_state(root)
    _verify_state(root, state)
    result: dict[str, Any] = {
        "project": str(root),
        "current_version": state["version"],
        "source_supplied": source is not None,
        "update_available": None,
    }
    if source is None:
        result["reason"] = "no update source supplied"
        return result
    with resolve_source(source, checksum=checksum) as resolved:
        result["candidate_version"] = resolved.version
        result["update_available"] = compare_versions(resolved.version, state["version"]) > 0
        result["source_kind"] = resolved.kind
    return result


def update(
    project: str | os.PathLike[str],
    *,
    source: str | os.PathLike[str],
    checksum: str | None = None,
) -> dict[str, Any]:
    """Update from an explicitly supplied validated source or archive."""

    root = _project_path(project)
    with resolve_source(source, checksum=checksum) as resolved:
        with project_lock(root):
            state = _require_state(root)
            _verify_state(root, state)
            config = _current_config(state)
            old_files = state["files"]
            assets = _build_assets(resolved.root, root, config, existing_owned=set(old_files))
            # Existing agent_docs are user-facing project memory.  Carry the
            # current bytes through an update so edits survive and a removed
            # source template cannot turn into a deletion of project memory.
            for relative, record in old_files.items():
                if record["kind"] != "memory":
                    continue
                target = _safe_target(root, relative)
                if target.exists():
                    if target.is_symlink() or not target.is_file():
                        raise ConflictError(f"project memory is not a regular file: {relative}")
                    assets[relative] = (target.read_bytes(), True)
            _apply_agent_model_overrides(assets, config)
            claude_metadata, updated_claude = _updated_claude(root, state, resolved.root)
            # New generated targets must not shadow user files.  Existing
            # targets are safe to replace only because their old hashes were
            # verified immediately above.
            for relative in assets:
                if relative not in old_files:
                    target = _safe_target(root, relative)
                    if target.exists():
                        raise ConflictError(f"update would overwrite an unowned file: {relative}")
            new_state = _state_for_assets(
                root,
                assets,
                version=resolved.version,
                source={"kind": resolved.kind, "name": resolved.name, "manifest_sha256": _manifest_hash(resolved.root)},
                config=config,
                claude=claude_metadata,
                status_value=state["status"],
            )
            with MutationTransaction(root) as transaction:
                for relative in sorted(set(old_files) - set(assets)):
                    if old_files[relative]["kind"] != "memory":
                        transaction.delete(_safe_target(root, relative))
                for relative, (data, retain) in sorted(assets.items()):
                    if state["status"] == "enabled" or retain:
                        transaction.write(_safe_target(root, relative), data)
                if updated_claude is not None:
                    transaction.write(_safe_target(root, CLAUDE_FILE_NAME), updated_claude)
                _save_state(transaction, root, new_state)
            _prune_empty_dirs(root)
            return _state_summary(new_state, root)


def report_command(
    *,
    transcripts: list[str | os.PathLike[str]],
    since: str | None = None,
    until: str | None = None,
    json_output: bool = False,
) -> str:
    """Delegate report parsing to the standalone native-plugin script."""

    import subprocess
    import sys

    script = Path(__file__).parent / "plugin" / "scripts" / "usage_report.py"
    if not script.is_file():
        raise LifecycleError(f"usage report script is unavailable: {script}")
    command = [sys.executable, str(script)]
    for transcript in transcripts:
        command.extend(("--transcript", os.fspath(transcript)))
    if since is not None:
        command.extend(("--since", since))
    if until is not None:
        command.extend(("--until", until))
    if json_output:
        command.append("--json")
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as exc:
        raise LifecycleError("could not execute usage report script") from exc
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise LifecycleError(detail or f"usage report exited with {completed.returncode}")
    return completed.stdout
