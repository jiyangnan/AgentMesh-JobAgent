"""Recovery preflight errors must preserve the already signed request and state."""
from __future__ import annotations

import copy
import sqlite3

import pytest

from jobagent.application import discover as existing
from jobagent.infra import discovery_state as storage, protocol, state
from jobagent.platforms.discovery import CollectionError
from tests.test_native_discovery import env as base_env  # noqa: F401 - fixture dependency
from tests.test_native_recovery import recovery_env  # noqa: F401 - shared fixture


@pytest.fixture
def env(base_env, monkeypatch):
    """Create the source with the same signed binding shape as the server."""
    bound_profile = {
        "schema_version": 1,
        "professional_profile": {"summary": "Synthetic confirmed binding material"},
    }
    profile_digest = protocol.digest_payload(bound_profile)
    binding = {
        "id": "binding-preserved",
        "context_id": "context-preserved",
        "resume_id": "resume-preserved",
        "resume_revision_id": "revision-preserved",
        "resume_revision_number": 1,
        "content_digest": "material-digest-preserved",
        "target_role": "产品经理",
        "resume_name": "Synthetic bound resume",
        "confirmed_at": "2026-09-22T00:00:00Z",
        "account_ref": "account-test",
    }
    base_env.active["resume_binding"] = {
        key: value for key, value in binding.items() if key != "account_ref"
    }
    base_env.active["intent"]["profile_digest"] = profile_digest

    def material(binding_id):
        return {
            "binding": copy.deepcopy(binding),
            "profile": copy.deepcopy(bound_profile),
            "profile_digest": profile_digest,
        }

    def start(**kwargs):
        base_env.starts.append(kwargs)
        plan = base_env.make_plan(kwargs["platform"], kwargs["request_id"])
        plan.update(
            resume_binding=copy.deepcopy(binding),
            context_id=binding["context_id"],
            round_id=base_env.active["round_id"],
            profile_digest=profile_digest,
        )
        return base_env.sign(plan)

    monkeypatch.setattr(existing.cloud_client, "resume_binding_material", material)
    monkeypatch.setattr(existing.cloud_client, "discovery_start", start)
    base_env.bound_profile = bound_profile
    base_env.bound_snapshot = binding
    return base_env


def _install_bound_material_failure(recovery, monkeypatch, *, code, status, retryable):
    def unavailable(binding_id):
        raise existing.cloud_client.CloudError(
            "Synthetic bound resume material failure",
            code=code, status=status, retryable=retryable,
        )

    # Keep binding_material_profile, _context and the real on-disk binding
    # clearer intact: only the cloud boundary is replaced.
    monkeypatch.setattr(existing.cloud_client, "resume_binding_material", unavailable)


def _snapshot(recovery):
    ledger_path = state.STATE_DIR / "browser-work.sqlite3"
    with sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True) as connection:
        ledger = tuple(connection.iterdump())
    return {
        "active_round": copy.deepcopy(recovery.env.active),
        "persisted_round": state.current_round_path().read_bytes(),
        "pending_request": recovery.pending_path.read_bytes(),
        "checkpoint": copy.deepcopy(storage.load_collection_checkpoint("boss")),
        "pending_decision": storage.load_pending_decision("boss"),
        "ledger": ledger,
        "cloud_starts": copy.deepcopy(recovery.env.starts),
        "cloud_renewals": copy.deepcopy(recovery.env.renewals),
        "cloud_decisions": copy.deepcopy(recovery.env.decisions),
    }


def _failure_payload(error):
    if isinstance(error, CollectionError):
        return {**(error.details or {}), "error": error.code}
    return error.payload


def test_recovery_material_network_error_keeps_real_cause_and_preserved_request(
    recovery_env, monkeypatch,
):
    recovery = recovery_env
    _install_bound_material_failure(
        recovery, monkeypatch, code="network_timeout", status=None, retryable=True,
    )
    before = _snapshot(recovery)

    with pytest.raises((recovery.ledger.BrowserWorkError, CollectionError)) as error:
        recovery.native.recover(recovery.source["work_id"], confirmed=True)

    assert _snapshot(recovery) == before
    payload = _failure_payload(error.value)
    assert payload["error"] == "resume_binding_material_unavailable"
    assert payload["request_preserved"] is True


def test_recovery_preparation_failure_preserves_bound_round_pending_and_ledger(
    recovery_env, monkeypatch,
):
    recovery = recovery_env
    _install_bound_material_failure(
        recovery, monkeypatch, code="preparation_required", status=409, retryable=False,
    )
    before = _snapshot(recovery)

    with pytest.raises((recovery.ledger.BrowserWorkError, CollectionError)) as error:
        recovery.native.recover(recovery.source["work_id"], confirmed=True)

    assert _snapshot(recovery) == before
    payload = _failure_payload(error.value)
    assert payload["error"] != "native_recovery_not_current"
    assert payload["request_preserved"] is True


def test_recovery_rejects_source_page_that_is_no_longer_unfinished(recovery_env):
    recovery = recovery_env
    checkpoint = storage.load_collection_checkpoint("boss")
    # The original source requests page 2. Once that page has been completed,
    # it must never be reopened even though the signed request is still saved.
    checkpoint["progress"]["completed_pages"].append([0, 2])
    storage.save_collection_checkpoint(
        "boss", request_id=recovery.source["binding"]["request_id"],
        plan=checkpoint["plan"], progress=checkpoint["progress"],
    )
    before = _snapshot(recovery)

    with pytest.raises((recovery.ledger.BrowserWorkError, CollectionError)) as error:
        recovery.native.recover(recovery.source["work_id"], confirmed=True)

    assert _failure_payload(error.value)["error"] == "native_recovery_not_current"
    assert _snapshot(recovery) == before


def test_nonretryable_material_failure_resumes_original_recovery_after_prerequisite(
    recovery_env, monkeypatch,
):
    recovery = recovery_env
    _install_bound_material_failure(
        recovery, monkeypatch, code="resume_profile_invalid", status=403,
        retryable=False,
    )
    before = _snapshot(recovery)
    command = f"jobagent work recover --work-id {recovery.source['work_id']} --confirm-recover"

    with pytest.raises((recovery.ledger.BrowserWorkError, CollectionError)) as error:
        recovery.native.recover(recovery.source["work_id"], confirmed=True)

    payload = _failure_payload(error.value)
    assert payload["retryable"] is False
    assert payload["request_preserved"] is True
    assert payload["next_suggested"] == command
    assert payload["recovery_command"] == command
    assert payload["recovery_cause"]["error"] == "resume_profile_invalid"
    assert payload["recovery_cause"]["status"] == 403
    assert _snapshot(recovery) == before


def test_saved_recovery_receipt_survives_material_outage_and_resumes_without_replay(
    recovery_env, monkeypatch,
):
    recovery = recovery_env
    material_unavailable = False

    def material(binding_id):
        if material_unavailable:
            raise existing.cloud_client.CloudError(
                "Synthetic service outage after the UI receipt was saved",
                code="cloud_gateway_unavailable", status=503, retryable=True,
            )
        return {
            "binding": copy.deepcopy(recovery.env.bound_snapshot),
            "profile": copy.deepcopy(recovery.env.bound_profile),
            "profile_digest": protocol.digest_payload(recovery.env.bound_profile),
        }

    monkeypatch.setattr(existing.cloud_client, "resume_binding_material", material)
    work = recovery.native.recover(recovery.source["work_id"], confirmed=True)["work"]
    assert work["action"] == "recover_session"
    begun = recovery.native.begin(work["work_id"])["work"]
    observed = recovery.observed(begun, recover=True)
    before_submit = _snapshot(recovery)
    material_unavailable = True

    with pytest.raises((recovery.ledger.BrowserWorkError, CollectionError)) as error:
        recovery.submit(begun, observed)

    payload = _failure_payload(error.value)
    command = f"jobagent work recover --work-id {recovery.source['work_id']} --confirm-recover"
    assert payload["error"] == "resume_binding_material_unavailable"
    assert payload["recovery_receipt_saved"] is True
    assert payload["browser_replay_permitted"] is False
    assert payload.get("recovery_state_changed") is not False
    assert payload["next_suggested"] == payload["recovery_command"] == command
    assert payload["request_preserved"] is True
    saved = recovery.ledger.get_work(work["work_id"], work["binding"])
    assert saved["state"] == "closed"
    assert saved["result"]["outcome"] == "success"
    assert saved["result"]["receipt_id"] == observed["receipt_id"]
    assert saved["result"]["evidence"] == observed["evidence"]
    after_submit = _snapshot(recovery)
    assert after_submit["ledger"] != before_submit["ledger"]
    for field in before_submit.keys() - {"ledger"}:
        assert after_submit[field] == before_submit[field]

    # The original command consumes its already committed UI result once the
    # service is available. It must not issue a second recovery observation.
    material_unavailable = False
    resumed = recovery.native.recover(recovery.source["work_id"], confirmed=True)["work"]
    assert resumed["action"] == "collect_search_page"
    assert resumed["task"]["page"] == recovery.source["task"]["page"]
    assert resumed["binding"] == recovery.source["binding"]
    assert resumed["state"] == "ready" and resumed["observation_attempts"] == 0
    assert recovery.ledger.get_work(work["work_id"], work["binding"]) == saved
    works = recovery.ledger.list_work(recovery.source["binding"])
    assert [item["work_id"] for item in works if item["action"] == "recover_session"] == [work["work_id"]]
    assert recovery.env.active["native_session"]["window_reference"] == "native-window-42"
    assert recovery.pending_path.read_bytes() == recovery.pending_bytes
    assert storage.load_collection_checkpoint("boss") == recovery.checkpoint
    assert len(recovery.env.starts) == 1
    assert recovery.env.renewals == recovery.env.decisions == []


@pytest.mark.parametrize("reason", ["resume_binding_stale", "resume_binding_released"])
def test_changed_material_requires_confirmed_new_round_without_recovery_loop(recovery_env, monkeypatch, reason):
    recovery = recovery_env
    def rejected(binding_id):
        raise existing.cloud_client.CloudError("Synthetic stale material", code="preparation_required",
                                               status=409, details={"reason": reason})
    monkeypatch.setattr(existing.cloud_client, "resume_binding_material", rejected)
    before = _snapshot(recovery)
    with pytest.raises(recovery.ledger.BrowserWorkError) as error:
        recovery.native.recover(recovery.source["work_id"], confirmed=True)
    payload = error.value.payload
    assert payload["error"] == "resume_binding_paused"
    assert payload["recovery_cause"]["reason"] == reason
    assert payload["recovery_requires_new_round"] is True
    assert payload["retryable"] is False and payload["requires_user_action"] is True
    assert payload["next_suggested"] == "jobagent round status"
    assert "recovery_command" not in payload
    assert _snapshot(recovery) == before
