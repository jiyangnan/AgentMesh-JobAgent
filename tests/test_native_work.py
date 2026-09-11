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
    if work["action"] == "submit_resume":
        result["evidence"].update(resume_state="sent", receipt_kind="application_history")
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
    ("boss", ["inspect_delivery", "open_communication", "send_greeting"]),
    ("liepin", ["inspect_delivery", "submit_resume", "open_communication", "send_greeting"]),
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
        work = native.begin(pending["work_id"])["work"]
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
    assert response["work"]["action"] == "send_greeting"
    assert native.audit("liepin", complete=False)["summary"]["greeting_sent"] == 0


def test_default_greeting_cannot_close_custom_message(env):
    env.choose("boss")
    response = env.start("boss")
    for _ in range(2):
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
    assert "关闭多余的 Chrome 窗口" in status["user_prompt"]
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
