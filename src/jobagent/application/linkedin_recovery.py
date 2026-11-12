"""Restart changed, unsubmitted applications without restoring old permission."""

from copy import deepcopy

from jobagent.infra import browser_work, rounds
from jobagent.infra.interaction_state import (
    clear_pending_interaction,
    load_pending_interaction,
)
from jobagent.infra.protocol import digest_payload


def issued(work):
    return (work["side_effect"] and work["state"] != "ready"
            and (work.get("result") or {}).get("outcome") != "cancelled")


def restart_entry(checkpoint, job_id):
    """Keep reusable facts; re-confirm attachment bytes even with the same name."""
    old = checkpoint["jobs"][job_id]
    for page in old.get("pages", []):
        for field in page["fields"]:
            if field["type"] != "attachment":
                continue
            reference = field.get("source", {}).get("reference", "")
            for key, fact in list(checkpoint["facts"].items()):
                if fact["source"].get("reference") == reference:
                    checkpoint["facts"].pop(key)
                    checkpoint.get("confirmed_prefills", {}).pop(key, None)
    checkpoint["generation"] = checkpoint.get("generation", 0) + 1
    checkpoint["jobs"][job_id] = {
        "status": "restart", "revision": old["revision"] + 1, "pages": [],
        "restarting": True, "processed": list(old.get("processed", [])),
    }


def recover_change(work, code):
    from jobagent.application import linkedin_application as preparation
    from jobagent.application import native_work

    if (work["binding"]["platform"] != "linkedin"
            or code != "linkedin_application_preview_changed" or issued(work)):
        return None
    active, checkpoint = preparation._load()
    job_id = work["binding"]["job_id"]
    if work["task"]["job"].get("application") != checkpoint["jobs"][job_id].get("application"):
        preparation.fail("linkedin_recovery_context_changed", "Recover only the current prepared application")
    if any(issued(w) and w["binding"].get("platform") == "linkedin"
           and w["binding"].get("job_id") == job_id
           for w in browser_work.list_account_work(native_work._binding()["account_ref"])):
        preparation.fail("native_previous_action_unresolved", "An issued application can only be reconciled")
    # Journal first. Restarting after a crash repeats only this cancellation;
    # it never creates another action intent or clears a receipt.
    active["linkedin_recovery"] = {
        "work_id": work["work_id"], "job_id": job_id,
        "context_digest": digest_payload(checkpoint["context"]),
        "source": deepcopy(work["task"]["delivery_source"]),
        "reason": code,
    }
    rounds.save_round(active)
    resume()
    return {**native_work.next_work(), "application_reprepared": True,
            "old_authorization_invalidated": True, "no_charge": True}


def resume():
    from jobagent.application import linkedin_application as preparation
    from jobagent.application import native_work

    active = rounds.ensure_current_round()
    journal = active.get("linkedin_recovery")
    if not journal:
        return
    active, checkpoint = preparation._load()
    if journal["context_digest"] != digest_payload(checkpoint["context"]):
        preparation.fail("linkedin_recovery_context_changed", "Keep the original recovery context")
    work = browser_work.get_work(journal["work_id"], native_work._binding("linkedin"))
    if issued(work):
        preparation.fail("native_previous_action_unresolved", "Do not reset an issued application")
    browser_work.cancel_unexecuted(work["work_id"], native_work._binding(), reason="application_changed")
    restart_entry(checkpoint, journal["job_id"])
    for item in browser_work.list_work(native_work._binding("linkedin")):
        result = item.get("result") or {}
        if (issued(item) and result.get("outcome") == "success"
                and result.get("evidence", {}).get("resume_state") == "sent"):
            done = checkpoint["jobs"].get(item["binding"].get("job_id"))
            if done:
                done["status"] = "unavailable"
    checkpoint.pop("card", None)
    pending = load_pending_interaction()
    if pending and (pending.get("context") or {}).get("platform") == "linkedin":
        clear_pending_interaction()
    # Resume the reviewed file so exclusions and explicit promotions survive.
    active["native_review"].update(input_path=journal["source"]["input_path"])
    active["platforms"]["linkedin"].pop("native_delivery", None)
    active["platforms"]["linkedin"]["status"] = "reviewed"
    active.setdefault("native_processed_work", []).append(work["work_id"])
    active.setdefault("linkedin_recovery_history", []).append(journal)
    active.pop("linkedin_recovery")
    preparation._save(active, checkpoint)


def replace_context(active, checkpoint, context):
    """A newly verified decision may replace a stale, unissued preparation."""
    from jobagent.application import linkedin_application as preparation
    from jobagent.application import native_work

    if any(checkpoint["context"].get(k) != context.get(k)
           for k in ("account_ref", "round_id", "session")):
        preparation.fail("linkedin_application_context_changed", "Restore the original browser account and round")
    works = browser_work.list_work(native_work._binding("linkedin"))
    if any(issued(w) and (w.get("result") or {}).get("outcome") != "success" for w in works):
        preparation.fail("native_previous_action_unresolved", "Reconcile the original submit before changing context")
    for work in works:
        if work["state"] != "closed":
            browser_work.cancel_unexecuted(work["work_id"], native_work._binding(), reason="application_changed")
        if (work.get("result") or {}).get("reason") == "application_changed" or work["state"] != "closed":
            processed = active.setdefault("native_processed_work", [])
            if work["work_id"] not in processed:
                processed.append(work["work_id"])
    pending = load_pending_interaction()
    if pending and (pending["interaction_id"].startswith(preparation.PREFIX)
                    or (pending.get("context") or {}).get("platform") == "linkedin"):
        clear_pending_interaction()
    active["platforms"]["linkedin"].pop("native_delivery", None)
    active.setdefault("linkedin_preparation_history", []).append({
        "context_digest": digest_payload(checkpoint["context"]),
        "generation": checkpoint.get("generation", 0),
    })
    fresh = {"context": context, "jobs": {}, "facts": {}, "plans": {}, "answered": {},
             "generation": checkpoint.get("generation", 0) + 1}
    active["linkedin_preparation"] = fresh
    rounds.save_round(active)
    return fresh
