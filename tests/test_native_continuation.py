"""A prerequisite failure is not a retry of an unknown external outcome."""
import copy
import json

import pytest

from jobagent.application import native_continuation as continuation
from jobagent.application import native_work as native
from jobagent.infra import browser_work as store, rounds
from tests.test_native_work import env, observation, submit  # noqa: F401


def legacy_resume(env, **evidence):
    env.choose("liepin")
    pre = native.begin(env.start("liepin")["work"]["work_id"])["work"]
    store.submit_work(pre["work_id"], native._binding(), observation(pre))
    task = copy.deepcopy(pre["task"])
    task.pop("delivery_order_version", None)
    task["resume_reference"] = "Synthetic online resume"
    old = store.ensure_work(action="submit_resume", task=task, binding=pre["binding"], side_effect=True)
    work = native.begin(old["work_id"])["work"]
    r = observation(work, side_effect_attempted=False, missing_prerequisite="open_communication", resume_state="not_sent", **evidence)
    r.update(outcome="uncertain", requires_technical_recovery=True, requires_user_action=False, reason="page_state_unknown")
    submit(env, work, r)
    return store.get_work(work["work_id"], native._binding())


def receipt(work):
    r = observation(work, side_effect_attempted=False, submission_control_present=False,
                    missing_prerequisite="open_communication", visible_controls=["聊一聊"],
                    resume_state="not_sent", communication_state="not_open", message_state="not_sent")
    r.update(receipt_id=work["work_id"] + "-continuation", outcome="not_attempted", reason="communication_prerequisite_missing")
    return r


def run(env, work, result=None):
    path = env.path / "continue.json"
    path.write_text(json.dumps(result or receipt(work)))
    return continuation.continue_unattempted(work["work_id"], str(path))


def test_continue_preserves_authorization_and_does_not_reissue_old_work(env):
    old = legacy_resume(env)
    before = rounds.ensure_current_round()
    shown = native.status()
    assert shown["continuation"]["new_execution_permitted"] is False
    assert "work continue" in shown["next_suggested"]
    r = receipt(old)
    response = run(env, old, r)
    assert response["work"]["action"] == "open_communication"
    assert response["work"]["binding"] == old["binding"]
    assert response["work"]["allowed_mode"] == "observe"
    closed = store.get_work(old["work_id"], native._binding())
    assert closed["state"] == "closed" and closed["result"]["outcome"] == "not_attempted"
    assert closed["nonce"] == old["nonce"]
    assert closed["observation_attempts"] == old["observation_attempts"]
    assert len(store.work_receipts(old["work_id"], native._binding())) == 2
    assert rounds.ensure_current_round()["platforms"]["liepin"]["native_delivery"] == before["platforms"]["liepin"]["native_delivery"]
    assert native.audit("liepin", complete=False)["summary"]["resume_submitted"] == 0
    again = run(env, old, r)
    assert again["work"]["work_id"] == response["work"]["work_id"]
    # A new receipt cannot replace a closed record.
    altered = receipt(old); altered["receipt_id"] += "-new"
    with pytest.raises(store.BrowserWorkError):
        run(env, old, altered)


def test_continuation_survives_crash_after_receipt_commit(env, monkeypatch):
    old = legacy_resume(env)
    original = native._delivery_next
    r = receipt(old)
    monkeypatch.setattr(native, "_delivery_next", lambda p: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        run(env, old, r)
    assert not store.has_open()
    assert native.audit("liepin")["summary"]["pending"] == 1
    assert rounds.ensure_current_round()["platforms"]["liepin"]["status"] != "completed"
    monkeypatch.setattr(native, "_delivery_next", original)
    assert run(env, old, r)["work"]["action"] == "open_communication"


@pytest.mark.parametrize("field,value", [
    ("side_effect_attempted", True), ("side_effect_attempted", None),
    ("side_effect_attempted", 0), ("submission_control_present", True),
    ("resume_state", "unknown"), ("resume_state", "sent"),
    ("communication_state", "unknown"), ("message_state", "sent"),
    ("history_checked", False), ("receipt_checked", False),
    ("visible_controls", []), ("existing_outgoing_text", "an existing message"),
    ("outgoing_text", "an existing message"),
    ("company", "Other company"), ("account_label", "Other account"),
])
def test_continuation_refuses_incomplete_or_conflicting_evidence(env, field, value):
    old = legacy_resume(env)
    r = receipt(old); r["evidence"][field] = value
    with pytest.raises(store.BrowserWorkError):
        run(env, old, r)
    assert store.get_work(old["work_id"], native._binding())["state"] == "reconcile_only"


@pytest.mark.parametrize("field,value", [("nonce", "different"), ("binding", {"account_ref": "other", "round_id": "round-test"})])
def test_continuation_refuses_nonce_and_binding_changes(env, field, value):
    old = legacy_resume(env)
    r = receipt(old); r[field] = value
    with pytest.raises(store.BrowserWorkError):
        run(env, old, r)
    assert store.has_open()


def test_earlier_uncertainty_cannot_be_overwritten_to_gain_a_retry(env):
    old = legacy_resume(env)
    uncertain = observation(old, side_effect_attempted=True)
    uncertain.update(receipt_id="earlier-ambiguous", outcome="uncertain")
    store.submit_work(old["work_id"], native._binding(), uncertain)
    no_action = copy.deepcopy(old["result"]); no_action["receipt_id"] = "later-no-action"
    store.submit_work(old["work_id"], native._binding(), no_action)
    with pytest.raises(store.BrowserWorkError, match="Earlier ambiguous"):
        run(env, old)


def test_expired_authorization_does_not_close_original_work(env, monkeypatch):
    old = legacy_resume(env)
    monkeypatch.setattr(native, "_review_for", lambda w: (_ for _ in ()).throw(ValueError("expired")))
    with pytest.raises(ValueError, match="expired"):
        run(env, old)
    assert store.get_work(old["work_id"], native._binding())["state"] == "reconcile_only"


def test_new_order_never_uses_legacy_continuation(env):
    old = legacy_resume(env)
    assert not continuation.eligible({**old, "task": {**old["task"], "delivery_order_version": 2}})
    for action in ("send_greeting", "open_communication", "inspect_delivery"):
        assert not continuation.eligible({**old, "action": action})
    for platform in ("boss", "zhilian", "51job"):
        assert not continuation.eligible({**old, "binding": {**old["binding"], "platform": platform}})


@pytest.mark.parametrize("automatic_resume", [False, True])
def test_recovered_job_completes_without_duplicate_actions(env, automatic_resume):
    old = legacy_resume(env)
    response = run(env, old)
    actions = []
    while response.get("work"):
        work = native.begin(response["work"]["work_id"])["work"]
        actions.append(work["action"])
        r = observation(work)
        if work["task"].get("inspection_phase") == "after_communication" and automatic_resume:
            r["evidence"].update(resume_state="sent", receipt_kind="resume_card")
        response = submit(env, work, r)
    assert actions == ["open_communication", "inspect_delivery", *([] if automatic_resume else ["prepare_resume", "submit_resume"]), "send_greeting"]
    assert response["completion_state"] == "completed"
    assert response["summary"]["resume_submitted"] == 1
    assert response["summary"]["greeting_sent"] == 1
    assert response["summary"]["pending"] == response["summary"]["unresolved"] == 0


def test_current_session_change_refuses_continuation(env):
    old = legacy_resume(env)
    active = rounds.ensure_current_round()
    active["native_session"]["profile_label"] = "Changed profile"
    rounds.save_round(active)
    with pytest.raises(store.BrowserWorkError, match="changed|unchanged"):
        run(env, old)
    assert store.has_open()


def test_recovered_job_recognizes_exact_existing_receipts_without_resend(env):
    old = legacy_resume(env)
    response = run(env, old)
    work = native.begin(response["work"]["work_id"])["work"]
    response = submit(env, work, observation(work))
    work = native.begin(response["work"]["work_id"])["work"]
    assert work["task"]["inspection_phase"] == "after_communication"
    result = observation(work, resume_state="sent", receipt_kind="resume_card",
                         existing_outgoing_text=work["task"]["job"]["cloud_greeting"],
                         message_state="sent")
    response = submit(env, work, result)
    assert not response.get("work")
    assert response["summary"]["pending"] == 0
    assert response["summary"]["greeting_sent"] == response["summary"]["resume_submitted"] == 1
    assert not any(w["action"] == "send_greeting" for w in store.list_account_work(native._binding()["account_ref"]))
