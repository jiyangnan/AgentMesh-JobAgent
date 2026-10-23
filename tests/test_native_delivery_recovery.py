"""Synthetic native observations only; preserve earlier real-action intents."""
import copy

import pytest

from jobagent.application import native_work as native
from jobagent.infra import browser_work as store, rounds
from tests.test_native_work import env, observation, submit


def pending(env, count=1):
    env.choose("liepin", count=count)
    return native.begin(env.start("liepin")["work"]["work_id"])["work"]


def recovery_receipt(work, **changes):
    result = observation(work, native_computer_use_available=True, browser="chrome",
        window_reference="com.google.Chrome", window_reference_kind="app_scoped_window",
        window_context={"app_reference": "com.google.Chrome", "window_title": "Synthetic Liepin",
                        "selection_verified": True, "selection_evidence": "Synthetic original profile and account visible"},
        group_reference="Synthetic existing task tabs", login_state="authenticated",
        account_navigation=True, resume_or_activity=True)
    result["evidence"].update(changes)
    return result


def test_recover_keeps_completed_jobs_and_only_reissues_unattempted_inspection(env):
    work = pending(env, count=2)
    while work["binding"]["job_id"] == "job0":
        response = submit(env, work)
        work = native.begin(response["work"]["work_id"])["work"]
    source = copy.deepcopy(store.get_work(work["work_id"], work["binding"]))
    original = copy.deepcopy(rounds.ensure_current_round())
    completed = [w for w in store.list_work(native._binding()) if w["binding"].get("job_id") == "job0"]
    recovery = native.recover(work["work_id"], confirmed=True)["work"]
    assert not recovery["side_effect"] and recovery["action"] == "recover_session"
    assert rounds.ensure_current_round() == original
    begun = native.begin(recovery["work_id"])["work"]
    resumed = submit(env, begun, recovery_receipt(begun))["work"]
    assert resumed["action"] == "inspect_delivery" and not resumed["side_effect"]
    assert resumed["binding"] == source["binding"]
    assert resumed["work_id"] != source["work_id"]
    assert resumed["task"]["session"]["window_reference"] == "com.google.Chrome"
    current = rounds.ensure_current_round()
    assert current["native_session"]["id"] == original["native_session"]["id"]
    assert current["platforms"] == original["platforms"]
    assert current["intent"] == original["intent"]
    assert [w for w in store.list_work(native._binding()) if w["binding"].get("job_id") == "job0"] == completed
    archived = store.get_work(source["work_id"], source["binding"])
    assert archived["nonce"] == source["nonce"] and archived["task"] == source["task"]
    assert archived["result"]["outcome"] == "cancelled"
    assert native.recover(source["work_id"], confirmed=True)["work"]["work_id"] == resumed["work_id"]


@pytest.mark.parametrize("field,value", [("profile_label", "Other"), ("account_label", "Other"),
    ("native_computer_use_available", False), ("window_context", {}), ("resume_or_activity", False)])
def test_recovery_rejects_unverified_context_without_rebinding(env, field, value):
    source = pending(env)
    recovery = native.recover(source["work_id"], confirmed=True)["work"]
    begun = native.begin(recovery["work_id"])["work"]
    original = copy.deepcopy(rounds.ensure_current_round())
    with pytest.raises(store.BrowserWorkError):
        submit(env, begun, recovery_receipt(begun, **{field: value}))
    assert rounds.ensure_current_round() == original
    assert store.get_work(begun["work_id"], begun["binding"])["state"] != "closed"


def test_recovery_requires_confirmation_and_original_scope(env):
    source = pending(env)
    before = copy.deepcopy(store.get_work(source["work_id"], source["binding"]))
    with pytest.raises(store.BrowserWorkError):
        native.recover(source["work_id"], confirmed=False)
    env.reviewed["send_candidates"][0]["title"] = "Changed approved job"
    with pytest.raises(store.BrowserWorkError):
        native.recover(source["work_id"], confirmed=True)
    assert store.get_work(source["work_id"], source["binding"]) == before


def test_recovery_cannot_reissue_started_or_ready_external_action(env):
    source = pending(env)
    action = submit(env, source)["work"]
    assert action["action"] == "open_communication"
    for issued in (False, True):
        if issued:
            native.begin(action["work_id"])
        before = copy.deepcopy(store.get_work(action["work_id"], action["binding"]))
        with pytest.raises(store.BrowserWorkError):
            native.recover(action["work_id"], confirmed=True)
        assert store.get_work(action["work_id"], action["binding"]) == before


def test_after_communication_inspection_is_not_window_recovery_entry(env):
    source = pending(env)
    action = native.begin(submit(env, source)["work"]["work_id"])["work"]
    post = submit(env, action)["work"]
    assert post["task"]["inspection_phase"] == "after_communication"
    with pytest.raises(store.BrowserWorkError):
        native.recover(post["work_id"], confirmed=True)
    assert store.get_work(action["work_id"], action["binding"])["result"]["outcome"] == "success"


def test_crash_after_archiving_inspection_preserves_authorization_and_recovers_once(env, monkeypatch):
    source = pending(env)
    original = copy.deepcopy(rounds.ensure_current_round())
    ensure = store.ensure_work
    monkeypatch.setattr(store, "ensure_work", lambda **kw: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        native.recover(source["work_id"], confirmed=True)
    assert rounds.ensure_current_round() == original
    monkeypatch.setattr(store, "ensure_work", ensure)
    recovery = native.recover(source["work_id"], confirmed=True)["work"]
    begun = native.begin(recovery["work_id"])["work"]
    same = native.recover(source["work_id"], confirmed=True)["work"]
    assert same["work_id"] == begun["work_id"] and same["observation_attempts"] == 1
    assert same["nonce"] == begun["nonce"]
