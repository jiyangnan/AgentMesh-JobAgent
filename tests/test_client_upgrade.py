from __future__ import annotations

import importlib
import json
import os
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


def test_upgrade_json_writer_uses_process_unique_temporary_file(
    monkeypatch,
    tmp_path,
):
    upgrade = importlib.import_module("jobagent.infra.client_upgrade")
    path = tmp_path / "client_upgrade_state.json"
    observed = []
    original_replace = Path.replace

    def observe_replace(self, target):
        observed.append((self.name, Path(target).name))
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", observe_replace)

    upgrade._write_json(path, {"ok": True})

    assert observed == [
        (f".client_upgrade_state.json.{os.getpid()}.tmp", path.name)
    ]
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}
    assert not (tmp_path / observed[0][0]).exists()


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
    assert report["version_changed"] is False
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
    assert migrated_round["schema_version"] == 3
    assert migrated_round["migration"]["from_schema_version"] == 1
    assert migrated_round["intent"]["status"] == "legacy_implicit"
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


def test_delivery_confirmation_upgrade_preserves_decision_and_requires_fresh_review(tmp_path):
    upgrade = importlib.import_module("jobagent.infra.client_upgrade")
    app_dir = tmp_path / ".jobagent"
    state = app_dir / "state"
    _seed_preserved_state(app_dir)
    _write_json(
        state / "client_upgrade_state.json",
        {
            "client_version": "0.5.7",
            "protocol_version": 1,
            "state_migration_version": 3,
            "status": "ready",
        },
    )
    _write_json(
        state / "current_round.json",
        {
            "schema_version": 3,
            "round_id": "round-old-preview",
            "status": "active",
            "platform_order": ["boss", "liepin", "zhilian", "51job"],
            "platforms": {
                "boss": {
                    "status": "reviewed",
                    "next_suggested": (
                        "jobagent boss greet send --input old.review.json "
                        "--preview-id dpv_old"
                    ),
                },
                "liepin": {"status": "pending"},
                "zhilian": {"status": "pending"},
                "51job": {"status": "pending"},
            },
            "intent": {
                "status": "confirmed",
                "target_roles": ["数据分析师"],
                "source": "user_explicit",
            },
        },
    )
    review_path = state / "discoveries" / "boss" / "dis-old.review.json"
    _write_json(
        review_path,
        {
            "platform": "boss",
            "discover_id": "dis-old",
            "manifest": {"signed": True},
            "user_overrides": [
                {"job_id": "review-1", "from": "review", "to": "selected"}
            ],
            "send_candidates": [{"id": "selected-1"}, {"id": "review-1"}],
            "delivery_preview": {
                "preview_id": "dpv_old",
                "requires_user_confirmation": False,
                "automatic_continuation": True,
            },
            "delivery_authorization": {"authorization_id": "legacy"},
        },
    )
    target_role_pending = {
        "kind": "target_role_confirmation",
        "interaction_id": "target-role-1",
    }
    _write_json(state / "pending_interaction.json", target_role_pending)

    report = upgrade.run_client_upgrade(
        app_dir=app_dir,
        current_version="0.5.8",
        protocol_version=1,
    )

    migrated_review = json.loads(review_path.read_text(encoding="utf-8"))
    migrated_round = json.loads(
        (state / "current_round.json").read_text(encoding="utf-8")
    )
    assert report["ok"] is True
    assert report["state_migration_version"] == 5
    assert "delivery_preview" not in migrated_review
    assert "delivery_authorization" not in migrated_review
    assert [item["id"] for item in migrated_review["send_candidates"]] == [
        "selected-1",
        "review-1",
    ]
    assert migrated_review["user_overrides"][0]["job_id"] == "review-1"
    assert migrated_round["platforms"]["boss"]["status"] == "awaiting_delivery_confirmation"
    assert migrated_round["platforms"]["boss"]["next_suggested"] == (
        "jobagent boss greet preview"
    )
    assert json.loads(
        (state / "pending_interaction.json").read_text(encoding="utf-8")
    ) == target_role_pending


def test_reviewability_upgrade_invalidates_pending_zhilian_preview_without_losing_decision(
    tmp_path,
):
    upgrade = importlib.import_module("jobagent.infra.client_upgrade")
    app_dir = tmp_path / ".jobagent"
    state = app_dir / "state"
    _seed_preserved_state(app_dir)
    _write_json(
        state / "client_upgrade_state.json",
        {
            "client_version": "0.5.22",
            "protocol_version": 1,
            "state_migration_version": 4,
            "status": "ready",
        },
    )
    _write_json(
        state / "current_round.json",
        {
            "schema_version": 3,
            "round_id": "round-zhilian-review-repair",
            "status": "active",
            "platform_order": ["boss", "liepin", "zhilian", "51job"],
            "platforms": {
                "boss": {"status": "completed"},
                "liepin": {"status": "completed"},
                "zhilian": {
                    "status": "awaiting_delivery_confirmation",
                    "next_suggested": (
                        "jobagent interaction respond --interaction-id old-preview "
                        "--choice confirm_all"
                    ),
                    "evidence": {
                        "discover_id": "dis-review-repair",
                        "preview_id": "dpv_bad",
                    },
                },
                "51job": {"status": "pending"},
            },
        },
    )
    decision_path = state / "discoveries" / "zhilian" / "dis-review-repair.json"
    review_path = state / "discoveries" / "zhilian" / "dis-review-repair.review.json"
    signed_manifest = {"manifest_type": "decision_manifest", "signature": "preserved"}
    _write_json(
        decision_path,
        {
            "platform": "zhilian",
            "discover_id": "dis-review-repair",
            "manifest": signed_manifest,
        },
    )
    _write_json(
        review_path,
        {
            "platform": "zhilian",
            "discover_id": "dis-review-repair",
            "manifest": signed_manifest,
            "send_candidates": [{"id": "job-bad", "title": "查看更多信息"}],
            "delivery_preview": {"preview_id": "dpv_bad"},
        },
    )
    _write_json(
        state / "pending_interaction.json",
        {
            "kind": "delivery_confirmation",
            "interaction_id": "old-preview",
            "context": {"discover_id": "dis-review-repair"},
        },
    )

    report = upgrade.run_client_upgrade(
        app_dir=app_dir,
        current_version="0.5.23",
        protocol_version=1,
    )

    migrated_round = json.loads(
        (state / "current_round.json").read_text(encoding="utf-8")
    )
    migrated_review = json.loads(review_path.read_text(encoding="utf-8"))
    assert report["state_migration_version"] == 5
    assert decision_path.exists()
    assert migrated_review["manifest"] == signed_manifest
    assert "delivery_preview" not in migrated_review
    assert not (state / "pending_interaction.json").exists()
    assert migrated_round["platforms"]["zhilian"]["status"] == (
        "awaiting_delivery_confirmation"
    )
    assert migrated_round["platforms"]["zhilian"]["next_suggested"] == (
        "jobagent zhilian apply review"
    )
    assert "preview_id" not in migrated_round["platforms"]["zhilian"]["evidence"]


def test_reviewability_upgrade_preserves_other_platform_preview_and_interaction(tmp_path):
    upgrade = importlib.import_module("jobagent.infra.client_upgrade")
    app_dir = tmp_path / ".jobagent"
    state = app_dir / "state"
    _seed_preserved_state(app_dir)
    _write_json(
        state / "client_upgrade_state.json",
        {
            "client_version": "0.5.22",
            "protocol_version": 1,
            "state_migration_version": 4,
            "status": "ready",
        },
    )
    current_round = {
        "schema_version": 3,
        "round_id": "round-boss-preview",
        "status": "active",
        "platform_order": ["boss", "liepin", "zhilian", "51job"],
        "platforms": {
            "boss": {
                "status": "awaiting_delivery_confirmation",
                "next_suggested": "jobagent interaction respond --interaction-id boss-preview",
                "evidence": {"discover_id": "dis-boss", "preview_id": "dpv-boss"},
            },
            "liepin": {"status": "pending"},
            "zhilian": {"status": "pending"},
            "51job": {"status": "pending"},
        },
    }
    review_path = state / "discoveries" / "boss" / "dis-boss.review.json"
    review_payload = {
        "platform": "boss",
        "discover_id": "dis-boss",
        "manifest": {"signed": True},
        "send_candidates": [{"id": "boss-1", "title": "产品经理"}],
        "delivery_preview": {"preview_id": "dpv-boss"},
    }
    interaction = {
        "kind": "delivery_confirmation",
        "interaction_id": "boss-preview",
        "context": {"discover_id": "dis-boss"},
    }
    _write_json(state / "current_round.json", current_round)
    _write_json(review_path, review_payload)
    _write_json(state / "pending_interaction.json", interaction)

    report = upgrade.run_client_upgrade(
        app_dir=app_dir,
        current_version="0.5.23",
        protocol_version=1,
    )

    assert report["ok"] is True
    assert json.loads(review_path.read_text(encoding="utf-8")) == review_payload
    assert json.loads(
        (state / "pending_interaction.json").read_text(encoding="utf-8")
    ) == interaction
    assert json.loads(
        (state / "current_round.json").read_text(encoding="utf-8")
    ) == current_round


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
    assert report["version_changed"] is True
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
