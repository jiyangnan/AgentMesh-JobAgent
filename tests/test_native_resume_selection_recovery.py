"""Resume-name recovery from immutable 0.6.18 delivery receipts."""
import copy
import json
import base64
from datetime import datetime, timedelta, timezone

import pytest

from jobagent import cli
from jobagent.application import delivery, native_work as native, workflow
from jobagent.infra import browser_work as store, client_upgrade, rounds, state, protocol
from tests.test_native_work import env, observation, submit  # noqa: F401


DEFAULT_GREETING = "我对您在招的数据平台产品经理职位很感兴趣，希望可以详聊。"
REAL_LOAD_REVIEWED = delivery._load_reviewed


def stopped_round(env, *, processed=True):
    env.choose("liepin", count=10)
    env.jobs[0].update(id="1983000001", url="https://www.liepin.com/job/1983000001.shtml",
                       title="数据平台产品经理", company="Synthetic Example")
    response = env.start("liepin")
    active = rounds.ensure_current_round()
    active["resume_binding"] = {"id": "cloud-binding-original", "resume_id": "cloud-resume-original",
                                "resume_revision_id": "original-revision", "resume_name": "Cloud name is not the platform name"}
    active["platforms"]["boss"] = {"status": "completed", "evidence": {"greeting_sent": 21}}
    rounds.save_round(active)
    response = submit(env, native.begin(response["work"]["work_id"])["work"])
    response = submit(env, native.begin(response["work"]["work_id"])["work"])
    work = native.begin(response["work"]["work_id"])["work"]
    result = observation(work, resume_reference=None, existing_outgoing_text=DEFAULT_GREETING,
                         message_state="sent", communication_state="open")
    # Commit exactly the old accepted result, without invoking its failed scheduler.
    store.submit_work(work["work_id"], native._binding(), result)
    if processed:
        active = rounds.ensure_current_round()
        active.setdefault("native_processed_work", []).append(work["work_id"])
        rounds.save_round(active)
    return work, result


def resume_observation(work, **changes):
    return observation(work, resume_reference="Visible existing account resume",
                       resume_selection_verified=True, submission_attempted=False,
                       communication_state="open", existing_outgoing_text=DEFAULT_GREETING,
                       message_state="sent", **changes)


@pytest.mark.parametrize("processed", [True, False])
def test_closed_missing_name_receipt_gets_one_read_only_recovery(env, processed):
    old, receipt = stopped_round(env, processed=processed)
    before = copy.deepcopy(store.list_account_work("account-test"))
    active = rounds.ensure_current_round()
    reviewed = copy.deepcopy(env.reviewed)
    response = native.next_work()
    work = response["work"]
    assert work["action"] == "inspect_resume_selection"
    assert work["task"]["resume_selection_source"] == old["work_id"]
    assert not work["side_effect"] and work["state"] == "ready"
    assert "resume_selection_verified" in work["task"]["result_schema"]["evidence"]
    assert native.next_work()["work"]["work_id"] == work["work_id"]
    assert store.list_account_work("account-test")[:len(before)] == before
    assert env.reviewed == reviewed
    after = rounds.ensure_current_round()
    for key in ("round_id", "intent", "resume_binding", "native_session"):
        assert after[key] == active[key]
    assert after["platforms"] == active["platforms"]
    assert sum(w["action"] == "open_communication" for w in store.list_account_work("account-test")) == 1
    assert native.audit("liepin")["summary"]["pending"] == 1
    assert rounds.round_status()["current_platform"] == "liepin"
    begun = native.begin(work["work_id"])["work"]
    assert begun["allowed_mode"] == "observe" and begun["execution_permitted"]
    response = submit(env, begun, resume_observation(begun))
    assert response["work"]["action"] == "prepare_resume"
    assert response["work"]["task"]["resume_reference"] == "Visible existing account resume"
    assert store.get_work(old["work_id"], native._binding())["result"] == receipt
    assert native.next_work()["work"]["work_id"] == response["work"]["work_id"]


def test_new_receipt_missing_name_closes_normally_and_offers_recovery(env):
    env.choose("liepin")
    response = env.start("liepin")
    for _ in range(2):
        response = submit(env, native.begin(response["work"]["work_id"])["work"])
    work = native.begin(response["work"]["work_id"])["work"]
    response = submit(env, work, observation(work, resume_reference=None))
    assert store.get_work(work["work_id"], native._binding())["state"] == "closed"
    assert response["work"]["action"] == "inspect_resume_selection"


@pytest.mark.parametrize("change", [
    {"resume_reference": None}, {"resume_reference": "  "}, {"resume_reference": ["name"]},
    {"resume_selection_verified": False}, {"submission_attempted": True},
    {"communication_state": "not_open"}, {"conversation_job_verified": False},
    {"account_label": "another account"}, {"job_id": "another job"},
])
def test_unverified_resume_selection_does_not_close_or_issue_a_send(env, change):
    stopped_round(env)
    work = native.begin(native.next_work()["work"]["work_id"])["work"]
    result = resume_observation(work)
    result["evidence"].update(change)
    with pytest.raises(store.BrowserWorkError):
        submit(env, work, result)
    assert store.get_work(work["work_id"], native._binding())["state"] != "closed"
    assert not any(w["action"] in {"submit_resume", "send_greeting"} for w in store.list_account_work("account-test"))


def test_recovery_rechecks_newly_visible_receipts_and_never_repeats_them(env):
    stopped_round(env)
    work = native.begin(native.next_work()["work"]["work_id"])["work"]
    result = resume_observation(work)
    result["evidence"].update(resume_state="sent", receipt_kind="resume_card",
                              existing_outgoing_text=work["task"]["job"]["cloud_greeting"])
    response = submit(env, work, result)
    assert response["work"]["action"] == "inspect_delivery"
    assert response["work"]["task"]["job"]["id"] == env.jobs[1]["id"]
    assert not any(w["action"] in {"submit_resume", "send_greeting"} for w in store.list_account_work("account-test"))


def test_terminal_or_issued_resume_never_gains_selection_recovery(env):
    old, _ = stopped_round(env)
    task = copy.deepcopy(old["task"])
    task["resume_reference"] = "Original visible resume"
    issued = store.ensure_work(action="submit_resume", task=task, binding=old["binding"], side_effect=True)
    issued = store.begin_work(issued["work_id"], native._binding())
    assert native.next_work()["work"]["allowed_mode"] == "reconcile_only"
    result = observation(issued)
    result["outcome"] = "unresolved"
    store.submit_work(issued["work_id"], native._binding(), result)
    assert native.next_work()["completion_state"] == "paused_on_failure"
    assert not any(w["action"] == "inspect_resume_selection" for w in store.list_account_work("account-test"))


def test_workflow_advance_and_closed_receipt_replay_reuse_the_same_recovery(env, monkeypatch):
    old, receipt = stopped_round(env)
    monkeypatch.setattr(workflow, "current_account_ref", lambda: "account-test")
    response = workflow.next_action()
    action = response["action"]
    args = cli.build_parser().parse_args(action["argv"][1:])
    response = workflow.advance(args.action_id, args.expected_revision)
    assert response["work"]["action"] == "inspect_resume_selection"
    replay = submit(env, old, receipt)
    assert replay["work"]["work_id"] == response["work"]["work_id"]
    changed = copy.deepcopy(receipt)
    changed["receipt_id"] += "-replacement"
    changed["evidence"]["resume_reference"] = "New name"
    with pytest.raises(store.BrowserWorkError):
        submit(env, old, changed)


def test_v0618_upgrade_preserves_closed_ledger_cloud_resume_and_original_platforms(env):
    stopped_round(env)
    marker = state.STATE_DIR / "client_upgrade_state.json"
    marker.write_text(json.dumps({"state_migration_version": 8, "client_version": "0.6.18", "protocol_version": 1}))
    before = {p: p.read_bytes() for p in env.path.rglob("*") if p.is_file()}
    report = client_upgrade.run_client_upgrade(app_dir=env.path, current_version="0.6.19", protocol_version=1)
    assert report["ok"] and report["cleared"] == report["migrated"] == report["archived"] == []
    for path, data in before.items():
        if path != marker:
            assert path.read_bytes() == data
    assert not (state.STATE_DIR / "profile.json").exists()
    assert native.next_work()["work"]["action"] == "inspect_resume_selection"
    assert rounds.ensure_current_round()["platforms"]["boss"]["evidence"]["greeting_sent"] == 21


@pytest.mark.parametrize("platform", ["zhilian", "51job"])
def test_resume_only_platform_missing_name_has_read_only_recovery_and_can_finish(env, platform):
    env.choose(platform)
    work = native.begin(env.start(platform)["work"]["work_id"])["work"]
    response = submit(env, work, observation(work, resume_reference=None))
    assert response["work"]["action"] == "inspect_resume_selection"
    work = native.begin(response["work"]["work_id"])["work"]
    response = submit(env, work, resume_observation(work))
    assert response["work"]["action"] == "submit_resume"
    work = native.begin(response["work"]["work_id"])["work"]
    response = submit(env, work, observation(work, resume_reference="Visible existing account resume"))
    assert response["summary"]["resume_submitted"] == 1 and response["summary"]["pending"] == 0


def test_crash_after_new_read_only_receipt_commit_recovers_without_rewriting_old_receipts(env):
    stopped_round(env)
    work = native.begin(native.next_work()["work"]["work_id"])["work"]
    result = resume_observation(work)
    store.submit_work(work["work_id"], native._binding(), result)
    response = native.next_work()
    assert response["work"]["action"] == "prepare_resume"
    assert submit(env, work, result)["work"]["work_id"] == response["work"]["work_id"]


def test_changed_authorization_cannot_begin_recovery_or_issue_any_resume(env, monkeypatch):
    stopped_round(env)
    work = native.next_work()["work"]
    monkeypatch.setattr(native, "_review_for", lambda w: (_ for _ in ()).throw(ValueError("expired authorization")))
    with pytest.raises(ValueError, match="expired authorization"):
        native.begin(work["work_id"])
    assert store.get_work(work["work_id"], native._binding())["nonce"] is None
    assert not any(w["action"] == "submit_resume" for w in store.list_account_work("account-test"))


def test_signed_94_closed_round_preserves_21_boss_results_and_recovers_only_first_job(env, monkeypatch):
    """Real signature, preview, authorization, migration, ledger and CLI dispatch."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from jobagent.infra.delivery_preview import build_delivery_preview
    from jobagent.infra.delivery_authorization import build_delivery_authorization

    env.choose("liepin", count=10)
    monkeypatch.setattr(delivery, "_load_reviewed", REAL_LOAD_REVIEWED)
    monkeypatch.setattr("jobagent.infra.account_state.current_account_ref", lambda: "account-test")
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY",
        base64.urlsafe_b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode())
    source = env.reviewed["source_path"]
    preview = build_delivery_preview(platform="liepin", discover_id="discover-test", send_candidates=env.jobs,
        send_command=f"jobagent liepin apply send --input {source}", selected_count=10,
        promoted_count=0, review_count=0, rejected_count=0, skipped_delivered_count=0)
    authorization = build_delivery_authorization(account_ref="account-test", round_id="round-test",
        platform="liepin", discover_id="discover-test", preview=preview, send_candidates=env.jobs,
        interaction_id="original-confirmation")
    manifest = {"manifest_type": "decision_manifest", "protocol_version": 1,
        "signature_algorithm": "Ed25519", "manifest_id": "manifest-original", "platform": "liepin",
        "discover_id": "discover-test", "deduplicated_count": 10, "selected": copy.deepcopy(env.jobs),
        "review": [], "rejected": [], "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    manifest["signature"] = base64.urlsafe_b64encode(key.sign(protocol.canonical_json_bytes(manifest))).decode()
    review = {"platform": "liepin", "discover_id": "discover-test", "manifest": manifest,
        "send_candidates": env.jobs, "delivery_preview": preview, "delivery_authorization": authorization}
    state.save_json(env.path / "decision.review.json", review)
    active = rounds.ensure_current_round()
    active["platforms"]["boss"] = {"status": "completed", "evidence": {"greeting_sent": 21}}
    for index in range(91):
        task = {"session": active["native_session"]}
        action = "inspect_session"
        if index < 21:
            action = "send_greeting"
            task.update(job={"id": f"boss-{index}", "cloud_greeting": "Synthetic preserved greeting"},
                        delivery_source={"preview_id": "boss-original", "authorization_id": "boss-original"})
        binding = {"account_ref": "account-test", "round_id": "round-test", "platform": "boss", "job_id": f"boss-{index}"}
        old = store.ensure_work(action=action, task=task, binding=binding, side_effect=index < 21, key=f"history-{index}")
        old = store.begin_work(old["work_id"], binding)
        result = {"receipt_id": f"history-{index}", "binding": binding, "nonce": old["nonce"], "outcome": "success",
            "evidence": {"outgoing_text": "Synthetic preserved greeting", "message_state": "sent"}}
        store.submit_work(old["work_id"], binding, result)
    rounds.save_round(active)
    response = native.start_delivery("liepin", input_path=source, preview_id=preview["preview_id"],
                                     authorization_id=authorization["authorization_id"])
    for _ in range(2):
        response = submit(env, native.begin(response["work"]["work_id"])["work"])
    old = native.begin(response["work"]["work_id"])["work"]
    receipt = observation(old, resume_reference=None, existing_outgoing_text=DEFAULT_GREETING, message_state="sent")
    store.submit_work(old["work_id"], native._binding(), receipt)
    active = rounds.ensure_current_round()
    active["resume_binding"] = {"id": "original-cloud-binding", "resume_id": "original-cloud-resume",
                               "resume_revision_id": "original-cloud-revision"}
    rounds.save_round(active)
    before = store.list_account_work("account-test")
    assert len(before) == 94 and all(w["state"] == "closed" for w in before)
    review_bytes = (env.path / "decision.review.json").read_bytes()
    state.save_json(state.STATE_DIR / "client_upgrade_state.json",
                   {"state_migration_version": 8, "client_version": "0.6.18", "protocol_version": 1})
    assert client_upgrade.run_client_upgrade(app_dir=env.path, current_version="0.6.19")["ok"]
    response = cli._dispatch(cli.build_parser().parse_args(["work", "next"]))
    recovery = native.begin(response["work"]["work_id"])["work"]
    assert recovery["action"] == "inspect_resume_selection" and recovery["side_effect"] is False
    response = submit(env, recovery, resume_observation(recovery))
    assert response["work"]["action"] == "prepare_resume"
    assert store.list_account_work("account-test")[:94] == before
    assert (env.path / "decision.review.json").read_bytes() == review_bytes
    assert native.audit("boss", complete=False)["summary"]["greeting_sent"] == 21
    assert native.audit("liepin")["summary"]["greeting_sent"] == 0
    assert rounds.round_status()["current_platform"] == "liepin"
    assert all(w["binding"].get("job_id") == env.jobs[0]["id"] for w in store.list_account_work("account-test")[94:])
    assert rounds.ensure_current_round()["platforms"]["zhilian"]["status"] == "pending"
    assert rounds.ensure_current_round()["platforms"]["51job"]["status"] == "pending"
