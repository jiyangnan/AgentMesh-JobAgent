"""Public-distribution analytics hooks use persisted native delivery receipts."""
from __future__ import annotations

import builtins
import copy
import json

import pytest

from jobagent.application import native_work as native
from jobagent.infra import account_state, analytics, browser_work as store, state


GREETING = "您好，希望进一步交流这个岗位。"


def work(action, *, side_effect=True, outcome="success", **evidence):
    return {"action": action, "side_effect": side_effect, "state": "closed",
            "task": {"job": {"cloud_greeting": GREETING}, "delivery_source": {"preview_id": "preview-test"}},
            "result": {"outcome": outcome, "evidence": evidence}}


@pytest.mark.parametrize("platform,records", [
    ("boss", [work("send_greeting")]),
    ("liepin", [work("submit_resume", resume_state="sent"), work("send_greeting")]),
    ("zhilian", [work("submit_resume", resume_state="sent")]),
    ("51job", [work("submit_resume", resume_state="sent")]),
    ("liepin", [work("inspect_delivery", side_effect=False, resume_state="sent"), work("send_greeting")]),
])
def test_only_complete_platform_delivery_records_fact(monkeypatch, platform, records):
    calls = []
    monkeypatch.setattr(analytics, "record_delivery_verified", lambda value: calls.append(value))
    native._record_completed_native_delivery_fact(platform, {"job-test": records}, completed=True)
    assert calls == [platform]


@pytest.mark.parametrize("platform,records,completed", [
    ("boss", [work("send_greeting")], False),
    ("boss", [], True),
    ("boss", [work("open_communication", default_greeting_observed=True)], True),
    ("boss", [work("inspect_delivery", side_effect=False, existing_outgoing_text=GREETING, message_state="sent")], True),
    ("liepin", [work("send_greeting")], True),
    ("liepin", [work("submit_resume", resume_state="sent")], True),
    ("zhilian", [work("submit_resume", outcome="unresolved")], True),
    ("51job", [work("inspect_delivery", side_effect=False, resume_state="sent")], True),
])
def test_incomplete_default_history_and_unresolved_results_do_not_record(
    monkeypatch, platform, records, completed,
):
    calls = []
    monkeypatch.setattr(analytics, "record_delivery_verified", lambda value: calls.append(value))
    native._record_completed_native_delivery_fact(platform, {"job-test": records}, completed=completed)
    assert calls == []


@pytest.mark.parametrize("missing_module", [False, True])
def test_optional_analytics_failure_cannot_fail_delivery(monkeypatch, missing_module):
    def failed_record(_platform):
        raise OSError("synthetic local spool failure")

    monkeypatch.setattr(analytics, "record_delivery_verified", failed_record)
    if missing_module:
        original = builtins.__import__

        def importing(name, *args, **kwargs):
            if name == "jobagent.infra.analytics":
                raise ModuleNotFoundError(name)
            return original(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", importing)
    native._record_completed_native_delivery_fact("boss", {"job-test": [work("send_greeting")]}, completed=True)


@pytest.fixture
def persisted_batch(monkeypatch, tmp_path):
    home = tmp_path / "home"
    app = home / ".jobagent"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    for name in ("JOBAGENT_ANALYTICS_DISABLED", "JOBAGENT_ANALYTICS_KILL_SWITCH", "DO_NOT_TRACK"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {"APP_DIR": app, "STATE_DIR": app / "state", "LOG_DIR": app / "logs",
                        "ROUNDS_DIR": app / "state/rounds", "LOCKS_DIR": app / "state/locks"}.items():
        monkeypatch.setattr(state, name, value)
    monkeypatch.setattr(account_state, "APP_DIR", app)
    key, account = "jobagent_live_native_analytics_test", "acct_native_test"
    account_state.ensure_account_state({"account": {"account_ref": account}}, api_key=key, app_dir=app)
    monkeypatch.setattr("jobagent.infra.credentials.load_api_key", lambda: key)
    monkeypatch.setattr(analytics, "schedule_flush", lambda **_kwargs: False)
    monkeypatch.setattr("jobagent.infra.cloud_client.analytics_events", lambda *_a, **_k: pytest.fail("no relay in test"))
    active = {"round_id": "round-native-test", "platforms": {"zhilian": {"status": "sent"}}}
    state.save_json(state.current_round_path(), active)
    monkeypatch.setattr(native.rounds, "round_status", lambda: copy.deepcopy(active))
    monkeypatch.setattr(native.rounds, "complete_platform_after_audit", lambda _p: {
        **copy.deepcopy(active), "platforms": {"zhilian": {"status": "completed"}}})
    binding = {"account_ref": account, "round_id": active["round_id"], "platform": "zhilian", "job_id": "job-test"}
    item = store.ensure_work(action="submit_resume", side_effect=True, binding=binding,
        task={"job": {"cloud_greeting": ""}, "delivery_source": {"preview_id": "preview-test"}})
    item = store.begin_work(item["work_id"], binding)

    def close(outcome="success"):
        return store.submit_work(item["work_id"], binding, {
            "receipt_id": "receipt-test", "nonce": item["nonce"], "binding": binding,
            "outcome": outcome, "evidence": {"resume_state": "sent" if outcome == "success" else "unknown"}})

    return close, analytics._spool_path(account)


def test_audit_records_only_after_persisted_completion_and_is_idempotent(persisted_batch):
    close, spool = persisted_batch
    native.audit("zhilian")
    assert not spool.exists()  # Intent without a receipt is not a delivery.
    close()
    observed = native.audit("zhilian", complete=False)
    assert observed["summary"]["resume_submitted"] == 1
    assert not spool.exists()  # A read-only audit cannot create a completion fact.
    first = native.audit("zhilian")
    assert first["workflow"]["platforms"]["zhilian"]["status"] == "completed"
    contents = spool.read_bytes()
    data = json.loads(contents)
    assert data["facts"] == ["delivery_verified:zhilian"]
    assert len(data["events"]) == 1
    native.audit("zhilian")
    assert spool.read_bytes() == contents


def test_terminal_unresolved_audit_does_not_record_delivery_fact(persisted_batch):
    close, spool = persisted_batch
    close("unresolved")
    result = native.audit("zhilian")
    assert result["summary"]["unresolved"] == 1
    assert not spool.exists()
