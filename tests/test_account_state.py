from __future__ import annotations

import json

import pytest

from jobagent.infra import account_state
from jobagent.infra.account_state import (
    AccountStateError,
    bind_legacy_state,
    ensure_account_state,
    state_owner_status,
    switch_account_state,
)


def _account(ref: str) -> dict:
    return {"account": {"account_ref": ref}}


def test_empty_state_is_bound_to_verified_account(tmp_path):
    status = ensure_account_state(_account("acct_account_a"), app_dir=tmp_path)

    assert status["status"] == "ready"
    owner = json.loads((tmp_path / "state_owner.json").read_text(encoding="utf-8"))
    assert owner["account_ref"] == "acct_account_a"


def test_legacy_state_requires_explicit_binding(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "profile.json").write_text("{}", encoding="utf-8")

    with pytest.raises(AccountStateError) as error:
        ensure_account_state(_account("acct_account_a"), app_dir=tmp_path)

    assert error.value.payload["error"] == "local_state_owner_required"
    result = bind_legacy_state(
        _account("acct_account_a"),
        confirm_legacy=True,
        app_dir=tmp_path,
    )
    assert result["local_state"]["ready"] is True


def test_switch_preserves_and_restores_each_accounts_state(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    ensure_account_state(_account("acct_account_a"), app_dir=tmp_path)
    (state / "profile.json").write_text('{"owner":"a"}', encoding="utf-8")
    (state / "pending_interaction.json").write_text(
        '{"interaction_id":"interaction-a"}',
        encoding="utf-8",
    )
    (state / "pending_round_binding.json").write_text(
        '{"binding":{"id":"binding-a"}}',
        encoding="utf-8",
    )
    (state / "product_announcements.json").write_text(
        '{"account_ref":"acct_account_a","delivered":{"workbench":{}}}',
        encoding="utf-8",
    )
    (state / "archive").mkdir()
    (state / "archive" / "old-decision.json").write_text(
        '{"owner":"a"}', encoding="utf-8"
    )
    (state / "browser_session.json").write_text('{"tab":"old"}', encoding="utf-8")

    switched = switch_account_state(
        _account("acct_account_b"),
        new_state=True,
        app_dir=tmp_path,
    )

    assert switched["browser_profile_preserved"] is True
    assert not (state / "profile.json").exists()
    assert not (state / "pending_interaction.json").exists()
    # A staged resume binding is account-owned: it must move with the switch
    # instead of leaking the previous account's resume choice.
    assert not (state / "pending_round_binding.json").exists()
    assert not (state / "product_announcements.json").exists()
    assert not (state / "archive").exists()
    assert not (state / "browser_session.json").exists()
    (state / "profile.json").write_text('{"owner":"b"}', encoding="utf-8")

    restored = switch_account_state(
        _account("acct_account_a"),
        new_state=True,
        app_dir=tmp_path,
    )

    assert "profile.json" in restored["restored"]
    assert "pending_interaction.json" in restored["restored"]
    assert "pending_round_binding.json" in restored["restored"]
    assert "archive" in restored["restored"]
    assert json.loads((state / "profile.json").read_text(encoding="utf-8"))["owner"] == "a"
    assert json.loads(
        (state / "archive" / "old-decision.json").read_text(encoding="utf-8")
    )["owner"] == "a"
    assert json.loads(
        (state / "pending_interaction.json").read_text(encoding="utf-8")
    )["interaction_id"] == "interaction-a"
    assert json.loads(
        (state / "product_announcements.json").read_text(encoding="utf-8")
    )["account_ref"] == "acct_account_a"
    assert state_owner_status("acct_account_a", app_dir=tmp_path)["ready"] is True
    saved_b = tmp_path / "accounts" / "acct_account_b" / "state" / "profile.json"
    assert json.loads(saved_b.read_text(encoding="utf-8"))["owner"] == "b"


def test_bound_state_rejects_silent_reassignment(tmp_path):
    ensure_account_state(_account("acct_account_a"), app_dir=tmp_path)

    with pytest.raises(AccountStateError) as error:
        ensure_account_state(_account("acct_account_b"), app_dir=tmp_path)

    assert error.value.payload["error"] == "local_state_account_mismatch"
    assert error.value.payload["next_suggested"] == "jobagent account switch --new-state"


def test_online_binding_records_non_reversible_key_proof(tmp_path):
    api_key = "agentmesh_live_account_a_secret"

    ensure_account_state(
        _account("acct_account_a"),
        api_key=api_key,
        app_dir=tmp_path,
    )

    owner = json.loads((tmp_path / "state_owner.json").read_text(encoding="utf-8"))
    assert owner["schema_version"] == 2
    assert owner["key_fingerprint"] != api_key
    assert api_key not in json.dumps(owner)
    assert owner["verified_at"]


def test_offline_proof_accepts_same_key_and_rejects_cross_account_key(tmp_path):
    api_key = "agentmesh_live_account_a_secret"
    ensure_account_state(
        _account("acct_account_a"),
        api_key=api_key,
        app_dir=tmp_path,
    )

    proof = account_state.verify_offline_account_state(api_key, app_dir=tmp_path)

    assert proof["ready"] is True
    assert proof["offline"] is True
    assert proof["stale"] is True
    assert "key_fingerprint" not in proof

    with pytest.raises(AccountStateError) as error:
        account_state.verify_offline_account_state(
            "agentmesh_live_account_b_secret",
            app_dir=tmp_path,
        )

    assert error.value.payload["error"] == "offline_account_proof_mismatch"
    assert "workflow" not in error.value.payload
    assert "acct_account_a" not in json.dumps(error.value.payload)


def test_legacy_owner_migrates_only_when_bound_credentials_are_unchanged(
    tmp_path,
    monkeypatch,
):
    credentials = tmp_path / "credentials"
    credentials.write_text("agentmesh_live_account_a_secret\n", encoding="utf-8")
    owner = tmp_path / "state_owner.json"
    owner.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "account_ref": "acct_account_a",
                "bound_at": "2026-08-13T08:00:00+00:00",
                "reason": "new_empty_state",
            }
        ),
        encoding="utf-8",
    )
    credentials.touch()
    monkeypatch.setattr(
        account_state,
        "_credential_mtime",
        lambda _path: 1_786_607_999.0,
    )

    proof = account_state.verify_offline_account_state(
        "agentmesh_live_account_a_secret",
        app_dir=tmp_path,
        credential_file=credentials,
        env_key_in_use=False,
    )

    assert proof["ready"] is True
    migrated = json.loads(owner.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert migrated["proof_source"] == "legacy_bound_credentials"

    owner.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "account_ref": "acct_account_a",
                "bound_at": "2026-08-13T08:00:00+00:00",
                "reason": "new_empty_state",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        account_state,
        "_credential_mtime",
        lambda _path: 1_786_608_001.0,
    )

    with pytest.raises(AccountStateError) as error:
        account_state.verify_offline_account_state(
            "agentmesh_live_new_or_other_account_key",
            app_dir=tmp_path,
            credential_file=credentials,
            env_key_in_use=False,
        )

    assert error.value.payload["error"] == "offline_account_proof_required"
