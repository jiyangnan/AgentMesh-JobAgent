"""JobAgent-owned delivery transport over the existing durable work ledger.

The host never receives a browser mutation capability. A CLI invocation claims
one intent, performs at most one recruiting action, and validates its receipt.
An interrupted intent can only be inspected on the next invocation.
"""
from __future__ import annotations

import copy
from contextlib import ExitStack
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

from jobagent.infra import browser_work as store, rounds, state
from jobagent.infra.account_state import current_account_ref
from jobagent.infra.protocol import digest_payload

EXECUTOR = "playwright"
SOURCE = "playwright_dom_observation"
_RECEIPT_CAPABILITY = object()


def enabled(active=None):
    active = active if active is not None else state.load_json(state.current_round_path()) or {}
    return active.get("delivery_executor", active.get("browser_executor")) == EXECUTOR


def owns(work):
    return work.get("task", {}).get("executor") == EXECUTOR


def _error(code, message):
    raise store.BrowserWorkError(code, message)


def session(active=None):
    active = active if active is not None else rounds.ensure_current_round()
    value = active.get("playwright_session")
    if value and (value.get("account_ref") != current_account_ref() or value.get("round_id") != active["round_id"]):
        _error("playwright_session_binding_mismatch", "Keep the original verified account and round.")
    return value


def ensure_session(platform):
    active = rounds.ensure_current_round()
    current = session(active)
    collection = active.get("collection_session") or {}
    if current:
        if collection and current.get("profile_directory") != collection.get("profile_directory"):
            _error("playwright_profile_changed", "The collection profile changed; do not switch browser accounts.")
        return None
    from jobagent.drivers.boss.chrome_manager import ChromeInstanceManager
    manager = ChromeInstanceManager()
    profile = str(Path(collection.get("profile_directory") or manager.user_data_dir).resolve())
    current = {"account_ref": current_account_ref(), "round_id": active["round_id"],
               "profile_directory": profile, "port": collection.get("port", manager.port),
               "accounts": {}, "collection_session_id": collection.get("id")}
    current["id"] = "playwright_" + digest_payload(current)[7:31]
    active.update(playwright_session=current, browser_session_id=current["id"])
    rounds.save_round(active)
    return None


def migrate(*, confirmed: bool):
    """Explicit, restartable transport change. Signed business state is retained."""
    from jobagent.application import native_work as native
    if not confirmed:
        _error("user_confirmation_required", "Changing an in-flight executor requires explicit user authorization.")
    active = rounds.ensure_current_round()
    binding = native._binding()
    if enabled(active) and not active.get("playwright_migration_pending"):
        return {"ok": True, "event": "browser_executor_selected", "executor": EXECUTOR,
                "idempotent_replay": True, "next_suggested": "jobagent work next"}
    if any(choice.get("status") in {"awaiting", "paused"}
           for choice in active.get("native_resume_choices", {}).values()):
        _error("executor_migration_needs_reconciliation", "A prepared resume dialog must be reconciled with its original executor before migration.")
    works = store.list_work(binding)
    account_works = store.list_account_work(binding["account_ref"])
    pending = [w for w in works if w["state"] != "closed"]
    for work in pending:
        # Do not convert an issued external action, a prepared chooser, or a
        # post-communication inspection into a fresh write permission.
        safe = (not work["side_effect"] and work["action"] in {"bind_session", "inspect_session", "inspect_delivery"}
                and work["task"].get("inspection_phase") != "after_communication")
        if work["action"] == "inspect_delivery":
            native._review_for(work)
            safe = safe and not any(w["side_effect"] and w["binding"].get("job_id") == work["binding"].get("job_id")
                                    and w["binding"].get("platform") == work["binding"].get("platform") for w in account_works)
            safe = safe and not native._legacy_history(work["binding"]["platform"], work["task"]["job"]["url"], work["binding"]["job_id"])
        if not safe:
            _error("executor_migration_needs_reconciliation", "An outstanding action must be reconciled before changing executors; no new delivery permission was issued.")
    journal = active.get("playwright_migration_pending") or {
        "from": active.get("delivery_executor", "codex_native"), "to": EXECUTOR,
        "created_at": rounds.utc_now(), "superseded_inspections": [w["work_id"] for w in pending],
        "round_snapshot_digest": digest_payload(active),
    }
    # Journal first; a restart finishes exactly these cancellations. No rows,
    # receipts, approvals, discovery IDs or billing references are deleted.
    active["playwright_migration_pending"] = journal
    rounds.save_round(active)
    for work_id in journal["superseded_inspections"]:
        store.cancel_unexecuted(work_id, binding)
    old = active.get("native_session")
    if old:
        from jobagent.drivers.boss.chrome_manager import ChromeInstanceManager
        collection = active.get("collection_session") or {}
        manager = ChromeInstanceManager()
        value = {k: copy.deepcopy(old[k]) for k in ("id", "account_ref", "round_id", "accounts") if k in old}
        value.update(profile_directory=str(Path(collection.get("profile_directory") or manager.user_data_dir).resolve()),
                     port=collection.get("port", manager.port), collection_session_id=collection.get("id"))
        active["playwright_session"] = value
    active.setdefault("native_processed_work", []).extend(
        wid for wid in journal["superseded_inspections"] if wid not in active.get("native_processed_work", []))
    active.setdefault("playwright_migrations", []).append(journal)
    active.pop("playwright_migration_pending", None)
    active.update(browser_executor=EXECUTOR, delivery_executor=EXECUTOR)
    rounds.save_round(active)
    return {"ok": True, "event": "browser_executor_selected", "executor": EXECUTOR,
            "round_id": active["round_id"], "request_preserved": True,
            "superseded_read_only_work": journal["superseded_inspections"],
            "next_suggested": "jobagent work next"}


def present(work):
    from jobagent.drivers.playwright_guard import NavigationGuard
    target_session = work["task"].get("session") or {}
    if target_session.get("profile_directory"):
        try:
            NavigationGuard(target_session["profile_directory"], work["binding"]["platform"], work["task"].get("job", {}).get("url", "")).check()
        except store.BrowserWorkError as exc:
            return {**exc.payload, "executor": EXECUTOR, "work_id": work["work_id"]}
    rejected = state.load_json(receipt_path(work["work_id"]).with_suffix(".validation-block.json"))
    if rejected and work["state"] != "closed":
        return {**rejected, "executor": EXECUTOR, "work_id": work["work_id"],
                "requires_technical_recovery": True, "automatic_retry_allowed": False,
                "next_suggested": "jobagent work status"}
    result = work.get("result") or {}
    blocked = result.get("requires_user_action") or result.get("requires_technical_recovery")
    diagnostic = result.get("diagnostic") or {}
    return {"ok": True, "event": "browser_execution_required", "executor": EXECUTOR,
            "work": {k: work[k] for k in ("work_id", "action", "state", "side_effect", "binding")},
            "request_preserved": True, "reconciliation_only": work["side_effect"] and work["state"] != "ready",
            "requires_user_action": bool(result.get("requires_user_action")),
            "requires_technical_recovery": bool(result.get("requires_technical_recovery")),
            "reason": result.get("reason") if blocked else None,
            "message": diagnostic.get("message") if blocked and diagnostic.get("message") else
                       "JobAgent executes this work in its own Playwright runtime. Never perform the action through a host browser tool.",
            **({"diagnostic": diagnostic, "automatic_retry_allowed": False} if blocked else {}),
            "next_suggested": f"jobagent work run --work-id {work['work_id']}"}


def validate_evidence(work, result, evidence):
    from jobagent.application import native_work as native
    current = session()
    if not owns(work) or not current or work["binding"].get("session_id") != current["id"]:
        _error("playwright_session_binding_mismatch", "The work must belong to the preserved Playwright session.")
    if evidence.get("profile_directory") != current["profile_directory"] or evidence.get("profile_verified") is not True:
        _error("playwright_profile_unverified", "Verify the actual Chrome profile before observing or acting.")
    platform = work["binding"]["platform"]
    halted = result.get("requires_user_action") or result.get("requires_technical_recovery")
    if not halted and (not evidence.get("page_target_id") or not native._official(platform, evidence.get("page_url"))):
        _error("playwright_page_untrusted", "Use a verified target on the platform's official HTTPS origin.")
    account = current.get("accounts", {}).get(platform)
    if not halted and account and evidence.get("account_label") != account:
        _error("playwright_account_changed", "The visible platform account must match the preserved session.")


def receipt_path(work_id):
    return state.STATE_DIR / "playwright_receipts" / f"{work_id}.json"


def select_resume(platform, reference, *, confirmed):
    from jobagent.application import native_work as native
    from jobagent.application.native_resume_choice import validate_preparation
    if not confirmed:
        _error("user_confirmation_required", "The user must explicitly choose this attachment for the remaining approved list.")
    if platform == 'zhilian':
        from jobagent.platforms.zhilian.playwright_resume import select_existing
        return select_existing(reference)
    active = rounds.ensure_current_round()
    source = active["platforms"][platform].get("native_delivery") or {}
    matching = [w for w in store.list_work(native._binding(platform))
                if w["action"] == "prepare_resume" and w["state"] == "closed"
                and (w.get("result") or {}).get("outcome") == "success"
                and w["binding"].get("authorization_id") == source.get("authorization_id")]
    prepared = next((w for w in reversed(matching) if reference in
                     [o["reference"] for o in w["result"]["evidence"].get("attachment_options", [])]), None)
    if not prepared:
        _error("attachment_not_observed", "Choose an exact attachment from the current list's observed resume dialog.")
    validate_preparation(prepared, prepared["result"]["evidence"])
    native._review_for(prepared)
    evidence = prepared["result"]["evidence"]
    preference = {"account_ref": current_account_ref(), "authorization_id": source["authorization_id"],
                  "online_reference": evidence["resume_reference"], "attachment_reference": reference,
                  "option_references": sorted(o["reference"] for o in evidence["attachment_options"]),
                  "confirmed_at": rounds.utc_now(), "source_work_id": prepared["work_id"]}
    preference["consent_id"] = "batch_attachment_" + digest_payload(preference)[7:31]
    active["playwright_resume_preference"] = preference
    rounds.save_round(active)
    record = active.get("native_resume_choices", {}).get(prepared["work_id"])
    if record and record.get("status") in {"awaiting", "paused"}:
        from jobagent.application.native_resume_choice import respond
        option = next(key for key, value in record["options"].items() if value == reference)
        result = respond(record["interaction"]["interaction_id"], option)
        if not result or not result.get("ok"):
            return result
    return {"ok": True, "event": "platform_resume_preference_saved", "request_preserved": True,
            "attachment_reference": reference, "applies_to": "unchanged_remaining_authorized_jobs",
            "next_suggested": "jobagent work next"}


def accept(work_id, path):
    from jobagent.application import native_work as native
    try:
        result = native.submit(work_id, str(path), _capability=_RECEIPT_CAPABILITY)
        path.with_suffix(".validation-block.json").unlink(missing_ok=True)
        return result
    except store.BrowserWorkError as exc:
        # Keep rejected evidence, but do not replay it forever. An issued intent
        # still exists in the ledger, so the next run can only reconcile it.
        archived = path.with_name(path.stem + ".rejected-" + uuid.uuid4().hex + ".json")
        path.replace(archived)
        state.save_json(archived.with_suffix(".error.json"), exc.payload)
        state.save_json(path.with_suffix(".validation-block.json"), exc.payload)
        return {**exc.payload, "executor": EXECUTOR, "requires_technical_recovery": True,
                "receipt_rejected": True, "automatic_retry_allowed": False,
                "next_suggested": "jobagent work status"}


def pace(current_session):
    """Keep the same request spacing across CLI restarts and rounds."""
    path = Path(current_session["profile_directory"]) / "jobagent-playwright-pacing.json"
    last = state.load_json(path) or {}
    now = time.time()
    remaining = float(last.get("next_start_not_before", 0)) - now
    if remaining > 6:
        _error("playwright_clock_changed", "The persisted browser clock moved backwards; preserve the task for review.")
    if remaining > 0:
        time.sleep(remaining)
    state.save_json(path, {"last_started_at": time.time(),
                           "next_start_not_before": time.time() + secrets.choice((2, 3, 5))})


def run(work_id: str, *, max_steps: int = 1):
    from jobagent.application import native_work as native
    from jobagent.drivers.playwright_runtime import PlaywrightRuntime
    from jobagent.infra.diagnostics import emit_stage, progress_heartbeat
    if not 1 <= max_steps <= 30:
        _error("playwright_step_limit_invalid", "Run between one and thirty already-authorized work items.")
    started = time.monotonic()
    completed = []
    response = None
    with ExitStack() as stack:
        stack.enter_context(progress_heartbeat("browser_delivery_in_progress", executor=EXECUTOR))
        runtime = None
        for _ in range(max_steps):
            work_started = time.monotonic()
            work = store.get_work(work_id, native._binding())
            rounds.assert_platform_turn(work["binding"]["platform"])
            if not enabled() or not owns(work):
                _error("playwright_work_required", "Use the explicit migration before executing this work with Playwright.")
            emit_stage("browser_work_in_progress", executor=EXECUTOR, platform=work["binding"]["platform"],
                       action=work["action"], reconciliation_only=work["side_effect"] and work["state"] != "ready")
            saved = state.load_json(receipt_path(work_id))
            if saved and (work.get("result") or {}).get("outcome") not in {"uncertain", "unresolved"}:
                response = accept(work_id, receipt_path(work_id))
            elif work["state"] == "closed":
                response = native._continue(work)
            else:
                if work["state"] == "ready" and work["task"].get("delivery_source"):
                    native._review_for(work)
                # Browser connection, profile and preconditions are read-only. If
                # they fail, do not consume a never-issued external action intent.
                if runtime is None:
                    runtime = stack.enter_context(PlaywrightRuntime(session()))
                if runtime:
                    adapter = runtime.adapter(work)
                    try:
                        adapter.prepare(work)
                    except store.BrowserWorkError as exc:
                        exc.payload.update(work_id=work_id, action=work["action"],
                            requires_technical_recovery=True, automatic_retry_allowed=False,
                            next_suggested="jobagent work status")
                        raise
                    pace(session())
                    if work["state"] == "ready" or not work["side_effect"]:
                        try:
                            work = store.begin_work(work_id, native._binding())
                        except store.BrowserWorkError as exc:
                            # Read-only work that exhausted its observation budget
                            # may still produce one final observation receipt.
                            if work["side_effect"] or exc.payload.get("error") != "browser_work_observation_limit":
                                raise
                            work = store.pending_work(native._binding())
                    else:
                        work = store.pending_work(native._binding())
                    may_act = bool(work.get("execution_permitted"))
                    step_start = time.monotonic()
                    try:
                        result = adapter.execute(work, may_act=may_act)
                    except Exception as exc:
                        # Never retry a click after any exception. Keep the intent
                        # and browser visible; the next call is receipt inspection.
                        result = {"outcome": "uncertain", "requires_technical_recovery": True,
                                  "reason": "page_state_unknown", "evidence": runtime.evidence(work),
                                  "diagnostic": {"type": type(exc).__name__, "message": str(exc)[:800],
                                                 "code": exc.payload.get("error") if isinstance(exc, store.BrowserWorkError) else "playwright_observation_incomplete"}}
                    result.setdefault("evidence", {}).update(source=SOURCE, observed_at=rounds.utc_now())
                    result.update(receipt_id="pw_" + uuid.uuid4().hex, nonce=work["nonce"], binding=work["binding"],
                                  execution_seconds=round(time.monotonic() - step_start, 3))
                    path = receipt_path(work_id)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    state.save_json(path, result)
                    response = accept(work_id, path)
            elapsed = round(time.monotonic() - work_started, 3)
            completed.append({"work_id": work_id, "action": work["action"], "elapsed_seconds": elapsed})
            emit_stage("browser_work_observed", executor=EXECUTOR, platform=work["binding"]["platform"],
                       action=work["action"], elapsed_seconds=elapsed,
                       requires_technical_recovery=bool(response.get("requires_technical_recovery")))
            if response.get("requires_user_action") or response.get("requires_technical_recovery"):
                break
            following = response.get("work") or {}
            if response.get("executor") != EXECUTOR or not following.get("work_id") or following["work_id"] == work_id:
                break
            work_id = following["work_id"]
    return {**(response or {}), "execution": {"executor": EXECUTOR, "elapsed_seconds": round(time.monotonic() - started, 3),
                                             "work_items": completed}}
