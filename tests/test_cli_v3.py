from __future__ import annotations

import base64
import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jobagent.cli import _dispatch, _login, build_parser
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
    assert parser.parse_args(["round", "status"]).round_command == "status"
    assert parser.parse_args(
        ["round", "skip", "--platform", "liepin", "--confirm-skip"]
    ).confirm_skip is True
    assert parser.parse_args(["boss", "discover"]).platform_command == "discover"
    assert parser.parse_args(["boss", "greet", "send", "--dry-run"]).limit == 100
    assert parser.parse_args(["liepin", "apply", "review"]).apply_command == "review"
    assert parser.parse_args(["zhilian", "apply", "send", "--dry-run"]).dry_run is True
    assert parser.parse_args(["51job", "audit"]).platform_command == "audit"

    for retired in (
        ["boss", "collect"],
        ["boss", "rank"],
        ["liepin", "greet", "preview"],
        ["zhilian", "apply", "open"],
        ["51job", "rank", "--local"],
        ["resume", "analyze", "--file", "resume.pdf", "--local"],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(retired)


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
    args = build_parser().parse_args(
        ["round", "skip", "--platform", "liepin", "--confirm-skip"]
    )

    result = _dispatch(args)

    assert updates == [("liepin", "skipped_this_round")]
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


def test_send_requires_explicit_confirmation_before_loading_browser_state():
    args = build_parser().parse_args(["liepin", "apply", "send"])
    assert _dispatch(args) == {
        "ok": False,
        "error": "user_confirmation_required",
        "platform": "liepin",
        "message": "Review the selected jobs and explicitly confirm the real send action.",
    }


def test_discover_verifies_both_signatures_and_discards_raw_candidates(tmp_path, monkeypatch):
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
    assert statuses == [("51job", "discovered")]
    assert result["workflow"]["round_id"] == "round-1"
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert "candidates" not in persisted
    assert persisted["manifest"]["candidate_digest"] == candidate_digest(candidates)


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
            "selected": [{"id": "s", "title": "Selected", "classification": "selected"}],
            "review": [{"id": "r", "title": "Review", "classification": "review"}],
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
        lambda: {"round_id": "round-1", "next_suggested": "jobagent liepin apply send --confirm-submit"},
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

    private, public = _key_pair()
    monkeypatch.setattr(updates, "RELEASE_SIGNING_PUBLIC_KEY", public)
    manifest = _sign(
        private,
        {
            "product": "jobagent",
            "channel": "stable",
            "latest_client_version": "0.4.0",
            "minimum_supported_version": "0.3.0",
            "protocol_version": 1,
            "git_tag": "v0.4.0",
            "git_commit": "a" * 40,
            "artifact_sha256": "b" * 64,
            "published_at": "2026-07-11T00:00:00Z",
            "required": False,
            "notes_url": "https://example.test/v0.4.0",
        },
    )
    assert updates.verify_release_manifest(manifest)["latest_client_version"] == "0.4.0"
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
