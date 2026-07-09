from __future__ import annotations

import json
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
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
