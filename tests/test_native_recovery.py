"""Read-only recovery preserves signed discovery; all browser facts are synthetic."""
from __future__ import annotations

import copy
import json
import socket
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from jobagent.application import native_discovery
from jobagent.infra import discovery_state as storage, rounds, state
from tests.test_native_discovery import env, _real_ledger, _wire_native_work, receipt


@pytest.fixture
def recovery_env(env, monkeypatch):
    def deny_network(*args, **kwargs):
        pytest.fail("Recovery must not use a real network connection")

    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    for name, value in {
        "APP_DIR": env.tmp_path,
        "LOG_DIR": env.tmp_path / "logs",
        "ROUNDS_DIR": env.tmp_path / "rounds",
        "LOCKS_DIR": env.tmp_path / "locks",
    }.items():
        monkeypatch.setattr(state, name, value)
    ledger = _real_ledger(env, monkeypatch, "recovery-ledger")
    native = _wire_native_work(env, monkeypatch, ledger)
    monkeypatch.setattr(native, "MIN_ACTION_INTERVAL_SECONDS", 0)
    env.active["platforms"]["boss"] = {"status": "login_verified"}
    env.active["native_session"]["window_reference"] = "Google Chrome: old BOSS page title"

    def save_round(value):
        state.save_json(state.current_round_path(), value)

    monkeypatch.setattr(rounds, "save_round", save_round)
    monkeypatch.setattr(rounds, "round_status", lambda: {
        "round_id": env.active["round_id"], "current_platform": "boss",
        "platforms": copy.deepcopy(env.active["platforms"]),
    })
    save_round(env.active)
    serial = 0

    def submit(work, result):
        nonlocal serial
        serial += 1
        path = env.tmp_path / f"recovery-result-{serial}.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        return native.submit(work["work_id"], str(path))

    def observed(work, *, recover=False):
        session = env.active["native_session"]
        result = {
            "receipt_id": f"observation-{work['work_id']}-{serial}",
            "nonce": work["nonce"], "binding": copy.deepcopy(work["binding"]),
            "outcome": "success", "evidence": {
                "source": "host_ui_observation",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "observation": "Synthetic account and browser evidence",
                "window_reference": session["window_reference"],
                "profile_label": session["profile_label"],
                "account_label": session["accounts"]["boss"],
                "page_url": "https://www.zhipin.com/web/geek/job",
            },
        }
        if recover:
            result["evidence"].update(
                native_computer_use_available=True, browser="chrome",
                window_reference="native-window-42", window_reference_kind="native_window_id",
                group_reference="recovered-task-group", login_state="authenticated",
                account_navigation=True, resume_or_activity=True,
            )
        return result

    first = native_discovery.start_discovery("boss", "session-test")["work"]
    first = native.begin(first["work_id"])["work"]
    collected = receipt(first, final=False)
    collected["evidence"].update(observed(first)["evidence"])
    source = submit(first, collected)["work"]
    assert source["task"]["page"] == 2
    for attempt in range(3):
        source = native.begin(source["work_id"])["work"]
        blocked = observed(source)
        blocked.update(receipt_id=f"source-block-{attempt}", outcome="uncertain",
                       requires_technical_recovery=True, reason="job_identity_unknown")
        response = submit(source, blocked)
    source = response["work"]
    assert source["observation_attempts"] == 3
    pending_path = storage.pending_start_path("boss")
    return SimpleNamespace(
        env=env, native=native, ledger=ledger, source=source,
        submit=submit, observed=observed, save_round=save_round,
        pending_path=pending_path, pending_bytes=pending_path.read_bytes(),
        checkpoint=copy.deepcopy(storage.load_collection_checkpoint("boss")),
        original_session=copy.deepcopy(env.active["native_session"]),
    )


def test_recover_preserves_existing_candidates_and_signed_request(recovery_env):
    r = recovery_env
    response = r.native.recover(r.source["work_id"], confirmed=True)
    work = response["work"]
    assert work["action"] == "recover_session" and work["side_effect"] is False
    assert work["binding"]["session_id"] == r.original_session["id"]
    old = r.ledger.get_work(r.source["work_id"], r.source["binding"])
    assert old["state"] == "closed" and old["result"]["outcome"] == "cancelled"
    assert r.native.next_work()["work"]["work_id"] == work["work_id"]
    assert r.native.status()["work"]["work_id"] == work["work_id"]
    begun = r.native.begin(work["work_id"])["work"]
    resumed = r.submit(begun, r.observed(begun, recover=True))["work"]
    assert resumed["action"] == "collect_search_page"
    assert resumed["work_id"] != r.source["work_id"]
    assert resumed["binding"] == r.source["binding"]
    assert resumed["task"]["page"] == 2 and resumed["observation_attempts"] == 0
    session = r.env.active["native_session"]
    assert session["id"] == r.original_session["id"]
    assert session["window_reference"] == "native-window-42"
    assert session["window_reference_kind"] == "native_window_id"
    assert session["group_reference"] == "recovered-task-group"
    assert session["profile_label"] == r.original_session["profile_label"]
    assert session["accounts"] == r.original_session["accounts"]
    assert "native_cancelled_work" not in r.env.active
    assert storage.load_collection_checkpoint("boss") == r.checkpoint
    assert r.pending_path.read_bytes() == r.pending_bytes
    assert r.checkpoint["progress"]["completed_pages"] == [[0, 1]]
    assert len(r.checkpoint["progress"]["candidates"]) == 1
    assert len(r.env.starts) == 1 and r.env.decisions == [] and r.env.renewals == []
    # An old successful page and its receipts were not erased by recovery.
    works = r.ledger.list_work(r.source["binding"])
    assert any(w["result"] and w["result"]["outcome"] == "page_collected" for w in works)


def test_recover_requires_confirmation_before_mutating_source(recovery_env):
    r = recovery_env
    before = r.ledger.get_work(r.source["work_id"], r.source["binding"])
    with pytest.raises(r.ledger.BrowserWorkError) as error:
        r.native.recover(r.source["work_id"], confirmed=False)
    assert error.value.payload["error"] == "user_confirmation_required"
    assert r.ledger.get_work(r.source["work_id"], r.source["binding"]) == before


def test_recover_reuses_task_and_never_resets_observation_budget(recovery_env):
    r = recovery_env
    work = r.native.recover(r.source["work_id"], confirmed=True)["work"]
    for attempt in range(3):
        begun = r.native.begin(work["work_id"])["work"]
        blocked = r.observed(begun)
        blocked.update(receipt_id=f"recovery-block-{attempt}", outcome="uncertain",
                       requires_technical_recovery=True, reason="page_state_unknown")
        r.submit(begun, blocked)
        same = r.native.recover(r.source["work_id"], confirmed=True)["work"]
        assert same["work_id"] == work["work_id"]
        assert same["observation_attempts"] == attempt + 1
    assert same["allowed_mode"] == "reconcile_only"
    terminal = r.native.status()
    assert terminal["recovery"]["status"] == "receipt_only"
    assert "after_confirmation_argv" not in terminal["recovery"]
    with pytest.raises(r.ledger.BrowserWorkError):
        r.native.begin(work["work_id"])
    r.native.cancel(work["work_id"], confirmed=True)
    count = len(r.ledger.list_work({"account_ref": "account-test", "round_id": "round-test"}))
    result = r.native.recover(r.source["work_id"], confirmed=True)
    assert not result.get("work") or result["work"]["state"] == "closed"
    assert not r.ledger.has_open()
    assert len(r.ledger.list_work({"account_ref": "account-test", "round_id": "round-test"})) == count


@pytest.mark.parametrize("field,value", [
    ("profile_label", "Different profile"),
    ("account_label", "Different platform account"),
    ("window_reference_kind", "page_title"),
    ("native_computer_use_available", False),
])
def test_recovery_rejects_changed_identity_and_unstable_window_evidence(recovery_env, field, value):
    r = recovery_env
    work = r.native.recover(r.source["work_id"], confirmed=True)["work"]
    begun = r.native.begin(work["work_id"])["work"]
    evidence = r.observed(begun, recover=True)
    evidence["evidence"][field] = value
    with pytest.raises(r.ledger.BrowserWorkError):
        r.submit(begun, evidence)
    assert r.env.active["native_session"] == r.original_session
    assert r.pending_path.read_bytes() == r.pending_bytes


def test_recover_rejects_cross_account_before_mutation(recovery_env, monkeypatch):
    r = recovery_env
    before = r.ledger.get_work(r.source["work_id"], r.source["binding"])
    monkeypatch.setattr(r.native, "current_account_ref", lambda: "different-account")
    with pytest.raises(r.ledger.BrowserWorkError):
        r.native.recover(r.source["work_id"], confirmed=True)
    assert r.ledger.get_work(r.source["work_id"], r.source["binding"]) == before


@pytest.mark.parametrize("side_effect,delivery_source", [(True, False), (False, True)])
def test_recover_rejects_side_effect_or_delivery_work(recovery_env, side_effect, delivery_source):
    r = recovery_env
    r.native.cancel(r.source["work_id"], confirmed=True)
    task = {"test": "not recoverable"}
    if delivery_source:
        task["delivery_source"] = {"preview_id": "synthetic-preview"}
    work = r.ledger.ensure_work(action="collect_search_page", task=task,
                               binding=r.source["binding"], side_effect=side_effect,
                               key="unsafe-recovery-source")
    if side_effect:
        work = r.ledger.begin_work(work["work_id"], work["binding"])
    before = r.ledger.get_work(work["work_id"], work["binding"])
    with pytest.raises(r.ledger.BrowserWorkError):
        r.native.recover(work["work_id"], confirmed=True)
    assert r.ledger.get_work(work["work_id"], work["binding"]) == before


def test_recover_cannot_replace_another_open_work(recovery_env):
    r = recovery_env
    r.native.cancel(r.source["work_id"], confirmed=True)
    other = r.ledger.ensure_work(action="inspect_session", task={"test": "different pending work"},
                                binding=r.source["binding"], key="different-pending")
    with pytest.raises(r.ledger.BrowserWorkError):
        r.native.recover(r.source["work_id"], confirmed=True)
    assert r.ledger.get_work(other["work_id"], other["binding"])["state"] == "ready"


def test_recover_reenters_after_source_cancel_before_task_creation(recovery_env, monkeypatch):
    r = recovery_env
    original_ensure = r.ledger.ensure_work

    def crash_on_recovery(*args, **kwargs):
        if kwargs.get("action") == "recover_session":
            raise OSError("synthetic crash before recovery task creation")
        return original_ensure(*args, **kwargs)

    monkeypatch.setattr(r.ledger, "ensure_work", crash_on_recovery)
    with pytest.raises(OSError):
        r.native.recover(r.source["work_id"], confirmed=True)
    assert r.ledger.get_work(r.source["work_id"], r.source["binding"])["state"] == "closed"
    monkeypatch.setattr(r.ledger, "ensure_work", original_ensure)
    recovered = r.native.recover(r.source["work_id"], confirmed=True)["work"]
    assert recovered["action"] == "recover_session"
    assert r.pending_path.read_bytes() == r.pending_bytes


def test_recover_replays_committed_success_after_round_save_crash(recovery_env, monkeypatch):
    r = recovery_env
    work = r.native.recover(r.source["work_id"], confirmed=True)["work"]
    begun = r.native.begin(work["work_id"])["work"]
    evidence = r.observed(begun, recover=True)
    before = copy.deepcopy(r.env.active)

    def crash(value):
        raise OSError("synthetic crash after recovery receipt commit")

    monkeypatch.setattr(rounds, "save_round", crash)
    with pytest.raises(OSError):
        r.submit(begun, evidence)
    assert r.ledger.get_work(work["work_id"], work["binding"])["state"] == "closed"
    r.env.active.clear()
    r.env.active.update(before)
    monkeypatch.setattr(rounds, "save_round", r.save_round)
    replayed = r.native.recover(r.source["work_id"], confirmed=True)
    assert replayed.get("work", {}).get("work_id") != r.source["work_id"]
    assert r.env.active["native_session"]["window_reference"] == "native-window-42"
    assert r.env.active["native_session"]["id"] == r.original_session["id"]
    assert "native_cancelled_work" not in r.env.active
    assert r.pending_path.read_bytes() == r.pending_bytes


def test_recover_rejects_cancelled_source_after_legacy_recovery_completed_discovery(recovery_env):
    r = recovery_env
    r.native.cancel(r.source["work_id"], confirmed=True)
    login = r.native.request_login("boss")["work"]
    login = r.native.begin(login["work_id"])["work"]
    verified = r.observed(login)
    verified["evidence"].update(login_state="authenticated", account_navigation=True, resume_or_activity=True)
    fresh_page = r.submit(login, verified)["work"]
    assert fresh_page["action"] == "collect_search_page"
    assert fresh_page["work_id"] != r.source["work_id"]
    begun = r.native.begin(fresh_page["work_id"])["work"]
    collected = receipt(begun, final=True)
    collected["evidence"].update(r.observed(begun)["evidence"])
    completed = r.submit(begun, collected)
    assert completed["discover_id"] == r.source["binding"]["discover_id"]
    assert r.env.active["platforms"]["boss"]["status"] == "discovered"
    assert not r.ledger.has_open()
    assert len(r.env.decisions) == 1 and len(r.env.starts) == 1
    before = copy.deepcopy(r.env.active)
    with pytest.raises(r.ledger.BrowserWorkError):
        r.native.recover(r.source["work_id"], confirmed=True)
    assert r.env.active == before
    assert not r.ledger.has_open()


@pytest.mark.parametrize("changed", ["profile", "account"])
def test_committed_recovery_cannot_rebind_changed_persisted_identity(recovery_env, monkeypatch, changed):
    r = recovery_env
    work = r.native.recover(r.source["work_id"], confirmed=True)["work"]
    begun = r.native.begin(work["work_id"])["work"]
    before = copy.deepcopy(r.env.active)

    def crash(value):
        raise OSError("synthetic crash before persisting recovered identity")

    monkeypatch.setattr(rounds, "save_round", crash)
    with pytest.raises(OSError):
        r.submit(begun, r.observed(begun, recover=True))
    r.env.active.clear()
    r.env.active.update(before)
    if changed == "profile":
        r.env.active["native_session"]["profile_label"] = "Different persisted profile"
    else:
        r.env.active["native_session"]["accounts"]["boss"] = "Different persisted account"
    monkeypatch.setattr(rounds, "save_round", r.save_round)
    r.save_round(r.env.active)
    changed_context = copy.deepcopy(r.env.active)
    with pytest.raises(r.ledger.BrowserWorkError):
        r.native.recover(r.source["work_id"], confirmed=True)
    assert r.env.active == changed_context
    assert r.pending_path.read_bytes() == r.pending_bytes
