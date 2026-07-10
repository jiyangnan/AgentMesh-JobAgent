from __future__ import annotations

import base64
import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jobagent.cli import _dispatch, build_parser
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
    assert parser.parse_args(["boss", "discover"]).platform_command == "discover"
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
    output = tmp_path / "decision.json"

    def save(payload):
        output.write_text(json.dumps({"manifest": payload}), encoding="utf-8")
        return output

    monkeypatch.setattr(application, "save_manifest", save)
    result = application.run_discover("51job", page_delay=0)
    assert result["selected"] == 1 and result["credits"] == 10
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
    monkeypatch.setattr(updates, "fetch_release_manifest", lambda: manifest)
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
