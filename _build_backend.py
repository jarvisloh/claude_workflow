"""Tiny dependency-free PEP 517 backend used for offline source builds.

The project deliberately has no runtime dependencies.  Keeping wheel
creation in the repository means ``pip wheel --no-deps`` works in an offline
environment where an isolated build cannot download setuptools.
"""

from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parent
NAME = "claude-workflow"
NORMALIZED_NAME = NAME.replace("-", "_")
VERSION = "0.1.0"
DIST_INFO = f"{NORMALIZED_NAME}-{VERSION}.dist-info"


def _record_hash(data: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
    return f"sha256={encoded}"


def _iter_files() -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    package = ROOT / "claude_workflow"
    for path in sorted(package.rglob("*")):
        relative_path = path.relative_to(ROOT)
        if any(part in {"__pycache__", ".pytest_cache", ".mypy_cache"} for part in relative_path.parts) or path.suffix == ".pyc":
            continue
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        files.append((relative, path.read_bytes()))
    return files


def _metadata() -> bytes:
    return (
        "Metadata-Version: 2.1\n"
        f"Name: {NAME}\n"
        f"Version: {VERSION}\n"
        "Summary: Project-scoped Claude Code workflow lifecycle tools\n"
        "Requires-Python: >=3.11\n"
        "License: MIT\n\n"
    ).encode("utf-8")


def _wheel() -> bytes:
    return (
        "Wheel-Version: 1.0\n"
        "Generator: claude-workflow-build-backend\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n\n"
    ).encode("ascii")


def _entry_points() -> bytes:
    return b"[console_scripts]\nclaude-workflow = claude_workflow.cli:main\n"


def _zip_bytes() -> bytes:
    members = _iter_files()
    generated = [
        (f"{DIST_INFO}/METADATA", _metadata()),
        (f"{DIST_INFO}/WHEEL", _wheel()),
        (f"{DIST_INFO}/entry_points.txt", _entry_points()),
    ]
    all_members = members + generated
    records: list[str] = []
    for name, data in all_members:
        records.append(f"{name},{_record_hash(data)},{len(data)}")
    records.append(f"{DIST_INFO}/RECORD,,")
    record_data = ("\n".join(records) + "\n").encode("utf-8")
    all_members.append((f"{DIST_INFO}/RECORD", record_data))
    all_members.sort(key=lambda item: item[0])
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in all_members:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return stream.getvalue()


def build_wheel(wheel_directory: str, config_settings=None, metadata_directory=None) -> str:
    destination = Path(wheel_directory)
    destination.mkdir(parents=True, exist_ok=True)
    filename = f"{NORMALIZED_NAME}-{VERSION}-py3-none-any.whl"
    (destination / filename).write_bytes(_zip_bytes())
    return filename


def get_requires_for_build_wheel(config_settings=None) -> list[str]:
    return []


def prepare_metadata_for_build_wheel(metadata_directory: str, config_settings=None) -> str:
    destination = Path(metadata_directory) / DIST_INFO
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "METADATA").write_bytes(_metadata())
    (destination / "WHEEL").write_bytes(_wheel())
    return DIST_INFO
