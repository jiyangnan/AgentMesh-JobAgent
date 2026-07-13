from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _seed_preserved_state(app_dir: Path) -> None:
    state = app_dir / "state"
    (app_dir / "credentials").parent.mkdir(parents=True, exist_ok=True)
    (app_dir / "credentials").write_text("jobagent_live_current\n", encoding="utf-8")
    _write_json(
        state / "profile.json",
        {
            "schema_version": 1,
            "preferences": {"targetRoles": [{"title": "AI产品经理"}]},
        },
    )
    _write_json(state / "audit_log.json", [{"job_id": "boss-1", "delivered": True}])
    _write_json(state / "liepin_audit_log.json", [{"job_id": "liepin-1"}])
    _write_json(state / "support_state.json", {"star_prompt_shown": True})
    _write_json(state / "discoveries" / "boss-old.json", {"manifest": {"signed": True}})


def test_old_install_clears_only_ephemeral_state_and_migrates_round(tmp_path):
    upgrade = importlib.import_module("jobagent.infra.client_upgrade")
    app_dir = tmp_path / ".jobagent"
    state = app_dir / "state"
    _seed_preserved_state(app_dir)
    for name in (
        "release_manifest_cache.json",
        "platform_tabs.json",
        "browser_session.json",
        "last_doctor_report.json",
        "last_probe_send.json",
    ):
        _write_json(state / name, {"stale": True})
    _write_json(
        state / "current_round.json",
        {
            "schema_version": 1,
            "round_id": "legacy-round",
            "status": "active",
            "platforms": {"boss": {"status": "completed"}},
        },
    )
    _write_json(state / "locks" / "activity.lock", {"pid": 999999})
    (state / "locks" / "update.lock").write_text("999999", encoding="utf-8")

    report = upgrade.run_client_upgrade(
        app_dir=app_dir,
        current_version="0.3.16",
        protocol_version=1,
        pid_alive=lambda _pid: False,
    )

    assert report["ok"] is True
    assert report["upgrade_detected"] is True
    assert set(report["cleared"]) == {
        "state/release_manifest_cache.json",
        "state/platform_tabs.json",
        "state/browser_session.json",
        "state/last_doctor_report.json",
        "state/last_probe_send.json",
        "state/locks/activity.lock",
        "state/locks/update.lock",
    }
    for name in (
        "release_manifest_cache.json",
        "platform_tabs.json",
        "browser_session.json",
        "last_doctor_report.json",
        "last_probe_send.json",
    ):
        assert not (state / name).exists()
    assert (app_dir / "credentials").read_text(encoding="utf-8").strip() == "jobagent_live_current"
    assert json.loads((state / "profile.json").read_text(encoding="utf-8"))["schema_version"] == 1
    assert (state / "audit_log.json").exists()
    assert (state / "liepin_audit_log.json").exists()
    assert (state / "support_state.json").exists()
    assert (state / "discoveries" / "boss-old.json").exists()
    migrated_round = json.loads((state / "current_round.json").read_text(encoding="utf-8"))
    assert migrated_round["schema_version"] == 2
    assert migrated_round["migration"]["from_schema_version"] == 1
    marker = json.loads((state / "client_upgrade_state.json").read_text(encoding="utf-8"))
    assert marker["client_version"] == "0.3.16"
    assert marker["state_migration_version"] == upgrade.STATE_MIGRATION_VERSION


def test_upgrade_conflicts_block_platform_but_allow_recovery_commands(tmp_path):
    upgrade = importlib.import_module("jobagent.infra.client_upgrade")
    app_dir = tmp_path / ".jobagent"
    state = app_dir / "state"
    (app_dir / "credentials").parent.mkdir(parents=True, exist_ok=True)
    (app_dir / "credentials").write_text("jba_live_retired\n", encoding="utf-8")
    _write_json(state / "profile.json", {"hardSkills": {"tools": ["JIRA"]}})

    report = upgrade.run_client_upgrade(
        app_dir=app_dir,
        current_version="0.3.16",
        protocol_version=1,
    )

    assert report["ok"] is False
    assert [item["code"] for item in report["conflicts"]] == [
        "retired_api_key",
        "profile_incompatible",
    ]
    with pytest.raises(upgrade.UpgradeCompatibilityError) as exc:
        upgrade.enforce_upgrade_for_command("boss", report)
    assert exc.value.payload["error"] == "client_upgrade_required"
    assert exc.value.payload["next_suggested"] == "jobagent init --key <your_api_key>"
    assert upgrade.enforce_upgrade_for_command("init", report) == report
    assert upgrade.enforce_upgrade_for_command("resume", report) == report
    assert upgrade.enforce_upgrade_for_command("upgrade-check", report) == report
    assert upgrade.enforce_upgrade_for_command("round-status", report) == report
    with pytest.raises(upgrade.UpgradeCompatibilityError):
        upgrade.enforce_upgrade_for_command("round-skip", report)


def test_upgrade_migration_is_idempotent(tmp_path):
    upgrade = importlib.import_module("jobagent.infra.client_upgrade")
    app_dir = tmp_path / ".jobagent"
    _seed_preserved_state(app_dir)
    _write_json(app_dir / "state" / "platform_tabs.json", {"tabs": {"boss": "stale"}})

    first = upgrade.run_client_upgrade(
        app_dir=app_dir,
        current_version="0.3.16",
        protocol_version=1,
    )
    second = upgrade.run_client_upgrade(
        app_dir=app_dir,
        current_version="0.3.16",
        protocol_version=1,
    )

    assert first["upgrade_detected"] is True
    assert "state/platform_tabs.json" in first["cleared"]
    assert second["upgrade_detected"] is False
    assert second["cleared"] == []
    assert second["ok"] is True
    assert (app_dir / "state" / "audit_log.json").exists()
    assert (app_dir / "state" / "discoveries" / "boss-old.json").exists()


def test_protocol_change_archives_unsigned_runtime_decisions_without_touching_audit(tmp_path):
    upgrade = importlib.import_module("jobagent.infra.client_upgrade")
    app_dir = tmp_path / ".jobagent"
    _seed_preserved_state(app_dir)
    _write_json(
        app_dir / "state" / "client_upgrade_state.json",
        {
            "client_version": "0.3.14",
            "protocol_version": 0,
            "state_migration_version": upgrade.STATE_MIGRATION_VERSION,
        },
    )

    report = upgrade.run_client_upgrade(
        app_dir=app_dir,
        current_version="0.3.16",
        protocol_version=1,
    )

    assert report["ok"] is True
    assert not (app_dir / "state" / "discoveries").exists()
    archived = list((app_dir / "state" / "archive").glob("discoveries-protocol-0-*"))
    assert len(archived) == 1
    assert (archived[0] / "boss-old.json").exists()
    assert (app_dir / "state" / "audit_log.json").exists()


def test_live_process_defers_migration_and_next_run_retries_it(tmp_path):
    upgrade = importlib.import_module("jobagent.infra.client_upgrade")
    app_dir = tmp_path / ".jobagent"
    _seed_preserved_state(app_dir)
    _write_json(app_dir / "state" / "platform_tabs.json", {"stale": True})
    _write_json(app_dir / "state" / "locks" / "activity.lock", {"pid": 42})

    blocked = upgrade.run_client_upgrade(
        app_dir=app_dir,
        current_version="0.3.16",
        protocol_version=1,
        pid_alive=lambda pid: pid == 42,
    )

    assert blocked["ok"] is False
    assert blocked["cleared"] == []
    assert (app_dir / "state" / "platform_tabs.json").exists()
    marker = json.loads(
        (app_dir / "state" / "client_upgrade_state.json").read_text(encoding="utf-8")
    )
    assert marker["migration_pending"] is True

    recovered = upgrade.run_client_upgrade(
        app_dir=app_dir,
        current_version="0.3.16",
        protocol_version=1,
        pid_alive=lambda _pid: False,
    )

    assert recovered["ok"] is True
    assert recovered["upgrade_detected"] is True
    assert "state/platform_tabs.json" in recovered["cleared"]
    assert "state/locks/activity.lock" in recovered["cleared"]


def test_invalid_current_round_is_archived_instead_of_crashing_later(tmp_path):
    upgrade = importlib.import_module("jobagent.infra.client_upgrade")
    app_dir = tmp_path / ".jobagent"
    _seed_preserved_state(app_dir)
    round_path = app_dir / "state" / "current_round.json"
    round_path.write_text("{broken", encoding="utf-8")

    report = upgrade.run_client_upgrade(
        app_dir=app_dir,
        current_version="0.3.16",
        protocol_version=1,
    )

    assert report["ok"] is True
    assert not round_path.exists()
    archived = list((app_dir / "state" / "archive").glob("current_round-invalid-*.json"))
    assert len(archived) == 1
    assert archived[0].read_text(encoding="utf-8") == "{broken"
    assert archived[0].relative_to(app_dir).as_posix() in report["archived"]
