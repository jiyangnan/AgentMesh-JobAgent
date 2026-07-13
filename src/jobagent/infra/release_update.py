"""Signed release policy check and safe updates for official managed installs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from jobagent import __version__
from jobagent.infra.cloud_client import PROTOCOL_VERSION
from jobagent.infra.protocol import RELEASE_SIGNING_PUBLIC_KEY, ProtocolError, verify_signed_payload
from jobagent.infra.state import (
    load_json,
    release_cache_path,
    save_json,
    update_lock_path,
)

DEFAULT_CORE_API_BASE = "https://api.agentmesh360.com"
OFFICIAL_REPO_URLS = {
    "https://github.com/jiyangnan/AgentMesh-JobAgent.git",
    "git@github.com:jiyangnan/AgentMesh-JobAgent.git",
}
CACHE_TTL_SECONDS = 5 * 60


class UpdateError(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _update_lock_pid(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, TypeError, ValueError):
        return 0


def _version(value: str) -> tuple[int, int, int]:
    try:
        parts = value.split(".")
        if len(parts) != 3:
            raise ValueError
        return tuple(int(part) for part in parts)  # type: ignore[return-value]
    except (TypeError, ValueError) as exc:
        raise UpdateError(f"invalid semantic version: {value}") from exc


def _core_base() -> str:
    return os.environ.get("JOBAGENT_CORE_API_BASE", DEFAULT_CORE_API_BASE).rstrip("/")


def fetch_release_manifest(*, force: bool = False) -> dict[str, Any] | None:
    cache = load_json(release_cache_path()) or {}
    cached_manifest = cache.get("manifest") if isinstance(cache.get("manifest"), dict) else None
    if not force and cached_manifest and time.time() - float(cache.get("fetched_at", 0)) < CACHE_TTL_SECONDS:
        return cached_manifest
    request = urllib.request.Request(
        _core_base() + "/v1/products/jobagent/client-release",
        headers={"Accept": "application/json", "User-Agent": f"jobagent/{__version__}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            manifest = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return cached_manifest
    if not isinstance(manifest, dict):
        return cached_manifest
    verify_release_manifest(manifest)
    save_json(release_cache_path(), {"fetched_at": time.time(), "manifest": manifest})
    return manifest


def verify_release_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    signed = verify_signed_payload(manifest, public_key=RELEASE_SIGNING_PUBLIC_KEY)
    if signed.get("product") != "jobagent" or signed.get("channel") != "stable":
        raise ProtocolError("release manifest product/channel mismatch")
    if signed.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError("release manifest protocol version mismatch")
    _version(str(signed.get("latest_client_version", "")))
    _version(str(signed.get("minimum_supported_version", "")))
    commit = str(signed.get("git_commit", ""))
    digest = str(signed.get("artifact_sha256", ""))
    if len(commit) != 40 or len(digest) != 64:
        raise ProtocolError("release manifest commit or artifact digest is invalid")
    return signed


def _package_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _install_metadata(root: Path) -> dict[str, Any] | None:
    override = os.environ.get("JOBAGENT_INSTALL_METADATA")
    path = Path(override).expanduser() if override else root / ".jobagent-install.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) and payload.get("managed") is True else None


def _run(root: Path, *args: str) -> str:
    result = subprocess.run(
        list(args),
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise UpdateError(result.stderr.strip() or "command failed: " + " ".join(args))
    return result.stdout.strip()


@contextmanager
def _update_lock():
    path = update_lock_path()
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError as exc:
            if _pid_alive(_update_lock_pid(path)):
                raise UpdateError("another Job Agent update is already running") from exc
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def _archive_sha256(root: Path, commit: str) -> str:
    import hashlib

    result = subprocess.run(
        ["git", "archive", "--format=tar", commit],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise UpdateError(result.stderr.decode("utf-8", errors="replace"))
    return hashlib.sha256(result.stdout).hexdigest()


def apply_managed_update(manifest: dict[str, Any], root: Path | None = None) -> str:
    root = root or _package_root()
    metadata = _install_metadata(root)
    if metadata is None:
        raise UpdateError("source checkout is not an official managed install")
    from jobagent.infra.activity import activity_lock_active

    if activity_lock_active():
        raise UpdateError("a Job Agent action is active; update deferred")
    with _update_lock():
        origin = _run(root, "git", "remote", "get-url", "origin")
        if origin not in OFFICIAL_REPO_URLS:
            raise UpdateError("managed install origin is not the official repository")
        if _run(root, "git", "status", "--porcelain"):
            raise UpdateError("managed install has local changes; update refused")
        old_commit = _run(root, "git", "rev-parse", "HEAD")
        tag = str(manifest["git_tag"])
        commit = str(manifest["git_commit"])
        _run(root, "git", "fetch", "--tags", "origin", tag)
        if _run(root, "git", "rev-parse", f"{tag}^{{commit}}") != commit:
            raise UpdateError("release tag does not resolve to the signed commit")
        if _archive_sha256(root, commit) != manifest["artifact_sha256"]:
            raise UpdateError("release artifact hash mismatch")
        try:
            _run(root, "git", "checkout", "--detach", commit)
            python = Path(sys.executable)
            _run(root, str(python), "-m", "pip", "install", "-e", str(root), "--quiet")
            smoke = _run(root, str(python), "-m", "jobagent", "--version")
            if manifest["latest_client_version"] not in smoke:
                raise UpdateError("updated CLI smoke check returned the wrong version")
        except Exception:
            _run(root, "git", "checkout", "--detach", old_commit)
            _run(root, str(sys.executable), "-m", "pip", "install", "-e", str(root), "--quiet")
            raise
    return str(manifest["latest_client_version"])


def check_for_update(*, auto_apply: bool = True, force: bool = False) -> dict[str, Any]:
    manifest = fetch_release_manifest(force=force)
    if manifest is None:
        return {"status": "unavailable", "current_version": __version__}
    latest = str(manifest["latest_client_version"])
    minimum = str(manifest["minimum_supported_version"])
    if _version(__version__) >= _version(latest):
        return {"status": "current", "current_version": __version__, "manifest": manifest}
    root = _package_root()
    managed = _install_metadata(root) is not None
    if not managed or not auto_apply:
        return {
            "status": "update_required" if _version(__version__) < _version(minimum) else "update_available",
            "current_version": __version__,
            "latest_version": latest,
            "managed": managed,
            "notes_url": manifest.get("notes_url"),
        }
    version = apply_managed_update(manifest, root=root)
    return {"status": "updated", "from_version": __version__, "to_version": version}


def maybe_auto_update() -> dict[str, Any]:
    return check_for_update(auto_apply=True)
