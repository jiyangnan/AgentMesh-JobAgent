"""Explicit window recovery before any external action on the current job."""
from __future__ import annotations

import copy

from jobagent.infra import browser_work as store, rounds

SCOPE = "unattempted_delivery_inspection"


def validate_source(source: dict, *, completed: bool = False) -> dict:
    from jobagent.application import native_work as native
    if (source["action"] != "inspect_delivery" or source["side_effect"]
            or not source["task"].get("delivery_source")
            or source["task"].get("inspection_phase") == "after_communication"
            or source["state"] == "closed" and (source.get("result") or {}).get("outcome") != "cancelled"):
        native._error("native_recovery_not_allowed", "Only an unfinished inspection before any action on this job can recover its window.")
    session = native._session()
    active = rounds.ensure_current_round()
    platform = source["binding"]["platform"]
    reviewed = native._review_for(source)
    if (not session or session != source["task"]["session"]
            or active["platforms"][platform].get("native_delivery") != source["task"]["delivery_source"]
            or reviewed["discover_id"] != source["binding"].get("discover_id")
            or native.digest_payload(reviewed["send_candidates"]) != source["binding"].get("candidate_digest")
            or source["task"]["job"] not in reviewed["send_candidates"]
            or any(source["task"]["delivery_source"].get(k) != source["binding"].get(k)
                   for k in ("preview_id", "authorization_id"))):
        native._error("native_recovery_context_mismatch", "Keep the original session, approved job list and authorization.")
    if any(w["side_effect"] and w["binding"].get("platform") == platform
           and w["binding"].get("job_id") == source["binding"]["job_id"]
           for w in store.list_account_work(source["binding"]["account_ref"])) or native._legacy_history(
               platform, source["task"]["job"]["url"], source["binding"]["job_id"]):
        native._error("native_recovery_not_allowed", "Existing action history requires reconciliation and cannot gain a fresh permission.")
    allowed = {source["work_id"]}
    if completed:
        allowed.update(w["work_id"] for w in store.list_work(native._binding())
                       if w["action"] == "recover_session" and w["task"].get("recovery_source") == source["work_id"])
    if any(w["state"] != "closed" and w["work_id"] not in allowed for w in store.list_work(native._binding())):
        native._error("native_recovery_work_pending", "Preserve the other pending browser work.")
    return session


def recover(source: dict) -> dict:
    from jobagent.application import native_work as native
    previous = next((w for w in store.list_work(native._binding())
                     if w["action"] == "recover_session" and w["task"].get("recovery_source") == source["work_id"]), None)
    if previous:
        if previous["state"] == "closed" and (previous.get("result") or {}).get("outcome") == "success":
            return native._continue(previous)
        return native.present(previous)
    session = copy.deepcopy(validate_source(source))
    platform = source["binding"]["platform"]
    # Archive only the read-only inspection; never call delivery cancellation.
    # A crash here can resume this same confirmed source without changing scope.
    store.cancel_unexecuted(source["work_id"], native._binding())
    task = {"recovery_scope": SCOPE, "recovery_source": source["work_id"],
            "session": session, "expected_profile_label": session["profile_label"],
            "expected_account_label": session["accounts"][platform],
            "url": source["task"]["job"]["url"],
            "instruction": "Use the supported host's native app interface to inspect the existing Chrome profile and original platform account. Inspect native account navigation and existing resume/activity evidence. Verify the exact original profile path through Chrome's visible profile information when needed. Return to the declared job URL. No collect, communicate, apply, send, resume selection or upload. This only restores window context; the original list and prior receipts remain unchanged.",
            "allowed_actions": ["inspect_native_windows_and_tabs", "inspect_chrome_profile", "inspect_account_and_resume_activity", "return_to_declared_job"],
            "forbidden_actions": ["collect_jobs", "open_communication", "apply", "send_message", "select_resume", "upload_resume", "change_profile", "change_account", "CDP", "page_script"],
            "result_schema": {"outcome": "success|uncertain", "evidence": {}}}
    work = store.ensure_work(action="recover_session", task=task,
        binding={**source["binding"], "recovery_source": source["work_id"]}, key=f"recover-delivery:{source['work_id']}")
    return native.present(work)


def finish(work: dict) -> dict:
    from jobagent.application import native_work as native
    source = store.get_work(work["task"]["recovery_source"], native._binding())
    validate_source(source, completed=True)
    active = rounds.ensure_current_round()
    e = work["result"]["evidence"]
    active["native_session"].update({k: e[k] for k in ("window_reference", "window_reference_kind", "group_reference")})
    processed = active.setdefault("native_processed_work", [])
    for work_id in (source["work_id"], work["work_id"]):
        if work_id not in processed:
            processed.append(work_id)
    rounds.save_round(active)
    return native._delivery_next(source["binding"]["platform"])


def superseded_inspections(works: list[dict]) -> set[str]:
    return {w["task"]["recovery_source"] for w in works
            if w["action"] == "recover_session" and w["task"].get("recovery_scope") == SCOPE
            and w["state"] == "closed" and (w.get("result") or {}).get("outcome") == "success"}
