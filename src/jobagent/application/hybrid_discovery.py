"""Phase routing for bulk read-only collection and native delivery.

Old rounds keep their executor and receipts until explicitly migrated. A new
round opts into this path; the native ledger remains the delivery authority.
"""
from __future__ import annotations

from contextlib import contextmanager
from time import monotonic

from jobagent.infra import browser_work, rounds, state


def enabled(active=None) -> bool:
    active = active if active is not None else state.load_json(state.current_round_path()) or {}
    return active.get("collection_executor") == "cdp_readonly"


def assert_collection_available(platform: str, *, allow_read_recovery=False) -> None:
    rounds.assert_platform_turn(platform)
    if browser_work.has_open():
        raise rounds.RoundOrderError({"ok": False, "error": "native_work_context_locked",
            "request_preserved": True, "next_suggested": "jobagent work next"})
    active = rounds.ensure_current_round()
    item = active.get("platforms", {}).get(platform, {})
    if item.get("native_delivery"):
        raise rounds.RoundOrderError({"ok": False, "error": "delivery_phase_locked",
            "request_preserved": True, "next_suggested": "jobagent work next",
            "message": "Preserve the authorized delivery and reconcile its receipts before collection."})
    if platform == "boss" and not allow_read_recovery:
        from jobagent.infra.collection_guard import boss_guard
        boss_guard().assert_available()


def resume_collection(platform: str, *, confirmed=False):
    """Recover the original collection after an explicit user response."""
    if platform != "boss" or not enabled():
        raise ValueError("This recovery applies only to the Boss read-only collector")
    assert_collection_available(platform, allow_read_recovery=True)
    if not confirmed:
        return {"ok": False, "error": "collection_resume_confirmation_required", "platform": platform,
                "requires_user_action": True, "automatic_retry_allowed": False, "request_preserved": True,
                "user_prompt": "请确认 Boss 页面已恢复，并明确表示继续原采集任务。",
                "next_suggested": "jobagent browser resume-collection --platform boss --confirm"}
    from jobagent.infra.collection_guard import boss_guard
    from jobagent.infra.browser_diagnostics import diagnose_browser
    result = boss_guard().resume(confirmed=True, check_page=lambda: diagnose_browser(platform))
    return {"ok": True, "event": "collection_read_resumed", "platform": platform,
            "request_preserved": True, "browser_requests_executed": False, **result,
            "next_suggested": f"jobagent {platform} discover"}


@contextmanager
def collection_reader(platform: str):
    from jobagent.drivers.collection_reader import CollectionReader
    from jobagent.infra.platform_lock import PlatformSessionLock
    from jobagent.infra.protocol import digest_payload
    assert_collection_available(platform)
    with PlatformSessionLock(platform=platform, command=f"jobagent {platform} collect-readonly"):
        # Check again after obtaining the process lock; native commands share
        # the CLI command lock and a durable unfinished ledger blocks takeover.
        assert_collection_available(platform)
        reader = CollectionReader(platform=platform)
        try:
            active = rounds.ensure_current_round()
            session = {"profile_directory": str(reader.manager.user_data_dir.resolve()),
                       "port": reader.manager.port, "round_id": active["round_id"]}
            session["id"] = "collection_" + digest_payload(session)[7:31]
            active["collection_session"] = session
            active["browser_phase"] = "collecting"
            rounds.save_round(active)
            yield reader
        finally:
            reader.disconnect()
            active = rounds.ensure_current_round()
            active["browser_phase"] = "collection_released"
            rounds.save_round(active)


def login(platform, args):
    from jobagent import cli
    active = rounds.ensure_current_round()
    if active.get("platforms", {}).get(platform, {}).get("status") in {"discovered", "reviewed", "awaiting_delivery_confirmation"}:
        return {"ok": True, "request_preserved": True, "workflow": rounds.round_status(),
                "next_suggested": rounds.round_status()["next_suggested"]}
    with collection_reader(platform) as reader:
        result = cli._login(platform, args, driver=reader)
    return {**result, "collection_executor": "cdp_readonly", "delivery_executor": "codex_native"}


def discover(platform, *, wait_seconds=6, page_delay=2.0, candidate_limit=None,
             recommendation_limit=None, accept_partial=False):
    from jobagent.application.discover import run_discover
    active = rounds.ensure_current_round()
    item = active.get("platforms", {}).get(platform, {})
    # Repeating Discover after success is a continuation, never a new bill.
    if item.get("status") in {"discovered", "awaiting_delivery_confirmation", "reviewed", "sent", "completed"}:
        workflow = rounds.round_status()
        return {"ok": True, "event": "discovery_already_completed", "request_preserved": True,
                "workflow": workflow, "next_suggested": workflow["next_suggested"]}
    assert_collection_available(platform)
    # Rounds created before options were persisted used 100. Only newly
    # created/explicitly opted-in rounds receive the smaller default.
    options = dict(active.get("discovery_options") or {"candidate_limit": 100, "recommendation_limit": None})
    requested = {"candidate_limit": candidate_limit, "recommendation_limit": recommendation_limit}
    from jobagent.infra.discovery_state import load_pending_start, load_pending_decision, load_collection_checkpoint
    preserved = load_pending_start(platform) or load_pending_decision(platform) or load_collection_checkpoint(platform)
    for key, value in requested.items():
        if value is None:
            continue
        if not 1 <= value <= 1000:
            raise ValueError(f"{key} must be between 1 and 1000")
        if preserved and options.get(key) != value:
            raise ValueError("The preserved Discover scope cannot change; finish the original request first")
        options[key] = value
    active["discovery_options"] = options
    rounds.save_round(active)
    started = monotonic()
    result = run_discover(platform, wait_seconds=wait_seconds, page_delay=page_delay,
                          **({"accept_partial": True} if accept_partial else {}))
    return {**result, "collection_executor": "cdp_readonly", "delivery_executor": "codex_native",
            "elapsed_seconds": round(monotonic() - started, 3)}


def enable_existing_round(*, confirmed=False):
    """Explicitly opt an idle round into bulk collection without replacing it."""
    active = rounds.ensure_current_round()
    if not confirmed:
        return {"ok": False, "error": "collection_migration_confirmation_required",
                "requires_user_action": True, "request_preserved": True,
                "next_suggested": "jobagent round use-bulk-discovery --confirm"}
    if browser_work.has_open():
        raise rounds.RoundOrderError({"ok": False, "error": "native_work_context_locked",
            "request_preserved": True, "next_suggested": "jobagent work next"})
    from jobagent.infra.discovery_state import load_collection_checkpoint, load_pending_decision, load_pending_start
    for platform in rounds.DEFAULT_PLATFORM_ORDER:
        checkpoint = load_collection_checkpoint(platform)
        if checkpoint or load_pending_start(platform) or load_pending_decision(platform) or active.get("platforms", {}).get(platform, {}).get("native_delivery"):
            raise ValueError("Finish the preserved discovery/delivery before changing its collection executor")
    active.update(collection_executor="cdp_readonly", delivery_executor="codex_native")
    active.setdefault("discovery_options", {"candidate_limit": 30, "recommendation_limit": None})
    rounds.save_round(active)
    return {"ok": True, "request_preserved": True, "workflow": rounds.round_status(),
            "next_suggested": rounds.round_status()["next_suggested"]}
