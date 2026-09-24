"""Explicitly close an old round before selecting a different resume direction."""
import shlex

from jobagent.infra import state, rounds, browser_work, protocol
from jobagent.infra.interaction_protocol import build_interaction_required, build_host_presentations
from jobagent.infra.interaction_state import save_pending_interaction, clear_pending_interaction


def _response(record):
    interaction = record["interaction"]
    return {"ok": False, "error": "interaction_required", "requires_user_action": True,
            "interaction": interaction, "host_presentations": build_host_presentations(interaction),
            "request_preserved": True, "user_prompt": interaction["fallback_text"],
            "next_suggested": "jobagent interaction respond --interaction-id " + shlex.quote(interaction["interaction_id"]) + " --choice <choice>"}


def pending():
    active = state.load_json(state.current_round_path()) or {}
    record = active.get("direction_change") or {}
    if record.get("status") == "awaiting":
        return _response(record)
    if record.get("status") == "finishing":
        return {"ok": True, "event": "direction_change_resuming", "request_preserved": True,
                "next_suggested": "jobagent interaction respond --interaction-id " + shlex.quote(record["interaction"]["interaction_id"]) + " --choice finish_round_and_rebind"}
    return None


def request_change(body):
    from jobagent.application.round_intent import _normalize_roles
    # Use the shared role normalization and never partially apply the other
    # filters bundled with a direction change.
    raw_roles = body["patch"].get("target_roles")
    if not isinstance(raw_roles, list) or not all(isinstance(role, str) for role in raw_roles):
        raise ValueError("Target roles must be a list of readable names")
    roles = _normalize_roles(raw_roles)
    if not roles or len(roles) != len(raw_roles) or len(roles) > 4:
        raise ValueError("At least one explicit target role is required")
    active = rounds.ensure_current_round()
    existing = active.get("direction_change")
    if existing and existing.get("status") in {"awaiting", "finishing"}:
        if existing["request"] != body:
            return {"ok": False, "error": "direction_change_already_pending", "next_suggested": "jobagent workflow next"}
        return _response(existing)
    identifier = "direction:" + protocol.digest_payload([active["round_id"], body]).split(":")[1][:32]
    text = ("当前轮次只能投递原简历绑定的方向。要改为“" + "、".join(roles) +
            "”，需要先结束旧轮，再选择对应方向的简历。\n"
            "请选择：1. 结束旧轮并重新选简历；2. 保留当前轮次。\n"
            "原投递和未决结果会保留；若有在途动作，必须先完成或核验。")
    interaction = build_interaction_required(interaction_id=identifier, product_id="job_agent",
        kind="round_direction_change", title="更改求职方向", prompt="是否结束当前轮次后换绑简历？",
        fields=[{"field_id": "direction_choice", "type": "single", "label": "轮次决定", "required": True,
            "options": [{"option_id": "finish_round_and_rebind", "label": "结束旧轮并换简历", "description": "串行结束剩余平台，保留历史，再选择新方向简历。"},
                        {"option_id": "keep_round", "label": "保留当前轮次", "description": "取消这次方向变更，保留原目标和待办。"}],
            "default_option_ids": [], "min_selections": 1, "max_selections": 1, "allow_other": False}],
        fallback_text=text, continuation_action="jobagent.interaction.respond", idempotency_key=identifier)
    record = {"status": "awaiting", "request": body, "interaction": interaction,
              "previous_pending": state.load_json(state.pending_interaction_path())}
    active["direction_change"] = record
    rounds.save_round(active)
    save_pending_interaction(interaction, stage="round_direction_change")
    return _response(record)


def respond(interaction_id, choice):
    active = state.load_json(state.current_round_path()) or {}
    record = active.get("direction_change") or {}
    if record.get("interaction", {}).get("interaction_id") != interaction_id:
        return None
    if choice not in {"finish_round_and_rebind", "keep_round"}:
        return {**_response(record), "error": "invalid_interaction_response"}
    if record.get("status") in {"completed", "cancelled"}:
        if choice != record["choice"]:
            return {"ok": False, "error": "interaction_response_conflict"}
        return {**record["result"], "replayed": True}
    if choice == "keep_round":
        if record.get("status") == "finishing":
            return {"ok": False, "error": "interaction_response_conflict"}
        record.update(status="cancelled", choice=choice)
        previous = record.get("previous_pending")
        if previous:
            state.save_json(state.pending_interaction_path(), previous)
        else:
            clear_pending_interaction()
        result = {"ok": True, "event": "direction_change_cancelled", "next_suggested": "jobagent workflow next"}
    else:
        if browser_work.has_open():
            return {"ok": False, "error": "native_work_context_locked", "request_preserved": True,
                    "next_suggested": "jobagent work next"}
        from jobagent.cli import build_parser, _dispatch_unlocked
        record.update(status="finishing", choice=choice)
        rounds.save_round(active)
        for _ in rounds.DEFAULT_PLATFORM_ORDER:
            workflow = rounds.round_status()
            if workflow.get("workflow_complete"):
                break
            platform = workflow.get("current_platform")
            if platform is None:
                platform = workflow["remaining_platforms"][0]
            result = _dispatch_unlocked(build_parser().parse_args(["round", "skip", "--platform", platform, "--confirm-skip"]))
            if result.get("ok") is not True:
                return result
        if not rounds.round_status().get("workflow_complete"):
            return {"ok": False, "error": "round_finish_incomplete", "next_suggested": "jobagent round status"}
        from jobagent.application.round_request import remember
        from jobagent.application.round_resume_binding import clear_pending_binding
        patch = record["request"]["patch"]
        clear_pending_binding()
        remember(roles=patch["target_roles"], cities=patch.get("target_cities") or active["intent"].get("target_cities"))
        clear_pending_interaction()
        # Retain salary/company updates until the new, matching round exists.
        from jobagent.application import workflow as journal
        value = journal._load()
        current_criteria = (active.get("round_criteria") or {}).get("criteria") or value.get("intent") or {}
        value["intent"] = {**current_criteria, **{k: v for k, v in patch.items() if k != "clear"}}
        value["new_round_requested_after"] = active["round_id"]
        for key in patch.get("clear", []):
            value["intent"].pop(key, None)
        cloud = value.get("cloud_workflow")
        if cloud:
            if not record.get("cloud_intent_payload"):
                record["cloud_intent_payload"] = {"request_id": "direction_" + journal._digest(record["request"])[:32],
                    "session_id": cloud["session_id"], "expected_revision": cloud["revision"], "criteria": value["intent"]}
                closed = state.load_json(state.current_round_path())
                closed["direction_change"] = record
                rounds.save_round(closed)
            response = journal.cloud_client.workflow_submit(record["cloud_intent_payload"])
            value["cloud_workflow"] = response["workflow"]
        journal._save(value)
        record.update(status="completed", choice=choice)
        result = {"ok": True, "event": "old_round_finished", "scope": "old_round",
                  "message": "旧轮已结束，历史投递结果保留。请继续选择新方向的简历。",
                  "next_suggested": "jobagent round start"}
    record["result"] = result
    active = state.load_json(state.current_round_path()) or active
    active["direction_change"] = record
    rounds.save_round(active)
    return result
