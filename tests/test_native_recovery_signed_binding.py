"""Recovery reads the original production-shaped signed binding without writes."""
from __future__ import annotations

import copy
import json
import socket
from types import SimpleNamespace

import pytest

from jobagent.application import discover as existing, native_discovery as discovery
from jobagent.infra import discovery_state as storage, profile_contract, rounds, state
from jobagent.infra.protocol import ProtocolError, digest_payload
from jobagent.platforms.discovery import CollectionError
from tests.test_native_discovery import env, _real_ledger, _wire_native_work

# The shared fixture patches these; this suite exercises actual persisted rounds.
_REAL_ROUND_FUNCTIONS = {name: getattr(rounds, name) for name in (
    "ensure_current_round", "assert_platform_turn", "round_status", "set_platform_status", "save_round",
)}


@pytest.fixture
def signed_recovery(env, monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("Real network and cloud mutations are forbidden")

    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    for name, value in {
        "APP_DIR": env.tmp_path, "LOG_DIR": env.tmp_path / "logs",
        "ROUNDS_DIR": env.tmp_path / "rounds", "LOCKS_DIR": env.tmp_path / "locks",
    }.items():
        monkeypatch.setattr(state, name, value)
    ledger = _real_ledger(env, monkeypatch, "signed-binding-ledger")
    native = _wire_native_work(env, monkeypatch, ledger)
    monkeypatch.setattr(native, "MIN_ACTION_INTERVAL_SECONDS", 0)
    for name, function in _REAL_ROUND_FUNCTIONS.items():
        monkeypatch.setattr(rounds, name, function)
    monkeypatch.setattr(existing, "require_compatible_profile", profile_contract.require_compatible_profile)
    # The local file is valid but is deliberately NOT the selected material.
    env.profile.clear()
    env.profile.update(schema_version=1, basic={"name": "Local fixture"})
    material_profile = {"schema_version": 1, "basic": {"name": "Bound fixture"}}
    snapshot = {
        "id": "binding-original", "context_id": "context-original",
        "resume_id": "resume-original", "resume_revision_id": "revision-original",
        "resume_revision_number": 2, "content_digest": "sha256:" + "1" * 64,
        "target_role": "产品经理", "resume_name": "Synthetic selected resume",
        "confirmed_at": "2026-09-22T00:00:00Z", "account_ref": "account-test",
    }
    active = env.active
    active.update(schema_version=rounds.ROUND_SCHEMA_VERSION, browser_session_id="session-test",
                  browser_executor="codex_native", platform_order=list(rounds.DEFAULT_PLATFORM_ORDER))
    active["platforms"]["boss"] = {"status": "login_verified"}
    active["intent"]["profile_digest"] = digest_payload(material_profile)
    # Selection responses omit account_ref; material and signed plans include it.
    active["resume_binding"] = {k: v for k, v in snapshot.items() if k != "account_ref"}
    rounds.save_round(active)
    state.save_json(state.profile_path(), env.profile)
    calls = []
    material = {"ok": True, "account_ref": "account-test", "binding": snapshot,
                "profile": material_profile, "profile_digest": digest_payload(material_profile),
                "offline": False, "stale": False}

    def fetch(binding_id):
        calls.append(binding_id)
        return copy.deepcopy(material)

    monkeypatch.setattr(existing.cloud_client, "resume_binding_material", fetch)

    def start(**kwargs):
        env.starts.append(kwargs)
        plan = env.make_plan("boss", kwargs["request_id"])
        plan.update(profile_digest=digest_payload(material_profile), resume_binding=copy.deepcopy(snapshot),
                    round_id=active["round_id"], context_id=snapshot["context_id"])
        return env.sign(plan)

    monkeypatch.setattr(existing.cloud_client, "discovery_start", start)
    monkeypatch.setattr(existing.cloud_client, "discovery_renew", deny)
    monkeypatch.setattr(existing.cloud_client, "discovery_decide", deny)
    work = discovery.start_discovery("boss", "session-test")["work"]
    native.cancel(work["work_id"], confirmed=True)
    work = ledger.get_work(work["work_id"], work["binding"])
    calls.clear()

    def persisted():
        return {str(path.relative_to(env.tmp_path)): path.read_bytes()
                for path in env.tmp_path.rglob("*") if path.is_file()}

    def replace_plan(change, *, resign=True):
        checkpoint = storage.load_collection_checkpoint("boss")
        change(checkpoint["plan"])
        if resign:
            checkpoint["plan"] = env.sign(checkpoint["plan"])
        storage.save_collection_checkpoint("boss", request_id=work["binding"]["request_id"],
                                          plan=checkpoint["plan"], progress=checkpoint["progress"])

    return SimpleNamespace(env=env, native=native, work=work, calls=calls, material=material,
                           snapshot=snapshot, persisted=persisted, replace_plan=replace_plan)


def test_recovery_reads_cleared_binding_from_original_signed_plan_without_writes(signed_recovery):
    r = signed_recovery
    rounds.clear_round_resume_binding()  # Actual 0.6.12 destructive branch.
    before = r.persisted()
    assert discovery.validate_recovery_source(r.work) == r.snapshot
    assert r.calls == [r.snapshot["id"]]
    assert r.persisted() == before
    assert "resume_binding" not in rounds.ensure_current_round()


def test_existing_selection_binding_without_account_ref_is_accepted(signed_recovery):
    r = signed_recovery
    before = r.persisted()
    assert discovery.validate_recovery_source(r.work) is None
    assert r.calls == [r.snapshot["id"]]
    assert r.persisted() == before


@pytest.mark.parametrize("reason", ["resume_binding_stale", "resume_binding_released", "resume_not_in_prepared_set"])
def test_signed_binding_does_not_bypass_current_server_material_rejection(signed_recovery, monkeypatch, reason):
    r = signed_recovery
    rounds.clear_round_resume_binding()
    def unavailable(binding_id):
        raise existing.cloud_client.CloudError("Synthetic 409", code="preparation_required", status=409,
                                               details={"reason": reason})
    monkeypatch.setattr(existing.cloud_client, "resume_binding_material", unavailable)
    before = r.persisted()
    with pytest.raises(CollectionError) as error:
        discovery.validate_recovery_source(r.work)
    assert error.value.code == "resume_binding_paused"
    assert error.value.__cause__.details["reason"] == reason
    assert r.persisted() == before


@pytest.mark.parametrize("field,value", [("id", "different-binding"), ("resume_revision_id", "different-revision"),
                                         ("account_ref", "different-account")])
def test_present_different_binding_is_never_replaced(signed_recovery, field, value):
    r = signed_recovery
    active = rounds.ensure_current_round()
    active["resume_binding"][field] = value
    rounds.save_round(active)
    before = r.persisted()
    with pytest.raises(CollectionError) as error:
        discovery.validate_recovery_source(r.work)
    assert error.value.code == "native_recovery_binding_mismatch"
    assert r.calls == []
    assert r.persisted() == before


def test_tampered_signature_is_rejected_before_material_read(signed_recovery):
    r = signed_recovery
    rounds.clear_round_resume_binding()
    r.replace_plan(lambda plan: plan["resume_binding"].update(id="tampered"), resign=False)
    before = r.persisted()
    with pytest.raises(ProtocolError, match="signature"):
        discovery.validate_recovery_source(r.work)
    assert r.calls == []
    assert r.persisted() == before


@pytest.mark.parametrize("field,value", [("account_ref", "another-account"), ("context_id", "another-context")])
def test_signed_foreign_account_or_context_is_rejected_before_material_read(signed_recovery, field, value):
    r = signed_recovery
    rounds.clear_round_resume_binding()
    r.replace_plan(lambda plan: plan["resume_binding"].update({field: value}))
    before = r.persisted()
    with pytest.raises(CollectionError) as error:
        discovery.validate_recovery_source(r.work)
    assert error.value.code == "native_recovery_binding_mismatch"
    assert r.calls == []
    assert r.persisted() == before


@pytest.mark.parametrize("field", ["binding", "profile_digest", "profile"])
def test_material_response_must_match_signed_snapshot_and_digest(signed_recovery, field):
    r = signed_recovery
    rounds.clear_round_resume_binding()
    if field == "binding":
        r.material[field] = {**r.material[field], "resume_revision_id": "changed"}
    elif field == "profile_digest":
        r.material[field] = "sha256:" + "0" * 64
    else:
        r.material[field] = {"schema_version": 1, "basic": {"name": "Changed material"}}
    before = r.persisted()
    with pytest.raises(CollectionError) as error:
        discovery.validate_recovery_source(r.work)
    assert error.value.code == "native_recovery_binding_mismatch"
    assert r.persisted() == before


def test_missing_signed_snapshot_does_not_infer_binding(signed_recovery):
    r = signed_recovery
    rounds.clear_round_resume_binding()
    r.replace_plan(lambda plan: [plan.pop(key) for key in ("resume_binding", "context_id", "round_id")])
    before = r.persisted()
    with pytest.raises(CollectionError) as error:
        discovery.validate_recovery_source(r.work)
    assert error.value.code == "native_discovery_context_mismatch"
    assert r.calls == []
    assert r.persisted() == before


@pytest.mark.parametrize("cleared_before_task", [True, False])
def test_success_restores_original_binding_with_window_in_one_round_save(signed_recovery, cleared_before_task):
    from datetime import datetime, timezone

    r = signed_recovery
    if cleared_before_task:
        rounds.clear_round_resume_binding()
    recovery = r.native.recover(r.work["work_id"], confirmed=True)["work"]
    if cleared_before_task:
        assert recovery["task"]["restore_resume_binding"] == r.snapshot
        assert "resume_binding" not in rounds.ensure_current_round()
    else:
        # A 0.6.12 recovery task has no restore hint. Its later material
        # failure could clear the binding after the task was issued.
        assert "restore_resume_binding" not in recovery["task"]
        rounds.clear_round_resume_binding()
    before_pending = storage.pending_start_path("boss").read_bytes()
    begun = r.native.begin(recovery["work_id"])["work"]
    session = rounds.ensure_current_round()["native_session"]
    result = {
        "receipt_id": "signed-binding-recovery-success", "nonce": begun["nonce"],
        "binding": begun["binding"], "outcome": "success", "evidence": {
            "source": "host_ui_observation", "observed_at": datetime.now(timezone.utc).isoformat(),
            "observation": "Synthetic current account and stable window proof",
            "page_url": "https://www.zhipin.com/web/geek/job",
            "native_computer_use_available": True, "browser": "chrome",
            "window_reference": "native-window-restored", "window_reference_kind": "native_window_id",
            "profile_label": session["profile_label"], "account_label": session["accounts"]["boss"],
            "group_reference": "native-group-restored", "login_state": "authenticated",
            "account_navigation": True, "resume_or_activity": True,
        },
    }
    path = r.env.tmp_path / "recovery-success.json"
    path.write_text(json.dumps(result))
    resumed = r.native.submit(begun["work_id"], str(path))["work"]
    active = rounds.ensure_current_round()
    assert active["resume_binding"] == r.snapshot
    assert active["native_session"]["window_reference"] == "native-window-restored"
    assert active["native_session"]["id"] == session["id"]
    assert begun["work_id"] in active["native_processed_work"]
    assert "native_cancelled_work" not in active
    assert resumed["action"] == "collect_search_page" and resumed["task"]["page"] == 1
    assert resumed["binding"] == r.work["binding"]
    assert storage.pending_start_path("boss").read_bytes() == before_pending
    assert len(r.env.starts) == 1 and r.env.renewals == [] and r.env.decisions == []
