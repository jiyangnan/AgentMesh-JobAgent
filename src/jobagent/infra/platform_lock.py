"""File lock for serializing real browser actions across platforms."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from jobagent.infra.rounds import ensure_current_round, set_platform_status, utc_now
from jobagent.infra.state import browser_session_lock_path


class PlatformLockError(RuntimeError):
    def __init__(self, payload: dict[str, Any]):
        super().__init__(str(payload.get("message") or payload.get("error") or "platform lock busy"))
        self.payload = payload


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


def _load_lock(path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@dataclass
class PlatformSessionLock:
    platform: str
    command: str

    def __post_init__(self) -> None:
        self.path = browser_session_lock_path()
        self.pid = os.getpid()
        self.acquired = False
        self.reentrant = False

    def acquire(self) -> "PlatformSessionLock":
        round_state = ensure_current_round()
        payload = {
            "round_id": round_state["round_id"],
            "platform": self.platform,
            "command": self.command,
            "pid": self.pid,
            "started_at": utc_now(),
        }

        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                self.acquired = True
                set_platform_status(self.platform, "active", command=self.command)
                return self
            except FileExistsError:
                existing = _load_lock(self.path)
                existing_pid = int(existing.get("pid") or 0)
                if existing_pid == self.pid:
                    self.acquired = True
                    self.reentrant = True
                    return self
                if not _pid_alive(existing_pid):
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                raise PlatformLockError({
                    "ok": False,
                    "error": "browser_session_lock_busy",
                    "message": "Another Job Agent browser action is already running.",
                    "current": existing,
                    "requested": payload,
                })

    def release(self) -> None:
        if not self.acquired or self.reentrant:
            return
        existing = _load_lock(self.path)
        if int(existing.get("pid") or 0) == self.pid:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.acquired = False

    def __enter__(self) -> "PlatformSessionLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            status = "blocked" if self.platform else "failed"
            set_platform_status(self.platform, status, command=self.command, evidence={"error": str(exc)})
        self.release()
