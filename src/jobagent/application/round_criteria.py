"""Transport and enforce server-signed, versioned non-role round criteria."""
import json
from pathlib import Path
from datetime import datetime, timezone

from jobagent.infra import rounds, state, cloud_client, protocol, browser_work
from jobagent.infra.account_state import current_account_ref
from jobagent.infra.delivery_preview import DeliveryPreviewError, preview_required_payload


def _verify(payload, kind, active):
    signed = protocol.verify_signed_payload(payload, public_key=protocol.DECISION_SIGNING_PUBLIC_KEY, expected_type=kind)
    if signed.get("protocol_version") != 2 or signed.get("account_ref") != current_account_ref() or signed.get("round_id") != active["round_id"]:
        raise ValueError("Criteria account or round mismatch")
    if signed.get("expires_at") and datetime.fromisoformat(signed["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
        raise ValueError("Criteria response expired")
    return dict(payload)


def update(path, expected_revision):
    source = Path(path)
    if source.stat().st_size > 65536:
        raise ValueError("Criteria patch exceeds 64 KiB")
    body = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(body, dict) or set(body) != {"request_id", "patch"} or not isinstance(body["patch"], dict):
        raise ValueError("Criteria input requires request_id and patch")
    return apply_update(body, expected_revision)


def apply_update(body, expected_revision):
    from jobagent.infra.discovery_state import load_pending_start, load_pending_decision
    from jobagent.infra.interaction_state import clear_pending_interaction, load_pending_interaction
    active = rounds.ensure_current_round()
    if active.get("status") != "active":
        raise ValueError("No active round to update")
    patch = body["patch"]
    if "target_roles" in patch and patch["target_roles"] != active.get("intent", {}).get("target_roles"):
        from jobagent.application.round_direction import request_change
        return request_change(body)
    if browser_work.has_open() or any(load_pending_start(p) or load_pending_decision(p) for p in rounds.DEFAULT_PLATFORM_ORDER):
        return {"ok": False, "error": "round_update_inflight", "request_preserved": True,
                "message": "原搜索或浏览器动作尚未完成，请先按原任务恢复；本次条件尚未应用。",
                "next_suggested": "jobagent work next"}
    # A pending cancellation remains the user's decision; a filter is not an
    # answer to search_again / skip_platform.
    from jobagent.application.delivery_followup import pending
    after_cancel = pending()
    if after_cancel:
        return after_cancel
    binding = active.get("resume_binding") or {}
    response = cloud_client.round_criteria_update(active["round_id"], {
        **body, "expected_revision": expected_revision,
        "resume_binding_id": binding.get("id"), "context_id": binding.get("context_id"),
        "original_intent": active["intent"]})
    if not response.get("ok"):
        return response
    scope = _verify(response["criteria"], "round_criteria", active)
    if scope.get("original_intent_digest") != protocol.digest_payload(active["intent"]):
        raise ValueError("Criteria changed the original role binding")
    if scope.get("criteria_digest") != protocol.digest_payload(scope["criteria"]):
        raise ValueError("Criteria digest mismatch")
    old = active.get("round_criteria")
    if old and old["criteria_revision"] > scope["criteria_revision"]:
        return {"ok": False, "error": "criteria_revision_conflict", "request_preserved": True,
                "next_suggested": "jobagent round status"}
    if old == scope:
        return {**response, "next_suggested": rounds.round_status().get("next_suggested")}
    if old:
        active.setdefault("criteria_history", []).append(old)
    active["round_criteria"] = scope
    current = rounds.round_status().get("current_platform")
    item = active.get("platforms", {}).get(current) or {}
    if item.get("status") in {"discovered", "reviewed", "awaiting_delivery_confirmation"}:
        verb = "greet preview" if current == "boss" else "apply review"
        from jobagent.infra.discovery_state import review_path
        import shlex
        saved = review_path(current, str((item.get("evidence") or {}).get("discover_id") or ""))
        source = f" --input {shlex.quote(str(saved))}" if saved.is_file() else ""
        item.update(status="discovered", next_suggested=f"jobagent {current} {verb}{source}")
        active.pop("native_review", None)
    rounds.save_round(active)
    interaction = load_pending_interaction()
    if interaction and interaction.get("stage") in {"delivery_choice", "delivery_exclusions"}:
        clear_pending_interaction()
    return {**response, "workflow": rounds.round_status(), "next_suggested": rounds.round_status().get("next_suggested")}


def apply_filter(review):
    active = state.load_json(state.current_round_path()) or {}
    scope = active.get("round_criteria")
    if not scope:
        return
    response = cloud_client.round_criteria_filter(active["round_id"], review["discover_id"])
    result = _verify(response["filter"], "criteria_filter", active)
    manifest = review["manifest"]
    if (result["criteria_revision"] != scope["criteria_revision"] or result["criteria_digest"] != scope["criteria_digest"]
            or result["discover_id"] != review["discover_id"] or result["manifest_id"] != manifest["manifest_id"]
            or result["candidate_digest"] != manifest["candidate_digest"]):
        raise ValueError("Signed criteria filter context mismatch")
    by_id = {item["id"]: item for item in result["items"]}
    selected = []
    for item in review["send_candidates"]:
        evidence = by_id.get(str(item.get("id") or item.get("job_id")))
        if evidence is None:
            raise ValueError("Signed filter omitted a candidate")
        if evidence["status"] != "excluded":
            selected.append({**item, "criteria_revision": scope["criteria_revision"],
                             "criteria_unknown_fields": evidence["unknown_fields"]})
    review["send_candidates"] = selected
    review["criteria_filter"] = result
    review["criteria_revision"] = scope["criteria_revision"]


def assert_current(review):
    active = state.load_json(state.current_round_path()) or {}
    scope = active.get("round_criteria")
    if not scope:
        return
    remote = cloud_client.round_criteria_status(active["round_id"])
    current = _verify(remote["criteria"], "round_criteria", active)
    if current != scope:
        if current["criteria_revision"] <= scope["criteria_revision"] or current["original_intent_digest"] != protocol.digest_payload(active["intent"]):
            raise ValueError("Criteria revision moved backwards or changed the role binding")
        active.setdefault("criteria_history", []).append(scope)
        active["round_criteria"] = current
        rounds.save_round(active)
    if current != scope or review.get("criteria_revision") != scope["criteria_revision"]:
        raise DeliveryPreviewError(preview_required_payload(review["platform"], review.get("source_path")))
    # An expired proof returns to a fresh, free filter and complete preview.
    proof = review.get("criteria_filter") or {}
    if proof.get("expires_at") and datetime.fromisoformat(proof["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
        raise DeliveryPreviewError(preview_required_payload(review["platform"], review.get("source_path")))
    proof = _verify(proof, "criteria_filter", active)
    if proof.get("criteria_revision") != scope["criteria_revision"] or proof.get("criteria_digest") != scope["criteria_digest"]:
        raise DeliveryPreviewError(preview_required_payload(review["platform"], review.get("source_path")))
    if (proof.get("manifest_id") != review["manifest"]["manifest_id"] or proof.get("discover_id") != review["discover_id"]
            or proof.get("candidate_digest") != review["manifest"]["candidate_digest"]):
        raise ValueError("Criteria proof does not belong to this decision")
    allowed = {item["id"]: item for item in proof["items"] if item["status"] != "excluded"}
    for item in review.get("send_candidates", []):
        evidence = allowed.get(str(item.get("id") or item.get("job_id")))
        if not evidence or item.get("criteria_revision") != scope["criteria_revision"] or item.get("criteria_unknown_fields") != evidence["unknown_fields"]:
            raise ValueError("Delivery candidate does not match the signed filter")
