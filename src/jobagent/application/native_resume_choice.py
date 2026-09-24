"""Persist an explicit choice from a platform's observed resume dialog."""
from __future__ import annotations

from jobagent.infra import browser_work as store, rounds, state
from jobagent.infra.interaction_protocol import build_host_presentations, build_interaction_required
from jobagent.infra.interaction_state import clear_pending_interaction, load_pending_interaction, save_pending_interaction
from jobagent.infra.protocol import digest_payload


def validate_preparation(work, evidence):
    from jobagent.application import native_work as native
    required = {"submission_attempted": False, "dialog_cancellable": True,
                "options_complete": True, "conversation_job_verified": True,
                "communication_state": "open", "resume_state": "not_sent"}
    if any(type(evidence.get(k)) is not type(v) or evidence.get(k) != v for k, v in required.items()):
        native._error("native_resume_dialog_unverified", "Inspect the cancellable dialog without submitting; unknown actions require reconciliation.")
    if evidence.get("resume_reference") != work["task"].get("resume_reference"):
        native._error("native_resume_changed", "The online resume must match the original preflight.")
    mode, options = evidence.get("submission_mode"), evidence.get("attachment_options")
    if mode not in {"online_only", "online_and_attachment"} or not isinstance(options, list) or len(options) > 20:
        native._error("native_resume_options_invalid", "Report the observed submission mode and all existing attachments (at most 20).")
    if (mode == "online_only" and options) or (mode == "online_and_attachment" and not options):
        native._error("native_resume_options_invalid", "The attachment list must agree with the visible submission mode.")
    refs = []
    for option in options:
        if not isinstance(option, dict) or set(option) != {"reference", "selected"}:
            native._error("native_resume_options_invalid", "Each attachment needs its visible reference and selected boolean.")
        ref = option["reference"]
        if not isinstance(ref, str) or not ref.strip() or len(ref) > 500 or type(option["selected"]) is not bool:
            native._error("native_resume_options_invalid", "Use the actual distinguishable name and visible upload time; never infer a selection.")
        refs.append(ref)
    if len(refs) != len(set(refs)) or sum(o["selected"] for o in options) > 1:
        native._error("native_resume_options_ambiguous", "Attachments must be distinguishable; do not choose by an ambiguous filename.")


def _response(record):
    interaction = record["interaction"]
    return {"ok": False, "event": "platform_resume_choice", "error": "interaction_required",
            "requires_user_action": True, "request_preserved": True,
            "delivery_paused": record.get("status") == "paused",
            "interaction": interaction, "host_presentations": build_host_presentations(interaction),
            "next_suggested": f'jobagent interaction respond --interaction-id "{interaction["interaction_id"]}" --attachment-id <option_id>',
            "workflow": rounds.round_status()}


def _check(record, active):
    from jobagent.application import native_work as native
    work = store.get_work(record["work_id"], native._binding())
    if (work["binding"] != record["binding"] or active.get("native_session") != work["task"]["session"]
            or active["platforms"]["liepin"].get("native_delivery") != work["task"]["delivery_source"]):
        native._error("native_resume_choice_context_mismatch", "Preserve the original account, session, job and delivery authorization.")
    return work


def selection(work):
    """Return a selected target, or the persistent unanswered interaction."""
    evidence = work["result"]["evidence"]
    validate_preparation(work, evidence)
    if evidence["submission_mode"] == "online_only":
        return {"submission_mode": "online_only", "attachment_reference": None}, None
    active = rounds.ensure_current_round()
    records = active.setdefault("native_resume_choices", {})
    record = records.get(work["work_id"])
    if record is None:
        interaction_id = "attachment:" + digest_payload([work["work_id"], work["binding"]])[7:]
        options = [{"option_id": "attachment_" + digest_payload(o["reference"])[7:31],
                    "label": o["reference"], "description": "平台当前默认选中；仍需你明确选择" if o["selected"] else "使用这份现有附件"}
                   for o in evidence["attachment_options"]]
        options.append({"option_id": "pause_delivery", "label": "暂停当前投递", "description": "保留进度，选择好附件后再继续；此时不提交简历"})
        job = work["task"]["job"]
        prompt = f'给「{job["title"]} · {job["company"]}」投递时，平台会同时发送在线简历「{evidence["resume_reference"]}」和一份附件。请选择本次附件；平台默认项不会自动采用。'
        fallback = prompt + "\n" + "\n".join(f'{i}. {o["label"]}（{o["description"]}）' for i, o in enumerate(options, 1))
        interaction = build_interaction_required(interaction_id=interaction_id, product_id="job_agent",
            kind="platform_resume_choice", title="选择平台简历附件", prompt=prompt,
            fields=[{"field_id": "attachment_id", "type": "single", "label": "简历附件", "options": options}],
            fallback_text=fallback, continuation_action="jobagent.interaction.respond", idempotency_key=interaction_id)
        record = {"work_id": work["work_id"], "binding": work["binding"], "status": "awaiting",
                  "interaction": interaction, "options": {o["option_id"]: o["label"] for o in options[:-1]}}
        records[work["work_id"]] = record
        rounds.save_round(active)
    _check(record, active)
    if record["status"] == "answered":
        current = load_pending_interaction() or {}
        if current.get("interaction_id") == record["interaction"]["interaction_id"]:
            clear_pending_interaction()
        return {"submission_mode": "online_and_attachment", "attachment_reference": record["options"][record["answer"]],
                "attachment_interaction_id": record["interaction"]["interaction_id"]}, None
    save_pending_interaction(record["interaction"], stage="platform_resume_choice", context={"work_id": work["work_id"]})
    return None, _response(record)


def pending(*, recover_answered=False):
    active = state.load_json(state.current_round_path()) or {}
    if active.get("status") != "active":
        return None
    for record in active.get("native_resume_choices", {}).values():
        if record["status"] in {"awaiting", "paused"}:
            _check(record, active)
            return _response(record)
        current = load_pending_interaction() or {}
        if recover_answered and record["status"] == "answered" and current.get("interaction_id") == record["interaction"]["interaction_id"]:
            _check(record, active)
            return {"ok": True, "event": "platform_resume_choice_saved", "next_suggested": "jobagent work next"}
    return None


def respond(interaction_id, attachment_id):
    active = state.load_json(state.current_round_path()) or {}
    for record in active.get("native_resume_choices", {}).values():
        if record["interaction"]["interaction_id"] != interaction_id:
            continue
        from jobagent.application import native_work as native
        work = _check(record, active)
        if attachment_id not in {*record["options"], "pause_delivery"}:
            return {**_response(record), "error": "invalid_interaction_response"}
        if record["status"] == "answered":
            if record["answer"] != attachment_id:
                return {"ok": False, "error": "interaction_response_conflict", "next_suggested": "jobagent work status"}
            # A response replay never reopens a completed platform or work.
            return {"ok": True, "idempotent_replay": True, "next_suggested": "jobagent work next"}
        rounds.assert_platform_turn("liepin")
        if store.has_open():
            return {"ok": False, "error": "native_work_context_locked", "next_suggested": "jobagent work status"}
        if attachment_id == "pause_delivery":
            record.update(status="paused")
            rounds.save_round(active)
            return _response(record)
        native._review_for(work)
        record.update(status="answered", answer=attachment_id, answered_at=rounds.utc_now())
        rounds.save_round(active)
        current = load_pending_interaction() or {}
        if current.get("interaction_id") == interaction_id:
            clear_pending_interaction()
        return native._delivery_next("liepin")
    return None
