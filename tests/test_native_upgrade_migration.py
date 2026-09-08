from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from jobagent.infra import browser_work, client_upgrade, rounds


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def old_round() -> dict:
    return {
        "schema_version": 3, "round_id": "round-preserved", "account_ref": "test-account",
        "status": "active", "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z",
        "browser_session_id": "local-cdp-19222", "platform_order": list(rounds.DEFAULT_PLATFORM_ORDER),
        "intent": {"status": "confirmed", "target_roles": ["Product Manager"], "profile_digest": "original"},
        "platforms": {
            "boss": {"status": "completed", "evidence": {"delivered": 2}},
            "liepin": {"status": "reviewed", "evidence": {
                "request_id": "liepin:preserved", "discover_id": "discover-preserved",
                "preview_id": "preview-preserved", "authorization_id": "auth-preserved",
            }, "native_delivery": {"preview_id": "preview-preserved", "authorization_id": "auth-preserved"}},
            "zhilian": {"status": "pending"}, "51job": {"status": "pending"},
        },
        "interaction_receipt": {"interaction_id": "confirmed-original"},
        "user_extension": {"keep": [1, 2]},
    }


def seed(root: Path) -> None:
    state = root / "state"
    write_json(state / "client_upgrade_state.json", {
        "state_migration_version": 7, "client_version": "0.5.44", "protocol_version": 1, "status": "ready",
    })
    write_json(state / "current_round.json", old_round())
    write_json(state / "rounds/round-preserved.json", old_round())
    for relative, value in {
        "state_owner.json": {"account_ref": "test-account"},
        "profile.json": {"schema_version": 1, "preferences": {"targetRoles": [{"title": "Product Manager"}]}},
        "discoveries/liepin/pending-start.json": {"request_id": "liepin:preserved", "collection": {"completed_pages": [[0, 1]]}},
        "discoveries/liepin/pending-decision.json": {"discover_id": "discover-preserved"},
        "discoveries/liepin/discover-preserved.json": {"signature": "signed-original"},
        "discoveries/liepin/discover-preserved.review.json": {
            "manifest": {"signature": "signed-original"}, "delivery_preview": {"preview_id": "preview-preserved"},
            "delivery_authorization": {"authorization_id": "auth-preserved"},
        },
        "pending_interaction.json": {"kind": "delivery_confirmation", "interaction_id": "original"},
        "audit_log.json": [{"job_id": "boss-one", "delivered": True}],
        "liepin_audit_log.json": [{"job_id": "liepin-one", "delivered": True}],
        "zhilian_audit_log.json": [{"job_id": "zhilian-one", "delivered": True}],
        "job51_audit_log.json": [{"job_id": "51job-one", "status": "unresolved"}],
        "browser_session.json": {"executor": "codex_native", "window_reference": "private-test-window"},
        "native_browser_session.json": {"session_id": "preserved-native-session"},
        "platform_tabs.json": {"liepin": "old-tab"},
    }.items():
        write_json(state / relative, value)
    (root / "credentials").write_text("jobagent_live_test_fixture\n")
    write_json(root / "chrome-profile/Default/Preferences", {"keep": True})


def ledger(root: Path, state: str = "closed", side_effect: int = 1) -> Path:
    path = root / "state/browser-work.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        browser_work._initialize(conn)
        conn.execute("INSERT INTO works VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
            "bw_fixture", "digest", "deliver_application", "{}",
            '{"account_ref":"test-account","round_id":"round-preserved"}', side_effect, state,
            "nonce", None, 0, "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z",
        ))
    return path


def test_v0544_to_v060_changes_only_round_executor_metadata_and_upgrade_marker(tmp_path):
    root = tmp_path / "app"
    seed(root)
    ledger(root)
    before = files(root)
    report = client_upgrade.run_client_upgrade(app_dir=root, current_version="0.6.0", protocol_version=1)
    assert report["ok"] and report["state_migration_version"] == 8
    assert report["cleared"] == report["archived"] == []
    assert report["migrated"] == ["state/current_round.json"]
    for name, data in before.items():
        if name not in {"state/current_round.json", "state/client_upgrade_state.json"}:
            assert (root / name).read_bytes() == data
    migrated = json.loads((root / "state/current_round.json").read_text())
    assert migrated["schema_version"] == 4
    assert migrated["browser_executor"] == "codex_native"
    assert migrated["browser_session_id"] == "native-unbound"
    assert migrated["legacy_browser_session_id"] == "local-cdp-19222"
    assert migrated["native_session"] is None
    for key in ("round_id", "account_ref", "intent", "platforms", "interaction_receipt", "user_extension"):
        assert migrated[key] == old_round()[key]
    migrated_bytes = (root / "state/current_round.json").read_bytes()
    again = client_upgrade.run_client_upgrade(app_dir=root, current_version="0.6.0", protocol_version=1)
    assert not again["upgrade_detected"]
    assert again["cleared"] == again["migrated"] == again["archived"] == []
    assert (root / "state/current_round.json").read_bytes() == migrated_bytes


@pytest.mark.parametrize("state", ["intent_recorded", "reconcile_only"])
def test_native_side_effect_defers_every_upgrade_write_and_uses_requested_root(tmp_path, monkeypatch, state):
    root = tmp_path / "custom-app"
    seed(root)
    ledger(root, state)
    # Deliberately damaged JSON and protocol boundary must not trigger an archive.
    (root / "state/current_round.json").write_text("{interrupted")
    write_json(root / "state/client_upgrade_state.json", {"state_migration_version": 7, "client_version": "0.5.44", "protocol_version": 0})
    monkeypatch.setattr(browser_work.state_store, "STATE_DIR", tmp_path / "different-state")
    before = files(root)
    report = client_upgrade.run_client_upgrade(app_dir=root, current_version="0.6.0", protocol_version=1)
    assert not report["ok"] and report["migration_pending"] and report["request_preserved"]
    assert report["conflicts"][-1]["code"] == "native_browser_work_inflight"
    assert report["conflicts"][-1]["work_state"] == state
    assert report["next_suggested"] == "jobagent work status"
    assert report["cleared"] == report["migrated"] == report["archived"] == []
    assert files(root) == before


@pytest.mark.parametrize("state,side_effect", [("ready", 1), ("closed", 1), ("intent_recorded", 0)])
def test_no_native_side_effect_does_not_block_migration(tmp_path, state, side_effect):
    root = tmp_path / "app"
    seed(root)
    path = ledger(root, state, side_effect)
    before = path.read_bytes()
    assert client_upgrade.run_client_upgrade(app_dir=root, current_version="0.6.0")["ok"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", ["not_sqlite", "empty", "version", "columns", "invalid_state"])
def test_unreadable_or_unsupported_native_ledger_is_fail_closed_and_unchanged(tmp_path, damage):
    root = tmp_path / "app"
    seed(root)
    path = ledger(root)
    if damage == "not_sqlite":
        path.write_bytes(b"not sqlite")
    elif damage == "empty":
        path.write_bytes(b"")
    else:
        with sqlite3.connect(path) as conn:
            if damage == "version":
                conn.execute("PRAGMA user_version = 99")
            elif damage == "columns":
                conn.execute("DROP TABLE receipts")
            else:
                conn.execute("PRAGMA ignore_check_constraints = ON")
                conn.execute("UPDATE works SET state = 'ambiguous'")
    before = files(root)
    report = client_upgrade.run_client_upgrade(app_dir=root, current_version="0.6.0")
    assert report["conflicts"][-1]["code"] == "native_browser_work_storage_unavailable"
    assert files(root) == before


def test_native_ledger_is_opened_explicitly_read_only(tmp_path, monkeypatch):
    root = tmp_path / "app"
    seed(root)
    path = ledger(root)
    connect = sqlite3.connect
    calls = []

    def observe(database, *args, **kwargs):
        calls.append((database, kwargs))
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(client_upgrade.sqlite3, "connect", observe)
    assert client_upgrade._native_work_upgrade_conflict(root) is None
    assert calls == [(path.absolute().as_uri() + "?mode=ro", {"uri": True, "timeout": 1.0})]


def test_read_error_preserves_marker_and_every_asset(tmp_path, monkeypatch):
    root = tmp_path / "app"
    seed(root)
    ledger(root)
    before = files(root)

    def cannot_open(*args, **kwargs):
        raise sqlite3.OperationalError("unable to open")

    monkeypatch.setattr(client_upgrade.sqlite3, "connect", cannot_open)
    report = client_upgrade.run_client_upgrade(app_dir=root, current_version="0.6.0")
    assert report["conflicts"][-1]["code"] == "native_browser_work_storage_unavailable"
    assert files(root) == before


@pytest.mark.parametrize("command", ["work", "work-next", "work-begin", "work-submit", "work-status"])
def test_work_recovery_only_exempts_native_inflight_not_account_or_profile_conflicts(command):
    report = {"ok": False, "conflicts": [{"code": "native_browser_work_inflight"}], "next_suggested": "jobagent work status"}
    assert client_upgrade.enforce_upgrade_for_command(command, report) == report
    for code in ("retired_api_key", "profile_incompatible", "native_browser_work_storage_unavailable"):
        blocked = {**report, "conflicts": report["conflicts"] + [{"code": code}]}
        with pytest.raises(client_upgrade.UpgradeCompatibilityError):
            client_upgrade.enforce_upgrade_for_command(command, blocked)


def test_existing_native_session_and_delivery_are_preserved_by_schema_migration():
    payload = old_round()
    payload["browser_session_id"] = "native-preserved"
    payload["native_session"] = {"id": "native-preserved", "account_ref": "test-account", "round_id": "round-preserved", "accounts": {"liepin": "test-account-label"}}
    original = deepcopy(payload)
    migrated = rounds.migrate_round_payload(payload)
    assert payload == original
    assert migrated["native_session"] == original["native_session"]
    assert migrated["browser_session_id"] == "native-preserved"
    assert migrated["platforms"] == original["platforms"]
    assert rounds.migrate_round_payload(migrated) == migrated


def test_round_status_preserves_inflight_old_schema_without_implicit_migration(tmp_path, monkeypatch):
    root = tmp_path / "app"
    seed(root)
    ledger(root, "reconcile_only")
    current = root / "state/current_round.json"
    monkeypatch.setattr(rounds, "current_round_path", lambda: current)
    monkeypatch.setattr(rounds, "rounds_dir", lambda: root / "state/rounds")
    before = files(root)
    assert rounds.ensure_current_round()["schema_version"] == 3
    status = rounds.round_status()
    assert status["platforms"]["liepin"]["native_delivery"] == old_round()["platforms"]["liepin"]["native_delivery"]
    assert files(root) == before
