import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jobagent.application import round_criteria as criteria
from jobagent.infra import state, rounds, protocol
from jobagent.infra.delivery_preview import DeliveryPreviewError, build_delivery_preview, validate_delivery_preview


@pytest.fixture
def scope(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "ROUNDS_DIR", tmp_path / "rounds")
    monkeypatch.setattr(criteria, "current_account_ref", lambda: "acct_criteria")
    monkeypatch.setattr(criteria.browser_work, "has_open", lambda: False)
    key = Ed25519PrivateKey.generate()
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", base64.urlsafe_b64encode(raw).decode().rstrip("="))
    def sign(value):
        value = {"protocol_version": 2, "account_ref": "acct_criteria", **value,
                 "key_id": "synthetic", "signature_algorithm": "Ed25519"}
        return {**value, "signature": base64.urlsafe_b64encode(key.sign(protocol.canonical_json_bytes(value))).decode().rstrip("=")}
    intent = {"status": "confirmed", "target_roles": ["产品经理"], "target_cities": ["武汉"],
              "profile_digest": "sha256:" + "1" * 64, "confirmed_at": "2026-09-24", "source": "user_explicit"}
    active = rounds.start_new_round(intent)
    value = {"target_roles": ["产品经理"], "target_cities": ["上海"]}
    signed = sign({"manifest_type": "round_criteria", "round_id": active["round_id"], "criteria_revision": 1,
                   "original_intent_digest": protocol.digest_payload(intent), "criteria": value, "criteria_digest": protocol.digest_payload(value)})
    calls = []
    def update(round_id, body):
        calls.append((round_id, body))
        return {"ok": True, "criteria": signed, "additional_credits": 0}
    monkeypatch.setattr(criteria.cloud_client, "round_criteria_update", update)
    monkeypatch.setattr(criteria.cloud_client, "round_criteria_status", lambda _: {"criteria": signed})
    return active, signed, calls, sign


def test_update_keeps_round_and_original_direction(scope):
    active, signed, calls, _ = scope
    result = criteria.apply_update({"request_id": "criteria01", "patch": {"target_cities": ["上海"]}}, 0)
    updated = rounds.ensure_current_round()
    assert result["ok"] and result["additional_credits"] == 0
    assert updated["round_id"] == active["round_id"]
    assert updated["intent"] == active["intent"]
    assert updated["round_criteria"] == signed
    assert calls[0][1]["expected_revision"] == 0


def test_open_native_task_blocks_patch_without_server_mutation(scope, monkeypatch):
    active, _, calls, _ = scope
    monkeypatch.setattr(criteria.browser_work, "has_open", lambda: True)
    result = criteria.apply_update({"request_id": "criteria01", "patch": {"target_cities": ["上海"]}}, 0)
    assert result["error"] == "round_update_inflight" and calls == []
    assert rounds.ensure_current_round()["intent"] == active["intent"]


def test_old_preview_cannot_use_new_scope_and_filter_keeps_unknown_visible(scope, monkeypatch):
    active, signed, _, sign = scope
    criteria.apply_update({"request_id": "criteria01", "patch": {"target_cities": ["上海"]}}, 0)
    review = {"platform": "boss", "discover_id": "dis-one", "manifest": {"manifest_id": "manifest-one", "candidate_digest": "sha256:" + "2" * 64},
              "send_candidates": [{"id": "job1", "job_id": "job1", "title": "产品经理", "company": "示例公司", "area": "上海", "salary": "20-30K", "url": "https://example.test/job1"},
                                  {"id": "job2", "job_id": "job2"}]}
    with pytest.raises(DeliveryPreviewError):
        criteria.assert_current(review)
    proof = sign({"manifest_type": "criteria_filter", "round_id": active["round_id"], "discover_id": "dis-one",
                  "manifest_id": "manifest-one", "candidate_digest": review["manifest"]["candidate_digest"],
                  "criteria_revision": 1, "criteria_digest": signed["criteria_digest"],
                  "items": [{"id": "job1", "status": "review", "unknown_fields": ["fortune_global_500"]},
                            {"id": "job2", "status": "excluded", "unknown_fields": []}],
                  "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()})
    monkeypatch.setattr(criteria.cloud_client, "round_criteria_filter", lambda *_: {"filter": proof})
    criteria.apply_filter(review)
    assert len(review["send_candidates"]) == 1
    criteria.assert_current(review)
    preview = build_delivery_preview(platform="boss", discover_id="dis-one", send_candidates=review["send_candidates"],
        send_command="jobagent boss greet send", selected_count=2, promoted_count=0, review_count=0, rejected_count=0, skipped_delivered_count=0)
    assert "筛选信息未知" in preview["fallback_text"]
    assert "指定年份世界500强" in preview["fallback_text"]
    validate_delivery_preview(preview, send_candidates=review["send_candidates"], expected_platform="boss", expected_discover_id="dis-one")
    review["send_candidates"][0]["criteria_unknown_fields"] = []
    with pytest.raises(ValueError, match="signed filter"):
        criteria.assert_current(review)


def test_forged_or_cross_account_filter_fails(scope, monkeypatch):
    active, signed, _, sign = scope
    active["round_criteria"] = signed
    rounds.save_round(active)
    proof = sign({"manifest_type": "criteria_filter", "account_ref": "acct_other", "round_id": active["round_id"]})
    monkeypatch.setattr(criteria.cloud_client, "round_criteria_filter", lambda *_: {"filter": proof})
    with pytest.raises(ValueError, match="account or round"):
        criteria.apply_filter({"discover_id": "dis-one"})
