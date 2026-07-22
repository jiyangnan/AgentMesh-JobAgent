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
    _with_login_workflow,
    build_parser,
)
from jobagent.infra.protocol import canonical_json_bytes, candidate_digest, digest_payload


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
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def test_public_parser_exposes_only_v3_platform_commands():
    parser = build_parser()
    assert parser.parse_args(["upgrade-check"]).command == "upgrade-check"
    assert parser.parse_args(["account", "status"]).account_command == "status"
    assert parser.parse_args(
        ["account", "bind", "--confirm-legacy"]
    ).confirm_legacy is True
    assert parser.parse_args(["account", "switch", "--new-state"]).new_state is True
    assert parser.parse_args(["round", "status"]).round_command == "status"
    assert parser.parse_args(["round", "start"]).round_command == "start"
    assert parser.parse_args(["round", "audit", "--failures-only"]).failures_only is True
    assert parser.parse_args(
        ["round", "skip", "--platform", "liepin", "--confirm-skip"]
    ).confirm_skip is True
    assert parser.parse_args(["boss", "discover"]).platform_command == "discover"
    assert parser.parse_args(["boss", "greet", "send", "--dry-run"]).limit == 100
    assert parser.parse_args(["liepin", "apply", "review"]).apply_command == "review"
    assert parser.parse_args(["zhilian", "apply", "send", "--dry-run"]).dry_run is True
    assert parser.parse_args(["51job", "audit"]).platform_command == "audit"
    assert parser.parse_args(["boss", "audit", "--details"]).details is True
    assert parser.parse_args(
        ["browser", "diagnose", "--platform", "boss"]
    ).browser_command == "diagnose"

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
            "targetRoles": [
                {"title": "AI产品经理", "confidence": 0.98, "priority": 1}
            ]
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

    monkeypatch.setattr(application.cloud_client, "discovery_start", unexpected_cloud_call)

    with pytest.raises(ValueError, match="resume analyze"):
        application.run_discover("boss")


def test_resume_analyze_stamps_profile_schema_version(tmp_path, monkeypatch):
    from jobagent import cli

    source = tmp_path / "resume.txt"
    source.write_text("A sufficiently long resume body for schema testing.", encoding="utf-8")
    output = tmp_path / "profile.json"
    monkeypatch.setattr("jobagent.domain.resume_parser.ResumeParser.parse", lambda self, path: source.read_text())
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
        ["resume", "analyze", "--file", str(source), "--output", str(output)]
    )

    cli._resume_analyze(args)

    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["schema_version"] >= 1


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
        lambda *, api_key=None: calls.append(("verify", api_key))
        or {"account": {"id": 1, "account_ref": "acct_test_account"}},
    )
    monkeypatch.setattr(
        "jobagent.infra.credentials.save_api_key",
        lambda key: calls.append(("save", key)) or Path("/tmp/credentials"),
    )
    monkeypatch.setattr(
        "jobagent.infra.account_state.ensure_account_state",
        lambda _account: {"status": "ready", "ready": True},
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

    monkeypatch.setattr("jobagent.infra.credentials.load_api_key", lambda: "jobagent_live_bad")
    monkeypatch.setattr("jobagent.infra.cloud_client.health", lambda: {"status": "ok"})
    monkeypatch.setattr(
        "jobagent.infra.cloud_client.me",
        lambda: (_ for _ in ()).throw(CloudError("invalid", status=401, code="invalid_api_key")),
    )

    result = cli._doctor_env()

    assert result["ok"] is False
    assert result["api_key_configured"] is True
    assert result["api_key_valid"] is False
    assert result["api_key_error"] == "invalid_api_key"
    assert "jobagent init --key" in result["api_key_action"]


def test_doctor_env_treats_signup_trial_as_immediately_usable(tmp_path, monkeypatch):
    from jobagent import cli

    monkeypatch.setattr("jobagent.infra.credentials.load_api_key", lambda: "agentmesh_live_trial")
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
    monkeypatch.setattr("jobagent.infra.state.profile_path", lambda: tmp_path / "profile.json")
    monkeypatch.setattr(
        "jobagent.infra.account_state.ensure_account_state",
        lambda _account: {"status": "ready", "ready": True},
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

    monkeypatch.setattr("jobagent.infra.credentials.load_api_key", lambda: "agentmesh_live_empty")
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
        lambda _account: {"status": "ready", "ready": True},
    )
    monkeypatch.setattr("jobagent.infra.state.profile_path", lambda: tmp_path / "profile.json")

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
    from jobagent.platforms.liepin.session import LiepinSessionGuide, LiepinSessionStatus

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
    assert result["next_suggested"] == "jobagent boss greet send --input /tmp/review.json"


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
    assert restored["next_suggested"] == "jobagent boss greet send --input /tmp/review.json"


def test_login_reauthentication_infers_reviewed_progress_after_interruption(monkeypatch):
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
            {
                "next_suggested": "jobagent boss greet send --input /tmp/review.json"
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
    assert result["next_suggested"] == "jobagent boss greet send --input /tmp/review.json"


@pytest.mark.parametrize(
    "guide_class",
    [
        pytest.param(
            __import__("jobagent.platforms.liepin.session", fromlist=["LiepinSessionGuide"]).LiepinSessionGuide,
            id="liepin",
        ),
        pytest.param(
            __import__("jobagent.platforms.zhilian.session", fromlist=["ZhilianSessionGuide"]).ZhilianSessionGuide,
            id="zhilian",
        ),
        pytest.param(
            __import__("jobagent.platforms.job51.session", fromlist=["Job51SessionGuide"]).Job51SessionGuide,
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


def test_selected_send_runs_without_per_platform_confirmation(monkeypatch):
    calls = []
    monkeypatch.setattr("jobagent.infra.rounds.assert_platform_turn", lambda platform: None)
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
                "limit": 100,
                "dry_run": False,
                "stop_on_failure": True,
            },
        )
    ]


def test_public_docs_do_not_restore_per_platform_send_confirmation():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    claude_guide = (root / "skills/claude-code/README.md").read_text(encoding="utf-8")

    assert "confirmed send" not in readme
    assert "without the user's explicit confirmation" not in readme
    assert readme.count("automatic selected delivery") == 4
    assert "Starting a job-search round authorizes automatic delivery" in readme
    assert "automatic selected delivery" in claude_guide


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


def test_discover_verifies_both_signatures_and_discards_raw_candidates(tmp_path, monkeypatch, capsys):
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
            "billing": {"action": "jobagent.discover", "credits": 10, "transaction_id": "77"},
        },
    )
    monkeypatch.setattr(application, "profile_path", lambda: tmp_path / "profile.json")
    monkeypatch.setattr(application, "load_json", lambda _path: profile)
    monkeypatch.setattr(application.cloud_client, "discovery_start", lambda **_kwargs: plan)
    monkeypatch.setattr(application.cloud_client, "discovery_decide", lambda **_kwargs: manifest)
    monkeypatch.setattr(application, "collect_from_search_plan", lambda *_args, **_kwargs: candidates)
    monkeypatch.setattr(application, "active_command", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(application, "PlatformSessionLock", lambda *_args, **_kwargs: nullcontext())
    pending_writes: list[dict] = []
    pending_clears: list[str | None] = []
    monkeypatch.setattr(application, "load_pending_decision", lambda _platform: None)
    monkeypatch.setattr(
        application,
        "save_pending_decision",
        lambda platform, **payload: pending_writes.append({"platform": platform, **payload}),
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
        lambda: {"round_id": "round-1", "next_suggested": "jobagent 51job apply review"},
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
    assert pending_writes == [
        {"platform": "51job", "plan": plan, "jobs": candidates}
    ]
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
        lambda *_args, **_kwargs: pytest.fail("pending decision recollected browser jobs"),
    )

    recovered = application.run_discover("51job", page_delay=0)

    assert recovered["resumed"] is True
    assert recovered["discover_id"] == discover_id
    assert pending_clears == [discover_id, discover_id]


def test_unexpected_cli_error_writes_diagnostic_log(tmp_path, monkeypatch, capsys):
    from jobagent import cli
    from jobagent.infra import diagnostics

    monkeypatch.setattr(diagnostics, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cli, "_maybe_update", lambda _args: None)
    monkeypatch.setattr(cli, "_prepare_client_upgrade", lambda _args: None)
    monkeypatch.setattr(cli, "_verify_state_owner_for_command", lambda _args: None)
    monkeypatch.setattr(cli, "_dispatch", lambda _args: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("sys.argv", ["jobagent", "profile", "show"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().err)
    log_path = Path(payload["diagnostic_log"])
    assert log_path.exists()
    assert "RuntimeError: boom" in log_path.read_text(encoding="utf-8")


def test_cli_blocks_platform_dispatch_when_upgrade_requires_recovery(monkeypatch, capsys):
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


def test_review_requires_confirmation_to_promote_and_never_promotes_rejected(tmp_path, monkeypatch):
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
            "rejected": [{"id": "x", "title": "Rejected", "classification": "rejected"}],
            "billing": {"action": "jobagent.discover", "credits": 10, "transaction_id": "1"},
        },
    )
    source = tmp_path / "decision.json"
    source.write_text(
        json.dumps({"platform": "liepin", "discover_id": "dis_review", "manifest": manifest}),
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
    result = review_decision(
        "liepin",
        input_path=str(source),
        promoted_ids=["r"],
        confirm_promote=True,
        output_path=str(output),
    )
    reviewed = json.loads(output.read_text(encoding="utf-8"))
    assert result["send_count"] == 2
    assert [item["id"] for item in reviewed["send_candidates"]] == ["s", "r"]
    assert reviewed["user_overrides"] == [{"job_id": "r", "from": "review", "to": "selected"}]
    assert statuses == [("liepin", "reviewed")]
    assert result["workflow"]["round_id"] == "round-1"


def test_send_marks_platform_sent_and_audit_advances_to_next_platform(monkeypatch):
    import jobagent.application.delivery as delivery
    from jobagent.domain.models import SendAttempt

    reviewed = {
        "discover_id": "dis_boss",
        "send_candidates": [
            {"url": "https://www.zhipin.com/job_detail/1.html", "cloud_greeting": "您好"}
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
    monkeypatch.setattr(delivery, "active_command", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(delivery, "PlatformSessionLock", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(delivery, "print_first_delivery_star_prompt_once", lambda **_kwargs: None)
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

    monkeypatch.setattr(delivery, "AuditLog", lambda: type("Log", (), {"summary": lambda self: {}, "list_recent": lambda self, _n: []})())
    monkeypatch.setattr(
        delivery.rounds,
        "complete_platform_after_audit",
        lambda platform: {"round_id": "round-1", "current_platform": "liepin", "next_suggested": "jobagent liepin login --check"},
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
    assert failures["platforms"]["boss"]["records"][0]["error"] == "delivery_not_verified"


def test_boss_review_excludes_previously_delivered_jobs(tmp_path, monkeypatch):
    import jobagent.infra.audit as audit
    import jobagent.infra.protocol as protocol
    from jobagent.application.review import review_decision

    private, public = _key_pair()
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", public)
    delivered_url = "https://www.zhipin.com/job_detail/already.html?ka=search_list_jname_1"
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
            "billing": {"action": "jobagent.discover", "credits": 0, "transaction_id": "1"},
        },
    )
    source = tmp_path / "decision.json"
    source.write_text(
        json.dumps({"platform": "boss", "discover_id": "dis_boss_review", "manifest": manifest}),
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


def test_boss_send_skips_previously_delivered_jobs_before_opening_browser(tmp_path, monkeypatch):
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
            {"url": "https://www.zhipin.com/job_detail/first.html", "cloud_greeting": "one"},
            {"url": "https://www.zhipin.com/job_detail/second.html", "cloud_greeting": "two"},
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
            {"url": "https://www.zhipin.com/job_detail/first.html", "cloud_greeting": "one"},
            {"url": "https://www.zhipin.com/job_detail/second.html", "cloud_greeting": "two"},
        ],
        dry_run=False,
    )

    assert opened == ["https://www.zhipin.com/job_detail/first.html"]
    assert len(attempts) == 1
    assert attempts[0].error == "chat_entry_failed"


def test_boss_audit_does_not_treat_platform_default_only_as_personalized_delivery(tmp_path):
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
    monkeypatch.setattr(updates, "apply_managed_update", lambda *_args, **_kwargs: "0.4.2")

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
    assert all(details["notes_url"] == manifest["notes_url"] for _stage, details in events)


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
    assert events[-1][1]["next_suggested"].startswith("Resolve the reported update error")


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

    monkeypatch.setattr(updates, "maybe_auto_update", lambda **_kwargs: {"status": "current"})
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
