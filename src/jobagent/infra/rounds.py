"""Delivery round state for one multi-platform job application pass."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from jobagent.infra.state import current_round_path, rounds_dir, save_json, load_json

DEFAULT_PLATFORM_ORDER = ["boss", "liepin", "zhilian"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_round_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _round_file(round_id: str):
    directory = rounds_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{round_id}.json"


def _default_platform_state() -> dict[str, dict[str, Any]]:
    return {platform: {"status": "pending"} for platform in DEFAULT_PLATFORM_ORDER}


def ensure_current_round() -> dict[str, Any]:
    """Return the active delivery round, creating one when needed."""
    current = load_json(current_round_path())
    if current and current.get("status") == "active" and current.get("round_id"):
        return current

    round_id = new_round_id()
    now = utc_now()
    state: dict[str, Any] = {
        "round_id": round_id,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "platform_order": list(DEFAULT_PLATFORM_ORDER),
        "browser_session_id": "local-cdp-19222",
        "platforms": _default_platform_state(),
    }
    save_round(state)
    return state


def save_round(state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    save_json(current_round_path(), state)
    round_id = str(state.get("round_id") or "")
    if round_id:
        save_json(_round_file(round_id), state)


def set_platform_status(
    platform: str,
    status: str,
    *,
    command: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update the current round platform status without changing global config."""
    state = ensure_current_round()
    platforms = state.setdefault("platforms", _default_platform_state())
    item = platforms.setdefault(platform, {})
    item["status"] = status
    item["updated_at"] = utc_now()
    if command:
        item["last_command"] = command
    if evidence:
        item["evidence"] = evidence
    save_round(state)
    return state


def mark_browser_session(session_id: str = "local-cdp-19222") -> dict[str, Any]:
    state = ensure_current_round()
    state["browser_session_id"] = session_id
    save_round(state)
    return state
