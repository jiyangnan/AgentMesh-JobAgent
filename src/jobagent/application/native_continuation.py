"""Continue a verified, unattempted legacy Liepin prerequisite failure.

This is not retry permission for an ambiguous external action. The original
intent and every receipt remain immutable; only explicit no-action evidence
can settle the old ordering error before the missing prerequisite is issued.
"""
from __future__ import annotations

import json
from pathlib import Path

from jobagent.infra import browser_work as store


def eligible(work: dict) -> bool:
    result = work.get("result") or {}
    e = result.get("evidence") or {}
    return (work.get("action") == "submit_resume" and work.get("side_effect") is True
            and work.get("binding", {}).get("platform") == "liepin"
            and work.get("task", {}).get("delivery_source") is not None
            and "delivery_order_version" not in work.get("task", {})
            and work.get("state") == "reconcile_only"
            and result.get("requires_technical_recovery") is True
            and result.get("requires_user_action") is False
            and result.get("reason") == "page_state_unknown"
            and e.get("side_effect_attempted") is False
            and e.get("missing_prerequisite") == "open_communication"
            and e.get("resume_state") in (None, "not_sent")
            and e.get("communication_state") in (None, "not_open")
            and e.get("message_state") in (None, "not_sent")
            and not e.get("existing_outgoing_text") and not e.get("outgoing_text"))


def contract(work: dict) -> dict | None:
    if not eligible(work):
        return None
    return {
        "kind": "liepin_unattempted_prerequisite", "status": "evidence_required",
        "work_id": work["work_id"], "new_execution_permitted": False,
        "command_template": f"jobagent work continue --work-id {work['work_id']} --result <result.json>",
        "instruction": "Use only actual already obtained or read-only evidence. This continuation settles a legacy resume task only when no external action was attempted. It preserves the original round, preview and authorization; it never retries an uncertain click or resets an observation budget.",
        "result_schema": {
            "required": ["receipt_id", "nonce", "binding", "outcome", "reason", "evidence"],
            "outcome": "not_attempted", "reason": "communication_prerequisite_missing",
            "evidence": {
                "common": "All common native account/window evidence and exact job identity from work.task.result_schema",
                "history_checked": True, "receipt_checked": True, "login_state": "authenticated",
                "resume_state": "not_sent", "communication_state": "not_open", "message_state": "not_sent",
                "side_effect_attempted": False, "submission_control_present": False,
                "missing_prerequisite": "open_communication",
                "visible_controls": "Observed nonempty list containing 聊一聊 or 立即沟通; not guessed",
            },
        },
    }


def continue_unattempted(work_id: str, result_path: str) -> dict:
    from jobagent.application import native_work as native
    from jobagent.infra import rounds

    binding = native._binding()
    work = store.get_work(work_id, binding)
    rounds.assert_platform_turn(work["binding"]["platform"])
    path = Path(result_path)
    if not path.is_file() or path.stat().st_size > 2_000_000:
        native._error("native_result_file_invalid", "Provide a local observation JSON no larger than 2 MB.")
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        native._error("native_result_file_invalid", "Cannot read the observation JSON.")
    if not isinstance(result, dict):
        native._error("native_result_file_invalid", "Observation result must be an object.")
    if work["state"] == "closed":
        if (work.get("result") or {}).get("outcome") != "not_attempted":
            native._error("native_continuation_not_allowed", "A settled delivery cannot be reopened.")
        store.submit_work(work_id, binding, result)  # identical replay only
        return native._delivery_next("liepin")
    if not eligible(work):
        native._error("native_continuation_not_allowed", "Only an explicitly unattempted legacy Liepin prerequisite failure can continue.")
    receipts = store.work_receipts(work_id, binding)
    if not receipts or any(not eligible({**work, "result": r}) for r in receipts):
        native._error("native_continuation_not_allowed", "Earlier ambiguous action evidence cannot be replaced with a no-action claim.")
    if (result.get("outcome") != "not_attempted"
            or result.get("reason") != "communication_prerequisite_missing"
            or result.get("requires_user_action") or result.get("requires_technical_recovery")):
        native._error("native_continuation_evidence_invalid", "Provide the declared no-action continuation receipt.")
    e = native._common_evidence(work, result)
    expected = {"side_effect_attempted": False, "submission_control_present": False,
                "missing_prerequisite": "open_communication", "history_checked": True,
                "receipt_checked": True, "login_state": "authenticated", "resume_state": "not_sent",
                "communication_state": "not_open", "message_state": "not_sent"}
    if any(type(e.get(k)) is not type(v) or e.get(k) != v for k, v in expected.items()):
        native._error("native_continuation_evidence_invalid", "Unknown or attempted actions remain reconciliation-only.")
    controls = e.get("visible_controls")
    if not isinstance(controls, list) or not all(isinstance(c, str) for c in controls) or not set(controls).intersection({"聊一聊", "立即沟通"}):
        native._error("native_continuation_evidence_invalid", "Observe the actual communication entry; do not infer the missing prerequisite.")
    if e.get("existing_outgoing_text") or e.get("outgoing_text"):
        native._error("native_continuation_evidence_invalid", "Existing outgoing evidence requires ordinary reconciliation.")
    native._validate_delivery({**work, "action": "inspect_delivery"}, {**result, "outcome": "success"}, e)
    reviewed = native._review_for(work)  # current original signature, scope and authorization
    active = rounds.ensure_current_round()
    session = active.get("native_session") or {}
    source = work["task"]["delivery_source"]
    if (session.get("id") != work["binding"].get("session_id")
            or session != work["task"].get("session")
            or active["platforms"]["liepin"].get("native_delivery") != source
            or reviewed["discover_id"] != work["binding"].get("discover_id")
            or native.digest_payload(reviewed["send_candidates"]) != work["binding"].get("candidate_digest")
            or not any(j == work["task"]["job"] for j in reviewed["send_candidates"])
            or any(source.get(k) != work["binding"].get(k) for k in ("preview_id", "authorization_id"))):
        native._error("native_continuation_context_mismatch", "The original session, list and authorization must remain unchanged.")
    others = [w for w in store.list_account_work(binding["account_ref"])
              if w["work_id"] != work_id and w.get("side_effect")
              and w["binding"].get("platform") == "liepin"
              and w["binding"].get("job_id") == work["binding"]["job_id"]]
    if others or native._legacy_history("liepin", work["task"]["job"]["url"], work["binding"]["job_id"]):
        native._error("native_continuation_not_allowed", "Other action history must be reconciled; no new permission was issued.")
    store.submit_work(work_id, binding, result)
    return native._delivery_next("liepin")
