"""Cancel one delivery list; wait for an explicit next-platform decision."""
from __future__ import annotations

from typing import Any

from jobagent.infra import rounds, state
from jobagent.infra.account_state import current_account_ref
from jobagent.infra.interaction_protocol import build_host_presentations, build_interaction_required
from jobagent.infra.interaction_state import clear_pending_interaction, save_pending_interaction
from jobagent.infra.protocol import digest_payload


def _response(record: dict) -> dict:
    interaction = record["interaction"]
    return {"ok": False, "error": "interaction_required", "event": "delivery_cancelled",
            "platform": record["platform"], "delivered": 0, "requires_user_action": True,
            "message": "当前清单已取消。请选择重新搜索本平台，或跳过本平台。",
            "interaction": interaction, "host_presentations": build_host_presentations(interaction),
            "next_suggested": f'jobagent interaction respond --interaction-id "{interaction["interaction_id"]}" --choice <search_again|skip_platform>',
            "workflow": rounds.round_status()}


def register(platform: str, review: dict, review_path: str, reason: str) -> dict:
    from jobagent.infra import browser_work
    if browser_work.has_open():
        raise rounds.RoundOrderError({"ok": False, "error": "native_work_context_locked",
                                      "next_suggested": "jobagent work status"})
    active = rounds.ensure_current_round()
    rounds.assert_platform_turn(platform)
    item = active["platforms"][platform]
    if item.get("native_delivery"):
        raise rounds.RoundOrderError({"ok": False, "error": "delivery_already_started",
                                      "next_suggested": "jobagent work status"})
    discover_id = str(review["discover_id"])
    preview_id = str((review.get("delivery_preview") or {}).get("preview_id") or "")
    existing = item.get("delivery_followup")
    if existing and existing["discover_id"] == discover_id:
        return _response(existing) if existing["status"] == "awaiting" else {
            "ok": False, "error": "delivery_list_cancelled", "next_suggested": "jobagent workflow next"}
    interaction_id = "after-cancel:" + digest_payload({"round": active["round_id"], "platform": platform,
                                                      "discover": discover_id, "preview": preview_id}).split(":")[-1]
    interaction = build_interaction_required(
        interaction_id=interaction_id, product_id="job_agent", kind="delivery_after_cancel",
        title="当前清单已取消", prompt="接下来要重新搜索本平台，还是跳过本平台？",
        fields=[{"field_id": "after_cancel_choice", "type": "single", "label": "下一步",
                 "options": [{"option_id": "search_again", "label": "重新在该平台搜索新岗位",
                              "description": "保留本轮，检查本次搜索费用与额度，生成新清单后重新确认"},
                             {"option_id": "skip_platform", "label": "跳过该平台",
                              "description": "本轮该平台按跳过收尾，继续下一平台"}]}],
        fallback_text="当前清单已取消。请选择：1. 重新在该平台搜索新岗位（新搜索按正常费用检查）；2. 跳过该平台。",
        continuation_action="jobagent.interaction.respond", idempotency_key=interaction_id)
    record = {"status": "awaiting", "platform": platform, "round_id": active["round_id"],
              "account_ref": current_account_ref(), "discover_id": discover_id,
              "preview_id": preview_id, "review_path": review_path, "reason": reason,
              "interaction": interaction}
    item.setdefault("cancelled_delivery_lists", {})[discover_id] = {
        "preview_id": preview_id, "reason": reason, "cancelled_at": rounds.utc_now()}
    item["delivery_followup"] = record
    item["status"] = "awaiting_after_cancel_choice"
    item["next_suggested"] = "jobagent workflow next"
    rounds.save_round(active)
    save_pending_interaction(interaction, stage="delivery_after_cancel", context=record)
    return _response(record)


def pending() -> dict | None:
    active = state.load_json(state.current_round_path()) or {}
    if active.get("status") != "active":
        return None
    for item in active.get("platforms", {}).values():
        record = item.get("delivery_followup") or {}
        if record.get("status") == "awaiting":
            if record.get("account_ref") != current_account_ref():
                raise ValueError("Delivery follow-up belongs to another account")
            return _response(record)
    return None


def respond(interaction_id: str, choice: str) -> dict | None:
    active = state.load_json(state.current_round_path()) or {}
    for platform, item in active.get("platforms", {}).items():
        record = item.get("delivery_followup") or {}
        if (record.get("interaction") or {}).get("interaction_id") != interaction_id:
            continue
        if record.get("account_ref") != current_account_ref() or record.get("round_id") != active.get("round_id"):
            return {"ok": False, "error": "interaction_context_mismatch"}
        if choice not in {"search_again", "skip_platform"}:
            return {**_response(record), "error": "invalid_interaction_response"}
        if record.get("status") == "answered":
            if record.get("choice") != choice:
                return {"ok": False, "error": "interaction_response_conflict"}
            return {"ok": True, "idempotent_replay": True, "workflow": rounds.round_status(),
                    "next_suggested": "jobagent workflow next"}
        rounds.assert_platform_turn(platform)
        from jobagent.infra import browser_work
        from jobagent.infra.discovery_state import load_pending_start, load_pending_decision
        if browser_work.has_open() or load_pending_start(platform) or load_pending_decision(platform):
            return {"ok": False, "error": "delivery_followup_inflight", "request_preserved": True,
                    "next_suggested": "jobagent work status"}
        record.update(status="answered", choice=choice, answered_at=rounds.utc_now())
        item["status"] = "skipped_this_round" if choice == "skip_platform" else "login_verified"
        item["next_suggested"] = f"jobagent {platform} discover"
        if choice == "skip_platform":
            item.pop("next_suggested", None)
        else:
            # A new request is allocated only by Discover. Marking this transition
            # once cannot double-charge or discard the original signed artifacts.
            item["search_attempt"] = int(item.get("search_attempt") or 1) + 1
        if active.get("native_review", {}).get("platform") == platform:
            active.pop("native_review")
        rounds.save_round(active)
        clear_pending_interaction()
        workflow = rounds.round_status()
        return {"ok": True, "event": "platform_skipped" if choice == "skip_platform" else "search_again_requested",
                "platform": platform, "new_search_requires_credit_check": choice == "search_again",
                "workflow": workflow, "next_suggested": workflow.get("next_suggested")}
    return None


def assert_list_active(platform: str, discover_id: str) -> None:
    active = state.load_json(state.current_round_path()) or {}
    item = active.get("platforms", {}).get(platform, {})
    if discover_id in item.get("cancelled_delivery_lists", {}):
        from jobagent.infra.delivery_preview import DeliveryPreviewError
        raise DeliveryPreviewError({"ok": False, "error": "delivery_list_cancelled",
                                    "message": "该清单已取消，不能重新取得投递授权。请继续当前待办。",
                                    "next_suggested": "jobagent workflow next"})
