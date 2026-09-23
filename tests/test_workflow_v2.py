import json
from types import SimpleNamespace

import pytest

from jobagent import cli
from jobagent.application import workflow as flow
from jobagent.infra import state, rounds, account_state, browser_work


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "ROUNDS_DIR", tmp_path / "rounds")
    monkeypatch.setattr(flow, "current_account_ref", lambda: "acct_synthetic")
    monkeypatch.setattr(account_state, "current_account_ref", lambda: "acct_synthetic")
    monkeypatch.setattr(browser_work, "has_open", lambda: False)
    monkeypatch.setattr(browser_work, "list_work", lambda *_: [])
    from jobagent.application import delivery_followup
    monkeypatch.setattr(delivery_followup, "current_account_ref", lambda: "acct_synthetic")
    return tmp_path


def test_next_has_one_stable_action_and_never_runs_doctor(isolated, monkeypatch):
    monkeypatch.setattr(cli, "_doctor_env", lambda: pytest.fail("next executed doctor"))
    first = flow.next_action()
    second = flow.next_action()
    assert first["action"] == second["action"]
    assert first["action"]["business_argv"] == ["jobagent", "doctor", "env"]
    assert not state.current_round_path().exists()


def test_advance_is_once_and_status_does_not_repeat(isolated, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_doctor_env", lambda: calls.append(1) or {"ok": True, "workflow": {"ready": True}, "next_suggested": "jobagent round start"})
    action = flow.next_action()["action"]
    args = cli.build_parser().parse_args(action["argv"][1:])
    assert flow.advance(args.action_id, args.expected_revision)["ok"]
    assert flow.advance(args.action_id, args.expected_revision)["replayed"]
    assert calls == [1]
    assert flow.next_action()["action"] == {"type": "done", "scope": "setup"}
    assert not state.current_round_path().exists()


def test_stale_action_cannot_execute_after_state_changed(isolated, monkeypatch):
    action = flow.next_action()["action"]
    state.save_json(state.STATE_DIR / "pending_round_binding.json", {"changed": True})
    monkeypatch.setattr(cli, "_doctor_env", lambda: pytest.fail("stale execution"))
    args = cli.build_parser().parse_args(action["argv"][1:])
    assert flow.advance(args.action_id, args.expected_revision)["error"] == "workflow_action_stale"


def test_interruption_never_reexecutes_claimed_action(isolated, monkeypatch):
    calls = []
    def interrupted():
        calls.append(1)
        raise KeyboardInterrupt()
    monkeypatch.setattr(cli, "_doctor_env", interrupted)
    args = cli.build_parser().parse_args(flow.next_action()["action"]["argv"][1:])
    with pytest.raises(KeyboardInterrupt):
        flow.advance(args.action_id, args.expected_revision)
    assert flow.advance(args.action_id, args.expected_revision)["error"] == "workflow_result_unresolved"
    assert flow.next_action()["error"] == "workflow_result_unresolved"
    assert calls == [1]


def test_old_native_output_is_not_new_permission(isolated, monkeypatch):
    monkeypatch.setattr(cli, "_doctor_env", lambda: {"ok": True, "event": "browser_work_required", "native_step_issued": True,
        "work": {"nonce": "once", "allowed_mode": "execute"}})
    args = cli.build_parser().parse_args(flow.next_action()["action"]["argv"][1:])
    assert flow.advance(args.action_id, args.expected_revision)["native_step_issued"]
    repeated = flow.advance(args.action_id, args.expected_revision)
    assert "native_step_issued" not in repeated
    assert "work" not in repeated


def test_account_switch_cannot_read_prior_journal(isolated, monkeypatch):
    flow.next_action()
    monkeypatch.setattr(flow, "current_account_ref", lambda: "acct_other")
    with pytest.raises(ValueError, match="another account"):
        flow.next_action()


def test_submission_preserves_input_once_without_creating_round(isolated, monkeypatch):
    calls = []
    def submit(body):
        calls.append(body)
        return {"ok": True, "workflow": {"session_id": "wfs_test", "revision": 1, "criteria": body["criteria"]}}
    monkeypatch.setattr(flow.cloud_client, "workflow_submit", submit)
    file = isolated / "input.json"
    file.write_text(json.dumps({"request_id": "request01", "criteria": {"target_roles": ["产品经理"], "target_cities": ["武汉"]}}))
    assert flow.submit(str(file))["ok"]
    assert flow.submit(str(file))["replayed"]
    assert len(calls) == 1
    assert not state.current_round_path().exists()
    from jobagent.application.round_request import request
    assert request()["target_roles"] == ["产品经理"]


def test_answer_file_uses_existing_card_choices(isolated):
    from jobagent.application.interaction_answer import apply_answer_file
    file = isolated / "answer.json"
    file.write_text(json.dumps({"choice": "exclude_jobs", "exclude_indices": [2, 4]}))
    args = cli.build_parser().parse_args(["interaction", "respond", "--interaction-id", "test", "--answer-file", str(file)])
    apply_answer_file(args)
    assert args.choice == "exclude_jobs" and args.exclude_index == [2, 4]
    file.write_text(json.dumps({"exclude_indices": [True]}))
    args.answer_file = str(file)
    args.choice, args.exclude_index = None, []
    with pytest.raises(ValueError, match="positive integers"):
        apply_answer_file(args)


def test_direction_change_waits_then_finishes_old_round_serially(isolated, monkeypatch):
    from jobagent.application import round_direction
    original = rounds.start_new_round({"status": "confirmed", "target_roles": ["产品经理"],
        "target_cities": ["武汉"], "profile_digest": "sha256:" + "1" * 64, "confirmed_at": "2026-09-24", "source": "user_explicit"})
    response = round_direction.request_change({"request_id": "direction01", "patch": {"target_roles": ["工程师"], "target_cities": ["上海"]}})
    assert rounds.round_status()["round_id"] == original["round_id"]
    assert rounds.round_status()["intent"]["target_roles"] == ["产品经理"]
    identifier = response["interaction"]["interaction_id"]
    monkeypatch.setattr(browser_work, "has_open", lambda: True)
    assert round_direction.respond(identifier, "finish_round_and_rebind")["error"] == "native_work_context_locked"
    assert not rounds.round_status()["workflow_complete"]
    monkeypatch.setattr(browser_work, "has_open", lambda: False)
    result = round_direction.respond(identifier, "finish_round_and_rebind")
    assert result["event"] == "old_round_finished"
    assert rounds.round_status()["workflow_complete"]
    assert round_direction.respond(identifier, "finish_round_and_rebind")["replayed"]
    assert rounds.round_status()["round_id"] == original["round_id"]


def test_new_contract_lists_round_update_and_answer_file():
    from jobagent.infra.workflow_protocol import contract
    value = contract(cli.build_parser())
    assert value["protocol_version"] == 2
    assert value["action_types"] == ["run", "ask", "native_work", "handoff", "wait", "blocked", "done"]
    assert ["jobagent", "round", "update"] in [c["argv_prefix"] for c in value["commands"]]


def test_answer_file_does_not_silently_ignore_fields_from_another_card(isolated):
    from jobagent.application.interaction_answer import apply_answer_file
    state.save_json(state.pending_interaction_path(), {"interaction_id": "delivery-card",
        "kind": "delivery_confirmation", "interaction": {"kind": "delivery_confirmation", "fields": []}})
    file = isolated / "answer.json"
    for answer in ({"choice": "confirm_all", "target_roles": ["工程师"]},
                   {"choice": "confirm_all", "exclude_indices": [1]}):
        file.write_text(json.dumps(answer))
        args = cli.build_parser().parse_args(["interaction", "respond", "--interaction-id", "delivery-card", "--answer-file", str(file)])
        with pytest.raises(ValueError):
            apply_answer_file(args)


def test_workflow_dispatches_tls_recovery_before_resuming_doctor(isolated, monkeypatch):
    from jobagent.infra import tls_support
    monkeypatch.setattr(cli, "_doctor_env", lambda: {"ok": False,
        "error": "tls_trust_configuration_failed", "next_suggested": "jobagent doctor tls"})
    action = cli.build_parser().parse_args(flow.next_action()["action"]["argv"][1:])
    flow.advance(action.action_id, action.expected_revision)
    recovery = flow.next_action()["action"]
    assert recovery["business_argv"] == ["jobagent", "doctor", "tls"]
    calls = []
    monkeypatch.setattr(tls_support, "transport_preflight", lambda: calls.append(1) or {
        "ok": True, "event": "tls_ready", "next_suggested": "jobagent doctor env"})
    action = cli.build_parser().parse_args(recovery["argv"][1:])
    assert flow.advance(action.action_id, action.expected_revision)["event"] == "tls_ready"
    assert calls == [1]
    assert flow.next_action()["action"]["business_argv"] == ["jobagent", "doctor", "env"]
