"""Persist read pacing and interruptions with the dedicated browser profile.

These are conservative local load limits, not a guarantee about the platform's
limits. New rounds, processes and account state directories share the same
budget when they use the same browser profile. No job or credential is stored.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import random
import time

from jobagent.infra import state
from jobagent.infra.diagnostics import emit_stage

READ_INTERVAL_SECONDS = (2.0, 3.0, 5.0)
MIN_INTERVAL_SECONDS = min(READ_INTERVAL_SECONDS)
WINDOW_SECONDS = 60.0
MAX_REQUESTS_PER_WINDOW = 20
RECOVERY_DELAY_SECONDS = 300.0


def _error(code, message, **details):
    from jobagent.platforms.discovery import CollectionError
    return CollectionError(code, message, details={
        "platform": "boss", "retryable": False, "automatic_retry_allowed": False,
        "request_preserved": True, **details})


class CollectionGuard:
    def __init__(self, profile):
        profile = Path(profile).resolve()
        self.path = profile.parent / (profile.name + ".boss-read-state.json")

    def _load(self):
        if self.path.is_symlink():
            raise _error("collection_read_state_invalid", "Preserve the unexpected collection state link",
                         requires_technical_recovery=True)
        if not self.path.exists():
            return {"schema_version": 1, "requests": [], "total_requests": 0}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            blocked = data.get("blocked")
            suspension = data.get("suspension")
            if (data.get("schema_version") != 1 or not isinstance(data.get("requests"), list)
                    or not all(isinstance(t, (float, int)) and math.isfinite(t) for t in data["requests"])
                    or data["requests"] != sorted(data["requests"])
                    or not isinstance(data.get("inflight", False), bool)
                    or not isinstance(data.get("total_requests"), int) or data["total_requests"] < 0
                    or ("next_read_at" in data and (isinstance(data["next_read_at"], bool)
                        or not isinstance(data["next_read_at"], (int, float))
                        or not math.isfinite(data["next_read_at"]) or data["next_read_at"] < 0
                        or (data["requests"] and data["next_read_at"] < data["requests"][-1] + MIN_INTERVAL_SECONDS)))
                    or ("suspension" in data and (not isinstance(suspension, dict)
                        or not isinstance(suspension.get("reason"), str) or not suspension["reason"]
                        or not isinstance(suspension.get("at"), (int, float))
                        or not math.isfinite(suspension["at"])))
                    or ("blocked" in data and (not isinstance(blocked, dict) or not blocked.get("code")
                        or not isinstance(blocked.get("not_before"), (int, float))
                        or not math.isfinite(blocked["not_before"])))):
                raise ValueError("invalid read state")
            return data
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            raise _error("collection_read_state_invalid", "Preserve the unreadable collection state for technical recovery",
                         requires_technical_recovery=True) from exc

    @contextmanager
    def _lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(".lock")
        if lock_path.is_symlink() or self.path.is_symlink():
            raise _error("collection_read_state_invalid", "Collection state must not be a symbolic link",
                         requires_technical_recovery=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        acquired = False
        try:
            try:
                if os.name == "nt":
                    import msvcrt
                    if os.fstat(fd).st_size == 0:
                        os.write(fd, b"\0")
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                raise _error("collection_read_busy", "Another read owns this browser profile; do not start another collector") from exc
            yield
        finally:
            if acquired:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            # Keep the lock inode so another process cannot lock a new inode.

    @staticmethod
    def _blocked_error(data):
        if data.get("suspension"):
            return _error("collection_read_suspended", "Live collection is suspended pending technical review",
                requires_user_action=False, requires_technical_recovery=True,
                suspension_reason=data["suspension"]["reason"],
                message_detail="Page verification or ordinary resume confirmation cannot release this suspension.")
        blocked = data.get("blocked") or {"code": "collection_read_interrupted"}
        user_action = blocked.get("code") == "boss_verification_required" or blocked.get("upstream_code") in {
            7, 31, 32, 35, 36, 37, 1011, 5012, 5002, 5003, 5004, 120, 121, 122, 200404}
        return _error("collection_read_blocked", "Collection remains stopped after an interrupted or rejected read",
            blocked_reason=blocked.get("code"), upstream_code=blocked.get("upstream_code"),
            requires_user_action=user_action, requires_technical_recovery=not user_action,
            user_prompt=("Boss 采集已停止。请先处理页面上的登录或安全验证；确认页面恢复后再明确回复继续。程序不会自动重试。" if user_action else None),
            next_suggested="jobagent browser resume-collection --platform boss --confirm",
            retry_after_seconds=max(0, math.ceil(float(blocked.get("not_before") or 0) - time.time())))

    def assert_available(self):
        data = self._load()
        if data.get("suspension") or data.get("blocked") or data.get("inflight"):
            raise self._blocked_error(data)

    def _record_block(self, data, code, upstream_code=None):
        data["blocked"] = {"code": code, "upstream_code": upstream_code,
                           "at": time.time(), "not_before": time.time() + RECOVERY_DELAY_SECONDS}
        data["inflight"] = False
        state.save_json(self.path, data)

    def block(self, code, *, upstream_code=None):
        """Record an already-observed rejection without accessing the browser."""
        with self._lock():
            self._record_block(self._load(), code, upstream_code)

    def suspend(self, reason):
        """Hold live testing without clearing requests, rejection or business state.

        This engineering hold has no automatic/ordinary-confirmation recovery.
        Releasing it requires a separately reviewed recovery after repair.
        """
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("A suspension reason is required")
        with self._lock():
            data = self._load()
            data["suspension"] = {"reason": reason, "at": time.time()}
            state.save_json(self.path, data)

    def call(self, kind, callback):
        from jobagent.platforms.discovery import CollectionError
        with self._lock():
            data = self._load()
            if data.get("suspension") or data.get("blocked") or data.get("inflight"):
                raise self._blocked_error(data)
            if "next_read_at" not in data and data["requests"]:
                # Upgrade an existing budget once. Restarting must not redraw
                # a shorter delay or discard the last request timestamp.
                data["next_read_at"] = data["requests"][-1] + random.choice(READ_INTERVAL_SECONDS)
                state.save_json(self.path, data)
            while True:
                now = time.time()
                times = [t for t in data["requests"] if now - t < WINDOW_SECONDS]
                if times and times[-1] > now:
                    raise _error("collection_clock_changed", "Clock moved backwards; preserve the request budget",
                                 requires_technical_recovery=True)
                delay = max(0.0, float(data.get("next_read_at", 0)) - now)
                if len(times) >= MAX_REQUESTS_PER_WINDOW:
                    delay = max(delay, times[-MAX_REQUESTS_PER_WINDOW] + WINDOW_SECONDS - now)
                if delay <= 0:
                    break
                emit_stage("collection_read_throttled", platform="boss", read_kind=kind,
                           wait_seconds=round(delay, 3))
                time.sleep(min(delay, WINDOW_SECONDS))
            data.update(requests=[*times, now], total_requests=int(data.get("total_requests") or 0) + 1,
                        inflight=True, last_kind=kind,
                        next_read_at=now + random.choice(READ_INTERVAL_SECONDS))
            state.save_json(self.path, data)  # A killed process must not erase an attempted request.
            try:
                result = callback()
            except BaseException as exc:
                code = exc.code if isinstance(exc, CollectionError) else "collection_read_interrupted"
                details = dict(exc.details or {}) if isinstance(exc, CollectionError) else {}
                self._record_block(data, code, details.get("upstream_code"))
                if isinstance(exc, CollectionError):
                    exc.details = {**details, **self._blocked_error(data).details}
                raise
            data["inflight"] = False
            state.save_json(self.path, data)
            return result

    def resume(self, *, confirmed, check_page):
        """An explicit human response permits one DOM-only readiness check."""
        with self._lock():
            data = self._load()
            if data.get("suspension"):
                raise self._blocked_error(data)
            if not confirmed:
                raise self._blocked_error(data)
            if not data.get("blocked") and not data.get("inflight"):
                return {"already_available": True}
            blocked = data.get("blocked") or {}
            if time.time() < float(blocked.get("not_before") or 0):
                raise self._blocked_error(data)
            observed = check_page()
            if (not observed.get("ready_for_platform_work")
                    or observed.get("verification_required") is not False):
                raise self._blocked_error(data)
            data.pop("blocked", None)
            data["inflight"] = False
            state.save_json(self.path, data)  # Never reset the rolling request budget on resume.
            return {"already_available": False}


def boss_guard():
    from jobagent.drivers.boss.chrome_manager import ChromeInstanceManager
    return CollectionGuard(ChromeInstanceManager().user_data_dir)
