"""Cross-process guard for browser/discover/send activity."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone

from jobagent.infra.state import activity_lock_path


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _read_lock(path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def activity_lock_active() -> bool:
    """Return whether a live process owns the activity lock, reclaiming stale files."""
    path = activity_lock_path()
    if not path.exists():
        return False
    payload = _read_lock(path)
    if _pid_alive(int(payload.get("pid") or 0)):
        return True
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return False


@contextmanager
def active_command(command: str):
    path = activity_lock_path()
    payload = {
        "pid": os.getpid(),
        "command": command,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            break
        except FileExistsError:
            if activity_lock_active():
                owner = _read_lock(path)
                raise RuntimeError(
                    "Another Job Agent action is active: "
                    f"{owner.get('command') or 'unknown command'} (pid {owner.get('pid') or 'unknown'})"
                )
    try:
        yield
    finally:
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if current.get("pid") == os.getpid():
                path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass
