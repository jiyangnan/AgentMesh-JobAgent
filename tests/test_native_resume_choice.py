"""A platform default attachment is not an explicit user selection."""
import copy
import json

import pytest

from jobagent import cli
from jobagent.application import native_resume_choice as choices, native_work as native, workflow
from jobagent.infra import browser_work as store, rounds
from jobagent.infra.interaction_state import load_pending_interaction
from jobagent.infra.workflow_protocol import with_contract
from tests.test_native_work import env, observation, submit  # noqa: F401


OPTIONS = [{"reference": "Project manager (2026-09-14)", "selected": True},
           {"reference": "Product manager (2026-05-21)", "selected": False}]


def prepare(env):
    env.choose("liepin")
    response = env.start("liepin")
    while response["work"]["action"] != "prepare_resume":
        work = native.begin(response["work"]["work_id"])["work"]
        response = submit(env, work)
    work = native.begin(response["work"]["work_id"])["work"]
    assert not work["side_effect"] and work["allowed_mode"] == "observe"
    return work


def show(env):
    work = prepare(env)
    response = submit(env, work, observation(work, submission_mode="online_and_attachment", attachment_options=OPTIONS))
    return work, response


def answer(response, index=1):
    card = response["interaction"]
    option = card["fields"][0]["options"][index]["option_id"]
    args = cli.build_parser().parse_args(["interaction", "respond", "--interaction-id", card["interaction_id"], "--attachment-id", option])
    return cli._interaction_respond(args)


def test_default_does_not_authorize_a_submission_and_audit_stays_pending(env, monkeypatch):
    work, response = show(env)
    card = response["interaction"]
    assert card["kind"] == "platform_resume_choice"
    assert len(card["fields"][0]["options"]) == 3
    assert "default_option_ids" not in card["fields"][0]
    assert with_contract(response)["action"]["response_arguments"]["answer_flag"] == "--attachment-id"
    assert store.has_open() is False
    assert not any(w["action"] == "submit_resume" for w in store.list_account_work("account-test"))
    assert native.audit("liepin")["summary"]["pending"] == 1
    assert rounds.ensure_current_round()["platforms"]["liepin"]["status"] != "completed"
    assert native.next_work()["interaction"] == card
    monkeypatch.setattr(workflow, "current_account_ref", lambda: "account-test")
    assert workflow._source({})["interaction"] == card
    assert choices.respond(card["interaction_id"], "")["error"] == "invalid_interaction_response"
    assert choices.respond(card["interaction_id"], "invented")["error"] == "invalid_interaction_response"


def test_selected_attachment_is_required_in_final_permission_and_receipt(env):
    prepared, response = show(env)
    result = answer(response)
    task = result["work"]["task"]
    assert result["work"]["action"] == "submit_resume"
    assert task["attachment_reference"] == OPTIONS[1]["reference"]
    assert task["attachment_interaction_id"] == response["interaction"]["interaction_id"]
    assert result["work"]["binding"] == prepared["binding"]
    work = native.begin(result["work"]["work_id"])["work"]
    assert work["allowed_mode"] == "execute_once"
    wrong = observation(work, attachment_reference=OPTIONS[0]["reference"])
    with pytest.raises(store.BrowserWorkError, match="user-selected"):
        submit(env, work, wrong)
    assert native.begin(work["work_id"])["work"]["allowed_mode"] == "reconcile_only"
    result = submit(env, work)
    assert result["work"]["action"] == "send_greeting"
    work = native.begin(result["work"]["work_id"])["work"]
    result = submit(env, work)
    assert result["summary"]["resume_submitted"] == result["summary"]["greeting_sent"] == 1
    assert result["summary"]["pending"] == 0


def test_pause_and_restart_preserve_the_card_and_no_submit_permission(env):
    _, response = show(env)
    paused = answer(response, 2)
    assert paused["delivery_paused"] is True
    assert paused["interaction"] == response["interaction"]
    assert not store.has_open()
    from jobagent.infra.interaction_state import clear_pending_interaction
    clear_pending_interaction()  # simulate a lost presentation slot, not user consent
    restored = native.next_work()
    assert restored["interaction"] == response["interaction"]
    selected = answer(restored)
    assert selected["work"]["task"]["attachment_reference"] == OPTIONS[1]["reference"]


def test_answer_replay_cannot_change_selection_or_reissue_permission(env):
    _, response = show(env)
    selected = answer(response)
    work = native.begin(selected["work"]["work_id"])["work"]
    assert answer(response)["idempotent_replay"] is True
    assert answer(response, 0)["error"] == "interaction_response_conflict"
    assert native.next_work()["work"]["work_id"] == work["work_id"]
    assert native.next_work()["work"]["allowed_mode"] == "reconcile_only"


def test_answer_file_and_host_mapping_use_product_option_ids(env):
    _, response = show(env)
    mapping = response["host_presentations"]["adapters"]["codex"]["answer_mapping"]
    path = env.path / "answer.json"
    path.write_text(json.dumps({"attachment_id": mapping[OPTIONS[1]["reference"]]}))
    args = cli.build_parser().parse_args(["interaction", "respond", "--interaction-id", response["interaction"]["interaction_id"], "--answer-file", str(path)])
    from jobagent.application.interaction_answer import apply_answer_file
    apply_answer_file(args)
    assert cli._interaction_respond(args)["work"]["task"]["attachment_reference"] == OPTIONS[1]["reference"]


def test_crash_after_answer_commit_restores_continuation_not_question(env, monkeypatch):
    _, response = show(env)
    original = choices.clear_pending_interaction
    monkeypatch.setattr(choices, "clear_pending_interaction", lambda: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError, match="crash"):
        answer(response)
    assert load_pending_interaction()
    monkeypatch.setattr(choices, "clear_pending_interaction", original)
    monkeypatch.setattr(workflow, "current_account_ref", lambda: "account-test")
    restored = workflow._source({})
    assert restored["next_suggested"] == "jobagent work next" and not restored.get("interaction")
    continued = native.next_work()
    assert continued["work"]["task"]["attachment_reference"] == OPTIONS[1]["reference"]
    assert load_pending_interaction() is None


@pytest.mark.parametrize("field,value", [
    ("submission_attempted", True), ("submission_attempted", 0),
    ("dialog_cancellable", False), ("options_complete", False),
    ("conversation_job_verified", False), ("resume_state", "sent"),
    ("resume_reference", "Other resume"), ("submission_mode", "unknown"),
    ("attachment_options", []), ("attachment_options", [OPTIONS[0], OPTIONS[0]]),
    ("attachment_options", [{"reference": "file", "selected": 1}]),
])
def test_incomplete_or_ambiguous_dialog_cannot_generate_user_choice(env, field, value):
    work = prepare(env)
    result = observation(work, submission_mode="online_and_attachment", attachment_options=OPTIONS)
    result["evidence"][field] = value
    with pytest.raises(store.BrowserWorkError):
        submit(env, work, result)
    assert not rounds.ensure_current_round().get("native_resume_choices")
    assert not any(w["action"] == "submit_resume" for w in store.list_account_work("account-test"))


def test_expired_authorization_and_changed_session_preserve_unanswered_choice(env, monkeypatch):
    _, response = show(env)
    monkeypatch.setattr(native, "_review_for", lambda w: (_ for _ in ()).throw(ValueError("expired")))
    with pytest.raises(ValueError, match="expired"):
        answer(response)
    assert choices.pending()["interaction"] == response["interaction"]
    active = rounds.ensure_current_round()
    active["native_session"]["profile_label"] = "Other profile"
    rounds.save_round(active)
    with pytest.raises(store.BrowserWorkError, match="original account"):
        answer(response)


def test_old_completed_submission_does_not_request_another_attachment(env):
    env.choose("liepin")
    response = env.start("liepin")
    for _ in range(3):
        work = native.begin(response["work"]["work_id"])["work"]
        if work["task"].get("inspection_phase") == "after_communication":
            store.submit_work(work["work_id"], native._binding(), observation(work))
            old_task = copy.deepcopy(work["task"])
            old_task["resume_reference"] = "Synthetic online resume"
            old = store.ensure_work(action="submit_resume", task=old_task, binding=work["binding"], side_effect=True)
            old = store.begin_work(old["work_id"], native._binding())
            store.submit_work(old["work_id"], native._binding(), observation(old))
            response = native._delivery_next("liepin")
            break
        response = submit(env, work)
    assert response["work"]["action"] == "send_greeting"
    assert not rounds.ensure_current_round().get("native_resume_choices")
