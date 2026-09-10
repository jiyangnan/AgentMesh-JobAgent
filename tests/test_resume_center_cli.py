"""Resume-center CLI bridge (P1): read-only online resume facts for agents."""
from __future__ import annotations

import argparse
from typing import Any

import pytest

from jobagent.cli import _dispatch, build_parser
from jobagent.infra import cloud_client
from jobagent.infra.cloud_client import CloudError


def _args(argv: str) -> argparse.Namespace:
    return build_parser().parse_args(argv.split())


def _preparation(
    *,
    resumes: list[dict[str, Any]] | None = None,
    state: str = "ready",
    ready: bool = True,
) -> dict[str, Any]:
    if resumes is None:
        resumes = [
            {
                "id": "resume-a",
                "name": "数据产品方向",
                "target_role": "数据产品经理",
                "status": "active",
                "version": 5,
                "material_version": 5,
                "confirmed_revision_id": "rev-5",
                "confirmed_revision_number": 2,
                "confirmed_at": "2026-09-10T01:00:00Z",
                "updated_at": "2026-09-10T02:00:00Z",
                "has_draft": False,
            },
            {
                "id": "resume-b",
                "name": "项目管理方向",
                "target_role": "项目经理",
                "status": "active",
                "version": 2,
                "material_version": 2,
                "confirmed_revision_id": None,
                "confirmed_revision_number": None,
                "confirmed_at": None,
                "updated_at": "2026-09-09T02:00:00Z",
                "has_draft": True,
            },
        ]
    return {
        "ok": True,
        "protocol": "agentmesh360.resume_preparation",
        "protocol_version": 1,
        "account_ref": "acct_synthetic",
        "state_revision": 4,
        "state": state,
        "ready": ready,
        "checked_at": "2026-09-11T00:00:00Z",
        "offline": False,
        "stale": False,
        "resumes": resumes,
        "receipt": {"id": "receipt-1", "confirmed_at": "2026-09-10T01:00:00Z", "revisions": []},
        "blockers": [],
        "requires_user_action": not ready,
        "next_suggested": None,
        "workbench_url": "https://agentmesh360.com/workbench/#/resumes",
    }


@pytest.fixture(autouse=True)
def _no_local_snapshot(monkeypatch, tmp_path):
    # Point the local profile snapshot at an empty directory unless a test
    # explicitly overrides it, so fallback branches are deterministic.
    import jobagent.infra.state as state_mod

    monkeypatch.setattr(state_mod, "STATE_DIR", tmp_path, raising=False)


def test_resume_list_returns_online_facts(monkeypatch):
    monkeypatch.setattr(
        cloud_client, "resume_center_preparation", lambda: _preparation()
    )
    result = _dispatch(_args("resume list"))
    assert result["ok"] is True and result["source"] == "resume_center"
    assert result["state"] == "ready" and result["ready"] is True
    assert [r["name"] for r in result["resumes"]] == ["数据产品方向", "项目管理方向"]
    assert result["resumes"][0]["confirmed"] is True
    assert result["resumes"][1]["confirmed"] is False
    assert result["resumes"][1]["has_draft"] is True
    # No resume body, no personal info, only user-authored metadata fields.
    assert all(set(r) == {"id", "name", "target_role", "version", "confirmed", "has_draft", "updated_at"} for r in result["resumes"])
    assert result["workbench_url"].startswith("https://agentmesh360.com/workbench")


def test_resume_status_matches_list_and_detail_is_reserved(monkeypatch):
    monkeypatch.setattr(
        cloud_client, "resume_center_preparation", lambda: _preparation()
    )
    assert _dispatch(_args("resume status"))["source"] == "resume_center"
    with pytest.raises(CloudError) as exc:
        _dispatch(_args("resume status --id resume-a"))
    assert exc.value.code == "resume_detail_not_available"


def test_resume_list_surfaces_unavailable_as_cloud_error(monkeypatch):
    def _unavailable():
        raise CloudError(
            "resume center is not enabled",
            status=503,
            code="resume_center_unavailable",
        )

    monkeypatch.setattr(cloud_client, "resume_center_preparation", _unavailable)
    with pytest.raises(CloudError) as exc:
        _dispatch(_args("resume list"))
    assert exc.value.code == "resume_center_unavailable"


def test_profile_show_prefers_cloud_facts(monkeypatch):
    monkeypatch.setattr(
        cloud_client, "resume_center_preparation", lambda: _preparation()
    )
    result = _dispatch(_args("profile show"))
    assert result["source"] == "resume_center"
    assert len(result["resumes"]) == 2
    assert "profile" not in result  # no stale local copy mixed in


def test_profile_show_falls_back_to_local_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cloud_client,
        "resume_center_preparation",
        lambda: _preparation(resumes=[], state="empty", ready=False),
    )
    import jobagent.infra.state as state_mod

    profile_file = tmp_path / "profile.json"
    profile_file.write_text('{"basic": {"name": "Synthetic Local"}}', encoding="utf-8")
    monkeypatch.setattr(state_mod, "profile_path", lambda: profile_file)
    result = _dispatch(_args("profile show"))
    assert result["source"] == "local_snapshot"
    assert result["profile"]["basic"]["name"] == "Synthetic Local"
    assert "may be outdated" in result["stale_warning"]
    assert result["fallback_reason"] == "resume_center_empty"
    assert result["next_suggested"] == "jobagent resume list"


def test_profile_show_falls_back_on_unreachable_cloud(monkeypatch, tmp_path):
    def _down():
        raise CloudError("tls eof", status=502, code="cloud_gateway_unavailable", retryable=True)

    monkeypatch.setattr(cloud_client, "resume_center_preparation", _down)
    import jobagent.infra.state as state_mod

    monkeypatch.setattr(state_mod, "profile_path", lambda: tmp_path / "missing.json")
    result = _dispatch(_args("profile show"))
    assert result["source"] == "local_snapshot"
    assert result["profile"] is None
    assert result["fallback_reason"] == "cloud_error:cloud_gateway_unavailable"


def test_profile_show_never_masks_auth_failures(monkeypatch):
    def _denied():
        raise CloudError("invalid key", status=401, code="invalid_api_key")

    monkeypatch.setattr(cloud_client, "resume_center_preparation", _denied)
    with pytest.raises(CloudError) as exc:
        _dispatch(_args("profile show"))
    assert exc.value.status == 401


def test_resume_list_is_read_only_and_not_context_locked(monkeypatch):
    # The native-work context lock exists for commands that change profile or
    # round context; a read-only listing must stay available.
    from jobagent.infra import browser_work

    monkeypatch.setattr(
        cloud_client, "resume_center_preparation", lambda: _preparation()
    )
    monkeypatch.setattr(browser_work, "has_open", lambda: True)
    result = _dispatch(_args("resume list"))
    assert result["source"] == "resume_center"
