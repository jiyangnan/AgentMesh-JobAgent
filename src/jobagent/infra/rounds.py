"""Delivery round state for one multi-platform job application pass."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from jobagent.infra.protocol import digest_payload
from jobagent.infra.state import current_round_path, rounds_dir, save_json, load_json

DEFAULT_PLATFORM_ORDER = ["boss", "liepin", "zhilian", "51job"]
TERMINAL_PLATFORM_STATUSES = {"completed", "skipped_this_round"}
ROUND_SCHEMA_VERSION = 4
DEFAULT_BROWSER_EXECUTOR = "codex_native"
UNBOUND_BROWSER_SESSION = "native-unbound"
PLATFORM_LOGIN_VERIFICATION_TTL_SECONDS = 30 * 60
DELIVERY_POLICY = {
    "selected": "user_confirmed_after_preview",
    "review": "explicit_override_only",
    "rejected": "never",
    "per_platform_confirmation": True,
}
ROUND_EXECUTION_POLICY = {
    "mode": "vertical_end_to_end",
    "prelogin_future_platforms": False,
    "advance_only_after": "audit",
    "stages": [
        "login",
        "discover",
        "review",
        "delivery_preview",
        "delivery_confirmation",
        "send",
        "audit",
    ],
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


def _create_round(
    intent: dict[str, Any] | None = None,
    *,
    interaction_receipt: dict[str, Any] | None = None,
    resume_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
        "browser_executor": DEFAULT_BROWSER_EXECUTOR,
        "browser_session_id": UNBOUND_BROWSER_SESSION,
        "native_session": None,
        "intent": intent or {
            "status": "legacy_implicit",
            "target_roles": [],
            "source": "internal_compatibility",
            "confirmed_at": now,
        },
        "platforms": _default_platform_state(),
    }
    if resume_binding and resume_binding.get("id"):
        state["resume_binding"] = resume_binding
    if interaction_receipt is not None:
        state["interaction_receipt"] = interaction_receipt
    save_round(state)
    return state


def attach_round_resume_binding(
    binding: dict[str, Any],
    *,
    intent: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Bind (or rebind) the active round after a user-confirmed selection.

    ``intent`` upgrades an already-active round to the binding's own material
    form (direction roles, explicit cities, material digest): without it a
    round created before the binding keeps its old digest and the server
    rejects every later discovery with an unrecoverable digest mismatch.
    """
    current = load_json(current_round_path())
    if not current or current.get("status") != "active" or not current.get("round_id"):
        return None
    current["resume_binding"] = binding
    if intent is not None:
        current["intent"] = intent
    current["updated_at"] = utc_now()
    save_round(current)
    return current


def clear_round_resume_binding() -> dict[str, Any] | None:
    """Drop the binding after the server reports it stale (pause + reselect)."""
    current = load_json(current_round_path())
    if not current or not current.get("resume_binding"):
        return None
    current.pop("resume_binding", None)
    current["updated_at"] = utc_now()
    save_round(current)
    return current


def start_new_round(
    intent: dict[str, Any] | None = None,
    *,
    interaction_receipt: dict[str, Any] | None = None,
    resume_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start a round explicitly, or return the already-active round."""
    current = load_json(current_round_path())
    if current and current.get("status") == "active" and current.get("round_id"):
        active = _migrate_round(current)
        if intent is not None and not _same_intent(active.get("intent"), intent):
            raise RoundOrderError(
                {
                    "ok": False,
                    "error": "round_intent_conflict",
                    "message": "The active round already has a different target-role intent.",
                    "round_id": active["round_id"],
                    "target_roles": (active.get("intent") or {}).get("target_roles") or [],
                    "next_suggested": "jobagent round status",
                }
            )
        return active
    return _create_round(
        intent, interaction_receipt=interaction_receipt, resume_binding=resume_binding
    )


def _migrate_round(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("schema_version") == ROUND_SCHEMA_VERSION:
        return state
    # Recovery commands may read a pre-migration round with an unresolved
    # native effect. Preserve that original binding until reconciliation.
    from jobagent.infra.client_upgrade import _native_work_upgrade_conflict

    path = current_round_path()
    if _native_work_upgrade_conflict(path.parent.parent, ledger_path=path.with_name("browser-work.sqlite3")):
        return deepcopy(state)
    migrated = migrate_round_payload(state)
    save_round(migrated)
    return migrated


def migrate_round_payload(state: dict[str, Any]) -> dict[str, Any]:
    """Return a current-schema round without reading or writing global state."""
    if state.get("schema_version") == ROUND_SCHEMA_VERSION:
        return deepcopy(state)
    if state.get("schema_version") == 3:
        migrated = deepcopy(state)
        migrated["schema_version"] = ROUND_SCHEMA_VERSION
        migrated["updated_at"] = utc_now()
        migrated.setdefault("migration", {
            "from_schema_version": 3,
            "reason": "preserve_round_and_bind_native_executor",
        })
        return _migrate_browser_executor(migrated)
    if state.get("schema_version") == 2:
        migrated = deepcopy(state)
        migrated["schema_version"] = ROUND_SCHEMA_VERSION
        migrated["updated_at"] = utc_now()
        migrated["intent"] = {
            "status": "legacy_implicit",
            "target_roles": [],
            "source": "pre_target_role_confirmation",
            "confirmed_at": state.get("created_at") or utc_now(),
        }
        migrated["migration"] = {
            "from_schema_version": 2,
            "reason": "preserve_active_round_and_mark_legacy_intent",
        }
        return _migrate_browser_executor(migrated)
    return _migrate_browser_executor({
        "schema_version": ROUND_SCHEMA_VERSION,
        "round_id": state.get("round_id") or new_round_id(),
        "status": "active",
        "created_at": state.get("created_at") or utc_now(),
        "updated_at": utc_now(),
        "platform_order": list(DEFAULT_PLATFORM_ORDER),
        "browser_session_id": state.get("browser_session_id") or "local-cdp-19222",
        "intent": {
            "status": "legacy_implicit",
            "target_roles": [],
            "source": "legacy_state_migration",
            "confirmed_at": state.get("created_at") or utc_now(),
        },
        "platforms": _default_platform_state(),
        "migration": {
            "from_schema_version": state.get("schema_version"),
            "reason": "reset_legacy_ambiguous_platform_statuses",
        },
    })


def _migrate_browser_executor(state: dict[str, Any]) -> dict[str, Any]:
    """Change transport metadata without discarding an existing native binding."""
    state["browser_executor"] = DEFAULT_BROWSER_EXECUTOR
    if not isinstance(state.get("native_session"), dict):
        previous = state.get("browser_session_id")
        if previous and previous != UNBOUND_BROWSER_SESSION:
            state.setdefault("legacy_browser_session_id", previous)
        state["browser_session_id"] = UNBOUND_BROWSER_SESSION
        state["native_session"] = None
    return state


def _same_intent(current: Any, requested: dict[str, Any]) -> bool:
    current_roles = [
        str(role).strip().casefold()
        for role in (current or {}).get("target_roles", [])
        if str(role).strip()
    ]
    requested_roles = [
        str(role).strip().casefold()
        for role in requested.get("target_roles", [])
        if str(role).strip()
    ]
    return current_roles == requested_roles


_PROFILE_REFRESH_SAFE_STATUSES = {"pending", "login_verified", "active", "blocked"}
_PROFILE_REFRESH_UNSAFE_EVIDENCE = {
    "discover_id",
    "preview_id",
    "authorization_id",
    "attempted",
    "delivered",
    "reviewed_count",
}


def active_round_profile_refresh_conflict(state: dict[str, Any]) -> dict[str, Any] | None:
    """Return why an active round cannot be rebound to a newly analyzed profile."""

    if state.get("status") != "active" or not state.get("round_id"):
        return None
    intent = state.get("intent")
    if not isinstance(intent, dict) or intent.get("status") != "confirmed":
        return {
            "code": "active_round_intent_not_confirmed",
            "message": "The active round does not have a confirmed target-role intent.",
        }
    platforms = state.get("platforms")
    if not isinstance(platforms, dict):
        return {
            "code": "active_round_platform_state_invalid",
            "message": "The active round platform state is invalid.",
        }
    for platform in state.get("platform_order") or DEFAULT_PLATFORM_ORDER:
        item = platforms.get(platform) or {}
        status = str(item.get("status") or "pending")
        if status not in _PROFILE_REFRESH_SAFE_STATUSES:
            return {
                "code": "active_round_already_progressed",
                "message": (
                    f"The active round already progressed to {platform}:{status}; "
                    "its profile binding cannot be changed."
                ),
            }
        evidence = item.get("evidence")
        if not isinstance(evidence, dict):
            continue
        resume_status = str(evidence.get("resume_status") or "")
        if resume_status and resume_status not in _PROFILE_REFRESH_SAFE_STATUSES:
            return {
                "code": "active_round_already_progressed",
                "message": (
                    f"The active round has resumable {platform}:{resume_status} progress; "
                    "its profile binding cannot be changed."
                ),
            }
        for key in _PROFILE_REFRESH_UNSAFE_EVIDENCE:
            value = evidence.get(key)
            if value is not None and value != "" and value != 0 and value is not False:
                return {
                    "code": "active_round_has_delivery_evidence",
                    "message": (
                        f"The active round already records {platform} {key}; "
                        "its profile binding cannot be changed."
                    ),
                }
    return None


def _round_current_platform(state: dict[str, Any]) -> str | None:
    platforms = state.get("platforms") or {}
    for platform in state.get("platform_order") or DEFAULT_PLATFORM_ORDER:
        status = str((platforms.get(platform) or {}).get("status") or "pending")
        if status not in TERMINAL_PLATFORM_STATUSES:
            return platform
    return None


def _login_receipt_is_recent(
    state: dict[str, Any],
    platform: str,
    login: dict[str, Any],
) -> bool:
    if not login.get("logged_in"):
        return False
    if str(login.get("platform") or "") != platform:
        return False
    if str(login.get("round_id") or "") != str(state.get("round_id") or ""):
        return False
    if str(login.get("browser_session_id") or "") != str(
        state.get("browser_session_id") or ""
    ):
        return False
    try:
        verified_at = datetime.fromisoformat(
            str(login.get("verified_at") or "").replace("Z", "+00:00")
        )
    except ValueError:
        return False
    if verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - verified_at).total_seconds()
    return -300 <= age_seconds <= PLATFORM_LOGIN_VERIFICATION_TTL_SECONDS


def reconcile_round_profile_payload(
    state: dict[str, Any],
    profile: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Rebind a pre-delivery active round to an explicitly updated profile."""

    updated = deepcopy(state)
    intent = updated.get("intent")
    if not isinstance(intent, dict) or intent.get("status") != "confirmed":
        return updated, False
    current_digest = str(intent.get("profile_digest") or "")
    profile_digest = digest_payload(profile)
    if current_digest == profile_digest:
        return updated, False
    conflict = active_round_profile_refresh_conflict(updated)
    if conflict is not None:
        raise RoundOrderError(
            {
                "ok": False,
                "error": "active_round_profile_changed",
                "message": conflict["message"],
                "conflict": conflict["code"],
                "round_id": updated.get("round_id"),
                "next_suggested": "jobagent round status",
            }
        )
    intent["profile_digest"] = profile_digest
    reconciled_at = utc_now()
    updated["profile_reconciliation"] = {
        "reason": "pre_delivery_profile_update",
        "from_profile_digest": current_digest or None,
        "to_profile_digest": profile_digest,
        "reconciled_at": reconciled_at,
    }
    platform = _round_current_platform(updated)
    if platform:
        item = (updated.get("platforms") or {}).setdefault(platform, {"status": "pending"})
        evidence = item.get("evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        login = evidence.get("login")
        evidence.pop("error", None)
        evidence.pop("resume_status", None)
        evidence.pop("resume_next_suggested", None)
        if evidence:
            item["evidence"] = evidence
        else:
            item.pop("evidence", None)
        status = (
            "login_verified"
            if isinstance(login, dict)
            and _login_receipt_is_recent(updated, platform, login)
            else "pending"
        )
        item["status"] = status
        item["updated_at"] = reconciled_at
        item["next_suggested"] = _default_next_command(platform, status)
    return updated, True


def reconcile_active_round_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Persist a safe profile rebind and invalidate only its stale start request."""

    state = load_json(current_round_path())
    if not state or state.get("status") != "active" or not state.get("round_id"):
        return {"changed": False, "workflow": round_status()}
    platform = _round_current_platform(state)
    if platform:
        from jobagent.infra.discovery_state import (
            clear_pending_start,
            load_pending_decision,
            load_pending_start,
        )

        pending_start = load_pending_start(platform)
        if load_pending_decision(platform) is not None or (pending_start and pending_start.get("collection") is not None):
            raise RoundOrderError(
                {
                    "ok": False,
                    "error": "active_round_profile_changed",
                    "message": (
                        "The active round already has a preserved signed decision; "
                        "its profile binding cannot be changed."
                    ),
                    "conflict": "active_round_has_pending_decision",
                    "round_id": state.get("round_id"),
                    "next_suggested": "jobagent round status",
                }
            )
    updated, changed = reconcile_round_profile_payload(state, profile)
    if changed:
        if platform:
            clear_pending_start(platform)
        save_round(updated)
    return {"changed": changed, "workflow": round_status()}


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


def recent_platform_login_verification(
    platform: str,
    *,
    max_age_seconds: int = PLATFORM_LOGIN_VERIFICATION_TTL_SECONDS,
) -> dict[str, Any] | None:
    """Return a short-lived login receipt bound to this round and browser session."""

    try:
        state = ensure_current_round()
    except RoundOrderError:
        return None
    item = (state.get("platforms") or {}).get(platform) or {}
    login = ((item.get("evidence") or {}).get("login") or {})
    if not isinstance(login, dict) or not login.get("logged_in"):
        return None
    if int(login.get("schema_version") or 0) != 1:
        return None
    if str(login.get("platform") or "") != platform:
        return None
    if str(login.get("round_id") or "") != str(state.get("round_id") or ""):
        return None
    if str(login.get("browser_session_id") or "") != str(
        state.get("browser_session_id") or ""
    ):
        return None
    try:
        verified_at = datetime.fromisoformat(
            str(login.get("verified_at") or "").replace("Z", "+00:00")
        )
    except ValueError:
        return None
    if verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - verified_at).total_seconds()
    if age_seconds < -300 or age_seconds > max(0, int(max_age_seconds)):
        return None

    receipt = dict(login)
    receipt.update(
        {
            "valid": True,
            "source": "recent_login_check",
            "age_seconds": max(0, round(age_seconds, 3)),
        }
    )
    return receipt


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
    if status == "awaiting_delivery_confirmation":
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
            "browser_session_id": None,
            "browser_executor": DEFAULT_BROWSER_EXECUTOR,
            "native_session": None,
            "platforms": {},
            "current_platform": None,
            "remaining_platforms": [],
            "next_suggested": "jobagent round start",
        }
    state = _migrate_round(state)
    migration_deferred = state.get("schema_version") != ROUND_SCHEMA_VERSION
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
        if not migration_deferred:
            save_round(state)
    current_platform = remaining[0] if remaining else None
    next_suggested = None
    if current_platform:
        item = platforms.get(current_platform, {})
        stored_next = item.get("next_suggested")
        migrated_next = _migrate_next_command(stored_next)
        if migrated_next != stored_next:
            item["next_suggested"] = migrated_next
            if not migration_deferred:
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
        "browser_session_id": state.get("browser_session_id"),
        "browser_executor": state.get("browser_executor", DEFAULT_BROWSER_EXECUTOR),
        "native_session": deepcopy(state.get("native_session")),
        "intent": state.get("intent"),
        "resume_binding": state.get("resume_binding"),
        "profile_reconciliation": state.get("profile_reconciliation"),
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
