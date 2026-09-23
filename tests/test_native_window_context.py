"""App-scoped host windows: synthetic observations and isolated real ledgers only."""
from __future__ import annotations

import copy
import socket
from datetime import datetime, timezone

import pytest

from jobagent.application import native_work as native
from jobagent.infra import browser_work as store, rounds
from tests.test_native_work import env, observation, submit


APP_REFERENCE = "com.example.synthetic.chrome"
APP_KIND = "app_scoped_window"


@pytest.fixture
def window_env(env, monkeypatch):
    def deny_network(*args, **kwargs):
        pytest.fail("Window-context tests must not contact a real service")

    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    env.choose("boss", session=False)
    env.active["platforms"]["boss"]["status"] = "active"
    rounds.save_round(copy.deepcopy(env.active))
    return env


def context(title="Synthetic BOSS window"):
    return {
        "app_reference": APP_REFERENCE,
        "window_title": title,
        "selection_verified": True,
        "selection_evidence": "Synthetic native app selection shows the intended profile and official platform window.",
    }


def bind_work():
    work = native.request_login("boss")["work"]
    assert work["action"] == "bind_session"
    return native.begin(work["work_id"])["work"]


def bind_receipt(work):
    return observation(
        work, native_computer_use_available=True, browser="chrome", reuse_status="reused",
        window_reference=APP_REFERENCE, window_reference_kind=APP_KIND,
        group_reference="Synthetic task group", window_context=context(),
    )


def app_observation(work, title="Synthetic BOSS window"):
    return observation(
        work, window_reference=APP_REFERENCE, window_reference_kind=APP_KIND,
        window_context=context(title), account_navigation=True, resume_or_activity=True,
    )


def test_app_scoped_bind_persists_kind_and_continues_same_round(window_env):
    work = bind_work()
    response = submit(window_env, work, bind_receipt(work))
    active = rounds.ensure_current_round()
    session = active["native_session"]
    assert session["window_reference_kind"] == APP_KIND
    assert session["window_reference"] == APP_REFERENCE
    assert active["round_id"] == work["binding"]["round_id"]
    assert response["work"]["action"] == "inspect_session"
    assert response["work"]["binding"]["session_id"] == session["id"]
    assert response["work"]["binding"]["round_id"] == active["round_id"]


def test_app_scoped_bind_rejects_missing_selection_context_without_saving(window_env):
    work = bind_work()
    result = bind_receipt(work)
    result["evidence"].pop("window_context")
    before = store.get_work(work["work_id"], work["binding"])
    with pytest.raises(store.BrowserWorkError):
        submit(window_env, work, result)
    after = store.get_work(work["work_id"], work["binding"])
    assert after["state"] == before["state"]
    assert after["result"] == before["result"]
    assert not rounds.ensure_current_round().get("native_session")


def test_bind_capability_error_names_invalid_fields_and_original_submit(window_env):
    work = bind_work()
    result = bind_receipt(work)
    result["evidence"].update(native_computer_use_available="true", browser="Chrome")
    result["evidence"].pop("reuse_status")
    with pytest.raises(store.BrowserWorkError) as caught:
        submit(window_env, work, result)
    payload = caught.value.payload
    assert payload["error"] == "native_capability_required"
    assert set(payload["invalid_fields"]) == {"native_computer_use_available", "browser", "reuse_status"}
    assert payload["next_suggested"] == f"jobagent work submit --work-id {work['work_id']} --result <result.json>"
    assert store.get_work(work["work_id"], work["binding"])["result"] is None


def begin_inspection(env):
    work = bind_work()
    inspection = submit(env, work, bind_receipt(work))["work"]
    assert inspection["action"] == "inspect_session"
    return native.begin(inspection["work_id"])["work"]


def test_current_title_can_change_without_changing_bound_app_or_round(window_env, monkeypatch):
    work = begin_inspection(window_env)
    original = copy.deepcopy(rounds.ensure_current_round())
    calls = []

    def discover(platform):
        calls.append((platform, copy.deepcopy(rounds.ensure_current_round())))
        return {"synthetic_discovery_continuation": True}

    monkeypatch.setattr(native, "request_discovery", discover)
    response = submit(window_env, work, app_observation(work, title="Synthetic profile home"))
    assert response == {"synthetic_discovery_continuation": True}
    assert len(calls) == 1 and calls[0][0] == "boss"
    active = calls[0][1]
    assert active["round_id"] == original["round_id"]
    assert active["intent"] == original["intent"]
    assert active["native_session"]["id"] == original["native_session"]["id"]
    assert active["native_session"]["window_reference"] == APP_REFERENCE
    assert active["native_session"]["accounts"]["boss"] == "Synthetic user"
    assert active["platforms"]["boss"]["status"] == "login_verified"


@pytest.mark.parametrize("field,value", [
    ("window_context", None),
    ("window_reference_kind", None),
    ("window_reference_kind", "native_window_id"),
    ("window_reference_kind", "page_title"),
    ("window_reference", "different-synthetic-app"),
    ("app_reference", "different-synthetic-app"),
    ("selection_verified", False),
    ("selection_verified", "true"),
    ("selection_verified", 1),
    ("window_title", ""),
    ("selection_evidence", ""),
])
def test_app_scoped_success_cannot_drop_or_change_context(window_env, field, value):
    work = begin_inspection(window_env)
    result = app_observation(work)
    if field in {"window_context", "window_reference_kind", "window_reference"}:
        if value is None:
            result["evidence"].pop(field)
        else:
            result["evidence"][field] = value
    else:
        result["evidence"]["window_context"][field] = value
    before = copy.deepcopy(rounds.ensure_current_round())
    with pytest.raises(store.BrowserWorkError):
        submit(window_env, work, result)
    saved = store.get_work(work["work_id"], work["binding"])
    assert saved["state"] == "intent_recorded"
    assert saved["result"] is None
    assert rounds.ensure_current_round() == before


@pytest.mark.parametrize("kind", [None, "native_window_id", "host_window_handle"])
def test_legacy_and_stable_reference_binding_remain_compatible(window_env, kind):
    work = bind_work()
    result = bind_receipt(work)
    result["evidence"].pop("window_context")
    result["evidence"].pop("window_reference_kind")
    result["evidence"]["window_reference"] = "synthetic-native-window-9"
    if kind:
        result["evidence"]["window_reference_kind"] = kind
    response = submit(window_env, work, result)
    assert response["work"]["action"] == "inspect_session"
    assert rounds.ensure_current_round()["native_session"]["window_reference"] == "synthetic-native-window-9"


def test_invalid_explicit_bind_kind_is_not_legacy_compatibility(window_env):
    work = bind_work()
    result = bind_receipt(work)
    result["evidence"]["window_reference_kind"] = "page_title"
    with pytest.raises(store.BrowserWorkError):
        submit(window_env, work, result)
    assert store.get_work(work["work_id"], work["binding"])["result"] is None


def test_app_context_pause_does_not_require_unobserved_selection(window_env):
    work = begin_inspection(window_env)
    result = {
        "receipt_id": "synthetic-window-ambiguity", "nonce": work["nonce"],
        "binding": copy.deepcopy(work["binding"]), "outcome": "uncertain",
        "requires_user_action": True, "reason": "session_unknown",
        "evidence": {
            "source": "host_ui_observation", "observed_at": datetime.now(timezone.utc).isoformat(),
            "observation": "Synthetic native selection is ambiguous; no target window claimed.",
            "window_reference": APP_REFERENCE, "profile_label": "Test profile",
        },
    }
    response = submit(window_env, work, result)
    assert response["requires_user_action"] is True
    assert response["work"]["state"] == "reconcile_only"
    assert store.get_work(work["work_id"], work["binding"])["result"] == result
    assert rounds.ensure_current_round()["native_session"]["accounts"] == {}


def test_old_pending_bind_gets_complete_examples_without_mutating_ledger(window_env):
    binding = {"account_ref": "account-test", "round_id": "round-test", "platform": "boss"}
    work = store.ensure_work(
        action="bind_session", binding=binding, key="synthetic-pre-upgrade-bind",
        task={"instruction": "Synthetic old binding instruction", "result_schema": {"evidence": {"browser": "chrome"}}},
    )
    begun = native.begin(work["work_id"])["work"]
    before = store.get_work(work["work_id"], binding)
    with store._transaction() as connection:
        original_row = tuple(connection.execute(
            "SELECT specification_digest, task_json FROM works WHERE work_id = ?", (work["work_id"],)
        ).fetchone())
    shown = native.present(before)["work"]
    variants = list(shown["task"]["result_examples"].values())
    all_examples = [shown["task"]["result_example"], *variants]
    for result in all_examples:
        assert result["binding"] == binding and result["nonce"] == begun["nonce"]
        evidence = result["evidence"]
        assert evidence["native_computer_use_available"] is True
        assert evidence["browser"] == "chrome"
        assert evidence["reuse_status"] in {"reused", "created_no_existing"}
        assert all(isinstance(evidence[key], str) and evidence[key] for key in
                   ("window_reference", "profile_label", "group_reference"))
    assert any(item["evidence"].get("window_reference_kind") == APP_KIND for item in variants)
    assert any(item["evidence"].get("window_reference_kind") in {"native_window_id", "host_window_handle"}
               for item in variants)
    after = store.get_work(work["work_id"], binding)
    assert after["task"] == before["task"]
    with store._transaction() as connection:
        current_row = tuple(connection.execute(
            "SELECT specification_digest, task_json FROM works WHERE work_id = ?", (work["work_id"],)
        ).fetchone())
    assert current_row == original_row
    assert after["nonce"] == before["nonce"]
    assert after["observation_attempts"] == before["observation_attempts"]


def test_rejected_bind_receipt_can_be_corrected_but_committed_receipt_is_immutable(window_env):
    work = bind_work()
    rejected = bind_receipt(work)
    rejected["evidence"]["native_computer_use_available"] = "true"
    with pytest.raises(store.BrowserWorkError):
        submit(window_env, work, rejected)
    accepted = copy.deepcopy(rejected)
    accepted["evidence"]["native_computer_use_available"] = True
    first = submit(window_env, work, accepted)
    replay = submit(window_env, work, accepted)
    assert first["work"]["work_id"] == replay["work"]["work_id"]
    changed = copy.deepcopy(accepted)
    changed["evidence"]["window_context"]["window_title"] = "Synthetic changed observation"
    with pytest.raises(store.BrowserWorkError) as caught:
        submit(window_env, work, changed)
    assert caught.value.payload["error"] == "browser_work_receipt_conflict"
    assert store.get_work(work["work_id"], work["binding"])["result"] == accepted


@pytest.mark.parametrize("original_kind", ["native_window_id", "host_window_handle"])
def test_bound_stable_kind_cannot_silently_change_to_app_scoped(window_env, monkeypatch, original_kind):
    monkeypatch.setattr(native, "request_discovery", lambda platform: {"synthetic_discovery_continuation": True})
    work = bind_work()
    result = bind_receipt(work)
    result["evidence"].pop("window_context")
    result["evidence"].update(window_reference="synthetic-native-window-9", window_reference_kind=original_kind)
    inspection = submit(window_env, work, result)["work"]
    inspection = native.begin(inspection["work_id"])["work"]
    switched = app_observation(inspection)
    switched["evidence"]["window_reference"] = "synthetic-native-window-9"
    switched["evidence"]["window_context"]["app_reference"] = "synthetic-native-window-9"
    before = copy.deepcopy(rounds.ensure_current_round())
    with pytest.raises(store.BrowserWorkError):
        submit(window_env, inspection, switched)
    assert store.get_work(inspection["work_id"], inspection["binding"])["result"] is None
    assert rounds.ensure_current_round() == before


@pytest.mark.parametrize("receipt_kind", ["native_window_id", "host_window_handle"])
def test_legacy_untyped_session_accepts_explicit_stable_receipt_kind(window_env, monkeypatch, receipt_kind):
    monkeypatch.setattr(native, "request_discovery", lambda platform: {"synthetic_discovery_continuation": True})
    work = bind_work()
    result = bind_receipt(work)
    result["evidence"].pop("window_context")
    result["evidence"].pop("window_reference_kind")
    result["evidence"]["window_reference"] = "synthetic-legacy-window"
    inspection = submit(window_env, work, result)["work"]
    inspection = native.begin(inspection["work_id"])["work"]
    observed = observation(inspection, window_reference="synthetic-legacy-window",
                           window_reference_kind=receipt_kind, account_navigation=True, resume_or_activity=True)
    assert submit(window_env, inspection, observed) == {"synthetic_discovery_continuation": True}
    assert not rounds.ensure_current_round()["native_session"].get("window_reference_kind")


def test_legacy_untyped_session_cannot_silently_become_app_scoped(window_env, monkeypatch):
    monkeypatch.setattr(native, "request_discovery", lambda platform: {"synthetic_discovery_continuation": True})
    work = bind_work()
    result = bind_receipt(work)
    result["evidence"].pop("window_context")
    result["evidence"].pop("window_reference_kind")
    inspection = submit(window_env, work, result)["work"]
    inspection = native.begin(inspection["work_id"])["work"]
    with pytest.raises(store.BrowserWorkError):
        submit(window_env, inspection, app_observation(inspection))
    assert store.get_work(inspection["work_id"], inspection["binding"])["result"] is None


@pytest.mark.parametrize("value", [{}, []])
def test_malformed_reuse_status_returns_structured_error(window_env, value):
    work = bind_work()
    result = bind_receipt(work)
    result["evidence"]["reuse_status"] = value
    with pytest.raises(store.BrowserWorkError) as caught:
        submit(window_env, work, result)
    assert caught.value.payload["error"] == "native_capability_required"
    assert caught.value.payload["invalid_fields"] == ["reuse_status"]
    assert store.get_work(work["work_id"], work["binding"])["result"] is None


def app_delivery_work(env):
    env.choose("boss")
    env.active["native_session"] = {
        "id": "synthetic-app-delivery-session", "account_ref": "account-test", "round_id": "round-test",
        "window_reference": APP_REFERENCE, "window_reference_kind": APP_KIND,
        "profile_label": "Test profile", "group_reference": "Synthetic task group",
        "accounts": {"boss": "Synthetic user"},
    }
    env.active["browser_session_id"] = env.active["native_session"]["id"]
    rounds.save_round(copy.deepcopy(env.active))
    response = env.start("boss")
    assert response["work"]["action"] == "inspect_delivery"
    return native.begin(response["work"]["work_id"])["work"]


@pytest.mark.parametrize("field,value", [
    ("profile_label", "Another synthetic profile"),
    ("account_label", "Another synthetic user"),
    ("page_url", "https://untrusted.invalid/"),
    ("observed_at", "2000-01-01T00:00:00+00:00"),
])
def test_app_context_keeps_existing_identity_url_and_freshness_guards(window_env, field, value):
    work = app_delivery_work(window_env)
    result = app_observation(work)
    result["evidence"][field] = value
    before = copy.deepcopy(rounds.ensure_current_round())
    with pytest.raises(store.BrowserWorkError):
        submit(window_env, work, result)
    assert store.get_work(work["work_id"], work["binding"])["result"] is None
    assert rounds.ensure_current_round() == before


def test_issued_side_effect_requires_current_context_and_pre_action_contract(window_env):
    inspect = app_delivery_work(window_env)
    response = submit(window_env, inspect, app_observation(inspect))
    work = native.begin(response["work"]["work_id"])["work"]
    assert work["side_effect"] is True and work["action"] == "open_communication"
    contract = work["task"]["window_context_contract"]
    assert contract["before_every_ui_action"] is True
    assert APP_KIND in contract["supported_reference_kinds"]
    assert contract["missing_window_id_is_missing_capability"] is False
    assert contract["examples_are_evidence"] is False
    assert work["task"]["result_schema"]["evidence_common"]["window_reference_kind"] == APP_KIND
    assert "window_context" in work["task"]["result_schema"]["evidence_common"]
    result = app_observation(work)
    result["evidence"].pop("window_context")
    with pytest.raises(store.BrowserWorkError):
        submit(window_env, work, result)
    assert store.get_work(work["work_id"], work["binding"])["result"] is None
    with pytest.raises(store.BrowserWorkError) as caught:
        native.cancel(work["work_id"], confirmed=True)
    assert caught.value.payload["error"] == "browser_work_reconciliation_required"


@pytest.fixture
def app_recovery_env(tmp_path, monkeypatch):
    from tests.test_native_discovery import env as discovery_fixture
    from tests.test_native_recovery import recovery_env as recovery_fixture

    discovery = discovery_fixture.__wrapped__(tmp_path, monkeypatch)
    return recovery_fixture.__wrapped__(discovery, monkeypatch)


def test_confirmed_readonly_recovery_can_change_scope_and_preserve_logical_session(app_recovery_env):
    r = app_recovery_env
    old_session_id = r.original_session["id"]
    recovery = r.native.recover(r.source["work_id"], confirmed=True)["work"]
    recovery = r.native.begin(recovery["work_id"])["work"]
    observed = r.observed(recovery, recover=True)
    observed["evidence"].update(window_reference=APP_REFERENCE, window_reference_kind=APP_KIND,
                                window_context=context())
    response = r.submit(recovery, observed)
    session = r.env.active["native_session"]
    assert session["id"] == old_session_id
    assert session["window_reference_kind"] == APP_KIND
    assert session["window_reference"] == APP_REFERENCE
    page = response["work"]
    assert page["action"] == "collect_search_page" and page["task"]["page"] == 2
    assert page["binding"]["session_id"] == old_session_id
    assert r.pending_path.read_bytes() == r.pending_bytes
    variants = page["task"]["result_examples"]
    assert set(variants) == {"results", "last_page", "no_results"}
    for result in [page["task"]["result_example"], *variants.values()]:
        assert result["evidence"]["window_reference_kind"] == APP_KIND
        assert result["evidence"]["window_context"]["app_reference"] == APP_REFERENCE
    assert "window_context" not in page["task"]["pause_result_example"]["evidence"]


@pytest.mark.parametrize("variant", [None, "native_window", APP_KIND])
def test_recovery_presentation_has_complete_typed_success_examples(app_recovery_env, variant):
    r = app_recovery_env
    work = r.native.recover(r.source["work_id"], confirmed=True)["work"]
    work = r.native.begin(work["work_id"])["work"]
    before = r.ledger.get_work(work["work_id"], work["binding"])
    presented = r.native.present(before)["work"]
    task = presented["task"]
    if variant is None:
        example = copy.deepcopy(task["result_example"])
    else:
        assert set(task.get("result_examples", {})) == {"native_window", APP_KIND}
        example = copy.deepcopy(task["result_examples"][variant])
    evidence = example["evidence"]
    required = {
        "source", "observed_at", "observation", "native_computer_use_available", "browser",
        "window_reference", "window_reference_kind", "profile_label", "account_label",
        "group_reference", "login_state", "account_navigation", "resume_or_activity", "page_url",
    }
    assert required <= evidence.keys()
    assert required <= task["result_schema"]["evidence"].keys()
    assert example["nonce"] == work["nonce"] and example["binding"] == work["binding"]
    assert example["outcome"] == "success"
    assert evidence["native_computer_use_available"] is True
    assert evidence["browser"] == "chrome" and evidence["login_state"] == "authenticated"
    assert evidence["account_navigation"] is True and evidence["resume_or_activity"] is True
    assert evidence["profile_label"] == task["expected_profile_label"]
    assert evidence["account_label"] == task["expected_account_label"]
    assert evidence["window_reference_kind"] == (APP_KIND if variant == APP_KIND else "native_window_id")
    assert all(isinstance(evidence[key], str) and evidence[key].strip()
               for key in ("window_reference", "group_reference", "page_url"))
    if variant == APP_KIND:
        assert evidence["window_context"]["app_reference"] == evidence["window_reference"]
        assert evidence["window_context"]["selection_verified"] is True
        assert {"app_reference", "window_title", "selection_verified", "selection_evidence"} <= (
            task["result_schema"]["evidence"]["window_context"].keys())
        evidence.update(window_reference=APP_REFERENCE, window_context=context())
    else:
        assert "window_context" not in evidence
        evidence["window_reference"] = "synthetic-recovered-window-42"

    # Replace only observation placeholders with synthetic facts. Required
    # capability, identity and login proof must already exist in the example.
    evidence.update(observed_at=datetime.now(timezone.utc).isoformat(),
                    observation="Synthetic current native account verification",
                    group_reference="synthetic-recovered-task-tab",
                    page_url="https://www.zhipin.com/web/geek/job")
    checked = r.native._common_evidence(before, example)
    r.native._validate_session_recovery(before, example, checked)
    assert r.ledger.get_work(work["work_id"], work["binding"]) == before
    assert "window_context" not in task["pause_result_example"]["evidence"]


@pytest.fixture
def host_window_collection_env(app_recovery_env):
    """Reach a fresh page through the real, confirmed recovery protocol."""
    r = app_recovery_env
    recovery = r.native.recover(r.source["work_id"], confirmed=True)["work"]
    recovery = r.native.begin(recovery["work_id"])["work"]
    r.host_page = r.submit(recovery, r.observed(recovery, recover=True))["work"]
    assert r.host_page["action"] == "collect_search_page"
    assert r.host_page["observation_attempts"] == 0
    return r


def host_window_pause(r, work, issue="window_unavailable"):
    observed = r.observed(work)
    observed.update(outcome="uncertain", requires_user_action=True, reason="permission_required")
    observed["evidence"] = {key: value for key, value in observed["evidence"].items()
                            if key in {"source", "observed_at", "observation", "window_reference", "profile_label"}}
    observed["evidence"].update(host_window_issue=issue,
        observation="Synthetic native window action is unavailable; no current page or job identity claimed.")
    return observed


@pytest.mark.parametrize("issue", ["window_unavailable", "ax_visual_mismatch"])
def test_host_window_pause_preserves_page_and_resumes_with_same_nonce(host_window_collection_env, issue):
    from tests.test_native_discovery import receipt

    r = host_window_collection_env
    work = r.native.begin(r.host_page["work_id"])["work"]
    session_before = copy.deepcopy(r.env.active["native_session"])
    paused = r.submit(work, host_window_pause(r, work, issue))
    assert paused["requires_user_action"] is True
    assert "前置" in paused["user_prompt"]
    assert "一次" in paused["user_prompt"]
    assert "host_window_issue" in paused["work"]["task"]["pause_result_schema"]["evidence_optional"]
    assert paused["work"]["observation_attempts"] == 1
    assert paused["work"]["nonce"] == work["nonce"]
    assert r.pending_path.read_bytes() == r.pending_bytes
    assert r.env.active["native_session"] == session_before
    assert not paused.get("recovery_command")

    # The user foregrounding a window is not simulated as a grant. Only the
    # existing protocol can issue the second permitted observation.
    resumed = r.native.begin(work["work_id"])["work"]
    assert resumed["nonce"] == work["nonce"]
    assert resumed["observation_attempts"] == 2
    complete = receipt(resumed, final=True)
    complete["evidence"].update(r.observed(resumed)["evidence"])
    response = r.submit(resumed, complete)
    assert response["ok"] is True
    assert r.ledger.get_work(work["work_id"], work["binding"])["state"] == "closed"
    assert len(r.env.starts) == 1 and len(r.env.decisions) == 1


def test_exhausted_host_pause_keeps_explicit_recovery_and_continues_preserved_page(host_window_collection_env):
    r = host_window_collection_env
    work = r.host_page
    for attempt in range(3):
        work = r.native.begin(work["work_id"])["work"]
        paused = r.submit(work, host_window_pause(r, work))
        assert paused["work"]["observation_attempts"] == attempt + 1
    prompt = paused["user_prompt"]
    assert "前置" in prompt and "不会恢复观察额度" in prompt
    assert "无需重新登录、取消任务" not in prompt
    assert "恢复会取消当前失败任务" in prompt
    assert "确认" in prompt
    assert paused["work"]["allowed_mode"] == "reconcile_only"
    assert paused["recovery_requires_confirmation"] is True
    assert "work recover" in paused["recovery_command"]
    assert paused["next_suggested"] == paused["recovery_command"]
    assert "work submit" in paused["completion_command"]
    assert r.ledger.get_work(work["work_id"], work["binding"])["state"] == "reconcile_only"
    with pytest.raises(r.ledger.BrowserWorkError) as limited:
        r.native.begin(work["work_id"])
    assert limited.value.payload["error"] == "browser_work_observation_limit"
    with pytest.raises(r.ledger.BrowserWorkError) as confirmation:
        r.native.recover(work["work_id"], confirmed=False)
    assert confirmation.value.payload["error"] == "user_confirmation_required"
    assert r.ledger.get_work(work["work_id"], work["binding"])["observation_attempts"] == 3

    recovery = r.native.recover(work["work_id"], confirmed=True)["work"]
    recovery = r.native.begin(recovery["work_id"])["work"]
    next_page = r.submit(recovery, r.observed(recovery, recover=True))["work"]
    assert next_page["action"] == "collect_search_page"
    assert next_page["task"]["page"] == work["task"]["page"] == 2
    assert next_page["work_id"] != work["work_id"]
    assert next_page["binding"] == work["binding"]
    assert next_page["observation_attempts"] == 0
    previous = r.ledger.get_work(work["work_id"], work["binding"])
    assert previous["state"] == "closed" and previous["observation_attempts"] == 3
    assert previous["nonce"] == work["nonce"]
    assert r.pending_path.read_bytes() == r.pending_bytes
    assert len(r.env.starts) == 1 and not r.env.decisions and not r.env.renewals


def test_real_job_identity_block_does_not_become_foreground_request(app_recovery_env):
    r = app_recovery_env
    blocked = r.native.present(r.ledger.get_work(r.source["work_id"], r.source["binding"]))
    assert blocked["error"] == "native_job_identity_unknown"
    assert blocked["requires_technical_recovery"] is True
    assert "前置" not in blocked["user_prompt"]
    assert blocked["work"]["allowed_mode"] == "reconcile_only"


@pytest.mark.parametrize("issue", ["job_identity_unknown", {}, [], None])
def test_host_pause_marker_must_be_typed_host_issue(host_window_collection_env, issue):
    r = host_window_collection_env
    work = r.native.begin(r.host_page["work_id"])["work"]
    # The fixture loads a separate real ledger module; window validators use
    # the ordinary module's error class, exactly as production imports do.
    with pytest.raises(store.BrowserWorkError) as caught:
        r.submit(work, host_window_pause(r, work, issue))
    assert caught.value.payload["error"] == "native_host_window_pause_invalid"
    assert r.ledger.get_work(work["work_id"], work["binding"])["result"] is None


def test_host_window_issue_cannot_reclassify_technical_job_error(host_window_collection_env):
    r = host_window_collection_env
    work = r.native.begin(r.host_page["work_id"])["work"]
    blocked = host_window_pause(r, work)
    blocked.update(requires_user_action=False, requires_technical_recovery=True, reason="job_identity_unknown")
    with pytest.raises(store.BrowserWorkError) as caught:
        r.submit(work, blocked)
    assert caught.value.payload["error"] == "native_host_window_pause_invalid"
    assert r.ledger.get_work(work["work_id"], work["binding"])["result"] is None


def test_untagged_permission_pause_keeps_actual_permission_handoff(window_env):
    work = begin_inspection(window_env)
    paused = app_observation(work)
    paused.update(outcome="uncertain", requires_user_action=True, reason="permission_required")
    paused["evidence"].pop("window_context")
    response = submit(window_env, work, paused)
    assert "Computer Use 权限" in response["user_prompt"]
    assert "前置" not in response["user_prompt"]


def test_foregrounding_does_not_reissue_side_effect_intent(window_env):
    inspect = app_delivery_work(window_env)
    action = submit(window_env, inspect, app_observation(inspect))["work"]
    work = native.begin(action["work_id"])["work"]
    paused = app_observation(work)
    paused.update(outcome="uncertain", requires_user_action=True, reason="permission_required")
    paused["evidence"].pop("window_context")
    paused["evidence"]["host_window_issue"] = "window_unavailable"
    response = submit(window_env, work, paused)
    assert "前置" in response["user_prompt"]
    same = native.begin(work["work_id"])["work"]
    assert same["nonce"] == work["nonce"] and same["allowed_mode"] == "reconcile_only"
    assert same["execution_permitted"] is False
    with pytest.raises(store.BrowserWorkError) as recovery:
        native.recover(work["work_id"], confirmed=True)
    assert recovery.value.payload["error"] == "native_recovery_not_allowed"
