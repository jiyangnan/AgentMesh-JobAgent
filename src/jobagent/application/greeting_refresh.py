"""Refresh only an unapproved list using the same signed cloud material."""
from __future__ import annotations

import copy

from jobagent.application import review
from jobagent.application.delivery_followup import assert_list_active
from jobagent.infra import browser_work, cloud_client, discovery_state, protocol, rounds, state
from jobagent.infra.account_state import current_account_ref
from jobagent.infra.diagnostics import emit_stage, progress_heartbeat
from jobagent.infra.interaction_state import load_pending_interaction, clear_pending_interaction


def _fail(code):
    raise browser_work.BrowserWorkError(code, "保留当前清单与投递记录；招呼只能在同一轮未授权、未执行的清单中更新。",
        additional_credits=0, requires_user_action=False, next_suggested="jobagent work status")


def _immutable(manifest):
    result = copy.deepcopy(manifest)
    for field in ("signature", "key_id", "signature_algorithm", "manifest_id", "created_at", "expires_at", "greeting_refresh"):
        result.pop(field, None)
    for bucket in ("selected", "review"):
        for job in result.get(bucket, []):
            job.pop("greeting", None)
            job.pop("greeting_evidence", None)
    return result


def refresh(platform, *, input_path=None, promoted_ids=None, confirm_promote=False, output_path=None):
    if platform not in {"boss", "liepin"}:
        _fail("greeting_refresh_platform_unsupported")
    active = rounds.ensure_current_round()
    rounds.assert_platform_turn(platform)
    account = current_account_ref()
    if not account or browser_work.has_open():
        _fail("greeting_refresh_context_locked")
    envelope = discovery_state.load_envelope(platform, input_path)
    old = protocol.verify_stored_decision(envelope["manifest"], platform=platform)
    discover_id = old["discover_id"]
    current = active.get("platforms", {}).get(platform, {})
    signed_binding = old.get("resume_binding") or {}
    signed_account = old.get("account_ref") or signed_binding.get("account_ref")
    if (signed_account and signed_account != account) or (active.get("resume_binding") and any(
            signed_binding.get(key) != value for key, value in active["resume_binding"].items())):
        _fail("greeting_refresh_context_mismatch")
    if (current.get("evidence", {}).get("discover_id") != discover_id or
            old.get("round_id") != active["round_id"] or
            old.get("intent_digest") != protocol.digest_payload(active.get("intent"))):
        _fail("greeting_refresh_context_mismatch")
    assert_list_active(platform, discover_id)
    # Even a closed/uncertain delivery task forbids regenerating its material.
    works = browser_work.list_work({"account_ref": account, "round_id": active["round_id"]})
    if envelope.get("delivery_authorization") or current.get("native_delivery") or any(
        w["binding"].get("discover_id") == discover_id and
        (w.get("side_effect") or w["binding"].get("authorization_id") or w["binding"].get("preview_id")) for w in works
    ):
        _fail("greeting_refresh_never_authorized_required")
    # Preserve explicit choices even when the caller points at the raw manifest.
    reviewed_path = discovery_state.review_path(platform, discover_id)
    existing = state.load_json(reviewed_path) or {}
    if existing.get("manifest", {}).get("manifest_id") == old["manifest_id"]:
        if existing.get("delivery_authorization"):
            _fail("greeting_refresh_never_authorized_required")
        for field in ("user_overrides", "user_delivery_exclusions"):
            if field in existing:
                envelope[field] = existing[field]
    saved_promotions = [item["job_id"] for item in envelope.get("user_overrides", [])
                        if item.get("from") == "review" and item.get("to") == "selected"]
    requested = list(promoted_ids or []) or saved_promotions
    confirmed = confirm_promote or (not promoted_ids and bool(saved_promotions))
    discovery_state.build_review(envelope, promoted_ids=requested, confirm_promote=confirmed)
    archive = state.STATE_DIR / "archive" / "greeting-revisions" / (protocol.digest_payload(old).split(":")[1] + ".json")
    if not archive.exists():
        state.save_json(archive, envelope)
    emit_stage("greeting_refresh_started", platform=platform, additional_credits=0)
    with progress_heartbeat("greeting_refresh_in_progress", platform=platform, additional_credits=0):
        response = cloud_client.discovery_greetings_refresh(discover_id=discover_id,
            expected_manifest_id=old["manifest_id"], expected_candidate_digest=old["candidate_digest"])
    new = protocol.verify_stored_decision(response["manifest"], platform=platform)
    if response.get("additional_credits") != 0 or _immutable(new) != _immutable(old):
        _fail("greeting_refresh_protocol_invalid")
    changed = new["manifest_id"] != old["manifest_id"]
    proof = new.get("greeting_refresh") or {}
    if changed and (proof.get("original_manifest_id") != old["manifest_id"] or proof.get("additional_credits") != 0 or proof.get("same_discover_id") is not True):
        _fail("greeting_refresh_protocol_invalid")
    for item in new.get("selected", []) + new.get("review", []):
        if not isinstance(item.get("greeting"), str) or not 1 <= len(item["greeting"]) <= 100:
            _fail("greeting_refresh_protocol_invalid")
    updated = {key: value for key, value in envelope.items() if key in ("platform", "discover_id", "user_overrides", "user_delivery_exclusions")}
    updated["manifest"] = response["manifest"]
    path = discovery_state.discovery_path(platform, discover_id)
    state.save_json(path, updated)
    pending = load_pending_interaction() or {}
    if changed and (pending.get("context") or {}).get("discover_id") == discover_id:
        clear_pending_interaction()
    result = review.review_decision(platform, input_path=str(path), promoted_ids=requested,
        confirm_promote=confirmed, output_path=output_path, native=True)
    result["greeting_refresh"] = {"changed": changed, "replayed": response.get("replayed", False), "additional_credits": 0}
    return result
