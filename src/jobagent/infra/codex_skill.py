"""Read and conservatively install the product-managed Codex skill.

This module never reads credentials, account state or a browser. The ownership
marker is a local file-management convention, not a signature or UI permission.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib import resources
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import tempfile
from typing import Any

from jobagent import __version__


SKILL_NAME = "codex-job-agent"
OWNER = "agentmesh360.jobagent.codex-skill"
MARKER = ".jobagent-managed.json"
FILES = ("SKILL.md", "agents/openai.yaml")


def _bundle() -> dict[str, bytes]:
    root = resources.files("jobagent").joinpath("data", "codex_skill")
    return {name: root.joinpath(*name.split("/")).read_bytes() for name in FILES}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def skill_contract() -> dict[str, Any]:
    """Return the installed client's complete, current instructions for hosts."""
    files = _bundle()
    return {
        "ok": True,
        "event": "codex_skill_contract",
        "skill_name": SKILL_NAME,
        "client_version": __version__,
        "protocol": "jobagent.browser_work",
        "protocol_version": 1,
        "instructions": files["SKILL.md"].decode("utf-8"),
        "file_sha256": {name: _digest(data) for name, data in files.items()},
        "next_suggested": "jobagent work next",
    }


def _default_target() -> Path:
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    return home / "skills" / SKILL_NAME


def _safe_relative(name: object) -> bool:
    if not isinstance(name, str) or "\\" in name:
        return False
    path = PurePosixPath(name)
    windows = PureWindowsPath(name)
    return not path.is_absolute() and not windows.drive and not windows.root and bool(path.parts) and all(
        part not in {".", ".."} for part in path.parts
    ) and str(path) == name


def _conflict(target: Path, code: str, paths: list[str]) -> dict[str, Any]:
    return {
        "ok": False,
        "event": "codex_skill_install_conflict",
        "error": code,
        "skill_name": SKILL_NAME,
        "target": str(target),
        "conflicts": paths,
        "message": "The Codex skill was not installed; existing files were preserved.",
        "recovery": "Choose a different skill target or review the listed files; no overwrite is automatic.",
    }


def _unsafe_path(target: Path, name: str) -> bool:
    path = target / name
    if path.is_symlink():
        return True
    for parent in path.parents:
        if parent == target:
            break
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            return True
    return path.exists() and not path.is_file()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".jobagent-skill-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _install_locked(target: Path, files: dict[str, bytes]) -> dict[str, Any]:
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        return _conflict(target, "codex_skill_target_conflict", [str(target)])
    old: dict[str, Any] | None = None
    if target.exists():
        marker = target / MARKER
        if not marker.is_file() or marker.is_symlink():
            return _conflict(target, "codex_skill_unmanaged_target", [str(marker)])
        try:
            old = json.loads(marker.read_text(encoding="utf-8"))
            if (
                not isinstance(old, dict)
                or old.get("owner") != OWNER
                or type(old.get("schema_version")) is not int
                or old.get("schema_version") != 1
                or not isinstance(old.get("files"), dict)
                or not old["files"]
                or any(not _safe_relative(name) for name in old["files"])
                or any(not isinstance(digest, str) or len(digest) != 64 for digest in old["files"].values())
            ):
                raise ValueError("Invalid ownership marker")
        except (OSError, ValueError):
            return _conflict(target, "codex_skill_invalid_marker", [str(marker)])

    conflicts: list[str] = []
    previous = old["files"] if old else {}
    for name in set(previous) | set(files):
        path = target / name
        if _unsafe_path(target, name):
            conflicts.append(str(path))
        elif name in previous:
            # Accept already-installed new bytes after an interrupted upgrade,
            # but never overwrite a user's different local modification.
            allowed = {previous[name]}
            if name in files:
                allowed.add(_digest(files[name]))
            if not path.is_file() or _digest(path.read_bytes()) not in allowed:
                conflicts.append(str(path))
        elif path.exists():
            conflicts.append(str(path))
    if conflicts:
        return _conflict(target, "codex_skill_local_changes", sorted(conflicts))

    target.mkdir(parents=True, exist_ok=True)
    changed = []
    for name, data in files.items():
        path = target / name
        if not path.exists() or path.read_bytes() != data:
            _atomic_write(path, data)
            changed.append(name)
    # Retain formerly managed files; never delete user-added or old assets.
    marker_data = {
        "owner": OWNER,
        "schema_version": 1,
        "client_version": __version__,
        "files": {**previous, **{name: _digest(data) for name, data in files.items()}},
    }
    marker_bytes = (json.dumps(marker_data, indent=2, sort_keys=True) + "\n").encode("utf-8")
    marker = target / MARKER
    if not marker.exists() or marker.read_bytes() != marker_bytes:
        _atomic_write(marker, marker_bytes)
    return {
        "ok": True,
        "event": "codex_skill_installed",
        "skill_name": SKILL_NAME,
        "client_version": __version__,
        "target": str(target),
        "status": "installed" if old is None else "updated" if changed or old != marker_data else "current",
        "updated_files": changed,
        "conflicts": [],
        "next_suggested": "jobagent work next",
    }


def install_skill(target: str | Path | None = None) -> dict[str, Any]:
    """Install into a skill directory, preserving custom and modified files.

    ``target`` is the complete skill directory, not the parent skills directory.
    An existing directory without this product's valid marker is a conflict,
    even if it is empty or contains identical bytes.
    """
    destination = (Path(target).expanduser() if target is not None else _default_target()).absolute()
    lock = destination.parent / f".{destination.name}.jobagent-install.lock"
    acquired = False
    try:
        files = _bundle()
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return _conflict(destination, "codex_skill_install_busy", [str(lock)])
        os.close(fd)
        acquired = True
        return _install_locked(destination, files)
    except (OSError, UnicodeError, ValueError) as exc:
        return {
            "ok": False,
            "event": "codex_skill_install_failed",
            "error": "codex_skill_install_failed",
            "target": str(destination),
            "message": f"Codex skill installation could not finish ({type(exc).__name__}); no user files are removed.",
        }
    finally:
        if acquired:
            lock.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    installer = subparsers.add_parser("install", help="Install the managed Codex skill without overwriting custom files")
    installer.add_argument("--target", type=Path, help="Complete destination skill directory")
    subparsers.add_parser("contract", help="Print current host instructions without reading account state")
    args = parser.parse_args()
    result = install_skill(args.target) if args.command == "install" else skill_contract()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
