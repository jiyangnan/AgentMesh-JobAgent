"""An active cloud-bound round must not be redirected to paid local analysis."""
import copy
import json

import pytest

from jobagent.infra import cloud_client, rounds, state, upgrade_readiness
from jobagent.infra.protocol import digest_payload
from tests.test_native_work import env  # noqa: F401


@pytest.fixture
def bound(env, monkeypatch):
    env.choose("liepin")
    profile = {"schema_version": 1, "basic": {"name": "Synthetic bound profile"}}
    binding = {"id": "binding-original", "context_id": "context-original",
        "resume_id": "resume-original", "resume_revision_id": "revision-original",
        "resume_revision_number": 2, "content_digest": "sha256:" + "1" * 64}
    active = rounds.ensure_current_round()
    active["resume_binding"] = copy.deepcopy(binding)
    active["intent"]["profile_digest"] = digest_payload(profile)
    rounds.save_round(active)
    material = {"ok": True, "account_ref": "acct_synthetic_original", "binding": copy.deepcopy(binding),
                "profile": profile, "profile_digest": digest_payload(profile)}
    calls = []
    monkeypatch.setattr(upgrade_readiness, "load_api_key", lambda: "agentmesh_live_synthetic")
    monkeypatch.setattr(cloud_client, "me", lambda: {"account": {"account_ref": "acct_synthetic_original"}})
    monkeypatch.setattr("jobagent.infra.account_state.current_account_ref", lambda: "acct_synthetic_original")
    monkeypatch.setattr(cloud_client, "resume_binding_material", lambda value: calls.append(value) or copy.deepcopy(material))
    return env, material, calls


@pytest.mark.parametrize("local", [None, {"hardSkills": {"tools": ["retired"]}}])
def test_bound_round_uses_its_read_only_cloud_material_and_preserves_state(bound, local):
    env, _, calls = bound
    if local:
        state.profile_path().write_text(json.dumps(local))
    before = {p: p.read_bytes() for p in env.path.rglob("*") if p.is_file()}
    report = upgrade_readiness.run_upgrade_check()
    assert report["ok"]
    assert report["checks"][1]["source"] == "round_resume_binding"
    assert calls == ["binding-original"]
    assert {p: p.read_bytes() for p in before} == before
    assert "analyze" not in json.dumps(report)


@pytest.mark.parametrize("change", [
    {"account_ref": "acct_other_account"},
    {"binding": {"id": "another-binding"}},
    {"profile_digest": "sha256:wrong"},
    {"profile": {"old_layout": True}},
    {"offline": True}, {"stale": True},
])
def test_cloud_binding_mismatch_never_falls_back_to_analysis(bound, change):
    _, material, _ = bound
    material.update(change)
    report = upgrade_readiness.run_upgrade_check()
    assert not report["ok"]
    assert report["checks"][1]["error"].startswith("bound_resume_")
    assert "analyze" not in report["next_suggested"]
    assert rounds.ensure_current_round()["resume_binding"]["id"] == "binding-original"


def test_foreign_local_owner_cannot_read_bound_material(bound, monkeypatch):
    _, _, calls = bound
    monkeypatch.setattr("jobagent.infra.account_state.current_account_ref", lambda: "acct_other_account")
    report = upgrade_readiness.run_upgrade_check()
    assert not report["ok"] and report["checks"][1]["error"] == "bound_resume_account_unverified"
    assert calls == []


def test_unavailable_material_preserves_binding_and_gives_read_only_recovery(bound, monkeypatch):
    _, _, _ = bound
    def unavailable(*args):
        raise cloud_client.CloudError("temporarily unavailable", code="cloud_gateway_unavailable", retryable=True)
    monkeypatch.setattr(cloud_client, "resume_binding_material", unavailable)
    report = upgrade_readiness.run_upgrade_check()
    assert not report["ok"] and report["checks"][1]["retryable"]
    assert report["next_suggested"] == "jobagent upgrade-check"
    assert rounds.ensure_current_round()["resume_binding"]["id"] == "binding-original"
