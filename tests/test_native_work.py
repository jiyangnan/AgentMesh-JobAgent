"""Whole native delivery loops, using synthetic UI receipts and temporary state."""
import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from jobagent import cli
from jobagent.application import delivery, native_work as native
from jobagent.infra import browser_work as store, rounds, state


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name, value in {"APP_DIR": tmp_path, "STATE_DIR": tmp_path / "state",
                        "ROUNDS_DIR": tmp_path / "state" / "rounds"}.items():
        monkeypatch.setattr(state, name, value)
    monkeypatch.setattr(native, "current_account_ref", lambda: "account-test")
    monkeypatch.setattr(native, "MIN_ACTION_INTERVAL_SECONDS", 0)
    monkeypatch.setattr("jobagent.drivers.boss.create_driver", lambda *a, **k: pytest.fail("CDP driver invoked"))
    source = tmp_path / "decision.review.json"
    active = {"schema_version": rounds.ROUND_SCHEMA_VERSION, "round_id": "round-test", "status": "active",
        "platform_order": list(native.PLATFORMS), "intent": {"target_cities": ["郑州"], "target_roles": ["数据分析师"]},
        "browser_executor": "codex_native", "browser_session_id": "native-test",
        "native_session": {"id": "native-test", "account_ref": "account-test", "round_id": "round-test",
            "window_reference": "chrome-window-1", "profile_label": "Test profile", "group_reference": "Job Agent",
            "accounts": {p: "Synthetic user" for p in native.PLATFORMS}},
        "platforms": {p: {"status": "pending"} for p in native.PLATFORMS}}
    jobs = []
    reviewed = {"source_path": str(source), "discover_id": "discover-test", "send_candidates": jobs}
    loads = []
    def load(platform, path, *, preview_id, authorization_id):
        loads.append((platform, path, preview_id, authorization_id))
        if preview_id != "preview-test" or authorization_id != "auth-test":
            raise ValueError("authorization required")
        return copy.deepcopy(reviewed)
    monkeypatch.setattr(delivery, "_load_reviewed", load)
    def choose(platform, count=1, session=True):
        index = native.PLATFORMS.index(platform)
        for i, p in enumerate(native.PLATFORMS):
            active["platforms"][p]["status"] = "skipped_this_round" if i < index else "reviewed" if i == index else "pending"
        if not session:
            active["native_session"] = None
        routes = {"boss": "https://www.zhipin.com/job_detail/{id}.html",
            "liepin": "https://www.liepin.com/job/{id}.shtml",
            "zhilian": "https://www.zhaopin.com/jobdetail/{id}.htm",
            "51job": "https://jobs.51job.com/zhengzhou/{id}.html"}
        jobs[:] = [{"id": f"job{i}", "url": routes[platform].format(id=f"job{i}"), "title": "数据分析师",
                    "company": f"Synthetic Company {i}", "cloud_greeting": "您好，我希望进一步了解这个数据分析岗位。"} for i in range(count)]
        rounds.save_round(copy.deepcopy(active))
    def start(platform, **kwargs):
        return native.start_delivery(platform, input_path=str(source), preview_id="preview-test",
                                     authorization_id="auth-test", **kwargs)
    return SimpleNamespace(choose=choose, start=start, active=active, reviewed=reviewed, jobs=jobs,
                           path=tmp_path, loads=loads)


def observation(work, **changes):
    job = work["task"].get("job", {})
    result = {"receipt_id": work["work_id"] + "-receipt", "nonce": work["nonce"],
        "binding": work["binding"], "outcome": "success", "evidence": {
            "source": "host_ui_observation", "observed_at": datetime.now(timezone.utc).isoformat(),
            "observation": "Synthetic visible official job and account receipt",
            "window_reference": "chrome-window-1", "profile_label": "Test profile", "account_label": "Synthetic user",
            "page_url": job.get("url", native.ENTRY_URLS[work["binding"]["platform"]]),
            "job_url": job.get("url"), "job_id": job.get("id"), "title": job.get("title"), "company": job.get("company"),
            "history_checked": True, "receipt_checked": True, "login_state": "authenticated",
            "resume_state": "not_sent", "resume_reference": "Synthetic online resume", "communication_state": "not_open",
            "conversation_job_verified": True, "message_state": "not_sent"}}
    result["evidence"].update(changes)
    if work["task"].get("inspection_phase") == "after_communication":
        result["evidence"]["communication_state"] = "open"
    if work["task"].get("conversation_surface") == "boss_message_center":
        result["evidence"]["page_url"] = "https://www.zhipin.com/web/geek/chat?ka=header-message"
    if work["action"] == "submit_resume":
        result["evidence"].update(resume_state="sent", receipt_kind="application_history")
        if "submission_mode" in work["task"]:
            result["evidence"].update(submission_mode=work["task"]["submission_mode"],
                attachment_reference=work["task"]["attachment_reference"], attachment_selection_verified=True)
    elif work["action"] == "prepare_resume":
        result["evidence"].update(communication_state="open", submission_attempted=False,
            dialog_cancellable=True, options_complete=True, submission_mode="online_only", attachment_options=[])
    elif work["action"] == "open_communication":
        result["evidence"].update(communication_state="open", default_greeting_observed=True)
    elif work["action"] == "send_greeting":
        result["evidence"].update(outgoing_text=job["cloud_greeting"], message_state="sent")
    result["evidence"].update(changes)
    return result


def submit(env, work, result=None):
    result = result or observation(work)
    path = env.path / "result.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    return native.submit(work["work_id"], str(path))


@pytest.mark.parametrize("platform,actions", [
    ("boss", ["inspect_delivery", "open_communication", "inspect_delivery", "send_greeting"]),
    ("liepin", ["inspect_delivery", "open_communication", "inspect_delivery", "prepare_resume", "submit_resume", "send_greeting"]),
    ("zhilian", ["inspect_delivery", "submit_resume"]),
    ("51job", ["inspect_delivery", "submit_resume"]),
])
def test_complete_serial_platform_chain(env, platform, actions):
    env.choose(platform)
    response = env.start(platform)
    actual = []
    while response.get("work"):
        pending = response["work"]
        assert pending["allowed_mode"] == "observe"
        from jobagent.infra.workflow_protocol import with_contract
        assert with_contract(response)["agent_action"]["type"] == "run_cli"
        issued = native.begin(pending["work_id"])
        assert with_contract(issued)["agent_action"]["type"] == "native_work"
        assert with_contract(issued)["agent_action"]["allowed_mode"] == issued["work"]["allowed_mode"]
        work = issued["work"]
        actual.append(work["action"])
        assert work["task"]["result_example"]["nonce"] == work["nonce"]
        if work["side_effect"]:
            assert work["allowed_mode"] == "execute_once"
        response = submit(env, work)
    assert actual == actions
    assert response["completion_state"] == "completed"
    assert response["summary"]["greeting_sent"] == int(platform in {"boss", "liepin"})
    assert response["summary"]["resume_submitted"] == int(platform != "boss")
    assert native.next_work()["next_suggested"] == f"jobagent {platform} audit"
    audit = native.audit(platform)
    assert audit["workflow"]["platforms"][platform]["status"] == "completed"
    assert store.has_open() is False


def test_authorization_checked_before_any_native_binding(env):
    env.choose("boss", session=False)
    with pytest.raises(ValueError, match="authorization"):
        native.start_delivery("boss", input_path=None, preview_id=None, authorization_id=None)
    assert store.has_open() is False


def test_resume_does_not_count_as_greeting_or_cause_duplicate_communication(env):
    env.choose("liepin")
    response = env.start("liepin")
    work = native.begin(response["work"]["work_id"])["work"]
    response = submit(env, work)
    work = native.begin(response["work"]["work_id"])["work"]
    response = submit(env, work, observation(work, communication_state="open"))
    assert response["work"]["task"]["inspection_phase"] == "after_communication"
    work = native.begin(response["work"]["work_id"])["work"]
    response = submit(env, work, observation(work, resume_state="sent", receipt_kind="resume_card"))
    assert response["work"]["action"] == "send_greeting"
    assert native.audit("liepin", complete=False)["summary"]["greeting_sent"] == 0


def test_default_greeting_cannot_close_custom_message(env):
    env.choose("boss")
    response = env.start("boss")
    for _ in range(3):
        work = native.begin(response["work"]["work_id"])["work"]
        response = submit(env, work)
    work = native.begin(response["work"]["work_id"])["work"]
    with pytest.raises(store.BrowserWorkError, match="exact approved"):
        submit(env, work, observation(work, outgoing_text="系统默认招呼语"))
    assert store.get_work(work["work_id"], native._binding())["state"] == "intent_recorded"


def test_interrupted_begin_never_grants_second_action(env):
    env.choose("51job")
    response = env.start("51job")
    work = native.begin(response["work"]["work_id"])["work"]
    response = submit(env, work)
    first = native.begin(response["work"]["work_id"])["work"]
    second = native.begin(first["work_id"])["work"]
    assert first["allowed_mode"] == "execute_once"
    assert second["allowed_mode"] == "reconcile_only"
    result = observation(second)
    result.update(outcome="unresolved")
    response = submit(env, second, result)
    assert response["completion_state"] == "completed_with_unresolved"
    assert response["summary"]["unresolved"] == 1
    assert response["summary"]["resume_submitted"] == 0
    assert native.next_work()["next_suggested"] == "jobagent 51job audit"


def test_uncertain_receipt_then_delayed_success_has_no_new_permission(env):
    env.choose("51job")
    response = env.start("51job")
    work = native.begin(response["work"]["work_id"])["work"]
    response = submit(env, work)
    work = native.begin(response["work"]["work_id"])["work"]
    result = observation(work)
    result.update(outcome="uncertain", requires_user_action=True, reason="verification_required")
    paused = submit(env, work, result)
    assert paused["requires_user_action"]
    assert "验证好了" in paused["user_prompt"] and "https://" in paused["user_prompt"]
    resumed = native.begin(work["work_id"])["work"]
    assert resumed["allowed_mode"] == "reconcile_only"
    success = observation(resumed)
    success["receipt_id"] += "-late"
    completed = submit(env, resumed, success)
    assert completed["summary"]["resume_submitted"] == 1


@pytest.mark.parametrize("change,error", [
    ({"window_reference": "other-window"}, "native_browser_changed"),
    ({"profile_label": "other-profile"}, "native_browser_changed"),
    ({"account_label": "other-user"}, "native_platform_account_changed"),
    ({"page_url": "https://www.zhipin.com.evil.example/"}, "native_page_untrusted"),
    ({"page_url": "https://www.zhipin.com:444/"}, "native_page_untrusted"),
    ({"job_id": "other-job"}, "native_job_binding_mismatch"),
    ({"company": "Other Company"}, "native_job_identity_mismatch"),
    ({"observed_at": "2020-01-01T00:00:00Z"}, "native_observation_expired"),
    ({"observed_at": "2026-01-01T00:00:00"}, "native_observation_expired"),
])
def test_receipt_bindings_fail_closed(env, change, error):
    env.choose("boss")
    response = env.start("boss")
    work = native.begin(response["work"]["work_id"])["work"]
    with pytest.raises(store.BrowserWorkError) as caught:
        submit(env, work, observation(work, **change))
    assert caught.value.payload["error"] == error
    assert store.has_open()


def test_resume_identity_cannot_change_after_preflight(env):
    env.choose("51job")
    response = env.start("51job")
    response = submit(env, native.begin(response["work"]["work_id"])["work"])
    work = native.begin(response["work"]["work_id"])["work"]
    with pytest.raises(store.BrowserWorkError, match="preflight"):
        submit(env, work, observation(work, resume_reference="Unapproved replacement"))


def test_existing_history_prevents_new_delivery(env):
    env.choose("51job")
    (state.STATE_DIR / "job51_audit_log.json").write_text(json.dumps([
        {"job_url": "https://we.51job.com/pc/search", "evidence": {"job_id": "job0"}, "status": "unresolved"}]))
    response = env.start("51job")
    assert response["work"]["task"]["historical_action_requires_reconciliation"] is True
    work = native.begin(response["work"]["work_id"])["work"]
    with pytest.raises(store.BrowserWorkError, match="previous action"):
        submit(env, work)
    assert not any(w["side_effect"] for w in store.list_account_work("account-test"))


@pytest.mark.parametrize("command", ["boss login --check", "liepin discover", "browser diagnose --platform boss"])
def test_cli_routes_browser_entrypoints_to_native(env, monkeypatch, command):
    platform = "liepin" if command.startswith("liepin") else "boss"
    env.choose(platform, session=False)
    args = cli.build_parser().parse_args(command.split())
    response = cli._dispatch(args)
    assert response["event"] == "browser_work_required"
    assert response["work"]["action"] == "bind_session"
    assert response["host_contract"]["protocol_version"] == 1


@pytest.mark.parametrize("command", ["round start", "round skip --platform boss --confirm-skip", "account switch --new-state", "init --key synthetic", "resume analyze --file synthetic.pdf"])
def test_ready_work_blocks_context_change(env, command):
    env.choose("boss")
    env.start("boss")
    assert not store.has_inflight() and store.has_open()
    response = cli._dispatch(cli.build_parser().parse_args(command.split()))
    assert response["error"] == "native_work_context_locked"


def test_explicit_cancel_only_safe_work(env):
    env.choose("boss")
    response = env.start("boss")
    with pytest.raises(store.BrowserWorkError):
        native.cancel(response["work"]["work_id"], confirmed=False)
    result = native.cancel(response["work"]["work_id"], confirmed=True)
    assert result["event"] == "browser_work_cancelled" and not store.has_open()


def test_issued_delivery_cannot_be_cancelled(env):
    env.choose("51job")
    response = env.start("51job")
    response = submit(env, native.begin(response["work"]["work_id"])["work"])
    work = native.begin(response["work"]["work_id"])["work"]
    with pytest.raises(store.BrowserWorkError, match="cannot be cancelled"):
        native.cancel(work["work_id"], confirmed=True)


def test_update_does_not_replace_inflight_host_work(env, monkeypatch):
    env.choose("boss")
    response = env.start("boss")
    native.begin(response["work"]["work_id"])
    monkeypatch.delenv("JOBAGENT_SKIP_UPDATE", raising=False)
    monkeypatch.setattr("jobagent.infra.release_update.maybe_auto_update", lambda **k: pytest.fail("updated during host work"))
    args = cli.build_parser().parse_args(["work", "next"])
    cli._maybe_update(args)
    assert args._native_update_deferred is True


def test_completed_observation_requires_serial_pacing(env, monkeypatch):
    env.choose("boss")
    response = env.start("boss")
    response = submit(env, native.begin(response["work"]["work_id"])["work"])
    monkeypatch.setattr(native, "MIN_ACTION_INTERVAL_SECONDS", 2)
    waited = native.begin(response["work"]["work_id"])
    assert waited["event"] == "browser_work_wait" and waited["wait_seconds"] > 0
    assert not waited["requires_user_action"]
    assert store.get_work(response["work"]["work_id"], native._binding())["state"] == "ready"


def test_unknown_visible_greeting_does_not_get_send_permission(env):
    env.choose("boss")
    response = env.start("boss")
    work = native.begin(response["work"]["work_id"])["work"]
    with pytest.raises(store.BrowserWorkError, match="unknown"):
        submit(env, work, observation(work, communication_state="open", message_state="unknown",
                                      existing_outgoing_text=env.jobs[0]["cloud_greeting"]))
    assert not any(w["side_effect"] for w in store.list_account_work("account-test"))


@pytest.mark.parametrize("value", ["false", "true", 1, {}, []])
def test_evidence_booleans_are_not_truthiness_flags(env, value):
    env.choose("boss")
    response = env.start("boss")
    work = native.begin(response["work"]["work_id"])["work"]
    with pytest.raises(store.BrowserWorkError):
        submit(env, work, observation(work, history_checked=value))


def test_cancelled_delivery_next_is_safe_not_keyerror(env):
    env.choose("boss")
    response = env.start("boss")
    native.cancel(response["work"]["work_id"], confirmed=True)
    assert native.next_work()["event"] == "delivery_cancelled"


def test_51job_query_ids_are_distinct_in_legacy_dedupe(env):
    env.choose("51job")
    (state.STATE_DIR / "job51_audit_log.json").write_text(json.dumps([
        {"job_url": "https://we.51job.com/pc/jobdetail?jobId=old", "evidence": {"job_id": "old"}}]))
    assert not native._legacy_history("51job", "https://we.51job.com/pc/jobdetail?jobId=new", "new")
    assert native._legacy_history("51job", "https://we.51job.com/pc/jobdetail?jobId=old", "old")


def test_capability_pause_schema_is_self_contained(env):
    env.choose("boss", session=False)
    response = env.start("boss")
    work = native.begin(response["work"]["work_id"])["work"]
    sample = work["task"]["pause_result_example"]
    sample["receipt_id"] = "capability-pause"
    sample["reason"] = "permission_required"
    sample["evidence"]["observed_at"] = datetime.now(timezone.utc).isoformat()
    sample["evidence"]["observation"] = "Native tool not available in synthetic host"
    paused = submit(env, work, sample)
    assert paused["requires_user_action"] and not state.load_json(state.current_round_path())["native_session"]


def _pause_receipt(work, reason="session_unknown", attempt=0):
    return {"receipt_id": f"{work['work_id']}-pause-{attempt}", "nonce": work["nonce"], "binding": work["binding"],
            "outcome": "uncertain", "requires_user_action": True, "reason": reason,
            "evidence": {"source": "host_ui_observation",
                         "observed_at": datetime.now(timezone.utc).isoformat(),
                         "observation": "Synthetic ambiguous Chrome windows; only a new tab identified"}}


def _bind_receipt(work):
    return {"receipt_id": work["work_id"] + "-final", "nonce": work["nonce"], "binding": work["binding"],
            "outcome": "success",
            "evidence": {"source": "host_ui_observation",
                         "observed_at": datetime.now(timezone.utc).isoformat(),
                         "observation": "Single synthetic Chrome window stably identified",
                         "native_computer_use_available": True, "browser": "chrome",
                         "window_reference": "chrome-window-9", "profile_label": "Profile 1",
                         "group_reference": "Job Agent", "reuse_status": "created_no_existing"}}


def test_observation_limit_recovery_still_accepts_final_receipt(env):
    env.choose("boss", session=False)
    response = native.request_login("boss")
    work_id = response["work"]["work_id"]
    assert response["work"]["action"] == "bind_session"
    for attempt in range(store.MAX_OBSERVATION_ATTEMPTS):
        begun = native.begin(work_id)["work"]
        submit(env, begun, _pause_receipt(begun, attempt=attempt))
    with pytest.raises(store.BrowserWorkError) as caught:
        native.begin(work_id)
    payload = caught.value.payload
    assert payload["error"] == "browser_work_observation_limit"
    assert payload["work_id"] == work_id
    assert payload["next_suggested"].startswith("jobagent work submit")
    assert payload["cancel_command"].startswith("jobagent work cancel")
    status = native.request_login("boss")
    assert status["next_suggested"].startswith("jobagent work submit")
    assert status["work"]["allowed_mode"] == "reconcile_only"
    assert status["recovery"]["status"] == "receipt_only"
    assert "after_confirmation_argv" not in status["recovery"]
    assert native.status()["recovery"] == status["recovery"]
    assert "无需关闭其他窗口" in status["user_prompt"]
    submit(env, status["work"], _bind_receipt(status["work"]))
    assert rounds.ensure_current_round()["native_session"]["window_reference"] == "chrome-window-9"


def test_explicit_login_clears_cancelled_bind_marker(env):
    env.choose("boss", session=False)
    response = native.request_login("boss")
    native.cancel(response["work"]["work_id"], confirmed=True)
    cancelled = native.next_work()
    assert cancelled["event"] == "browser_work_cancelled"
    assert "jobagent boss login" in cancelled["user_prompt"]
    resumed = native.request_login("boss")
    assert resumed["event"] == "browser_work_required"
    assert resumed["work"]["action"] == "bind_session"
    assert native.next_work()["event"] == "browser_work_required"


@pytest.mark.parametrize("reason", native.TECHNICAL_BLOCK_REASONS)
def test_technical_block_preserves_work_without_false_user_handoff(env, reason):
    env.choose("zhilian")
    work = native.begin(env.start("zhilian")["work"]["work_id"])["work"]
    result = copy.deepcopy(work["task"]["blocked_result_example"])
    result.update(receipt_id="technical-block", reason=reason)
    result["evidence"].update(observed_at=datetime.now(timezone.utc).isoformat(),
        observation="Selected synthetic job title and visible detail link disagree")
    paused = submit(env, work, result)
    assert paused["requires_technical_recovery"] is True
    assert paused["requires_user_action"] is False
    assert paused["error"] == f"native_{reason}"
    assert "user_prompt" not in paused
    assert paused["retryable"] is False
    assert paused["next_suggested"] == "jobagent work status"
    assert paused["work"]["state"] == "reconcile_only"
    assert paused["work"]["binding"] == work["binding"]
    assert native.next_work()["requires_technical_recovery"] is True
    before = store.get_work(work["work_id"], work["binding"])
    assert native.status()["requires_technical_recovery"] is True
    assert store.get_work(work["work_id"], work["binding"]) == before
    # A read-only task can resume under the existing bounded observation budget.
    resumed = native.begin(work["work_id"])
    assert resumed["work"]["allowed_mode"] == "observe"
    assert resumed["work"]["nonce"] == work["nonce"]
    assert resumed["work"]["execution_permitted"] is True
    assert not resumed.get("requires_technical_recovery")


def test_technical_recovery_never_reissues_delivery_permission(env):
    env.choose("zhilian")
    inspected = native.begin(env.start("zhilian")["work"]["work_id"])["work"]
    pending = submit(env, inspected)
    work = native.begin(pending["work"]["work_id"])["work"]
    assert work["action"] == "submit_resume"
    assert work["allowed_mode"] == "execute_once"
    result = copy.deepcopy(work["task"]["blocked_result_example"])
    result["receipt_id"] = "identity-block"
    result["evidence"].update(observed_at=datetime.now(timezone.utc).isoformat(),
        observation="Synthetic detail changed; no verified application receipt")
    submit(env, work, result)
    resumed = native.begin(work["work_id"])["work"]
    assert resumed["allowed_mode"] == "reconcile_only"
    assert not resumed["execution_permitted"]
    assert native.audit("zhilian", complete=False)["summary"]["resume_submitted"] == 0


@pytest.mark.parametrize("updates", [
    {"reason": "login_required"}, {"requires_user_action": True},
    {"requires_technical_recovery": "true"}, {"reason": "invented"},
])
def test_technical_block_rejects_ambiguous_or_user_challenge_claims(env, updates):
    env.choose("boss", session=False)
    work = native.begin(env.start("boss")["work"]["work_id"])["work"]
    result = copy.deepcopy(work["task"]["blocked_result_example"])
    result.update(updates)
    with pytest.raises(store.BrowserWorkError) as caught:
        submit(env, work, result)
    assert caught.value.payload["error"] == "native_block_reason_invalid"


def test_pause_prompt_does_not_forward_untrusted_url(env):
    env.choose("boss", session=False)
    work = native.begin(env.start("boss")["work"]["work_id"])["work"]
    result = _pause_receipt(work, reason="login_required")
    result["evidence"]["page_url"] = "https://untrusted.invalid/login"
    response = submit(env, work, result)
    assert "untrusted.invalid" not in response["user_prompt"]
    assert native.ENTRY_URLS["boss"] in response["user_prompt"]


def test_host_contract_requires_complete_command_before_advancing(env):
    env.choose("boss")
    response = env.start("boss")
    contract = response["work"]["task"]["command_execution"]
    assert contract["completion_required"] is True
    assert "process/session handle" in contract["running_response"]
    assert "Do not run work next, work begin" in contract["while_running"]
    assert "read-only reconciliation" in contract["lost_response"]
    assert "poll that same process until it exits" in response["host_contract"]["instructions"]


def test_collection_identity_block_does_not_advance_or_decide(env, monkeypatch):
    env.choose("zhilian")
    from jobagent.application import native_discovery
    monkeypatch.setattr(native_discovery, "accept_page", lambda *a: pytest.fail("unverified page accepted"))
    binding = {"account_ref": "account-test", "round_id": "round-test", "platform": "zhilian",
               "session_id": "native-test", "request_id": "preserved-request", "discover_id": "preserved-discover"}
    pending = store.ensure_work(action="collect_search_page", binding=binding,
        key="synthetic-page", task={"query": "数据分析师", "city": "郑州", "page": 1})
    before = rounds.ensure_current_round()
    for attempt in range(store.MAX_OBSERVATION_ATTEMPTS):
        work = native.begin(pending["work_id"])["work"]
        result = copy.deepcopy(work["task"]["blocked_result_example"])
        result.update(receipt_id=f"blocked-{attempt}")
        result["evidence"].update(observed_at=datetime.now(timezone.utc).isoformat(),
            observation="Synthetic selected card differs from pending detail link")
        paused = submit(env, work, result)
        assert paused["work"]["binding"] == binding
        assert paused["requires_technical_recovery"]
    assert rounds.ensure_current_round() == before
    assert paused["work"]["allowed_mode"] == "reconcile_only"
    assert paused["recovery_command"].startswith("jobagent work recover")
    assert paused["recovery_requires_confirmation"] is True
    assert paused["completion_command"].startswith("jobagent work submit")
    assert native.status()["recovery_command"] == paused["recovery_command"]


def test_closed_native_work_never_reissues_host_action(env):
    from jobagent.infra.workflow_protocol import with_contract
    env.choose('zhilian')
    response = env.start('zhilian')
    work_id = response['work']['work_id']
    work = native.begin(work_id)['work']
    submit(env, work)
    # The inspection work is closed; the following work owns any new action.
    closed = store.get_work(work_id, work['binding'])
    assert closed['state'] == 'closed'
    assert with_contract(native.present(closed, execution=True))['agent_action']['type'] != 'native_work'


def boss_message_preflight(env):
    env.choose('boss')
    response = env.start('boss')
    for _ in range(2):
        work = native.begin(response['work']['work_id'])['work']
        response = submit(env, work)
    work = native.begin(response['work']['work_id'])['work']
    assert work['action'] == 'inspect_delivery'
    assert work['task']['inspection_phase'] == 'after_communication'
    return work


def test_boss_message_center_gate_preserves_permission_after_wrong_page(env):
    work = boss_message_preflight(env)
    assert work['side_effect'] is False
    before = copy.deepcopy(store.get_work(work['work_id'], native._binding()))
    with pytest.raises(store.BrowserWorkError) as error:
        submit(env, work, observation(work, page_url=work['task']['job']['url']))
    assert error.value.payload['error'] == 'native_message_center_required'
    assert store.get_work(work['work_id'], native._binding()) == before
    # Resume the same read-only task; only verified main-chat evidence grants send.
    response = submit(env, work)
    send = native.begin(response['work']['work_id'])['work']
    assert send['action'] == 'send_greeting' and send['allowed_mode'] == 'execute_once'
    assert send['task']['conversation_surface'] == 'boss_message_center'
    assert send['binding'] == work['binding']
    with pytest.raises(store.BrowserWorkError) as error:
        submit(env, send, observation(send, page_url=send['task']['job']['url']))
    assert error.value.payload['error'] == 'native_message_center_required'
    # A rejected receipt is never a fresh send permission.
    resumed = native.begin(send['work_id'])['work']
    assert resumed['allowed_mode'] == 'reconcile_only'
    assert 'Read-only reconciliation' in resumed['task']['instruction']
    assert 'send job.cloud_greeting exactly once' not in resumed['task']['instruction']
    # Non-success receipts may faithfully report the actual detail popup.
    result = observation(resumed, page_url=send['task']['job']['url'])
    result.update(outcome='unresolved', receipt_id='boss-unresolved')
    completed = submit(env, resumed, result)
    assert completed['completion_state'] == 'completed_with_unresolved'
    assert completed['summary']['greeting_sent'] == 0
    assert completed['summary']['unresolved'] == 1
    count = len(store.list_account_work('account-test'))
    assert env.start('boss')['summary']['unresolved'] == 1
    assert len(store.list_account_work('account-test')) == count


def test_boss_existing_signed_message_in_center_never_creates_send(env):
    work = boss_message_preflight(env)
    completed = submit(env, work, observation(work,
        existing_outgoing_text=work['task']['job']['cloud_greeting'], message_state='delivered'))
    assert completed['summary']['greeting_sent'] == 1
    assert not any(w['action'] == 'send_greeting' for w in store.list_account_work('account-test'))


@pytest.mark.parametrize('outcome', ['success', 'unresolved', 'inflight'])
def test_boss_older_send_contract_is_not_reissued_or_reopened(env, outcome):
    env.choose('boss')
    response = env.start('boss')
    for _ in range(2):
        work = native.begin(response['work']['work_id'])['work']
        if work['action'] == 'open_communication':
            # Persist the old communication result without scheduling new work.
            result = observation(work)
            native._validate_delivery(work, result, result['evidence'])
            store.submit_work(work['work_id'], native._binding(), result)
        else:
            response = submit(env, work)
    legacy_task = copy.deepcopy(work['task'])
    legacy_task['instruction'] = 'Send the original signed text once.'
    legacy_task.pop('conversation_surface', None)
    legacy_task.pop('inspection_phase', None)
    old = store.ensure_work(action='send_greeting', task=legacy_task, binding=work['binding'], side_effect=True)
    begun = native.begin(old['work_id'])['work']
    assert 'conversation_surface' not in begun['task']
    if outcome == 'inflight':
        shown = env.start('boss')['work']
        assert shown['work_id'] == old['work_id'] and shown['nonce'] == begun['nonce']
        assert shown['allowed_mode'] == 'reconcile_only'
        assert 'Read-only reconciliation' in shown['task']['instruction']
    else:
        result = observation(begun)
        result['outcome'] = outcome
        result['receipt_id'] += '-legacy'
        completed = submit(env, begun, result)
        assert completed['summary']['greeting_sent'] == int(outcome == 'success')
        assert completed['summary']['unresolved'] == int(outcome == 'unresolved')
        env.start('boss')
    all_work = store.list_account_work('account-test')
    assert sum(w['action'] == 'send_greeting' for w in all_work) == 1
    assert not any(w['task'].get('conversation_surface') for w in all_work)
