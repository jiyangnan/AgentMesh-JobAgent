from __future__ import annotations

import base64
import json
import os
import sys
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jobagent.cli import (
    _dispatch,
    _login,
    _maybe_update,
    _prepare_client_upgrade,
    _verify_state_owner_for_command,
    _with_login_workflow,
    build_parser,
)
from jobagent.infra.protocol import (
    ProtocolError,
    SearchPlanExpiredError,
    canonical_json_bytes,
    candidate_digest,
    digest_payload,
    verify_search_plan,
)


def _key_pair():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return private, base64.urlsafe_b64encode(public).decode("ascii").rstrip("=")


def _sign(private, payload):
    signed = dict(payload)
    signed["key_id"] = "test-key"
    signed["signature_algorithm"] = "Ed25519"
    signed["signature"] = (
        base64.urlsafe_b64encode(private.sign(canonical_json_bytes(signed)))
        .decode("ascii")
        .rstrip("=")
    )
    return signed


def _future(hours=1):
    return (
        (datetime.now(timezone.utc) + timedelta(hours=hours))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _past(minutes=1):
    return (
        (datetime.now(timezone.utc) - timedelta(minutes=minutes))
        .isoformat()
        .replace("+00:00", "Z")
    )


def test_public_parser_exposes_only_v3_platform_commands():
    parser = build_parser()
    assert parser.parse_args(["upgrade-check"]).command == "upgrade-check"
    assert parser.parse_args(["account", "status"]).account_command == "status"
    assert (
        parser.parse_args(["account", "bind", "--confirm-legacy"]).confirm_legacy
        is True
    )
    assert parser.parse_args(["account", "switch", "--new-state"]).new_state is True
    assert parser.parse_args(["round", "status"]).round_command == "status"
    assert parser.parse_args(["round", "start"]).round_command == "start"
    round_start = parser.parse_args(
        [
            "round",
            "start",
            "--accept-suggested",
            "--target-role",
            "数据运营经理",
        ]
    )
    assert round_start.accept_suggested is True
    assert round_start.target_role == ["数据运营经理"]
    interaction = parser.parse_args(
        [
            "interaction",
            "respond",
            "--interaction-id",
            "city-1",
            "--target-city",
            "郑州",
            "--target-city",
            "杭州",
        ]
    )
    assert interaction.target_city == ["郑州", "杭州"]
    assert (
        parser.parse_args(["round", "audit", "--failures-only"]).failures_only is True
    )
    assert (
        parser.parse_args(
            ["round", "skip", "--platform", "liepin", "--confirm-skip"]
        ).confirm_skip
        is True
    )
    assert parser.parse_args(["boss", "discover"]).platform_command == "discover"
    assert parser.parse_args(["boss", "greet", "send", "--dry-run"]).limit == 100
    assert parser.parse_args(["liepin", "apply", "review"]).apply_command == "review"
    assert parser.parse_args(["zhilian", "apply", "send", "--dry-run"]).dry_run is True
    assert parser.parse_args(["51job", "audit"]).platform_command == "audit"
    assert parser.parse_args(["boss", "audit", "--details"]).details is True
    assert (
        parser.parse_args(["browser", "diagnose", "--platform", "boss"]).browser_command
        == "diagnose"
    )

    for retired in (
        ["boss", "collect"],
        ["boss", "rank"],
        ["liepin", "greet", "preview"],
        ["zhilian", "apply", "open"],
        ["51job", "rank", "--local"],
        ["resume", "analyze", "--file", "resume.pdf", "--local"],
        ["boss", "greet", "send", "--confirm-send"],
        ["liepin", "apply", "send", "--confirm-submit"],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(retired)


def test_discover_rejects_incompatible_profile_before_cloud(tmp_path, monkeypatch):
    import jobagent.application.discover as application

    old_profile = {
        "hardSkills": {"tools": ["JIRA"]},
        "career": {
            "careerTrend": "upward",
            "stability": {"avgTenure": ""},
        },
        "preferences": {
            "targetRoles": [{"title": "AI产品经理", "confidence": 0.98, "priority": 1}]
        },
        "qualitySignals": {
            "language": "zh-CN",
            "structureScore": 1.0,
        },
    }
    monkeypatch.setattr(application, "profile_path", lambda: tmp_path / "profile.json")
    monkeypatch.setattr(application, "load_json", lambda _path: old_profile)

    def unexpected_cloud_call(**_kwargs):
        pytest.fail("incompatible profile reached the cloud")

    monkeypatch.setattr(
        application.cloud_client, "discovery_start", unexpected_cloud_call
    )

    with pytest.raises(ValueError, match="resume analyze"):
        application.run_discover("boss")


def test_resume_analyze_stamps_profile_schema_version(tmp_path, monkeypatch):
    from jobagent import cli

    source = tmp_path / "resume.txt"
    source.write_text(
        "A sufficiently long resume body for schema testing.", encoding="utf-8"
    )
    output = tmp_path / "profile.json"
    monkeypatch.setattr(
        "jobagent.domain.resume_parser.ResumeParser.parse",
        lambda self, path: source.read_text(),
    )
    monkeypatch.setattr(
        cli,
        "_resume_analyze",
        cli._resume_analyze,
    )
    monkeypatch.setattr(
        "jobagent.infra.cloud_client.resume_analyze",
        lambda *_args, **_kwargs: {
            "profile": {"preferences": {"targetRoles": [{"title": "AI产品经理"}]}}
        },
    )
    args = build_parser().parse_args(
        [
            "resume",
            "analyze",
            "--file",
            str(source),
            "--target-cities",
            "深圳",
            "--output",
            str(output),
        ]
    )

    cli._resume_analyze(args)

    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["schema_version"] >= 1


def test_first_resume_analysis_requires_target_cities_before_cloud_call(
    tmp_path,
    monkeypatch,
):
    from jobagent import cli

    source = tmp_path / "resume.txt"
    source.write_text("A sufficiently long resume body for city testing.", encoding="utf-8")
    monkeypatch.setattr(
        "jobagent.domain.resume_parser.ResumeParser.parse",
        lambda self, path: source.read_text(),
    )
    monkeypatch.setattr(
        "jobagent.infra.cloud_client.resume_analyze",
        lambda *_args, **_kwargs: pytest.fail("missing cities reached the cloud"),
    )
    monkeypatch.setattr(
        "jobagent.infra.state.profile_path",
        lambda: tmp_path / "profile.json",
    )

    result = cli._resume_analyze(
        build_parser().parse_args(["resume", "analyze", "--file", str(source)])
    )

    assert result["ok"] is False
    assert result["error"] == "target_cities_required"
    assert result["requires_user_action"] is True
    assert "本轮想看的城市" in result["user_prompt"]
    assert result["next_suggested"] == (
        "jobagent resume analyze --file <resume> --target-cities <city1> [city2 ...]"
    )
    assert not (tmp_path / "profile.json").exists()


def test_init_rejects_legacy_license_key_without_overwriting_credentials(monkeypatch):
    from jobagent import cli

    saved = []
    monkeypatch.setattr("jobagent.infra.credentials.save_api_key", saved.append)
    args = build_parser().parse_args(["init", "--key", "jba_live_old_key"])

    with pytest.raises(ValueError, match="API key"):
        cli._init(args)

    assert saved == []


def test_init_verifies_new_api_key_before_saving(monkeypatch):
    from jobagent import cli

    calls = []
    monkeypatch.setattr(
        "jobagent.infra.cloud_client.me",
        lambda *, api_key=None: (
            calls.append(("verify", api_key))
            or {"account": {"id": 1, "account_ref": "acct_test_account"}}
        ),
    )
    monkeypatch.setattr(
        "jobagent.infra.credentials.save_api_key",
        lambda key: calls.append(("save", key)) or Path("/tmp/credentials"),
    )
    monkeypatch.setattr(
        "jobagent.infra.account_state.ensure_account_state",
        lambda _account, **_kwargs: {"status": "ready", "ready": True},
    )
    args = build_parser().parse_args(["init", "--key", "jobagent_live_new"])

    result = cli._init(args)

    assert calls == [
        ("verify", "jobagent_live_new"),
        ("save", "jobagent_live_new"),
    ]
    assert result["account"]["account"]["id"] == 1


def test_upgrade_check_reports_legacy_key_and_profile_together(tmp_path, monkeypatch):
    from jobagent.infra import upgrade_readiness

    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps({"hardSkills": {"tools": ["JIRA"]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(upgrade_readiness, "load_api_key", lambda: "jba_live_old")
    monkeypatch.setattr(upgrade_readiness, "profile_path", lambda: profile)

    result = upgrade_readiness.run_upgrade_check()

    assert result["ok"] is False
    assert [check["error"] for check in result["checks"][:2]] == [
        "retired_license_key",
        "profile_incompatible",
    ]
    assert "jobagent init --key" in result["next_suggested"]


def test_doctor_env_verifies_current_api_key(monkeypatch):
    from jobagent import cli
    from jobagent.infra.cloud_client import CloudError

    monkeypatch.setattr(
        "jobagent.infra.credentials.load_api_key", lambda: "jobagent_live_bad"
    )
    monkeypatch.setattr("jobagent.infra.cloud_client.health", lambda: {"status": "ok"})
    monkeypatch.setattr(
        "jobagent.infra.cloud_client.me",
        lambda: (_ for _ in ()).throw(
            CloudError("invalid", status=401, code="invalid_api_key")
        ),
    )

    result = cli._doctor_env()

    assert result["ok"] is False
    assert result["api_key_configured"] is True
    assert result["api_key_valid"] is False
    assert result["api_key_error"] == "invalid_api_key"
    assert "jobagent init --key" in result["api_key_action"]


def test_doctor_env_treats_signup_trial_as_immediately_usable(tmp_path, monkeypatch):
    from jobagent import cli

    monkeypatch.setattr(
        "jobagent.infra.credentials.load_api_key", lambda: "agentmesh_live_trial"
    )
    monkeypatch.setattr("jobagent.infra.cloud_client.health", lambda: {"status": "ok"})
    monkeypatch.setattr(
        "jobagent.infra.cloud_client.me",
        lambda: {
            "account": {
                "credit": 50,
                "tier": "free",
                "unlimited": False,
                "source": "signup_trial",
                "expires_at": "2026-07-30T00:00:00Z",
                "account_ref": "acct_test_account",
            }
        },
    )
    monkeypatch.setattr(
        "jobagent.infra.state.profile_path", lambda: tmp_path / "profile.json"
    )
    monkeypatch.setattr(
        "jobagent.infra.account_state.ensure_account_state",
        lambda _account, **_kwargs: {"status": "ready", "ready": True},
    )
    monkeypatch.setattr(
        "jobagent.infra.rounds.round_status",
        lambda: {
            "status": "not_started",
            "next_suggested": "jobagent round start",
        },
    )

    result = cli._doctor_env()

    assert result["ok"] is True
    assert result["api_key_valid"] is True
    assert result["cloud_access"] == {
        "usable": True,
        "reason": "signup_trial_active",
        "credit": 50,
        "source": "signup_trial",
        "expires_at": "2026-07-30T00:00:00Z",
        "required_credits": 5,
        "paid_pass_required": False,
    }
    assert result["api_key_action"] is None
    assert result["next_suggested"] == "jobagent resume analyze --file <resume>"


def test_doctor_env_reports_healthy_environment_when_credits_are_insufficient(
    tmp_path, monkeypatch
):
    from jobagent import cli

    monkeypatch.setattr(
        "jobagent.infra.credentials.load_api_key", lambda: "agentmesh_live_empty"
    )
    monkeypatch.setattr("jobagent.infra.cloud_client.health", lambda: {"status": "ok"})
    monkeypatch.setattr(
        "jobagent.infra.cloud_client.me",
        lambda: {
            "account": {
                "account_ref": "acct_test_account",
                "credit": 0,
                "tier": "free",
                "unlimited": False,
                "source": "signup_trial",
                "expires_at": "2026-07-30T00:00:00Z",
            }
        },
    )
    monkeypatch.setattr(
        "jobagent.infra.account_state.ensure_account_state",
        lambda _account, **_kwargs: {"status": "ready", "ready": True},
    )
    monkeypatch.setattr(
        "jobagent.infra.state.profile_path", lambda: tmp_path / "profile.json"
    )
    monkeypatch.setattr(
        "jobagent.infra.rounds.round_status",
        lambda: {
            "status": "not_started",
            "next_suggested": "jobagent round start",
        },
    )

    result = cli._doctor_env()

    assert result["ok"] is True
    assert result["environment_healthy"] is True
    assert result["cloud_access"]["usable"] is False
    assert result["workflow"]["ready"] is False
    assert result["api_key_action"] is None
    assert result["next_suggested"] == "https://agentmesh360.com/app/#pricing"


def test_round_skip_requires_explicit_confirmation(monkeypatch):
    args = build_parser().parse_args(["round", "skip", "--platform", "liepin"])

    assert _dispatch(args) == {
        "ok": False,
        "error": "user_confirmation_required",
        "platform": "liepin",
        "message": "Explicitly confirm skipping this platform for the current round.",
    }


def test_round_skip_updates_only_current_round(monkeypatch):
    updates: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "jobagent.infra.rounds.set_platform_status",
        lambda platform, status, **_kwargs: updates.append((platform, status)),
    )
    monkeypatch.setattr(
        "jobagent.infra.rounds.round_status",
        lambda: {"round_id": "round-1", "current_platform": "zhilian"},
    )
    monkeypatch.setattr(
        "jobagent.infra.rounds.assert_platform_turn",
        lambda platform: {"current_platform": platform},
    )
    args = build_parser().parse_args(
        ["round", "skip", "--platform", "zhilian", "--confirm-skip"]
    )

    result = _dispatch(args)

    assert updates == [("zhilian", "skipped_this_round")]
    assert result["ok"] is True
    assert result["workflow"]["current_platform"] == "zhilian"


def test_round_status_uses_key_bound_local_state_during_transient_outage(monkeypatch):
    from jobagent.infra import account_state, cloud_client

    monkeypatch.setattr(
        "jobagent.infra.credentials.load_api_key",
        lambda: "agentmesh_live_account_a_secret",
    )
    monkeypatch.setattr(
        cloud_client,
        "me",
        lambda: (_ for _ in ()).throw(
            cloud_client.CloudError(
                "TLS connection ended unexpectedly",
                code="tls_connection_eof",
                retryable=True,
            )
        ),
    )
    monkeypatch.setattr(
        account_state,
        "verify_offline_account_state",
        lambda _key: {
            "ready": True,
            "offline": True,
            "stale": True,
            "verified_at": "2026-08-13T08:00:00+00:00",
        },
    )
    args = build_parser().parse_args(["round", "status"])

    verification = _verify_state_owner_for_command(args)

    assert verification == {
        "mode": "offline",
        "offline": True,
        "stale": True,
        "verified_at": "2026-08-13T08:00:00+00:00",
        "reason_code": "tls_connection_eof",
    }


def test_round_status_does_not_fallback_on_certificate_failure(monkeypatch):
    from jobagent.infra import account_state, cloud_client

    monkeypatch.setattr(
        "jobagent.infra.credentials.load_api_key",
        lambda: "agentmesh_live_account_a_secret",
    )
    monkeypatch.setattr(
        cloud_client,
        "me",
        lambda: (_ for _ in ()).throw(
            cloud_client.CloudError(
                "TLS certificate verification failed",
                code="tls_certificate_verification_failed",
                retryable=False,
            )
        ),
    )
    monkeypatch.setattr(
        account_state,
        "verify_offline_account_state",
        lambda _key: pytest.fail("certificate failure used offline local state"),
    )
    args = build_parser().parse_args(["round", "status"])

    with pytest.raises(cloud_client.CloudError) as error:
        _verify_state_owner_for_command(args)

    assert error.value.code == "tls_certificate_verification_failed"


def test_confirmed_round_skip_can_write_after_offline_proof(monkeypatch):
    from jobagent.infra import account_state, cloud_client

    updates: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "jobagent.infra.credentials.load_api_key",
        lambda: "agentmesh_live_account_a_secret",
    )
    monkeypatch.setattr(
        cloud_client,
        "me",
        lambda: (_ for _ in ()).throw(
            cloud_client.CloudError(
                "gateway unavailable",
                status=503,
                code="cloud_gateway_unavailable",
                retryable=True,
            )
        ),
    )
    monkeypatch.setattr(
        account_state,
        "verify_offline_account_state",
        lambda _key: {
            "ready": True,
            "offline": True,
            "stale": True,
            "verified_at": "2026-08-13T08:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        "jobagent.infra.rounds.assert_platform_turn",
        lambda platform: {"current_platform": platform},
    )
    monkeypatch.setattr(
        "jobagent.infra.rounds.set_platform_status",
        lambda platform, status, **_kwargs: updates.append((platform, status)),
    )
    monkeypatch.setattr(
        "jobagent.infra.rounds.round_status",
        lambda: {
            "round_id": "round-1",
            "current_platform": "51job",
            "next_suggested": "jobagent 51job login --check",
        },
    )
    args = build_parser().parse_args(
        ["round", "skip", "--platform", "zhilian", "--confirm-skip"]
    )

    verification = _verify_state_owner_for_command(args)
    result = _dispatch(args)

    assert verification["mode"] == "offline"
    assert updates == [("zhilian", "skipped_this_round")]
    assert result["workflow"]["current_platform"] == "51job"


def test_main_marks_offline_round_status_as_stale(monkeypatch, capsys):
    from jobagent import cli

    monkeypatch.setattr(cli, "_maybe_update", lambda _args: None)
    monkeypatch.setattr(cli, "_prepare_client_upgrade", lambda _args: None)
    monkeypatch.setattr(
        cli,
        "_verify_state_owner_for_command",
        lambda _args: {
            "mode": "offline",
            "offline": True,
            "stale": True,
            "verified_at": "2026-08-13T08:00:00+00:00",
            "reason_code": "network_timeout",
        },
    )
    monkeypatch.setattr(
        cli,
        "_dispatch",
        lambda _args: {"ok": True, "workflow": {"current_platform": "zhilian"}},
    )
    monkeypatch.setattr("sys.argv", ["jobagent", "round", "status"])

    cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["offline"] is True
    assert payload["stale"] is True
    assert payload["account_verification"]["reason_code"] == "network_timeout"


def test_main_marks_offline_round_control_error_as_stale(monkeypatch, capsys):
    from jobagent import cli
    from jobagent.infra.rounds import RoundOrderError

    verification = {
        "mode": "offline",
        "offline": True,
        "stale": True,
        "verified_at": "2026-08-13T08:00:00+00:00",
        "reason_code": "cloud_gateway_unavailable",
    }
    monkeypatch.setattr(cli, "_maybe_update", lambda _args: None)
    monkeypatch.setattr(cli, "_prepare_client_upgrade", lambda _args: None)
    monkeypatch.setattr(
        cli,
        "_verify_state_owner_for_command",
        lambda _args: verification,
    )
    monkeypatch.setattr(
        cli,
        "_dispatch",
        lambda _args: (_ for _ in ()).throw(
            RoundOrderError(
                {
                    "ok": False,
                    "error": "platform_out_of_order",
                    "workflow": {"current_platform": "zhilian"},
                }
            )
        ),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["jobagent", "round", "skip", "--platform", "51job", "--confirm-skip"],
    )

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"] == "platform_out_of_order"
    assert payload["offline"] is True
    assert payload["stale"] is True
    assert payload["account_verification"] == verification


def test_dispatch_checks_round_order_before_opening_platform_browser(monkeypatch):
    from jobagent.infra.rounds import RoundOrderError

    opened = False

    def fail_order(_platform):
        raise RoundOrderError(
            {
                "ok": False,
                "error": "platform_out_of_order",
                "current_platform": "boss",
            }
        )

    def fake_login(_platform, _args):
        nonlocal opened
        opened = True
        return {"ok": True}

    monkeypatch.setattr("jobagent.infra.rounds.assert_platform_turn", fail_order)
    monkeypatch.setattr("jobagent.cli._login", fake_login)
    args = build_parser().parse_args(["liepin", "login", "--check"])

    with pytest.raises(RoundOrderError):
        _dispatch(args)

    assert opened is False


def test_successful_login_check_advances_round_to_discover(monkeypatch):
    from jobagent.platforms.liepin.session import (
        LiepinSessionGuide,
        LiepinSessionStatus,
    )

    statuses: list[tuple[str, str]] = []
    monkeypatch.setattr(LiepinSessionGuide, "__init__", lambda self: None)
    monkeypatch.setattr(
        LiepinSessionGuide,
        "check",
        lambda self: LiepinSessionStatus(ok=True, logged_in=True, login_required=False),
    )
    monkeypatch.setattr(
        "jobagent.infra.rounds.set_platform_status",
        lambda platform, status, **_kwargs: statuses.append((platform, status)),
    )
    monkeypatch.setattr(
        "jobagent.infra.rounds.round_status",
        lambda: {"round_id": "round-1", "next_suggested": "jobagent liepin discover"},
    )
    args = build_parser().parse_args(["liepin", "login", "--check"])

    result = _login("liepin", args)

    assert statuses == [("liepin", "login_verified")]
    assert result["workflow"]["next_suggested"] == "jobagent liepin discover"


def test_successful_login_check_preserves_reviewed_progress(monkeypatch):
    statuses: list[tuple[str, str, str]] = []
    workflows = iter(
        [
            {
                "platforms": {
                    "boss": {
                        "status": "reviewed",
                        "next_suggested": "jobagent boss greet send --input /tmp/review.json",
                        "evidence": {"discover_id": "discover-1"},
                    }
                }
            },
            {
                "next_suggested": "jobagent boss greet send --input /tmp/review.json",
            },
        ]
    )
    monkeypatch.setattr("jobagent.infra.rounds.round_status", lambda: next(workflows))
    monkeypatch.setattr(
        "jobagent.infra.rounds.set_platform_status",
        lambda platform, status, **kwargs: statuses.append(
            (platform, status, kwargs["next_suggested"])
        ),
    )

    result = _with_login_workflow("boss", {"ok": True, "logged_in": True})

    assert statuses == [
        ("boss", "reviewed", "jobagent boss greet send --input /tmp/review.json")
    ]
    assert (
        result["next_suggested"] == "jobagent boss greet send --input /tmp/review.json"
    )


def test_successful_login_check_persists_bound_verification_receipt(monkeypatch):
    captured: list[dict] = []
    workflows = iter(
        [
            {
                "round_id": "round-1",
                "browser_session_id": "local-cdp-19222",
                "platforms": {"zhilian": {"status": "pending"}},
            },
            {"next_suggested": "jobagent zhilian discover"},
        ]
    )
    monkeypatch.setattr("jobagent.infra.rounds.round_status", lambda: next(workflows))
    monkeypatch.setattr(
        "jobagent.infra.rounds.set_platform_status",
        lambda _platform, _status, **kwargs: captured.append(kwargs["evidence"]),
    )

    _with_login_workflow("zhilian", {"ok": True, "logged_in": True})

    login = captured[0]["login"]
    assert login["schema_version"] == 1
    assert login["platform"] == "zhilian"
    assert login["round_id"] == "round-1"
    assert login["browser_session_id"] == "local-cdp-19222"
    assert datetime.fromisoformat(login["verified_at"]).tzinfo is not None


def test_login_reauthentication_restores_reviewed_progress(monkeypatch):
    statuses: list[tuple[str, str, dict, str]] = []
    workflow = {
        "platforms": {
            "boss": {
                "status": "reviewed",
                "next_suggested": "jobagent boss greet send --input /tmp/review.json",
                "evidence": {"discover_id": "discover-1"},
            }
        }
    }
    blocked_workflow = {
        "platforms": {
            "boss": {
                "status": "blocked",
                "next_suggested": "jobagent boss login --check",
                "evidence": {
                    "discover_id": "discover-1",
                    "resume_status": "reviewed",
                    "resume_next_suggested": "jobagent boss greet send --input /tmp/review.json",
                },
            }
        }
    }
    after_restore = {
        "next_suggested": "jobagent boss greet send --input /tmp/review.json"
    }
    workflows = iter([workflow, blocked_workflow, blocked_workflow, after_restore])
    monkeypatch.setattr("jobagent.infra.rounds.round_status", lambda: next(workflows))
    monkeypatch.setattr(
        "jobagent.infra.rounds.set_platform_status",
        lambda platform, status, **kwargs: statuses.append(
            (platform, status, kwargs["evidence"], kwargs["next_suggested"])
        ),
    )

    blocked = _with_login_workflow(
        "boss",
        {"ok": False, "logged_in": False, "requires_user_action": True},
    )
    restored = _with_login_workflow("boss", {"ok": True, "logged_in": True})

    assert statuses[0][1] == "blocked"
    assert statuses[0][2]["resume_status"] == "reviewed"
    assert blocked["next_suggested"] == "jobagent boss login --check"
    assert statuses[1][1] == "reviewed"
    assert statuses[1][2]["discover_id"] == "discover-1"
    assert (
        restored["next_suggested"]
        == "jobagent boss greet send --input /tmp/review.json"
    )


def test_login_reauthentication_infers_reviewed_progress_after_interruption(
    monkeypatch,
):
    statuses: list[tuple[str, str, str]] = []
    workflows = iter(
        [
            {
                "platforms": {
                    "boss": {
                        "status": "blocked",
                        "next_suggested": "jobagent boss greet send --input /tmp/review.json",
                        "evidence": {"error": "interrupted"},
                    }
                }
            },
            {"next_suggested": "jobagent boss greet send --input /tmp/review.json"},
        ]
    )
    monkeypatch.setattr("jobagent.infra.rounds.round_status", lambda: next(workflows))
    monkeypatch.setattr(
        "jobagent.infra.rounds.set_platform_status",
        lambda platform, status, **kwargs: statuses.append(
            (platform, status, kwargs["next_suggested"])
        ),
    )

    result = _with_login_workflow("boss", {"ok": True, "logged_in": True})

    assert statuses == [
        ("boss", "reviewed", "jobagent boss greet send --input /tmp/review.json")
    ]
    assert (
        result["next_suggested"] == "jobagent boss greet send --input /tmp/review.json"
    )


@pytest.mark.parametrize(
    "guide_class",
    [
        pytest.param(
            __import__(
                "jobagent.platforms.liepin.session", fromlist=["LiepinSessionGuide"]
            ).LiepinSessionGuide,
            id="liepin",
        ),
        pytest.param(
            __import__(
                "jobagent.platforms.zhilian.session", fromlist=["ZhilianSessionGuide"]
            ).ZhilianSessionGuide,
            id="zhilian",
        ),
        pytest.param(
            __import__(
                "jobagent.platforms.job51.session", fromlist=["Job51SessionGuide"]
            ).Job51SessionGuide,
            id="51job",
        ),
    ],
)
def test_browser_open_failure_is_not_misreported_as_login_required(guide_class):
    class OpenFailureDriver:
        def open_url_in_new_tab(self, _url, wait_seconds=5):
            return {"ok": False, "error": "browser_start_failed"}

    status = guide_class(driver=OpenFailureDriver()).check(wait_seconds=0)
    payload = status.to_dict()

    assert status.ok is False
    assert status.login_required is False
    assert payload["error"] == "browser_start_failed"
    assert "requires_user_action" not in payload


def test_send_dispatch_passes_delivery_authorization_handoff(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "jobagent.infra.rounds.assert_platform_turn", lambda platform: None
    )
    monkeypatch.setattr(
        "jobagent.application.delivery.send_reviewed",
        lambda platform, **kwargs: calls.append((platform, kwargs)) or {"ok": True},
    )
    args = build_parser().parse_args(["liepin", "apply", "send"])

    assert _dispatch(args) == {"ok": True}
    assert calls == [
        (
            "liepin",
            {
                "input_path": None,
                "preview_id": None,
                "authorization_id": None,
                "limit": 100,
                "dry_run": False,
                "stop_on_failure": True,
            },
        )
    ]


def test_public_docs_require_user_confirmed_delivery_authorization():
    root = Path(__file__).resolve().parents[1]
    docs = [
        (root / "AGENTS.md").read_text(encoding="utf-8"),
        (root / "README.md").read_text(encoding="utf-8"),
        (root / "docs/agent-onboarding.md").read_text(encoding="utf-8"),
        (root / "skills/claude-code/SKILL.md").read_text(encoding="utf-8"),
        (root / "skills/openclaw-job-agent/SKILL.md").read_text(encoding="utf-8"),
    ]

    for text in docs:
        assert "confirm_all" in text
        assert "exclude_jobs" in text
        assert "cancel_delivery" in text
        assert "--authorization-id" in text
        assert "automatic selected delivery" not in text


def test_public_readme_identifies_the_official_product_site():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")

    assert readme.startswith("# AgentMesh360 Job Agent\n")
    assert "[jobagent.agentmesh360.com](https://jobagent.agentmesh360.com/)" in readme


def test_public_agent_docs_forbid_batch_login_and_require_vertical_completion():
    root = Path(__file__).resolve().parents[1]
    docs = [
        (root / "README.md").read_text(encoding="utf-8"),
        (root / "docs/agent-onboarding.md").read_text(encoding="utf-8"),
        (root / "skills/claude-code/SKILL.md").read_text(encoding="utf-8"),
        (root / "skills/openclaw-job-agent/SKILL.md").read_text(encoding="utf-8"),
    ]

    for text in docs:
        assert "Never pre-login future platforms" in text
        assert "complete its audit before logging in to the next platform" in text


def test_public_agent_docs_encode_zero_credit_signup_and_legacy_trial_compatibility():
    root = Path(__file__).resolve().parents[1]
    agent_contract = (root / "AGENTS.md").read_text(encoding="utf-8")
    docs = [
        (root / "README.md").read_text(encoding="utf-8"),
        (root / "docs/agent-onboarding.md").read_text(encoding="utf-8"),
        (root / "skills/claude-code/SKILL.md").read_text(encoding="utf-8"),
        (root / "skills/openclaw-job-agent/SKILL.md").read_text(encoding="utf-8"),
    ]

    for text in docs:
        assert "active monthly pass and available credits" not in text
        assert "new accounts start with zero cloud credits" in text.lower()
        assert "50 shared trial credits" not in text
        assert "verified signup trial" not in text.lower()
    for skill in docs[2:]:
        assert "jobagent doctor env" in skill
        assert "cloud_access.usable=true" in skill
        assert "environment_healthy" in skill
        assert "workflow.ready" in skill
        assert "paid_pass_required=true" in skill
        assert "无需购买通行证" in skill
        assert "此前发放" in skill
    assert "New accounts start with zero cloud credits" in agent_contract
    assert "50 shared trial credits" not in agent_contract
    assert "jobagent doctor env" in agent_contract
    assert "top-level `next_suggested`" in agent_contract


def test_public_agent_docs_require_explicit_round_start():
    root = Path(__file__).resolve().parents[1]
    docs = [
        (root / "AGENTS.md").read_text(encoding="utf-8"),
        (root / "README.md").read_text(encoding="utf-8"),
        (root / "docs/agent-onboarding.md").read_text(encoding="utf-8"),
        (root / "skills/claude-code/SKILL.md").read_text(encoding="utf-8"),
        (root / "skills/openclaw-job-agent/SKILL.md").read_text(encoding="utf-8"),
    ]

    for text in docs:
        assert "jobagent round start" in text


def test_public_agent_docs_cover_account_recovery_browser_diagnostics_and_compact_audit():
    root = Path(__file__).resolve().parents[1]
    docs = [
        (root / "README.md").read_text(encoding="utf-8"),
        (root / "docs/agent-onboarding.md").read_text(encoding="utf-8"),
        (root / "skills/claude-code/SKILL.md").read_text(encoding="utf-8"),
        (root / "skills/openclaw-job-agent/SKILL.md").read_text(encoding="utf-8"),
    ]

    for text in docs:
        assert "jobagent account bind --confirm-legacy" in text
        assert "jobagent account switch --new-state" in text
        assert "jobagent browser diagnose --platform" in text
        assert "jobagent round audit" in text


def test_public_agent_docs_require_automatic_discover_transport_recovery():
    root = Path(__file__).resolve().parents[1]
    docs = [
        (root / "AGENTS.md").read_text(encoding="utf-8"),
        (root / "README.md").read_text(encoding="utf-8"),
        (root / "docs/agent-onboarding.md").read_text(encoding="utf-8"),
        (root / "skills/claude-code/SKILL.md").read_text(encoding="utf-8"),
        (root / "skills/openclaw-job-agent/SKILL.md").read_text(encoding="utf-8"),
    ]

    for text in docs:
        assert "retryable=true" in text
        assert "request_preserved=true" in text
        assert "next_suggested" in text
        assert "billing_status=not_charged" in text
        assert "offline=true" in text
        assert "stale=true" in text
        assert "offline_account_proof_required" in text
        assert "offline_account_proof_mismatch" in text


def test_public_agent_docs_require_native_interaction_and_text_fallback():
    root = Path(__file__).resolve().parents[1]
    docs = [
        (root / "AGENTS.md").read_text(encoding="utf-8"),
        (root / "README.md").read_text(encoding="utf-8"),
        (root / "docs/agent-onboarding.md").read_text(encoding="utf-8"),
        (root / "skills/claude-code/SKILL.md").read_text(encoding="utf-8"),
        (root / "skills/openclaw-job-agent/SKILL.md").read_text(encoding="utf-8"),
    ]

    for text in docs:
        assert "interaction_required" in text
        assert "fallback" in text
        assert "jobagent interaction respond" in text
        assert "interaction ID" in text
        assert "--target-role" in text


def test_public_agent_docs_treat_zhilian_route_keyword_as_opaque():
    root = Path(__file__).resolve().parents[1]
    docs = [
        (root / "AGENTS.md").read_text(encoding="utf-8"),
        (root / "README.md").read_text(encoding="utf-8"),
        (root / "docs/agent-onboarding.md").read_text(encoding="utf-8"),
        (root / "skills/claude-code/SKILL.md").read_text(encoding="utf-8"),
        (root / "skills/openclaw-job-agent/SKILL.md").read_text(encoding="utf-8"),
    ]

    for text in docs:
        assert "`kw...`" in text
        assert "next_suggested" in text


def test_search_plan_rejects_opaque_platform_keyword(monkeypatch):
    import jobagent.infra.protocol as protocol

    private, public = _key_pair()
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", public)
    profile = {"preferences": {"targetRoles": [{"title": "财务总监"}]}}
    plan = _sign(
        private,
        {
            "manifest_type": "search_plan",
            "protocol_version": 1,
            "discover_id": "dis_opaque_keyword",
            "platform": "zhilian",
            "profile_digest": digest_payload(profile),
            "queries": [
                {
                    "keyword": "01300K004004338VHKHKTEG",
                    "city": "上海",
                    "page_limit": 2,
                }
            ],
            "candidate_limit": 100,
            "expires_at": _future(),
        },
    )

    with pytest.raises(ProtocolError, match="opaque platform identifier"):
        verify_search_plan(plan, platform="zhilian", profile=profile)


def test_search_plan_binds_confirmed_round_intent(monkeypatch):
    import jobagent.infra.protocol as protocol

    private, public = _key_pair()
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", public)
    profile = {"preferences": {"targetRoles": [{"title": "数据分析师"}]}}
    round_intent = {
        "status": "confirmed",
        "target_roles": ["数据运营经理"],
        "source": "user_explicit",
        "profile_digest": digest_payload(profile),
        "confirmed_at": "2026-07-27T08:00:00+00:00",
    }
    plan = _sign(
        private,
        {
            "manifest_type": "search_plan",
            "protocol_version": 1,
            "discover_id": "dis_round_intent",
            "platform": "zhilian",
            "profile_digest": digest_payload(profile),
            "round_intent": round_intent,
            "intent_digest": digest_payload(round_intent),
            "queries": [
                {
                    "keyword": "数据运营经理",
                    "city": "上海",
                    "page_limit": 2,
                }
            ],
            "candidate_limit": 100,
            "expires_at": _future(),
        },
    )

    verified = verify_search_plan(
        plan,
        platform="zhilian",
        profile=profile,
        round_intent=round_intent,
    )

    assert verified["intent_digest"] == digest_payload(round_intent)


def test_search_plan_rejects_changed_round_intent(monkeypatch):
    import jobagent.infra.protocol as protocol

    private, public = _key_pair()
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", public)
    profile = {"preferences": {"targetRoles": [{"title": "数据分析师"}]}}
    signed_intent = {
        "status": "confirmed",
        "target_roles": ["数据运营经理"],
        "source": "user_explicit",
        "profile_digest": digest_payload(profile),
        "confirmed_at": "2026-07-27T08:00:00+00:00",
    }
    plan = _sign(
        private,
        {
            "manifest_type": "search_plan",
            "protocol_version": 1,
            "discover_id": "dis_changed_intent",
            "platform": "zhilian",
            "profile_digest": digest_payload(profile),
            "round_intent": signed_intent,
            "intent_digest": digest_payload(signed_intent),
            "queries": [
                {
                    "keyword": "数据运营经理",
                    "city": "上海",
                    "page_limit": 2,
                }
            ],
            "candidate_limit": 100,
            "expires_at": _future(),
        },
    )
    changed_intent = dict(signed_intent)
    changed_intent["target_roles"] = ["AI产品经理"]

    with pytest.raises(ProtocolError, match="intent digest mismatch"):
        verify_search_plan(
            plan,
            platform="zhilian",
            profile=profile,
            round_intent=changed_intent,
        )


@pytest.mark.parametrize(
    "keyword", ["财务总监", "Business Finance Leader", "FP&A负责人"]
)
def test_search_plan_accepts_readable_role_keywords(keyword, monkeypatch):
    import jobagent.infra.protocol as protocol

    private, public = _key_pair()
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", public)
    profile = {"preferences": {"targetRoles": [{"title": keyword}]}}
    plan = _sign(
        private,
        {
            "manifest_type": "search_plan",
            "protocol_version": 1,
            "discover_id": f"dis_{keyword}",
            "platform": "zhilian",
            "profile_digest": digest_payload(profile),
            "queries": [
                {
                    "keyword": keyword,
                    "city": "上海",
                    "page_limit": 2,
                }
            ],
            "candidate_limit": 100,
            "expires_at": _future(),
        },
    )

    verified = verify_search_plan(plan, platform="zhilian", profile=profile)

    assert verified["queries"][0]["keyword"] == keyword


def test_expired_search_plan_is_distinct_only_after_signature_and_binding_verification(
    monkeypatch,
):
    import jobagent.infra.protocol as protocol

    private, public = _key_pair()
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", public)
    profile = {"preferences": {"targetRoles": [{"title": "数据运营经理"}]}}
    request_id = "zhilian:request-expired"
    plan = _sign(
        private,
        {
            "manifest_type": "search_plan",
            "protocol_version": 1,
            "discover_id": "dis_expired",
            "request_id": request_id,
            "platform": "zhilian",
            "profile_digest": digest_payload(profile),
            "queries": [{"keyword": "数据运营经理", "city": "深圳", "page_limit": 1}],
            "candidate_limit": 100,
            "expires_at": _past(),
        },
    )

    with pytest.raises(SearchPlanExpiredError) as expired:
        verify_search_plan(
            plan,
            platform="zhilian",
            profile=profile,
            request_id=request_id,
        )
    assert expired.value.signed_plan["discover_id"] == "dis_expired"

    tampered = dict(plan)
    tampered["request_id"] = "zhilian:tampered"
    with pytest.raises(ProtocolError, match="signature verification failed"):
        verify_search_plan(
            tampered,
            platform="zhilian",
            profile=profile,
            request_id=request_id,
        )

    with pytest.raises(ProtocolError, match="request binding mismatch"):
        verify_search_plan(
            plan,
            platform="zhilian",
            profile=profile,
            request_id="zhilian:another-request",
        )


def test_discover_renews_expired_start_plan_with_same_request_and_no_browser_guessing(
    tmp_path,
    monkeypatch,
):
    import jobagent.application.discover as application
    import jobagent.infra.discovery_state as discovery_state
    import jobagent.infra.protocol as protocol

    private, public = _key_pair()
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", public)
    profile = {
        "schema_version": 1,
        "preferences": {"targetRoles": [{"title": "数据运营经理"}]},
    }
    intent = {"status": "confirmed", "target_roles": ["数据运营经理"]}
    request_id = "zhilian:request-expired"
    discover_id = "dis_expired_start"

    def plan(*, expires_at: str, revision: int, renewed: bool):
        payload = {
            "manifest_type": "search_plan",
            "protocol_version": 1,
            "discover_id": discover_id,
            "request_id": request_id,
            "platform": "zhilian",
            "profile_digest": digest_payload(profile),
            "intent_digest": digest_payload(intent),
            "round_intent": intent,
            "queries": [{"keyword": "数据运营经理", "city": "深圳", "page_limit": 1}],
            "candidate_limit": 100,
            "expires_at": expires_at,
            "plan_revision": revision,
            "reissued": renewed,
        }
        if renewed:
            payload["renewal"] = {
                "reason": "search_plan_expired",
                "request_id": request_id,
                "discover_id": discover_id,
                "request_preserved": True,
                "same_request_id": True,
                "same_discover_id": True,
                "additional_charge_on_renewal": False,
            }
        return _sign(private, payload)

    expired_plan = plan(expires_at=_past(), revision=1, renewed=False)
    renewed_plan = plan(expires_at=_future(), revision=2, renewed=True)
    candidates = [{"id": "job-1", "title": "数据运营经理"}]
    calls: dict[str, list] = {"start": [], "renew": [], "collect": [], "decision": []}
    monkeypatch.setattr(application, "profile_path", lambda: tmp_path / "profile.json")
    monkeypatch.setattr(application, "load_json", lambda _path: profile)
    monkeypatch.setattr(
        application, "require_compatible_profile", lambda _profile: None
    )
    monkeypatch.setattr(discovery_state, "discoveries_dir", lambda: tmp_path)
    monkeypatch.setattr(application, "load_pending_decision", lambda _platform: None)
    monkeypatch.setattr(
        application.rounds,
        "ensure_current_round",
        lambda: {"round_id": "round-1", "intent": intent},
    )
    login_verification = {
        "valid": True,
        "platform": "zhilian",
        "round_id": "round-1",
        "browser_session_id": "local-cdp-19222",
    }
    monkeypatch.setattr(
        application.rounds,
        "recent_platform_login_verification",
        lambda platform: login_verification if platform == "zhilian" else None,
    )
    monkeypatch.setattr(
        "jobagent.infra.account_state.current_account_ref",
        lambda: "acct_account_a",
    )
    discovery_state.save_pending_start(
        "zhilian",
        request_id=request_id,
        round_id="round-1",
        profile_digest=digest_payload(profile),
        intent_digest=digest_payload(intent),
        account_ref="acct_account_a",
    )

    def start(**kwargs):
        calls["start"].append(kwargs)
        return expired_plan

    def renew(**kwargs):
        calls["renew"].append(kwargs)
        return renewed_plan

    monkeypatch.setattr(application.cloud_client, "discovery_start", start)
    monkeypatch.setattr(application.cloud_client, "discovery_renew", renew)
    monkeypatch.setattr(
        application,
        "collect_from_search_plan",
        lambda value, **kwargs: calls["collect"].append(
            {"plan": value, "kwargs": kwargs}
        )
        or candidates,
    )
    monkeypatch.setattr(
        application, "active_command", lambda *_args, **_kwargs: nullcontext()
    )
    monkeypatch.setattr(
        application, "PlatformSessionLock", lambda *_args, **_kwargs: nullcontext()
    )
    monkeypatch.setattr(
        application,
        "_decision_result",
        lambda platform, **kwargs: (
            calls["decision"].append({"platform": platform, **kwargs})
            or {"ok": True, "discover_id": kwargs["plan"]["discover_id"]}
        ),
    )

    result = application.run_discover("zhilian", page_delay=0)

    assert result == {"ok": True, "discover_id": discover_id}
    assert calls["start"][0]["request_id"] == request_id
    assert calls["renew"] == [
        {
            "discover_id": discover_id,
            "platform": "zhilian",
            "profile_digest": digest_payload(profile),
            "intent_digest": digest_payload(intent),
        }
    ]
    assert len(calls["collect"]) == 1
    assert calls["collect"][0]["plan"]["discover_id"] == discover_id
    assert calls["collect"][0]["plan"]["request_id"] == request_id
    assert calls["collect"][0]["plan"]["plan_revision"] == 2
    assert "signature" not in calls["collect"][0]["plan"]
    assert calls["collect"][0]["kwargs"]["login_verification"] == login_verification
    assert calls["decision"][0]["request_id"] == request_id
    assert calls["decision"][0]["plan"]["plan_revision"] == 2


def test_expired_plan_renewal_failure_returns_complete_safe_recovery_contract(
    monkeypatch,
):
    import jobagent.application.discover as application

    expired_plan = {"discover_id": "dis_expired", "request_id": "zhilian:req"}

    def fail_renewal(**_kwargs):
        raise application.cloud_client.CloudError(
            "gateway unavailable",
            status=503,
            code="cloud_gateway_unavailable",
            retryable=True,
        )

    monkeypatch.setattr(application.cloud_client, "discovery_renew", fail_renewal)
    with pytest.raises(application.cloud_client.CloudError) as error:
        application._renew_expired_plan(
            "zhilian",
            expired_plan=expired_plan,
            profile={"schema_version": 1},
            round_intent=None,
            request_id="zhilian:req",
        )

    assert error.value.code == "search_plan_expired_recovery_pending"
    assert error.value.retryable is True
    assert error.value.details["request_preserved"] is True
    assert error.value.details["request_id"] == "zhilian:req"
    assert error.value.details["discover_id"] == "dis_expired"
    assert error.value.details["next_suggested"] == "jobagent zhilian discover"
    assert error.value.details["no_charge"] is True
    assert error.value.details["billing_status"] == "not_charged"
    assert error.value.details["requires_user_action"] is False
    assert error.value.details["billing"]["additional_charge_on_renewal"] is False


def test_tampered_renewed_plan_requires_user_action_and_never_becomes_recoverable(
    monkeypatch,
):
    import jobagent.application.discover as application

    monkeypatch.setattr(
        application.cloud_client,
        "discovery_renew",
        lambda **_kwargs: {
            "manifest_type": "search_plan",
            "discover_id": "dis_expired",
            "request_id": "zhilian:req",
            "signature_algorithm": "Ed25519",
            "signature": "tampered",
        },
    )

    with pytest.raises(application.cloud_client.CloudError) as error:
        application._renew_expired_plan(
            "zhilian",
            expired_plan={"discover_id": "dis_expired"},
            profile={"schema_version": 1},
            round_intent=None,
            request_id="zhilian:req",
        )

    assert error.value.code == "search_plan_expired_recovery_required"
    assert error.value.retryable is False
    assert (
        error.value.details["renewal_failure_code"]
        == "renewed_plan_verification_failed"
    )
    assert error.value.details["requires_user_action"] is True
    assert error.value.details["request_preserved"] is True
    assert error.value.details["no_charge"] is True


def test_expired_pending_decision_renews_without_recollecting_or_replacing_request(
    tmp_path,
    monkeypatch,
):
    import jobagent.application.discover as application
    import jobagent.infra.protocol as protocol

    private, public = _key_pair()
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", public)
    profile = {
        "schema_version": 1,
        "preferences": {"targetRoles": [{"title": "数据运营经理"}]},
    }
    intent = {"status": "confirmed", "target_roles": ["数据运营经理"]}
    request_id = "zhilian:preserved-after-collection"
    discover_id = "dis_preserved_after_collection"
    candidates = [
        {
            "id": "job-1",
            "title": "数据运营经理",
            "company": "Example",
            "salary": "20-30K",
            "url": "https://example.test/jobs/1",
        }
    ]

    def search_plan(*, expires_at: str, revision: int, renewed: bool):
        payload = {
            "manifest_type": "search_plan",
            "protocol_version": 1,
            "discover_id": discover_id,
            "request_id": request_id,
            "platform": "zhilian",
            "profile_digest": digest_payload(profile),
            "intent_digest": digest_payload(intent),
            "round_intent": intent,
            "queries": [{"keyword": "数据运营经理", "city": "深圳", "page_limit": 1}],
            "candidate_limit": 100,
            "expires_at": expires_at,
            "plan_revision": revision,
            "reissued": renewed,
        }
        if renewed:
            payload["renewal"] = {
                "reason": "search_plan_expired",
                "request_id": request_id,
                "discover_id": discover_id,
                "request_preserved": True,
                "same_request_id": True,
                "same_discover_id": True,
                "additional_charge_on_renewal": False,
            }
        return _sign(private, payload)

    expired_plan = search_plan(expires_at=_past(), revision=1, renewed=False)
    renewed_plan = search_plan(expires_at=_future(), revision=2, renewed=True)
    manifest = _sign(
        private,
        {
            "manifest_type": "decision_manifest",
            "protocol_version": 1,
            "manifest_id": "dm_preserved_after_collection",
            "discover_id": discover_id,
            "platform": "zhilian",
            "candidate_digest": candidate_digest(candidates),
            "intent_digest": digest_payload(intent),
            "input_count": 1,
            "deduplicated_count": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": _future(24),
            "selected": [
                {
                    **candidates[0],
                    "classification": "selected",
                    "score": 91,
                    "reason": "匹配",
                    "risk": "",
                }
            ],
            "review": [],
            "rejected": [],
            "billing": {
                "action": "jobagent.discover",
                "credits": 10,
                "transaction_id": "77",
            },
        },
    )
    monkeypatch.setattr(
        application,
        "load_pending_decision",
        lambda _platform: {
            "platform": "zhilian",
            "discover_id": discover_id,
            "request_id": request_id,
            "plan": expired_plan,
            "jobs": candidates,
        },
    )
    monkeypatch.setattr(
        application.cloud_client,
        "discovery_start",
        lambda **_kwargs: pytest.fail("pending decision created a replacement request"),
    )
    monkeypatch.setattr(
        application,
        "collect_from_search_plan",
        lambda *_args, **_kwargs: pytest.fail("pending decision recollected jobs"),
    )
    decisions: list[dict] = []

    def decide(**kwargs):
        decisions.append(kwargs)
        if len(decisions) == 1:
            raise application.cloud_client.CloudError(
                "search plan expired",
                status=410,
                code="search_plan_expired",
            )
        return manifest

    renewals: list[dict] = []
    monkeypatch.setattr(application.cloud_client, "discovery_decide", decide)
    monkeypatch.setattr(
        application.cloud_client,
        "discovery_renew",
        lambda **kwargs: renewals.append(kwargs) or renewed_plan,
    )
    pending_writes: list[dict] = []
    pending_clears: list[str | None] = []
    monkeypatch.setattr(
        application,
        "save_pending_decision",
        lambda platform, **payload: pending_writes.append(
            {"platform": platform, **payload}
        ),
    )
    monkeypatch.setattr(
        application,
        "clear_pending_decision",
        lambda _platform, *, discover_id=None: pending_clears.append(discover_id),
    )
    monkeypatch.setattr(
        application,
        "save_manifest",
        lambda _manifest: tmp_path / "decision.json",
    )
    monkeypatch.setattr(
        application.rounds, "set_platform_status", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        application.rounds,
        "round_status",
        lambda: {"round_id": "round-1", "status": "active"},
    )

    result = application._resume_pending_decision(
        "zhilian",
        profile=profile,
        round_intent=intent,
    )

    assert result is not None and result["resumed"] is True
    assert result["discover_id"] == discover_id
    assert decisions == [
        {"discover_id": discover_id, "jobs": candidates},
        {"discover_id": discover_id, "jobs": candidates},
    ]
    assert renewals == [
        {
            "discover_id": discover_id,
            "platform": "zhilian",
            "profile_digest": digest_payload(profile),
            "intent_digest": digest_payload(intent),
        }
    ]
    assert pending_writes == [
        {
            "platform": "zhilian",
            "plan": renewed_plan,
            "jobs": candidates,
            "request_id": request_id,
        }
    ]
    assert pending_clears == [discover_id]


def test_discover_verifies_both_signatures_and_discards_raw_candidates(
    tmp_path, monkeypatch, capsys
):
    import jobagent.application.discover as application
    import jobagent.infra.protocol as protocol

    private, public = _key_pair()
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", public)
    profile = {"preferences": {"targetRoles": [{"title": "AI产品经理"}]}}
    candidates = [
        {
            "id": "job-1",
            "title": "AI产品经理",
            "company": "Example",
            "salary": "60-84万/年",
            "url": "https://example.test/1",
        }
    ]
    discover_id = "dis_test123"
    plan = _sign(
        private,
        {
            "manifest_type": "search_plan",
            "protocol_version": 1,
            "discover_id": discover_id,
            "platform": "51job",
            "profile_digest": digest_payload(profile),
            "queries": [{"keyword": "AI产品经理", "city": "深圳", "page_limit": 1}],
            "candidate_limit": 100,
            "expires_at": _future(),
        },
    )
    manifest = _sign(
        private,
        {
            "manifest_type": "decision_manifest",
            "protocol_version": 1,
            "manifest_id": "dm_test",
            "discover_id": discover_id,
            "platform": "51job",
            "candidate_digest": candidate_digest(candidates),
            "input_count": 1,
            "deduplicated_count": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": _future(24),
            "selected": [
                {
                    "id": "job-1",
                    "title": "AI产品经理",
                    "company": "Example",
                    "salary": "60-84万/年",
                    "url": "https://example.test/1",
                    "classification": "selected",
                    "score": 90,
                    "reason": "匹配",
                    "risk": "",
                }
            ],
            "review": [],
            "rejected": [],
            "billing": {
                "action": "jobagent.discover",
                "credits": 10,
                "transaction_id": "77",
            },
        },
    )
    monkeypatch.setattr(application, "profile_path", lambda: tmp_path / "profile.json")
    monkeypatch.setattr(application, "load_json", lambda _path: profile)
    monkeypatch.setattr(
        application.cloud_client, "discovery_start", lambda **_kwargs: plan
    )
    monkeypatch.setattr(
        application.cloud_client, "discovery_decide", lambda **_kwargs: manifest
    )
    monkeypatch.setattr(
        application, "collect_from_search_plan", lambda *_args, **_kwargs: candidates
    )
    monkeypatch.setattr(
        application, "active_command", lambda *_args, **_kwargs: nullcontext()
    )
    monkeypatch.setattr(
        application, "PlatformSessionLock", lambda *_args, **_kwargs: nullcontext()
    )
    pending_writes: list[dict] = []
    pending_clears: list[str | None] = []
    pending_start_writes: list[dict] = []
    pending_start_clears: list[str | None] = []
    monkeypatch.setattr(application, "load_pending_decision", lambda _platform: None)
    monkeypatch.setattr(application, "load_pending_start", lambda _platform: None)
    monkeypatch.setattr(
        application,
        "save_pending_start",
        lambda platform, **payload: pending_start_writes.append(
            {"platform": platform, **payload}
        ),
    )
    monkeypatch.setattr(
        application,
        "clear_pending_start",
        lambda _platform, *, request_id=None: pending_start_clears.append(request_id),
    )
    monkeypatch.setattr(
        application,
        "save_pending_decision",
        lambda platform, **payload: pending_writes.append(
            {"platform": platform, **payload}
        ),
    )
    monkeypatch.setattr(
        application,
        "clear_pending_decision",
        lambda _platform, *, discover_id=None: pending_clears.append(discover_id),
    )
    statuses: list[tuple[str, str]] = []
    monkeypatch.setattr(
        application.rounds,
        "set_platform_status",
        lambda platform, status, **_kwargs: statuses.append((platform, status)),
    )
    monkeypatch.setattr(
        application.rounds,
        "round_status",
        lambda: {
            "round_id": "round-1",
            "next_suggested": "jobagent 51job apply review",
        },
    )
    monkeypatch.setattr(
        application.rounds,
        "ensure_current_round",
        lambda: {
            "round_id": "round-1",
            "intent": {
                "status": "legacy_implicit",
                "target_roles": [],
            },
        },
    )
    output = tmp_path / "decision.json"

    def save(payload):
        output.write_text(json.dumps({"manifest": payload}), encoding="utf-8")
        return output

    monkeypatch.setattr(application, "save_manifest", save)
    result = application.run_discover("51job", page_delay=0)
    assert result["selected"] == 1 and result["credits"] == 10
    assert result["resumed"] is False
    assert statuses == [("51job", "discovered")]
    assert len(pending_writes) == 1
    assert pending_writes[0]["platform"] == "51job"
    assert pending_writes[0]["plan"] == plan
    assert pending_writes[0]["jobs"] == candidates
    assert pending_writes[0]["request_id"] == pending_start_writes[0]["request_id"]
    assert len(pending_start_writes) == 1
    assert pending_start_writes[0]["request_id"].startswith("51job:")
    assert pending_start_clears == [pending_start_writes[0]["request_id"]]
    assert pending_clears == [discover_id]
    assert result["workflow"]["round_id"] == "round-1"
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert "candidates" not in persisted
    assert persisted["manifest"]["candidate_digest"] == candidate_digest(candidates)
    progress = capsys.readouterr().err
    assert '"stage": "search_plan_requested"' in progress
    assert '"stage": "browser_collection_started"' in progress
    assert '"stage": "cloud_decision_requested"' in progress

    monkeypatch.setattr(
        application,
        "load_pending_decision",
        lambda _platform: {
            "platform": "51job",
            "discover_id": discover_id,
            "plan": plan,
            "jobs": candidates,
        },
    )
    monkeypatch.setattr(
        application.cloud_client,
        "discovery_start",
        lambda **_kwargs: pytest.fail("pending decision started a new discovery"),
    )
    monkeypatch.setattr(
        application,
        "collect_from_search_plan",
        lambda *_args, **_kwargs: pytest.fail(
            "pending decision recollected browser jobs"
        ),
    )

    recovered = application.run_discover("51job", page_delay=0)

    assert recovered["resumed"] is True
    assert recovered["discover_id"] == discover_id
    assert pending_clears == [discover_id, discover_id]


def test_discover_start_failure_preserves_and_reuses_request_id(
    tmp_path,
    monkeypatch,
):
    import jobagent.application.discover as application
    import jobagent.infra.discovery_state as discovery_state

    profile = {
        "schema_version": 1,
        "preferences": {"targetRoles": [{"title": "数据运营经理"}]},
    }
    request_ids: list[str] = []
    monkeypatch.setattr(application, "profile_path", lambda: tmp_path / "profile.json")
    monkeypatch.setattr(application, "load_json", lambda _path: profile)
    monkeypatch.setattr(
        application, "require_compatible_profile", lambda _profile: None
    )
    monkeypatch.setattr(application, "load_pending_decision", lambda _platform: None)
    monkeypatch.setattr(discovery_state, "discoveries_dir", lambda: tmp_path)
    monkeypatch.setattr(
        application.rounds,
        "ensure_current_round",
        lambda: {
            "round_id": "round-1",
            "intent": {
                "status": "confirmed",
                "target_roles": ["数据运营经理"],
            },
        },
    )
    monkeypatch.setattr(
        "jobagent.infra.account_state.current_account_ref",
        lambda: "acct_account_a",
    )

    def fail_start(**kwargs):
        request_ids.append(kwargs["request_id"])
        raise application.cloud_client.CloudError(
            "TLS connection ended unexpectedly",
            code="tls_connection_eof",
            retryable=True,
        )

    monkeypatch.setattr(application.cloud_client, "discovery_start", fail_start)
    monkeypatch.setattr(
        application,
        "collect_from_search_plan",
        lambda *_args, **_kwargs: pytest.fail(
            "failed start reached browser collection"
        ),
    )

    errors = []
    for _attempt in range(2):
        with pytest.raises(application.cloud_client.CloudError) as error:
            application.run_discover("zhilian", page_delay=0)
        errors.append(error.value)

    assert len(request_ids) == 2
    assert request_ids[0] == request_ids[1]
    assert request_ids[0].startswith("zhilian:")
    assert errors[-1].details == {
        "request_preserved": True,
        "request_id": request_ids[0],
        "next_suggested": "jobagent zhilian discover",
        "no_charge": True,
        "billing_status": "not_charged",
        "billing": {
            "status": "not_charged",
            "retry_reuses_request_id": True,
            "additional_charge_on_retry": False,
        },
    }
    pending = discovery_state.load_pending_start("zhilian")
    assert pending is not None
    assert pending["request_id"] == request_ids[0]
    assert pending["round_id"] == "round-1"
    assert pending["account_ref"] == "acct_account_a"


@pytest.mark.parametrize(
    ("failure_code", "terminal_code"),
    [
        (
            "zhilian_city_evidence_pending",
            "zhilian_city_evidence_recovery_exhausted",
        ),
        (
            "zhilian_search_navigation_pending",
            "zhilian_search_navigation_recovery_exhausted",
        ),
    ],
)
def test_discover_collection_failure_preserves_request_and_no_charge(
    tmp_path,
    monkeypatch,
    failure_code,
    terminal_code,
):
    import jobagent.application.discover as application
    import jobagent.infra.discovery_state as discovery_state

    profile = {
        "schema_version": 1,
        "preferences": {"targetRoles": [{"title": "产品经理"}]},
    }
    plan = {"platform": "zhilian", "discover_id": "dis_city_recovery", "queries": [{}]}
    request_ids: list[str] = []
    monkeypatch.setattr(application, "profile_path", lambda: tmp_path / "profile.json")
    monkeypatch.setattr(application, "load_json", lambda _path: profile)
    monkeypatch.setattr(
        application, "require_compatible_profile", lambda _profile: None
    )
    monkeypatch.setattr(application, "load_pending_decision", lambda _platform: None)
    monkeypatch.setattr(discovery_state, "discoveries_dir", lambda: tmp_path)
    monkeypatch.setattr(
        application.rounds,
        "ensure_current_round",
        lambda: {
            "round_id": "round-1",
            "intent": {"status": "confirmed", "target_roles": ["产品经理"]},
        },
    )
    monkeypatch.setattr(
        "jobagent.infra.account_state.current_account_ref",
        lambda: "acct_account_a",
    )
    monkeypatch.setattr(
        application,
        "verify_search_plan",
        lambda value, **_kwargs: value,
    )
    monkeypatch.setattr(
        application, "active_command", lambda *_args, **_kwargs: nullcontext()
    )
    monkeypatch.setattr(
        application, "PlatformSessionLock", lambda *_args, **_kwargs: nullcontext()
    )

    def start(**kwargs):
        request_ids.append(kwargs["request_id"])
        return plan

    def fail_collection(*_args, **_kwargs):
        raise application.CollectionError(
            failure_code,
            "Zhilian collection recovery is still pending",
            details={"retryable": True},
        )

    monkeypatch.setattr(application.cloud_client, "discovery_start", start)
    monkeypatch.setattr(application, "collect_from_search_plan", fail_collection)

    errors = []
    for _attempt in range(3):
        with pytest.raises(application.CollectionError) as error:
            application.run_discover("zhilian", page_delay=0)
        errors.append(error.value)

    assert len(set(request_ids)) == 1
    assert errors[0].code == failure_code
    assert errors[0].details["retryable"] is True
    assert errors[0].details["recovery_attempt"] == 1
    assert errors[0].details["recovery_budget"] == 3
    assert errors[1].code == failure_code
    assert errors[1].details["retryable"] is True
    assert errors[1].details["recovery_attempt"] == 2
    assert errors[-1].code == terminal_code
    assert errors[-1].details == {
        "retryable": False,
        "request_preserved": True,
        "request_id": request_ids[0],
        "no_charge": True,
        "billing_status": "not_charged",
        "billing": {
            "status": "not_charged",
            "retry_reuses_request_id": True,
            "additional_charge_on_retry": False,
        },
        "next_suggested": "jobagent browser diagnose --platform zhilian",
        "requires_user_action": True,
        "recovery_attempt": 3,
        "recovery_budget": 3,
        "recovery_exhausted": True,
        "recovery_scope": "request_id",
    }
    pending = discovery_state.load_pending_start("zhilian")
    assert pending is not None
    assert pending["request_id"] == request_ids[0]
    assert pending["collection_recovery"]["attempts"] == 3


def test_liepin_city_failure_preserves_exact_request_without_charge(
    tmp_path,
    monkeypatch,
):
    import jobagent.application.discover as application
    import jobagent.infra.discovery_state as discovery_state

    profile = {
        "schema_version": 1,
        "preferences": {"targetRoles": [{"title": "高级产品经理"}]},
    }
    plan = {
        "platform": "liepin",
        "discover_id": "dis_liepin_city",
        "queries": [{"keyword": "高级产品经理", "city": "郑州", "page_limit": 1}],
    }
    request_ids: list[str] = []
    monkeypatch.setattr(application, "profile_path", lambda: tmp_path / "profile.json")
    monkeypatch.setattr(application, "load_json", lambda _path: profile)
    monkeypatch.setattr(application, "require_compatible_profile", lambda _profile: None)
    monkeypatch.setattr(application, "load_pending_decision", lambda _platform: None)
    monkeypatch.setattr(discovery_state, "discoveries_dir", lambda: tmp_path)
    monkeypatch.setattr(
        application.rounds,
        "ensure_current_round",
        lambda: {
            "round_id": "round-liepin",
            "intent": {
                "status": "confirmed",
                "target_roles": ["高级产品经理"],
                "target_cities": ["郑州", "杭州"],
            },
        },
    )
    monkeypatch.setattr(
        "jobagent.infra.account_state.current_account_ref",
        lambda: "acct_account_a",
    )
    monkeypatch.setattr(application, "verify_search_plan", lambda value, **_kwargs: value)
    monkeypatch.setattr(application, "active_command", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(
        application, "PlatformSessionLock", lambda *_args, **_kwargs: nullcontext()
    )

    def start(**kwargs):
        request_ids.append(kwargs["request_id"])
        return plan

    def fail_collection(*_args, **_kwargs):
        raise application.CollectionError(
            "liepin_city_evidence_unverified",
            "Liepin city evidence did not verify",
        )

    monkeypatch.setattr(application.cloud_client, "discovery_start", start)
    monkeypatch.setattr(application, "collect_from_search_plan", fail_collection)

    for _attempt in range(2):
        with pytest.raises(application.CollectionError) as error:
            application.run_discover("liepin", page_delay=0)
        assert error.value.details["request_preserved"] is True
        assert error.value.details["billing_status"] == "not_charged"
        assert error.value.details["retryable"] is False
        assert error.value.details["next_suggested"] == (
            "jobagent browser diagnose --platform liepin"
        )

    assert len(set(request_ids)) == 1
    pending = discovery_state.load_pending_start("liepin")
    assert pending is not None
    assert pending["request_id"] == request_ids[0]


def test_zhilian_search_navigation_recovery_is_not_classified_as_city_recovery():
    import jobagent.application.discover as application

    assert "zhilian_search_navigation_pending" not in application._ZHILIAN_CITY_RECOVERY_CODES
    assert (
        "zhilian_search_navigation_pending"
        in application._ZHILIAN_SEARCH_NAVIGATION_RECOVERY_CODES
    )
    assert (
        application._ZHILIAN_SEARCH_NAVIGATION_RECOVERY_TERMINAL
        == "zhilian_search_navigation_recovery_exhausted"
    )


def test_collection_recovery_budget_is_bound_to_exact_request(tmp_path, monkeypatch):
    import jobagent.infra.discovery_state as discovery_state

    monkeypatch.setattr(discovery_state, "discoveries_dir", lambda: tmp_path)
    discovery_state.save_pending_start(
        "zhilian",
        request_id="zhilian:req-a",
        round_id="round-1",
        profile_digest="sha256:profile",
        intent_digest="sha256:intent",
        account_ref="acct-a",
    )

    first = discovery_state.record_collection_recovery(
        "zhilian",
        request_id="zhilian:req-a",
        recovery_key="zhilian_city_convergence",
        client_version="0.5.14",
        budget=3,
    )
    assert first["attempts"] == 1

    with pytest.raises(ValueError, match="request mismatch"):
        discovery_state.record_collection_recovery(
            "zhilian",
            request_id="zhilian:req-b",
            recovery_key="zhilian_city_convergence",
            client_version="0.5.14",
            budget=3,
        )


def test_discovery_decision_failure_preserves_idempotent_billing_recovery(monkeypatch):
    import jobagent.application.discover as application

    def fail_decision(**_kwargs):
        raise application.cloud_client.CloudError(
            "gateway unavailable",
            status=503,
            code="cloud_gateway_unavailable",
            retryable=True,
        )

    monkeypatch.setattr(application.cloud_client, "discovery_decide", fail_decision)

    with pytest.raises(application.cloud_client.CloudError) as error:
        application._decision_result(
            "zhilian",
            plan={"discover_id": "dis_stable"},
            candidates=[{"id": "job-1"}],
            resumed=False,
        )

    assert error.value.details == {
        "request_preserved": True,
        "discover_id": "dis_stable",
        "next_suggested": "jobagent zhilian discover",
        "billing_status": "response_pending_reconciliation",
        "billing": {
            "status": "response_pending_reconciliation",
            "retry_reuses_discover_id": True,
            "additional_charge_on_retry": False,
        },
    }


def test_unexpected_cli_error_writes_diagnostic_log(tmp_path, monkeypatch, capsys):
    from jobagent import cli
    from jobagent.infra import diagnostics

    monkeypatch.setattr(diagnostics, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cli, "_maybe_update", lambda _args: None)
    monkeypatch.setattr(cli, "_prepare_client_upgrade", lambda _args: None)
    monkeypatch.setattr(cli, "_verify_state_owner_for_command", lambda _args: None)
    monkeypatch.setattr(
        cli, "_dispatch", lambda _args: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    monkeypatch.setattr("sys.argv", ["jobagent", "profile", "show"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().err)
    log_path = Path(payload["diagnostic_log"])
    assert log_path.exists()
    assert "RuntimeError: boom" in log_path.read_text(encoding="utf-8")


def test_cli_reports_delivery_preview_recovery_without_diagnostic_log(
    monkeypatch,
    capsys,
):
    from jobagent import cli
    from jobagent.infra.delivery_preview import DeliveryPreviewError

    payload = {
        "ok": False,
        "error": "delivery_preview_required",
        "request_preserved": True,
        "requires_user_action": False,
        "next_suggested": "jobagent liepin apply review --input old.review.json",
    }
    monkeypatch.setattr(cli, "_maybe_update", lambda _args: None)
    monkeypatch.setattr(cli, "_prepare_client_upgrade", lambda _args: None)
    monkeypatch.setattr(cli, "_verify_state_owner_for_command", lambda _args: None)
    monkeypatch.setattr(
        cli,
        "_dispatch",
        lambda _args: (_ for _ in ()).throw(DeliveryPreviewError(payload)),
    )
    monkeypatch.setattr("sys.argv", ["jobagent", "liepin", "apply", "send"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    reported = json.loads(capsys.readouterr().err)
    assert reported == payload
    assert "diagnostic_log" not in reported


def test_cli_reports_collection_recovery_contract(monkeypatch, capsys):
    from jobagent import cli
    from jobagent.platforms.discovery import CollectionError

    recovery = {
        "retryable": True,
        "request_preserved": True,
        "request_id": "zhilian:req-stable",
        "next_suggested": "jobagent zhilian discover",
        "billing_status": "not_charged",
        "billing": {
            "status": "not_charged",
            "retry_reuses_request_id": True,
            "additional_charge_on_retry": False,
        },
    }
    monkeypatch.setattr(cli, "_maybe_update", lambda _args: None)
    monkeypatch.setattr(cli, "_prepare_client_upgrade", lambda _args: None)
    monkeypatch.setattr(cli, "_verify_state_owner_for_command", lambda _args: None)
    monkeypatch.setattr(
        cli,
        "_dispatch",
        lambda _args: (_ for _ in ()).throw(
            CollectionError(
                "zhilian_page_state_unknown",
                "Zhilian page state stayed unknown",
                details=recovery,
            )
        ),
    )
    monkeypatch.setattr("sys.argv", ["jobagent", "zhilian", "discover"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    reported = json.loads(capsys.readouterr().err)
    assert reported["error"] == "zhilian_page_state_unknown"
    assert reported["request_preserved"] is True
    assert reported["request_id"] == "zhilian:req-stable"
    assert reported["next_suggested"] == "jobagent zhilian discover"
    assert reported["no_charge"] is True
    assert reported["billing"]["additional_charge_on_retry"] is False


def test_cli_blocks_platform_dispatch_when_upgrade_requires_recovery(
    monkeypatch, capsys
):
    from jobagent import cli
    from jobagent.infra.client_upgrade import UpgradeCompatibilityError

    dispatched = []
    monkeypatch.setattr(cli, "_maybe_update", lambda _args: None)
    monkeypatch.setattr(
        cli,
        "_prepare_client_upgrade",
        lambda _args: (_ for _ in ()).throw(
            UpgradeCompatibilityError(
                {
                    "ok": False,
                    "error": "client_upgrade_required",
                    "conflicts": [{"code": "retired_api_key"}],
                    "next_suggested": "jobagent init --key <your_api_key>",
                }
            )
        ),
    )
    monkeypatch.setattr(cli, "_dispatch", lambda args: dispatched.append(args))
    monkeypatch.setattr("sys.argv", ["jobagent", "boss", "discover"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    assert dispatched == []
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"] == "client_upgrade_required"
    assert payload["next_suggested"] == "jobagent init --key <your_api_key>"
    assert "diagnostic_log" not in payload


def test_review_requires_confirmation_to_promote_and_never_promotes_rejected(
    tmp_path, monkeypatch
):
    import jobagent.infra.protocol as protocol
    from jobagent.application.review import review_decision

    private, public = _key_pair()
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", public)
    manifest = _sign(
        private,
        {
            "manifest_type": "decision_manifest",
            "protocol_version": 1,
            "manifest_id": "dm_review",
            "discover_id": "dis_review",
            "platform": "liepin",
            "candidate_digest": "sha256:" + "a" * 64,
            "input_count": 3,
            "deduplicated_count": 3,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": _future(24),
            "selected": [
                {
                    "id": "s",
                    "title": "Selected",
                    "classification": "selected",
                    "greeting": "您好，selected 个性化招呼语。",
                }
            ],
            "review": [
                {
                    "id": "r",
                    "title": "Review",
                    "classification": "review",
                    "greeting": "您好，review 个性化招呼语。",
                }
            ],
            "rejected": [
                {"id": "x", "title": "Rejected", "classification": "rejected"}
            ],
            "billing": {
                "action": "jobagent.discover",
                "credits": 10,
                "transaction_id": "1",
            },
        },
    )
    source = tmp_path / "decision.json"
    source.write_text(
        json.dumps(
            {"platform": "liepin", "discover_id": "dis_review", "manifest": manifest}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="confirm-promote"):
        review_decision("liepin", input_path=str(source), promoted_ids=["r"])
    with pytest.raises(ValueError, match="Only review jobs"):
        review_decision(
            "liepin", input_path=str(source), promoted_ids=["x"], confirm_promote=True
        )
    output = tmp_path / "reviewed.json"
    statuses: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "jobagent.application.review.rounds.set_platform_status",
        lambda platform, status, **_kwargs: statuses.append((platform, status)),
    )
    monkeypatch.setattr(
        "jobagent.application.review.rounds.round_status",
        lambda: {"round_id": "round-1", "next_suggested": "jobagent liepin apply send"},
    )
    monkeypatch.setattr(
        "jobagent.infra.state.pending_interaction_path",
        lambda: tmp_path / "pending-interaction.json",
    )
    monkeypatch.setattr(
        "jobagent.application.review.current_account_ref",
        lambda: "acct_delivery_test",
    )
    result = review_decision(
        "liepin",
        input_path=str(source),
        promoted_ids=["r"],
        confirm_promote=True,
        output_path=str(output),
    )
    reviewed = json.loads(output.read_text(encoding="utf-8"))
    assert result["send_count"] == 2
    assert result["ok"] is False
    assert result["error"] == "interaction_required"
    assert result["event"] == "delivery_preview"
    assert result["display_required"] is True
    assert result["requires_user_action"] is True
    assert result["delivery_preview"]["protocol"] == "agentmesh360.delivery_preview"
    assert result["delivery_preview"]["requires_user_confirmation"] is True
    assert [item["title"] for item in result["delivery_preview"]["items"]] == [
        "Selected",
        "Review",
    ]
    assert result["interaction"]["kind"] == "delivery_confirmation"
    assert (
        result["delivery_preview"]["preview_id"]
        in result["interaction"]["interaction_id"]
    )
    assert reviewed["delivery_preview"] == result["delivery_preview"]
    assert "delivery_authorization" not in reviewed
    assert [item["id"] for item in reviewed["send_candidates"]] == ["s", "r"]
    assert reviewed["user_overrides"] == [
        {"job_id": "r", "from": "review", "to": "selected"}
    ]
    assert statuses == [("liepin", "awaiting_delivery_confirmation")]
    assert result["workflow"]["round_id"] == "round-1"


def test_reviewing_an_old_review_file_preserves_existing_promotions(
    tmp_path, monkeypatch
):
    import jobagent.infra.protocol as protocol
    from jobagent.application.review import review_decision

    private, public = _key_pair()
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", public)
    manifest = _sign(
        private,
        {
            "manifest_type": "decision_manifest",
            "protocol_version": 1,
            "manifest_id": "dm_old_review",
            "discover_id": "dis_old_review",
            "platform": "zhilian",
            "candidate_digest": "sha256:" + "a" * 64,
            "input_count": 2,
            "deduplicated_count": 2,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": _future(24),
            "selected": [
                {
                    "id": "selected",
                    "title": "数据分析师",
                    "company": "示例科技",
                    "salary": "20-30K",
                    "url": "https://www.zhaopin.com/jobdetail/selected.htm",
                    "classification": "selected",
                }
            ],
            "review": [
                {
                    "id": "promoted",
                    "title": "数据运营经理",
                    "company": "另一家公司",
                    "salary": "25-35K",
                    "url": "https://www.zhaopin.com/jobdetail/promoted.htm",
                    "classification": "review",
                }
            ],
            "rejected": [],
            "billing": {
                "action": "jobagent.discover",
                "credits": 10,
                "transaction_id": "1",
            },
        },
    )
    old_review = tmp_path / "old.review.json"
    old_review.write_text(
        json.dumps(
            {
                "platform": "zhilian",
                "discover_id": "dis_old_review",
                "manifest": manifest,
                "user_overrides": [
                    {"job_id": "promoted", "from": "review", "to": "selected"}
                ],
                "send_candidates": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "jobagent.application.review.rounds.set_platform_status",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "jobagent.application.review.rounds.round_status",
        lambda: {"round_id": "round-1"},
    )

    result = review_decision(
        "zhilian",
        input_path=str(old_review),
        output_path=str(tmp_path / "regenerated.review.json"),
    )

    assert [item["title"] for item in result["delivery_preview"]["items"]] == [
        "数据分析师",
        "数据运营经理",
    ]
    assert result["promoted"] == [
        {"job_id": "promoted", "from": "review", "to": "selected"}
    ]


def test_reviewing_an_adjusted_review_file_preserves_delivery_exclusions(
    tmp_path, monkeypatch
):
    import jobagent.infra.protocol as protocol
    from jobagent.application.review import review_decision

    private, public = _key_pair()
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", public)
    manifest = _sign(
        private,
        {
            "manifest_type": "decision_manifest",
            "protocol_version": 1,
            "manifest_id": "dm_adjusted_review",
            "discover_id": "dis_adjusted_review",
            "platform": "zhilian",
            "candidate_digest": "sha256:" + "a" * 64,
            "input_count": 2,
            "deduplicated_count": 2,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": _future(24),
            "selected": [
                {
                    "id": "keep",
                    "title": "数据分析师",
                    "company": "示例科技",
                    "salary": "20-30K",
                    "url": "https://www.zhaopin.com/jobdetail/keep.htm",
                    "classification": "selected",
                },
                {
                    "id": "exclude",
                    "title": "AI 产品经理",
                    "company": "另一家公司",
                    "salary": "25-35K",
                    "url": "https://www.zhaopin.com/jobdetail/exclude.htm",
                    "classification": "selected",
                },
            ],
            "review": [],
            "rejected": [],
            "billing": {
                "action": "jobagent.discover",
                "credits": 10,
                "transaction_id": "1",
            },
        },
    )
    old_review = tmp_path / "adjusted.review.json"
    old_review.write_text(
        json.dumps(
            {
                "platform": "zhilian",
                "discover_id": "dis_adjusted_review",
                "manifest": manifest,
                "user_overrides": [],
                "user_delivery_exclusions": [
                    {
                        "id": "exclude",
                        "job_id": "exclude",
                        "title": "AI 产品经理",
                    }
                ],
                "send_candidates": [{"id": "keep", "job_id": "keep"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "jobagent.application.review.rounds.set_platform_status",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "jobagent.application.review.rounds.round_status",
        lambda: {"round_id": "round-1"},
    )

    output = tmp_path / "regenerated.review.json"
    result = review_decision(
        "zhilian",
        input_path=str(old_review),
        output_path=str(output),
    )

    reviewed = json.loads(output.read_text(encoding="utf-8"))
    assert [item["title"] for item in result["delivery_preview"]["items"]] == [
        "数据分析师"
    ]
    assert [item["id"] for item in reviewed["send_candidates"]] == ["keep"]
    assert [item["id"] for item in reviewed["user_delivery_exclusions"]] == ["exclude"]


def test_send_marks_platform_sent_and_audit_advances_to_next_platform(monkeypatch):
    import jobagent.application.delivery as delivery
    from jobagent.domain.models import SendAttempt

    reviewed = {
        "discover_id": "dis_boss",
        "send_candidates": [
            {
                "url": "https://www.zhipin.com/job_detail/1.html",
                "cloud_greeting": "您好",
            }
        ],
    }
    statuses: list[tuple[str, str]] = []
    monkeypatch.setattr(delivery, "_load_reviewed", lambda *_args, **_kwargs: reviewed)
    monkeypatch.setattr(
        delivery,
        "_boss_send",
        lambda *_args, **_kwargs: [
            SendAttempt(
                job_url=reviewed["send_candidates"][0]["url"],
                message="您好",
                delivered=True,
            )
        ],
    )
    monkeypatch.setattr(delivery, "_append_boss_audit", lambda _attempts: None)
    monkeypatch.setattr(
        delivery, "active_command", lambda *_args, **_kwargs: nullcontext()
    )
    monkeypatch.setattr(
        delivery, "PlatformSessionLock", lambda *_args, **_kwargs: nullcontext()
    )
    monkeypatch.setattr(
        delivery, "print_first_delivery_star_prompt_once", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        delivery.rounds,
        "set_platform_status",
        lambda platform, status, **_kwargs: statuses.append((platform, status)),
    )
    monkeypatch.setattr(
        delivery.rounds,
        "round_status",
        lambda: {"round_id": "round-1", "next_suggested": "jobagent boss audit"},
    )

    sent = delivery.send_reviewed("boss", limit=100)

    assert statuses == [("boss", "sent")]
    assert sent["workflow"]["next_suggested"] == "jobagent boss audit"

    monkeypatch.setattr(
        delivery,
        "AuditLog",
        lambda: type(
            "Log", (), {"summary": lambda self: {}, "list_recent": lambda self, _n: []}
        )(),
    )
    monkeypatch.setattr(
        delivery.rounds,
        "complete_platform_after_audit",
        lambda platform: {
            "round_id": "round-1",
            "current_platform": "liepin",
            "next_suggested": "jobagent liepin login --check",
        },
    )
    audited = delivery.audit_platform("boss")

    assert audited["workflow"]["current_platform"] == "liepin"
    assert audited["next_suggested"] == "jobagent liepin login --check"
    assert "records" not in audited


def test_round_audit_is_compact_by_default_and_details_are_opt_in(monkeypatch):
    import jobagent.application.delivery as delivery

    class Log:
        def summary(self):
            return {"total": 200, "failed": 3}

        def list_recent(self, _n):
            return [
                {"delivered": True, "error": ""},
                {"delivered": False, "error": "delivery_not_verified"},
            ]

    monkeypatch.setattr(delivery, "_audit_log", lambda _platform: Log())
    monkeypatch.setattr(
        delivery.rounds,
        "round_status",
        lambda: {"round_id": "round-1", "next_suggested": "jobagent boss audit"},
    )

    compact = delivery.audit_round()
    failures = delivery.audit_round(platform="boss", failures_only=True)

    assert compact["failure_count"] == 12
    assert all("records" not in report for report in compact["platforms"].values())
    assert failures["platforms"]["boss"]["record_count"] == 1
    assert (
        failures["platforms"]["boss"]["records"][0]["error"] == "delivery_not_verified"
    )


def test_boss_review_excludes_previously_delivered_jobs(tmp_path, monkeypatch):
    import jobagent.infra.audit as audit
    import jobagent.infra.protocol as protocol
    from jobagent.application.review import review_decision

    private, public = _key_pair()
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", public)
    delivered_url = (
        "https://www.zhipin.com/job_detail/already.html?ka=search_list_jname_1"
    )
    pending_url = "https://www.zhipin.com/job_detail/pending.html"
    manifest = _sign(
        private,
        {
            "manifest_type": "decision_manifest",
            "protocol_version": 1,
            "manifest_id": "dm_boss_review",
            "discover_id": "dis_boss_review",
            "platform": "boss",
            "candidate_digest": "sha256:" + "a" * 64,
            "input_count": 2,
            "deduplicated_count": 2,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": _future(24),
            "selected": [
                {
                    "id": "already",
                    "title": "Already delivered",
                    "url": delivered_url,
                    "classification": "selected",
                    "greeting": "您好，已投递岗位。",
                },
                {
                    "id": "pending",
                    "title": "Pending",
                    "url": pending_url,
                    "classification": "selected",
                    "greeting": "您好，新岗位。",
                },
            ],
            "review": [],
            "rejected": [],
            "billing": {
                "action": "jobagent.discover",
                "credits": 0,
                "transaction_id": "1",
            },
        },
    )
    source = tmp_path / "decision.json"
    source.write_text(
        json.dumps(
            {"platform": "boss", "discover_id": "dis_boss_review", "manifest": manifest}
        ),
        encoding="utf-8",
    )
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(
        json.dumps([{"job_url": delivered_url.split("?", 1)[0], "delivered": True}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(audit, "audit_log_path", lambda: audit_path)
    monkeypatch.setattr(
        "jobagent.application.review.rounds.set_platform_status",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "jobagent.application.review.rounds.round_status",
        lambda: {"round_id": "round-1"},
    )
    output = tmp_path / "reviewed.json"

    result = review_decision("boss", input_path=str(source), output_path=str(output))

    reviewed = json.loads(output.read_text(encoding="utf-8"))
    assert result["send_count"] == 1
    assert result["skipped_delivered_count"] == 1
    assert [item["id"] for item in reviewed["send_candidates"]] == ["pending"]
    assert [item["id"] for item in reviewed["skipped_delivered"]] == ["already"]


def test_boss_send_skips_previously_delivered_jobs_before_opening_browser(
    tmp_path, monkeypatch
):
    import jobagent.infra.audit as audit
    from jobagent.application.delivery import _boss_send
    from jobagent.domain.models import SendAttempt

    delivered_url = "https://www.zhipin.com/job_detail/already.html"
    pending_url = "https://www.zhipin.com/job_detail/pending.html"
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(
        json.dumps([{"job_url": delivered_url, "delivered": True}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(audit, "audit_log_path", lambda: audit_path)
    driver = object()
    opened: list[str] = []
    monkeypatch.setattr("jobagent.drivers.boss.create_driver", lambda **_kwargs: driver)

    def fake_send(_driver, url, message):
        opened.append(url)
        return SendAttempt(job_url=url, message=message, delivered=True)

    monkeypatch.setattr(
        "jobagent.platforms.boss.send_flow.execute_boss_greeting_flow",
        fake_send,
    )

    attempts = _boss_send(
        [
            {"url": delivered_url, "cloud_greeting": "duplicate"},
            {"url": pending_url, "cloud_greeting": "new"},
        ],
        dry_run=False,
    )

    assert opened == [pending_url]
    assert [attempt.error for attempt in attempts] == ["already_delivered", ""]
    assert attempts[1].delivered is True


def test_boss_send_persists_attempt_and_stops_after_platform_default_only(monkeypatch):
    from jobagent.application.delivery import _boss_send
    from jobagent.domain.models import SendAttempt

    driver = object()
    opened: list[str] = []
    persisted: list[SendAttempt] = []
    monkeypatch.setattr("jobagent.drivers.boss.create_driver", lambda **_kwargs: driver)

    def fake_send(_driver, url, message):
        opened.append(url)
        return SendAttempt(
            job_url=url,
            message=message,
            delivered=False,
            error="delivery_not_verified",
            steps=[
                {
                    "step": "platform_default_sent",
                    "platformDefaultSent": True,
                    "autoSent": True,
                }
            ],
        )

    monkeypatch.setattr(
        "jobagent.platforms.boss.send_flow.execute_boss_greeting_flow",
        fake_send,
    )
    attempts = _boss_send(
        [
            {
                "url": "https://www.zhipin.com/job_detail/first.html",
                "cloud_greeting": "one",
            },
            {
                "url": "https://www.zhipin.com/job_detail/second.html",
                "cloud_greeting": "two",
            },
        ],
        dry_run=False,
        on_attempt=lambda attempt, _index, _total: persisted.append(attempt),
    )

    assert opened == ["https://www.zhipin.com/job_detail/first.html"]
    assert attempts == persisted
    assert len(attempts) == 1
    assert attempts[0].delivered is False


def test_boss_send_stops_after_first_generic_failure(monkeypatch):
    from jobagent.application.delivery import _boss_send
    from jobagent.domain.models import SendAttempt

    driver = object()
    opened: list[str] = []
    monkeypatch.setattr("jobagent.drivers.boss.create_driver", lambda **_kwargs: driver)

    def fake_send(_driver, url, message):
        opened.append(url)
        return SendAttempt(
            job_url=url,
            message=message,
            delivered=False,
            error="chat_entry_failed",
            steps=[{"step": "click_chat_entry", "ok": False}],
        )

    monkeypatch.setattr(
        "jobagent.platforms.boss.send_flow.execute_boss_greeting_flow",
        fake_send,
    )

    attempts = _boss_send(
        [
            {
                "url": "https://www.zhipin.com/job_detail/first.html",
                "cloud_greeting": "one",
            },
            {
                "url": "https://www.zhipin.com/job_detail/second.html",
                "cloud_greeting": "two",
            },
        ],
        dry_run=False,
    )

    assert opened == ["https://www.zhipin.com/job_detail/first.html"]
    assert len(attempts) == 1
    assert attempts[0].error == "chat_entry_failed"


def test_boss_audit_does_not_treat_platform_default_only_as_personalized_delivery(
    tmp_path,
):
    from jobagent.infra.audit import AuditLog

    audit_path = tmp_path / "audit.json"
    audit_path.write_text(
        json.dumps(
            [
                {
                    "job_url": "https://www.zhipin.com/job_detail/default-only.html",
                    "delivered": True,
                    "steps": [
                        {
                            "step": "platform_default_sent",
                            "platformDefaultSent": True,
                            "delivered": True,
                        }
                    ],
                },
                {
                    "job_url": "https://www.zhipin.com/job_detail/custom.html",
                    "delivered": True,
                    "steps": [
                        {
                            "step": "platform_default_sent",
                            "platformDefaultSent": True,
                            "delivered": True,
                        },
                        {"step": "verify_delivery", "delivered": True},
                    ],
                },
            ]
        ),
        encoding="utf-8",
    )
    log = AuditLog(path=audit_path)

    assert log.delivered_job_keys() == {"custom"}
    assert log.summary()["delivered"] == 1
    assert log.summary()["error_breakdown"] == {"platform_default_only": 1}
    assert log.list_recent(2)[1]["error"] == "platform_default_only"


def test_signed_release_manifest_is_verified_and_source_checkout_is_notice_only(
    tmp_path, monkeypatch
):
    import jobagent.infra.release_update as updates

    monkeypatch.setattr(updates, "__version__", "0.4.1")
    private, public = _key_pair()
    monkeypatch.setattr(updates, "RELEASE_SIGNING_PUBLIC_KEY", public)
    manifest = _sign(
        private,
        {
            "product": "jobagent",
            "channel": "stable",
            "latest_client_version": "0.4.2",
            "minimum_supported_version": "0.3.0",
            "protocol_version": 1,
            "git_tag": "v0.4.2",
            "git_commit": "a" * 40,
            "artifact_sha256": "b" * 64,
            "published_at": "2026-07-11T00:00:00Z",
            "required": False,
            "notes_url": "https://example.test/v0.4.2",
        },
    )
    assert updates.verify_release_manifest(manifest)["latest_client_version"] == "0.4.2"
    monkeypatch.setattr(updates, "fetch_release_manifest", lambda **_kwargs: manifest)
    monkeypatch.setattr(updates, "_package_root", lambda: tmp_path)
    result = updates.check_for_update(auto_apply=True)
    assert result["status"] == "update_available"
    assert result["managed"] is False

    tampered = dict(manifest)
    tampered["minimum_supported_version"] = "9.0.0"
    with pytest.raises(Exception, match="signature verification failed"):
        updates.verify_release_manifest(tampered)

    incompatible = _sign(
        private,
        {
            **{key: value for key, value in manifest.items() if key != "signature"},
            "protocol_version": 2,
        },
    )
    with pytest.raises(Exception, match="protocol version mismatch"):
        updates.verify_release_manifest(incompatible)


def test_explicit_update_check_bypasses_manifest_cache(monkeypatch):
    import jobagent.infra.release_update as updates

    calls = []

    def check_for_update(**kwargs):
        calls.append(kwargs)
        return {"status": "current"}

    monkeypatch.setattr(updates, "check_for_update", check_for_update)
    args = build_parser().parse_args(["update", "check"])

    assert _dispatch(args) == {"status": "current"}
    assert calls == [{"auto_apply": False, "force": True}]


def test_managed_update_emits_bounded_lifecycle_events(tmp_path, monkeypatch):
    import jobagent.infra.release_update as updates

    monkeypatch.setattr(updates, "__version__", "0.4.1")
    manifest = {
        "latest_client_version": "0.4.2",
        "minimum_supported_version": "0.3.0",
        "notes_url": "https://example.test/v0.4.2",
    }
    events = []
    monkeypatch.setattr(updates, "fetch_release_manifest", lambda **_kwargs: manifest)
    monkeypatch.setattr(updates, "_package_root", lambda: tmp_path)
    monkeypatch.setattr(updates, "_install_metadata", lambda _root: {"managed": True})
    monkeypatch.setattr(
        updates, "apply_managed_update", lambda *_args, **_kwargs: "0.4.2"
    )

    result = updates.check_for_update(
        auto_apply=True,
        on_event=lambda stage, **details: events.append((stage, details)),
    )

    assert result == {
        "status": "updated",
        "from_version": updates.__version__,
        "to_version": "0.4.2",
    }
    assert [stage for stage, _details in events] == [
        "client_update_detected",
        "client_update_started",
        "client_update_completed",
    ]
    assert all(details["automatic"] is True for _stage, details in events)
    assert all(
        details["notes_url"] == manifest["notes_url"] for _stage, details in events
    )


def test_managed_update_emits_failure_event(tmp_path, monkeypatch):
    import jobagent.infra.release_update as updates

    monkeypatch.setattr(updates, "__version__", "0.4.1")
    manifest = {
        "latest_client_version": "0.4.2",
        "minimum_supported_version": "0.3.0",
        "notes_url": "https://example.test/v0.4.2",
    }
    events = []
    monkeypatch.setattr(updates, "fetch_release_manifest", lambda **_kwargs: manifest)
    monkeypatch.setattr(updates, "_package_root", lambda: tmp_path)
    monkeypatch.setattr(updates, "_install_metadata", lambda _root: {"managed": True})

    def fail_update(*_args, **_kwargs):
        raise updates.UpdateError("managed install has local changes; update refused")

    monkeypatch.setattr(updates, "apply_managed_update", fail_update)

    with pytest.raises(updates.UpdateError, match="local changes"):
        updates.check_for_update(
            auto_apply=True,
            on_event=lambda stage, **details: events.append((stage, details)),
        )

    assert [stage for stage, _details in events] == [
        "client_update_detected",
        "client_update_started",
        "client_update_failed",
    ]
    assert events[-1][1]["next_suggested"].startswith(
        "Resolve the reported update error"
    )


def test_managed_update_hash_failure_emits_safe_installer_recovery(
    tmp_path, monkeypatch
):
    import jobagent.infra.release_update as updates

    monkeypatch.setattr(updates, "__version__", "0.5.1")
    manifest = {
        "latest_client_version": "0.5.2",
        "minimum_supported_version": "0.3.0",
        "notes_url": "https://example.test/v0.5.2",
    }
    events = []
    monkeypatch.setattr(updates, "fetch_release_manifest", lambda **_kwargs: manifest)
    monkeypatch.setattr(updates, "_package_root", lambda: tmp_path)
    monkeypatch.setattr(updates, "_install_metadata", lambda _root: {"managed": True})

    def fail_update(*_args, **_kwargs):
        raise updates.UpdateError("release artifact hash mismatch")

    monkeypatch.setattr(updates, "apply_managed_update", fail_update)

    with pytest.raises(updates.UpdateError, match="artifact hash mismatch"):
        updates.check_for_update(
            auto_apply=True,
            on_event=lambda stage, **details: events.append((stage, details)),
        )

    failure = events[-1][1]
    assert failure["error_code"] == "release_artifact_hash_mismatch"
    assert failure["recovery_commands"]["macos_linux"] == updates.POSIX_INSTALL_COMMAND
    assert (
        failure["recovery_commands"]["windows_powershell"]
        == updates.WINDOWS_INSTALL_COMMAND
    )
    assert (
        "preserves Job Agent account state and browser sessions"
        in failure["next_suggested"]
    )


def test_release_recovery_commands_download_before_execution():
    import jobagent.infra.release_update as updates

    posix = updates.POSIX_INSTALL_COMMAND
    assert "releases/latest/download/install.sh" in posix
    assert "raw.githubusercontent.com/jiyangnan/AgentMesh-JobAgent/main" in posix
    assert "curl -fL" in posix
    assert "--retry 3" in posix
    assert "-o \"$installer\"" in posix
    assert "bash \"$installer\"" in posix
    assert "| bash" not in posix

    powershell = updates.WINDOWS_INSTALL_COMMAND
    assert "releases/latest/download/install.ps1" in powershell
    assert "raw.githubusercontent.com/jiyangnan/AgentMesh-JobAgent/main" in powershell
    assert "Invoke-WebRequest" in powershell
    assert "-OutFile $installer" in powershell
    assert "& $installer" in powershell
    assert "| iex" not in powershell.lower()


def test_current_release_does_not_emit_update_events(monkeypatch):
    import jobagent.infra.release_update as updates

    monkeypatch.setattr(updates, "__version__", "0.4.2")
    monkeypatch.setattr(
        updates,
        "fetch_release_manifest",
        lambda **_kwargs: {
            "latest_client_version": "0.4.2",
            "minimum_supported_version": "0.3.0",
        },
    )
    events = []

    result = updates.check_for_update(
        on_event=lambda stage, **details: events.append((stage, details))
    )

    assert result["status"] == "current"
    assert events == []


def test_previous_managed_version_reads_pre_update_checkout(tmp_path, monkeypatch):
    import jobagent.infra.release_update as updates

    monkeypatch.setattr(updates, "_install_metadata", lambda _root: {"managed": True})
    values = {
        ("git", "rev-parse", "HEAD@{1}"): "a" * 40,
        ("git", "rev-parse", "HEAD"): "b" * 40,
        ("git", "show", f"{'a' * 40}:pyproject.toml"): (
            '[project]\nname = "jobagent"\nversion = "0.4.1"\n'
        ),
    }
    monkeypatch.setattr(updates, "_run", lambda _root, *args: values[args])

    assert updates.previous_managed_version(tmp_path) == "0.4.1"


def test_cli_update_restart_receipt_is_safe_and_emitted_once(monkeypatch, capsys):
    import jobagent.cli as cli
    import jobagent.infra.release_update as updates

    args = build_parser().parse_args(["doctor", "env"])
    calls = []

    def updated(*, on_event=None):
        assert on_event is not None
        return {"status": "updated", "from_version": "0.4.1", "to_version": "0.4.2"}

    class ExecCalled(RuntimeError):
        pass

    def fake_execv(executable, argv):
        calls.append((executable, argv))
        raise ExecCalled

    monkeypatch.setattr(updates, "maybe_auto_update", updated)
    monkeypatch.setattr(cli.os, "execv", fake_execv)
    monkeypatch.setattr(cli.sys, "argv", ["jobagent", "doctor", "env"])

    with pytest.raises(ExecCalled):
        _maybe_update(args)

    receipt = json.loads(os.environ[cli._UPDATE_RESUME_ENV])
    assert receipt == {
        "from_version": "0.4.1",
        "to_version": "0.4.2",
        "command": "jobagent doctor",
    }
    assert "env" not in os.environ[cli._UPDATE_RESUME_ENV]
    assert calls == [
        (sys.executable, [sys.executable, "-m", "jobagent", "doctor", "env"])
    ]

    monkeypatch.setattr(
        updates, "maybe_auto_update", lambda **_kwargs: {"status": "current"}
    )
    _maybe_update(args)
    first = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert [event["stage"] for event in first] == ["client_command_resumed"]
    assert first[0]["command"] == "jobagent doctor"
    assert cli._UPDATE_RESUME_ENV not in os.environ

    _maybe_update(args)
    assert capsys.readouterr().err == ""


def test_previous_client_upgrade_bootstraps_completion_events(monkeypatch, capsys):
    import jobagent.infra.client_upgrade as upgrades

    args = build_parser().parse_args(["round", "status"])
    report = {
        "ok": True,
        "upgrade_detected": True,
        "version_changed": True,
        "from_version": "0.4.1",
        "to_version": "0.4.2",
        "conflicts": [],
        "next_suggested": "jobagent round status",
    }
    monkeypatch.setattr(upgrades, "run_client_upgrade", lambda: report)

    assert _prepare_client_upgrade(args) == report
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert [event["stage"] for event in events] == [
        "client_update_completed",
        "client_command_resumed",
    ]
    assert all(event["bootstrap_compatibility"] is True for event in events)
    assert events[-1]["command"] == "jobagent round"


def test_uninitialized_previous_client_uses_managed_reflog_for_bootstrap(
    monkeypatch, capsys
):
    import jobagent.infra.client_upgrade as upgrades
    import jobagent.infra.release_update as updates

    args = build_parser().parse_args(["platforms", "status"])
    report = {
        "ok": True,
        "upgrade_detected": True,
        "version_changed": False,
        "from_version": "unknown",
        "to_version": "0.4.2",
        "conflicts": [],
        "next_suggested": "jobagent round status",
    }
    monkeypatch.setattr(upgrades, "run_client_upgrade", lambda: report)
    monkeypatch.setattr(updates, "previous_managed_version", lambda: "0.4.1")

    assert _prepare_client_upgrade(args) == report
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert [event["stage"] for event in events] == [
        "client_update_completed",
        "client_command_resumed",
    ]
    assert all(event["from_version"] == "0.4.1" for event in events)
    assert events[-1]["command"] == "jobagent platforms"


def test_update_lock_reclaims_dead_owner_but_blocks_live_owner(tmp_path, monkeypatch):
    import jobagent.infra.release_update as updates

    lock = tmp_path / "update.lock"
    monkeypatch.setattr(updates, "update_lock_path", lambda: lock)
    lock.write_text("999999", encoding="utf-8")
    monkeypatch.setattr(updates, "_pid_alive", lambda _pid: False)

    with updates._update_lock():
        assert lock.read_text(encoding="utf-8") == str(os.getpid())
    assert not lock.exists()

    lock.write_text("42", encoding="utf-8")
    monkeypatch.setattr(updates, "_pid_alive", lambda pid: pid == 42)
    with pytest.raises(updates.UpdateError, match="already running"):
        with updates._update_lock():
            pass
    assert lock.read_text(encoding="utf-8") == "42"
