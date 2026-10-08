"""Account-bound choices after a native batch or a platform audit."""
from __future__ import annotations

from copy import deepcopy

from jobagent.infra import browser_work, rounds, state
from jobagent.infra.interaction_protocol import build_host_presentations, build_interaction_required
from jobagent.infra.interaction_state import clear_pending_interaction, load_pending_interaction, save_pending_interaction
from jobagent.infra.protocol import digest_payload

NAMES = {"boss": "Boss 直聘", "liepin": "猎聘", "zhilian": "智联招聘", "51job": "前程无忧"}


def _context(active, platform):
    from jobagent.application import native_work
    session = active.get("native_session") or {}
    return {"account_ref": native_work.current_account_ref(), "round_id": active.get("round_id"),
            "session_id": session.get("id"), "window_reference": session.get("window_reference"),
            "profile_label": session.get("profile_label"),
            "platform_account": session.get("accounts", {}).get(platform)}


def _check(record, active):
    if _context(active, record["platform"]) != record["binding"]:
        raise ValueError("Native delivery follow-up context mismatch; preserve the original round and session.")
    source = active.get("platforms", {}).get(record["platform"], {}).get("native_delivery")
    if source != record["source"]:
        raise ValueError("Native delivery follow-up source changed; preserve the original authorization.")


def _response(record):
    current = load_pending_interaction() or {}
    interaction = record["interaction"]
    if current.get("interaction_id") != interaction["interaction_id"]:
        save_pending_interaction(interaction, stage="native_delivery_followup",
                                 context={"platform": record["platform"], "binding": record["binding"]})
    choices = "|".join(o["option_id"] for o in interaction["fields"][0]["options"])
    return {"ok": False, "error": "interaction_required", "event": interaction["kind"],
            "requires_user_action": True, "request_preserved": True,
            "completion_state": "batch_limit_reached" if record["kind"] == "batch" else "platform_completed",
            "platform": record["platform"], "summary": deepcopy(record["summary"]),
            "processed_count": record["processed_count"], "remaining_count": len(record["remaining_job_ids"]),
            "delivery_paused": record["status"] == "paused", "interaction": interaction,
            "user_prompt": interaction["fallback_text"], "host_presentations": build_host_presentations(interaction),
            "workflow": rounds.round_status(),
            "next_suggested": f'jobagent interaction respond --interaction-id "{interaction["interaction_id"]}" --choice <{choices}>'}


def pending():
    active = state.load_json(state.current_round_path()) or {}
    current = load_pending_interaction() or {}
    for record in active.get("native_delivery_followups", {}).values():
        if (record["status"] == "answered"
                and current.get("interaction_id") == record["interaction"]["interaction_id"]):
            from jobagent.application import native_work
            if (record["binding"]["account_ref"] != native_work.current_account_ref()
                    or record["binding"]["round_id"] != active.get("round_id")):
                raise ValueError("Native delivery follow-up account/round mismatch.")
            clear_pending_interaction()
            return {"ok": True, "event": "native_delivery_followup_saved", "request_preserved": True,
                    "workflow": rounds.round_status(), "next_suggested": "jobagent work next"}
        if active.get("status") != "active":
            continue
        if record["status"] in {"awaiting", "paused"}:
            _check(record, active)
            return _response(record)
    return None


def register(platform, summary, *, reviewed=None, source=None):
    """Present only a decision; never issue a browser permission here."""
    existing = pending()
    if existing:
        return existing
    if browser_work.has_open():
        raise rounds.RoundOrderError({"ok": False, "error": "native_work_context_locked",
                                      "next_suggested": "jobagent work status"})
    active = rounds.ensure_current_round()
    workflow = rounds.round_status()
    source = deepcopy(source or active["platforms"][platform].get("native_delivery"))
    batch = reviewed is not None
    candidates = list(reviewed["send_candidates"]) if batch else []
    limit = int(source["limit"]) if batch else int(summary["summary"]["jobs"])
    remaining_ids = [str(j["id"]) for j in candidates[limit:]]
    next_platform = next((p for p in workflow["remaining_platforms"] if p != platform), None)
    label = NAMES[platform]
    if batch:
        prompt = f"{label}本批前 {limit} 个岗位已处理，原清单剩余 {len(remaining_ids)} 个未投递。请选择下一步；已核验回执和原文案保留。"
        options = [{"option_id": "continue_delivery", "label": "继续剩余岗位",
                    "description": "复核原完整清单和授权，只处理尚未执行的岗位"},
                   {"option_id": "finish_platform", "label": f"结束本平台，进入{NAMES[next_platform]}" if next_platform else "结束本平台",
                    "description": "明确跳过本轮剩余岗位，保留已处理结果后继续平台顺序"},
                   {"option_id": "pause_delivery", "label": "稍后继续", "description": "保留本轮、授权和同一选择卡，不进行投递"}]
    else:
        prompt = f"{label}投递及审计已完成，本轮仍有未完成平台。是否继续{NAMES[next_platform]}？已有投递结果保留，新平台的最终投递清单仍需单独确认。"
        options = [{"option_id": "continue_platforms", "label": f"继续{NAMES[next_platform]}",
                    "description": "沿用原轮次进入下一平台，最终投递仍需单独确认"},
                   {"option_id": "pause_delivery", "label": "稍后继续", "description": "保留已完成结果和同一选择卡"}]
    binding = _context(active, platform)
    identifier = "native-followup:" + digest_payload([binding, source, "batch" if batch else "platform", limit])[7:]
    records = active.setdefault("native_delivery_followups", {})
    if identifier in records:
        record = records[identifier]
        if record["status"] in {"awaiting", "paused"}:
            return _response(record)
        return {"ok": True, "request_preserved": True, "next_suggested": "jobagent work next",
                "workflow": workflow}
    fallback = prompt + "\n" + "\n".join(f'{i}. {o["label"]}（{o["description"]}）' for i, o in enumerate(options, 1))
    interaction = build_interaction_required(interaction_id=identifier, product_id="job_agent",
        kind="delivery_batch_followup" if batch else "delivery_platform_followup", title="请选择后续安排",
        prompt=prompt, fields=[{"field_id": "delivery_followup_choice", "type": "single", "label": "下一步", "options": options}],
        fallback_text=fallback, continuation_action="jobagent.interaction.respond", idempotency_key=identifier)
    record = {"kind": "batch" if batch else "platform", "status": "awaiting", "platform": platform,
              "binding": binding, "source": source, "interaction": interaction, "next_platform": next_platform,
              "summary": deepcopy(summary["summary"]), "processed_count": limit,
              "remaining_job_ids": remaining_ids, "candidate_digest": digest_payload(candidates) if batch else None}
    records[identifier] = record
    rounds.save_round(active)
    return _response(record)


def respond(interaction_id, choice):
    active = state.load_json(state.current_round_path()) or {}
    record = active.get("native_delivery_followups", {}).get(interaction_id)
    if not record:
        return None
    from jobagent.application import native_work
    if (record["binding"]["account_ref"] != native_work.current_account_ref()
            or record["binding"]["round_id"] != active.get("round_id")):
        raise ValueError("Native delivery follow-up account/round mismatch.")
    if record["status"] == "answered":
        if record["choice"] != choice:
            return {"ok": False, "error": "interaction_response_conflict", "next_suggested": "jobagent work next"}
        current = load_pending_interaction() or {}
        if current.get("interaction_id") == interaction_id:
            clear_pending_interaction()
        return {"ok": True, "idempotent_replay": True, "workflow": rounds.round_status(),
                "next_suggested": "jobagent work next"}
    _check(record, active)
    options = {o["option_id"] for o in record["interaction"]["fields"][0]["options"]}
    if choice not in options:
        return {**_response(record), "error": "invalid_interaction_response"}
    if browser_work.has_open():
        return {"ok": False, "error": "native_work_context_locked", "request_preserved": True,
                "next_suggested": "jobagent work status"}
    if choice == "pause_delivery":
        record["status"] = "paused"
        rounds.save_round(active)
        return _response(record)
    if choice == "continue_delivery":
        rounds.assert_platform_turn(record["platform"])
        from jobagent.application.delivery import _load_reviewed
        source = record["source"]
        reviewed = _load_reviewed(record["platform"], source["input_path"], preview_id=source["preview_id"],
                                  authorization_id=source["authorization_id"])
        candidates = list(reviewed["send_candidates"])
        if digest_payload(candidates) != record["candidate_digest"]:
            raise ValueError("The authorized candidate list changed; preserve the original receipts.")
        if len(candidates) > 100:
            return {"ok": False, "error": "native_delivery_batch_capacity", "request_preserved": True,
                    "next_suggested": "jobagent work next"}
        active["platforms"][record["platform"]]["native_delivery"]["limit"] = len(candidates)
    elif choice == "finish_platform":
        rounds.assert_platform_turn(record["platform"])
        item = active["platforms"][record["platform"]]
        item["native_delivery_remainder_skipped"] = {"interaction_id": interaction_id,
            "job_ids": list(record["remaining_job_ids"]), "skipped_at": rounds.utc_now(), "source": deepcopy(record["source"])}
        item["status"] = "skipped_this_round"
        item.pop("next_suggested", None)
    elif rounds.round_status().get("current_platform") != record["next_platform"]:
        raise ValueError("The next platform changed; preserve the original follow-up.")
    record.update(status="answered", choice=choice, answered_at=rounds.utc_now())
    rounds.save_round(active)
    current = load_pending_interaction() or {}
    if current.get("interaction_id") == interaction_id:
        clear_pending_interaction()
    return {"ok": True, "event": "native_delivery_followup_saved", "request_preserved": True,
            "skipped_remaining": len(record["remaining_job_ids"]) if choice == "finish_platform" else 0,
            "workflow": rounds.round_status(), "next_suggested": "jobagent work next"}
