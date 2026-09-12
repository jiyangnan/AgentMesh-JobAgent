from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

APP_DIR = Path.home() / ".jobagent"
STATE_DIR = APP_DIR / "state"
LOG_DIR = APP_DIR / "logs"
ROUNDS_DIR = STATE_DIR / "rounds"
LOCKS_DIR = STATE_DIR / "locks"


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def save_json(path: Path, data: dict[str, Any]) -> None:
    ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def last_probe_path() -> Path:
    ensure_dirs()
    return STATE_DIR / "last_probe_send.json"


def last_doctor_path() -> Path:
    ensure_dirs()
    return STATE_DIR / "last_doctor_report.json"


def audit_log_path() -> Path:
    ensure_dirs()
    return STATE_DIR / "audit_log.json"


def profile_path() -> Path:
    ensure_dirs()
    return STATE_DIR / "profile.json"


def support_state_path() -> Path:
    ensure_dirs()
    return STATE_DIR / "support_state.json"


def current_round_path() -> Path:
    ensure_dirs()
    return STATE_DIR / "current_round.json"


def pending_interaction_path() -> Path:
    ensure_dirs()
    return STATE_DIR / "pending_interaction.json"


def resume_freshness_path() -> Path:
    ensure_dirs()
    return STATE_DIR / "resume_freshness_baselines.json"


def rounds_dir() -> Path:
    ensure_dirs()
    ROUNDS_DIR.mkdir(parents=True, exist_ok=True)
    return ROUNDS_DIR


def browser_session_path() -> Path:
    ensure_dirs()
    return STATE_DIR / "browser_session.json"


def platform_tabs_path() -> Path:
    ensure_dirs()
    return STATE_DIR / "platform_tabs.json"


def browser_session_lock_path() -> Path:
    ensure_dirs()
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    return LOCKS_DIR / "browser-session.lock"


def activity_lock_path() -> Path:
    ensure_dirs()
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    return LOCKS_DIR / "activity.lock"


def update_lock_path() -> Path:
    ensure_dirs()
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    return LOCKS_DIR / "update.lock"


def release_cache_path() -> Path:
    ensure_dirs()
    return STATE_DIR / "release_manifest_cache.json"


def discoveries_dir() -> Path:
    ensure_dirs()
    path = STATE_DIR / "discoveries"
    path.mkdir(parents=True, exist_ok=True)
    return path
