"""Delivery round state for one multi-platform job application pass."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from jobagent.infra.state import current_round_path, rounds_dir, save_json, load_json

DEFAULT_PLATFORM_ORDER = ["boss", "liepin", "zhilian", "51job"]
TERMINAL_PLATFORM_STATUSES = {"completed", "skipped_this_round"}
ROUND_SCHEMA_VERSION = 2
DELIVERY_POLICY = {
    "selected": "auto",
    "review": "explicit_override_only",
    "rejected": "never",
    "per_platform_confirmation": False,
}
ROUND_EXECUTION_POLICY = {
    "mode": "vertical_end_to_end",
    "prelogin_future_platforms": False,
    "advance_only_after": "audit",
    "stages": ["login", "discover", "review", "send", "audit"],
}


class RoundOrderError(RuntimeError):
    def __init__(self, payload: dict[str, Any]):
        super().__init__(str(payload.get("message") or payload.get("error") or "platform out of order"))
        self.payload = payload


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
    """Return the active delivery round without creating one."""
    current = load_json(current_round_path())
    if current and current.get("status") == "active" and current.get("round_id"):
        return _migrate_round(current)

    status = "completed" if current and current.get("status") == "completed" else "not_started"
    raise RoundOrderError(
        {
            "ok": False,
            "error": "round_completed" if status == "completed" else "round_not_started",
            "message": (
                "The previous round is complete. Start a new round explicitly."
                if status == "completed"
                else "Start a Job Agent round before changing workflow state."
            ),
            "next_suggested": "jobagent round start",
        }
    )


def _create_round() -> dict[str, Any]:
    """Create the persisted round state for the explicit start command."""

    round_id = new_round_id()
    now = utc_now()
    state: dict[str, Any] = {
        "schema_version": ROUND_SCHEMA_VERSION,
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


def start_new_round() -> dict[str, Any]:
    """Start a round explicitly, or return the already-active round."""
    current = load_json(current_round_path())
    if current and current.get("status") == "active" and current.get("round_id"):
        return _migrate_round(current)
    return _create_round()


def _migrate_round(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("schema_version") == ROUND_SCHEMA_VERSION:
        return state
    migrated = migrate_round_payload(state)
    save_round(migrated)
    return migrated


def migrate_round_payload(state: dict[str, Any]) -> dict[str, Any]:
    """Return a current-schema round without reading or writing global state."""
    if state.get("schema_version") == ROUND_SCHEMA_VERSION:
        return dict(state)
    return {
        "schema_version": ROUND_SCHEMA_VERSION,
        "round_id": state.get("round_id") or new_round_id(),
        "status": "active",
        "created_at": state.get("created_at") or utc_now(),
        "updated_at": utc_now(),
        "platform_order": list(DEFAULT_PLATFORM_ORDER),
        "browser_session_id": state.get("browser_session_id") or "local-cdp-19222",
        "platforms": _default_platform_state(),
        "migration": {
            "from_schema_version": state.get("schema_version"),
            "reason": "reset_legacy_ambiguous_platform_statuses",
        },
    }


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
    next_suggested: str | None = None,
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
    if next_suggested:
        item["next_suggested"] = next_suggested
    elif status in TERMINAL_PLATFORM_STATUSES:
        item.pop("next_suggested", None)
    save_round(state)
    return state


def mark_browser_session(session_id: str = "local-cdp-19222") -> dict[str, Any]:
    state = ensure_current_round()
    state["browser_session_id"] = session_id
    save_round(state)
    return state


def _default_next_command(platform: str, status: str) -> str:
    if status in {"pending", "active", "blocked"}:
        return f"jobagent {platform} login --check"
    if status == "login_verified":
        return f"jobagent {platform} discover"
    if status == "discovered":
        return (
            "jobagent boss greet preview"
            if platform == "boss"
            else f"jobagent {platform} apply review"
        )
    if status == "reviewed":
        return (
            "jobagent boss greet send"
            if platform == "boss"
            else f"jobagent {platform} apply send"
        )
    if status == "sent":
        return f"jobagent {platform} audit"
    return f"jobagent {platform} login --check"


def _migrate_next_command(command: str | None) -> str | None:
    if not command:
        return command
    migrated = command
    for retired_flag in (" --confirm-send", " --confirm-submit"):
        migrated = migrated.replace(retired_flag, "")
    return migrated


def round_status() -> dict[str, Any]:
    """Return machine-readable progress for the current multi-platform round."""
    state = load_json(current_round_path())
    if not state:
        return {
            "round_id": None,
            "status": "not_started",
            "workflow_complete": False,
            "continue_required": False,
            "delivery_policy": dict(DELIVERY_POLICY),
            "execution_policy": {
                **ROUND_EXECUTION_POLICY,
                "stages": list(ROUND_EXECUTION_POLICY["stages"]),
            },
            "platform_order": list(DEFAULT_PLATFORM_ORDER),
            "platforms": {},
            "current_platform": None,
            "remaining_platforms": [],
            "next_suggested": "jobagent round start",
        }
    state = _migrate_round(state)
    order = list(state.get("platform_order") or DEFAULT_PLATFORM_ORDER)
    platforms = state.setdefault("platforms", _default_platform_state())
    remaining = [
        platform
        for platform in order
        if str(platforms.get(platform, {}).get("status") or "pending")
        not in TERMINAL_PLATFORM_STATUSES
    ]
    workflow_complete = not remaining
    if workflow_complete and state.get("status") != "completed":
        state["status"] = "completed"
        save_round(state)
    current_platform = remaining[0] if remaining else None
    next_suggested = None
    if current_platform:
        item = platforms.get(current_platform, {})
        stored_next = item.get("next_suggested")
        migrated_next = _migrate_next_command(stored_next)
        if migrated_next != stored_next:
            item["next_suggested"] = migrated_next
            save_round(state)
        next_suggested = migrated_next or _default_next_command(
            current_platform,
            str(item.get("status") or "pending"),
        )
    return {
        "round_id": state.get("round_id"),
        "status": "completed" if workflow_complete else "active",
        "workflow_complete": workflow_complete,
        "continue_required": not workflow_complete,
        "delivery_policy": dict(DELIVERY_POLICY),
        "execution_policy": {
            **ROUND_EXECUTION_POLICY,
            "stages": list(ROUND_EXECUTION_POLICY["stages"]),
        },
        "platform_order": order,
        "platforms": platforms,
        "current_platform": current_platform,
        "remaining_platforms": remaining,
        "next_suggested": next_suggested,
    }


def complete_platform_after_audit(platform: str) -> dict[str, Any]:
    """Complete a platform only when a successful send reached the audit step."""
    state = load_json(current_round_path())
    if not state:
        return round_status()
    item = state.setdefault("platforms", _default_platform_state()).setdefault(
        platform,
        {"status": "pending"},
    )
    if item.get("status") == "sent":
        set_platform_status(
            platform,
            "completed",
            command=f"jobagent {platform} audit",
        )
    return round_status()


def assert_platform_turn(platform: str) -> dict[str, Any]:
    """Reject browser workflows that do not follow the persisted platform order."""
    workflow = round_status()
    if workflow["status"] == "not_started":
        raise RoundOrderError(
            {
                "ok": False,
                "error": "round_not_started",
                "message": "Start a Job Agent round before opening a recruiting platform.",
                "requested_platform": platform,
                "next_suggested": "jobagent round start",
                "workflow": workflow,
            }
        )
    if workflow["workflow_complete"]:
        raise RoundOrderError(
            {
                "ok": False,
                "error": "round_completed",
                "message": "The previous round is complete. Start a new round explicitly.",
                "requested_platform": platform,
                "next_suggested": "jobagent round start",
                "workflow": workflow,
            }
        )
    current_platform = workflow.get("current_platform")
    if current_platform != platform:
        raise RoundOrderError(
            {
                "ok": False,
                "error": "platform_out_of_order",
                "message": (
                    "Do not pre-login future platforms. Complete the current platform through "
                    "audit, or explicitly skip it, before continuing."
                ),
                "requested_platform": platform,
                "current_platform": current_platform,
                "next_suggested": workflow.get("next_suggested"),
                "execution_policy": workflow.get("execution_policy"),
                "workflow": workflow,
            }
        )
    return workflow
