from __future__ import annotations

import json

import pytest

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
    assert not (state / "archive").exists()
    assert not (state / "browser_session.json").exists()
    (state / "profile.json").write_text('{"owner":"b"}', encoding="utf-8")

    restored = switch_account_state(
        _account("acct_account_a"),
        new_state=True,
        app_dir=tmp_path,
    )

    assert "profile.json" in restored["restored"]
    assert "archive" in restored["restored"]
    assert json.loads((state / "profile.json").read_text(encoding="utf-8"))["owner"] == "a"
    assert json.loads(
        (state / "archive" / "old-decision.json").read_text(encoding="utf-8")
    )["owner"] == "a"
    assert state_owner_status("acct_account_a", app_dir=tmp_path)["ready"] is True
    saved_b = tmp_path / "accounts" / "acct_account_b" / "state" / "profile.json"
    assert json.loads(saved_b.read_text(encoding="utf-8"))["owner"] == "b"


def test_bound_state_rejects_silent_reassignment(tmp_path):
    ensure_account_state(_account("acct_account_a"), app_dir=tmp_path)

    with pytest.raises(AccountStateError) as error:
        ensure_account_state(_account("acct_account_b"), app_dir=tmp_path)

    assert error.value.payload["error"] == "local_state_account_mismatch"
    assert error.value.payload["next_suggested"] == "jobagent account switch --new-state"
