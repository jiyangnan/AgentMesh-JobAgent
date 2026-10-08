"""Batch and platform handoffs preserve prior delivery and require real choices."""
import copy
import json

import pytest

from tests.test_native_work import env, observation, submit
from jobagent import cli
from jobagent.application import delivery, native_work as native
from jobagent.infra import browser_work as store, rounds, state
from jobagent.infra.interaction_protocol import validate_interaction_required
from jobagent.infra.workflow_protocol import with_contract

REAL_LOAD_REVIEWED = delivery._load_reviewed


def settle(env, response, *, existing_first=False):
    steps = 0
    while response.get("work"):
        work = native.begin(response["work"]["work_id"])["work"]
        result = observation(work)
        if existing_first and work["binding"]["job_id"] == "job0" and work["action"] == "inspect_delivery":
            result["evidence"].update(resume_state="sent", communication_state="open",
                                      receipt_kind="resume_card")
        response = submit(env, work, result)
        steps += 1
        assert steps < 300
    return response


def batch(env):
    env.choose("liepin", count=37)
    return settle(env, env.start("liepin", limit=10), existing_first=True)


def answer(response, choice):
    args = cli.build_parser().parse_args(["interaction", "respond", "--interaction-id",
        response["interaction"]["interaction_id"], "--choice", choice])
    return cli._interaction_respond(args)


def receipts():
    binding = {"account_ref": "account-test", "round_id": "round-test", "platform": "liepin"}
    return {w["work_id"]: copy.deepcopy(w) for w in store.list_work(binding)}


def test_37_job_10_batch_returns_complete_choice_and_preserves_existing_resume(env):
    response = batch(env)
    assert response["completion_state"] == "batch_limit_reached"
    assert response["requires_user_action"] is True
    assert response["interaction"]["kind"] == "delivery_batch_followup"
    validate_interaction_required(response["interaction"])
    assert response["processed_count"] == 10 and response["remaining_count"] == 27
    assert "10" in response["user_prompt"] and "27" in response["user_prompt"]
    options = response["interaction"]["fields"][0]["options"]
    assert {o["option_id"] for o in options} == {"continue_delivery", "finish_platform", "pause_delivery"}
    assert with_contract(response)["action"]["type"] == "ask"
    works = list(receipts().values())
    assert len([w for w in works if w["action"] == "submit_resume"]) == 9
    assert len([w for w in works if w["action"] == "send_greeting"]) == 10
    assert {w["binding"]["job_id"] for w in works} == {f"job{i}" for i in range(10)}
    assert response["workflow"]["workflow_complete"] is False
    assert rounds.round_status()["next_suggested"] == "jobagent work next"


def test_unanswered_batch_restores_same_card_in_work_status_and_workflow(env, monkeypatch):
    response = batch(env)
    before = receipts()
    identifier = response["interaction"]["interaction_id"]
    state.pending_interaction_path().unlink()
    from jobagent.infra import account_state
    monkeypatch.setattr(account_state, "current_account_ref", lambda: "account-test")
    from jobagent.application import workflow
    monkeypatch.setattr(workflow, "current_account_ref", lambda: "account-test")
    for restored in [native.next_work(), native.status(), workflow.next_action()]:
        assert restored["interaction"]["interaction_id"] == identifier
        assert with_contract(restored)["action"]["type"] == "ask"
    assert receipts() == before
    assert store.has_open() is False


def test_pause_then_continue_only_original_unattempted_27(env):
    response = batch(env)
    old = receipts()
    source = copy.deepcopy(rounds.ensure_current_round()["platforms"]["liepin"]["native_delivery"])
    paused = answer(response, "pause_delivery")
    assert paused["delivery_paused"] is True
    assert native.next_work()["interaction"] == response["interaction"]
    assert receipts() == old
    accepted = answer(response, "continue_delivery")
    assert accepted["ok"] is True and accepted["next_suggested"] == "jobagent work next"
    saved = rounds.ensure_current_round()["platforms"]["liepin"]["native_delivery"]
    assert {k: v for k, v in saved.items() if k != "limit"} == {k: v for k, v in source.items() if k != "limit"}
    first = native.next_work()
    assert first["work"]["binding"]["job_id"] == "job10"
    complete = settle(env, first)
    assert complete["summary"]["greeting_sent"] == 37
    assert all(receipts()[key] == value for key, value in old.items())
    replay = answer(response, "continue_delivery")
    assert replay["idempotent_replay"] is True
    assert answer(response, "finish_platform")["error"] == "interaction_response_conflict"


def test_finish_batch_preserves_ten_receipts_and_records_unattempted_remainder(env):
    response = batch(env)
    old = receipts()
    result = answer(response, "finish_platform")
    assert result["ok"] is True and result["skipped_remaining"] == 27
    assert result["workflow"]["platforms"]["liepin"]["status"] == "skipped_this_round"
    assert result["workflow"]["current_platform"] == "zhilian"
    assert result["workflow"]["workflow_complete"] is False
    assert receipts() == old and store.has_open() is False
    assert answer(response, "finish_platform")["idempotent_replay"] is True


def test_changed_authorized_list_cannot_continue_batch(env):
    response = batch(env)
    old = receipts()
    env.jobs.reverse()
    with pytest.raises(ValueError, match="changed|mismatch"):
        answer(response, "continue_delivery")
    assert receipts() == old and store.has_open() is False


def test_platform_audit_asks_before_next_platform_and_last_platform_finishes(env):
    env.choose("liepin")
    settle(env, env.start("liepin"))
    response = native.audit("liepin")
    assert response["interaction"]["kind"] == "delivery_platform_followup"
    assert "智联" in response["user_prompt"]
    assert response["workflow"]["platforms"]["liepin"]["status"] == "completed"
    assert native.next_work()["interaction"] == response["interaction"]
    result = answer(response, "continue_platforms")
    assert result["ok"] is True and result["workflow"]["current_platform"] == "zhilian"
    assert store.has_open() is False
    env.choose("51job")
    settle(env, env.start("51job"))
    final = native.audit("51job")
    assert final["workflow"]["workflow_complete"] is True
    assert "interaction" not in final


def test_old_batch_without_followup_recovers_without_new_browser_work(env):
    batch(env)
    old = receipts()
    active = rounds.ensure_current_round()
    active.pop("native_delivery_followups")
    rounds.save_round(active)
    state.pending_interaction_path().unlink()
    restored = native.next_work()
    assert restored["interaction"]["kind"] == "delivery_batch_followup"
    assert restored["remaining_count"] == 27
    assert receipts() == old and store.has_open() is False


@pytest.mark.parametrize("mutation", ["account", "source", "session"])
def test_changed_context_cannot_resume_or_discard_batch(env, monkeypatch, mutation):
    response = batch(env)
    old = receipts()
    active = rounds.ensure_current_round()
    if mutation == "account":
        monkeypatch.setattr(native, "current_account_ref", lambda: "different-account")
    elif mutation == "source":
        active["platforms"]["liepin"]["native_delivery"]["authorization_id"] = "different-authorization"
    else:
        active["native_session"]["id"] = "different-session"
    rounds.save_round(active)
    for choice in ["continue_delivery", "finish_platform", "pause_delivery"]:
        with pytest.raises(ValueError, match="mismatch|changed"):
            answer(response, choice)
    assert receipts() == old and store.has_open() is False


def test_missing_authorization_blocks_continue_but_preserves_pause_and_finish(env, monkeypatch):
    response = batch(env)
    old = receipts()
    from jobagent.application import delivery
    def expired(*args, **kwargs):
        raise ValueError("authorization expired")
    monkeypatch.setattr(delivery, "_load_reviewed", expired)
    with pytest.raises(ValueError, match="authorization expired"):
        answer(response, "continue_delivery")
    assert native.next_work()["interaction"] == response["interaction"]
    assert answer(response, "pause_delivery")["delivery_paused"] is True
    assert answer(response, "finish_platform")["skipped_remaining"] == 27
    assert receipts() == old


def test_pending_card_blocks_direct_platform_send_and_login(env):
    response = batch(env)
    old = receipts()
    for argv in [["liepin", "apply", "send", "--limit", "100"], ["zhilian", "login", "--check"]]:
        args = cli.build_parser().parse_args(argv)
        result = cli._dispatch_unlocked(args)
        assert result["interaction"] == response["interaction"]
    assert env.start("liepin", limit=100)["interaction"] == response["interaction"]
    assert receipts() == old and store.has_open() is False


def test_pending_work_prevents_answer_from_closing_or_resuming_batch(env, monkeypatch):
    response = batch(env)
    old = receipts()
    monkeypatch.setattr(store, "has_open", lambda: True)
    for choice in ["continue_delivery", "finish_platform"]:
        assert answer(response, choice)["error"] == "native_work_context_locked"
    assert receipts() == old


def test_complete_ten_job_authorization_only_offers_next_platform(env):
    env.choose("liepin", count=10)
    result = settle(env, env.start("liepin", limit=10))
    assert result["completion_state"] == "completed"
    result = native.audit("liepin")
    assert result["interaction"]["kind"] == "delivery_platform_followup"
    assert result["remaining_count"] == 0
    assert {o["option_id"] for o in result["interaction"]["fields"][0]["options"]} == {"continue_platforms", "pause_delivery"}
    assert native.next_work()["interaction"] == result["interaction"]


def test_answer_saved_before_pending_cleanup_recovers_idempotently(env, monkeypatch):
    response = batch(env)
    old = receipts()
    from jobagent.application import native_delivery_followup
    with monkeypatch.context() as interrupted:
        interrupted.setattr(native_delivery_followup, "clear_pending_interaction", lambda: (_ for _ in ()).throw(RuntimeError("simulated interruption")))
        with pytest.raises(RuntimeError, match="simulated interruption"):
            answer(response, "continue_delivery")
    replay = answer(response, "continue_delivery")
    assert replay["idempotent_replay"] is True
    assert not state.pending_interaction_path().exists()
    assert receipts() == old


def test_saved_answer_recovers_without_asking_user_again(env, monkeypatch):
    response = batch(env)
    old = receipts()
    from jobagent.application import native_delivery_followup
    with monkeypatch.context() as interrupted:
        interrupted.setattr(native_delivery_followup, "clear_pending_interaction", lambda: (_ for _ in ()).throw(RuntimeError("interrupted")))
        with pytest.raises(RuntimeError):
            answer(response, "continue_delivery")
    recovered = native.next_work()
    assert recovered["next_suggested"] == "jobagent work next"
    assert "interaction" not in recovered
    assert not state.pending_interaction_path().exists()
    assert receipts() == old
    assert native.next_work()["work"]["binding"]["job_id"] == "job10"


def test_explicit_round_skip_finishes_same_pending_batch(env):
    response = batch(env)
    old = receipts()
    args = cli.build_parser().parse_args(["round", "skip", "--platform", "liepin", "--confirm-skip"])
    result = cli._dispatch_unlocked(args)
    assert result["skipped_remaining"] == 27
    assert result["workflow"]["current_platform"] == "zhilian"
    assert not state.pending_interaction_path().exists()
    assert receipts() == old


def test_direct_answer_rejects_fields_outside_followup_card(env):
    response = batch(env)
    old = receipts()
    args = cli.build_parser().parse_args(["interaction", "respond", "--interaction-id", response["interaction"]["interaction_id"],
        "--choice", "continue_delivery", "--exclude-index", "1"])
    assert cli._interaction_respond(args)["error"] == "invalid_interaction_response"
    assert native.next_work()["interaction"] == response["interaction"]
    assert receipts() == old


def test_read_only_audit_retains_unanswered_batch_handoff(env):
    response = batch(env)
    old = receipts()
    assert native.audit("liepin")["interaction"] == response["interaction"]
    assert receipts() == old


def test_signed_37_job_authorization_continues_and_preserves_boss_21(env, monkeypatch):
    import base64
    from datetime import datetime, timedelta, timezone
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from jobagent.application import delivery
    from jobagent.infra import protocol
    from jobagent.infra.delivery_preview import build_delivery_preview
    from jobagent.infra.delivery_authorization import build_delivery_authorization
    env.choose("liepin", count=37)
    monkeypatch.setattr(delivery, "_load_reviewed", REAL_LOAD_REVIEWED)
    monkeypatch.setattr("jobagent.infra.account_state.current_account_ref", lambda: "account-test")
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", base64.urlsafe_b64encode(
        key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode())
    source = env.reviewed["source_path"]
    preview = build_delivery_preview(platform="liepin", discover_id="discover-test", send_candidates=env.jobs,
        send_command=f"jobagent liepin apply send --input {source}", selected_count=37,
        promoted_count=0, review_count=0, rejected_count=0, skipped_delivered_count=0)
    authorization = build_delivery_authorization(account_ref="account-test", round_id="round-test",
        platform="liepin", discover_id="discover-test", preview=preview, send_candidates=env.jobs,
        interaction_id="synthetic-original-confirmation")
    manifest = {"manifest_type": "decision_manifest", "protocol_version": 1,
        "signature_algorithm": "Ed25519", "manifest_id": "synthetic-original", "platform": "liepin",
        "discover_id": "discover-test", "deduplicated_count": 37, "selected": copy.deepcopy(env.jobs),
        "review": [], "rejected": [], "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    manifest["signature"] = base64.urlsafe_b64encode(key.sign(protocol.canonical_json_bytes(manifest))).decode()
    state.save_json(env.path / "decision.review.json", {"platform": "liepin", "discover_id": "discover-test",
        "manifest": manifest, "send_candidates": env.jobs, "delivery_preview": preview,
        "delivery_authorization": authorization})
    active = rounds.ensure_current_round()
    active["platforms"]["boss"] = {"status": "completed", "evidence": {"greeting_sent": 21}}
    active["resume_binding"] = {"id": "synthetic-cloud-binding", "resume_id": "synthetic-cloud-resume",
                                "resume_revision_id": "synthetic-cloud-revision", "content_digest": "sha256:synthetic"}
    rounds.save_round(active)
    for i in range(21):
        binding = {"account_ref": "account-test", "round_id": "round-test", "platform": "boss", "job_id": f"boss-{i}"}
        task = {"session": active["native_session"], "job": {"id": f"boss-{i}", "cloud_greeting": "Synthetic preserved greeting"},
                "delivery_source": {"preview_id": "boss-original", "authorization_id": "boss-original"}}
        work = store.ensure_work(action="send_greeting", task=task, binding=binding, side_effect=True)
        work = store.begin_work(work["work_id"], binding)
        store.submit_work(work["work_id"], binding, {"receipt_id": f"boss-receipt-{i}", "binding": binding,
            "nonce": work["nonce"], "outcome": "success", "evidence": {"outgoing_text": "Synthetic preserved greeting", "message_state": "sent"}})
    signed_bytes = (env.path / "decision.review.json").read_bytes()
    initial = native.start_delivery("liepin", input_path=source, preview_id=preview["preview_id"],
                                   authorization_id=authorization["authorization_id"], limit=10)
    assert initial["event"] == "resume_freshness_gate"
    from jobagent.application import resume_freshness
    monkeypatch.setattr(resume_freshness.cloud_client, "resume_binding_material",
                        lambda identifier: {"binding": copy.deepcopy(active["resume_binding"])})
    synced = resume_freshness.respond(initial["interaction"]["interaction_id"], choice="synced")
    assert synced["ok"] is True, synced
    response = settle(env, native.start_delivery("liepin", input_path=source, preview_id=preview["preview_id"],
                      authorization_id=authorization["authorization_id"], limit=10), existing_first=True)
    assert response.get("remaining_count") == 27, response
    old = {w["work_id"]: copy.deepcopy(w) for w in store.list_account_work("account-test")}
    assert native.audit("boss", complete=False)["summary"]["greeting_sent"] == 21
    assert answer(response, "continue_delivery")["ok"] is True
    complete = settle(env, native.next_work())
    assert complete["summary"]["greeting_sent"] == 37
    after = {w["work_id"]: w for w in store.list_account_work("account-test")}
    assert all(after[k] == v for k, v in old.items())
    assert (env.path / "decision.review.json").read_bytes() == signed_bytes
    assert rounds.ensure_current_round()["resume_binding"] == active["resume_binding"]
    assert rounds.ensure_current_round()["platforms"]["boss"] == active["platforms"]["boss"]
    card = native.audit("liepin")
    assert card["interaction"]["kind"] == "delivery_platform_followup"
    assert card["workflow"]["current_platform"] == "zhilian"


def test_v0619_upgrade_restores_batch_choice_without_migrating_history(env):
    batch(env)
    active = rounds.ensure_current_round()
    active.pop("native_delivery_followups")
    rounds.save_round(active)
    state.pending_interaction_path().unlink()
    marker = state.STATE_DIR / "client_upgrade_state.json"
    marker.write_text(json.dumps({"state_migration_version": 8, "client_version": "0.6.19", "protocol_version": 1}))
    before = {p: p.read_bytes() for p in env.path.rglob("*") if p.is_file()}
    from jobagent.infra import client_upgrade
    report = client_upgrade.run_client_upgrade(app_dir=env.path, current_version="0.6.20", protocol_version=1)
    assert report["ok"] and report["cleared"] == report["migrated"] == report["archived"] == []
    for path, data in before.items():
        if path != marker:
            assert path.read_bytes() == data
    old = receipts()
    restored = native.next_work()
    assert restored["interaction"]["kind"] == "delivery_batch_followup"
    assert restored["remaining_count"] == 27
    assert receipts() == old
