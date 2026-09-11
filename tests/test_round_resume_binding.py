"""Round resume binding (P2): user-confirmed resume rides the delivery chain."""
from __future__ import annotations

import argparse
from typing import Any

import pytest

from jobagent.cli import _dispatch, build_parser
from jobagent.infra import cloud_client
from jobagent.infra.cloud_client import CloudError
from jobagent.infra.interaction_state import load_pending_interaction


def _args(argv: str) -> argparse.Namespace:
    return build_parser().parse_args(argv.split())


def _profile() -> dict[str, Any]:
    return {
        "basic": {"name": "Synthetic"},
        "_meta": {"targetRolePolicyVersion": 2},
        "preferences": {"targetRoles": [{"title": "Engineer", "priority": 1}], "targetCities": [{"city": "Hangzhou", "priority": 1}]},
    }


def _preparation(ready: bool = True, resumes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if resumes is None:
        resumes = [
            {"id": "resume-a", "name": "数据方向", "target_role": "数据产品经理",
             "version": 5, "confirmed_revision_id": "rev-5", "has_draft": False,
             "updated_at": "2026-09-11T00:00:00Z"},
            {"id": "resume-b", "name": "项目方向", "target_role": "项目经理",
             "version": 2, "confirmed_revision_id": "rev-2", "has_draft": False,
             "updated_at": "2026-09-11T00:00:00Z"},
        ]
    return {
        "ok": True, "state": "ready" if ready else "empty", "ready": ready,
        "state_revision": 7, "resumes": resumes,
        "receipt": {"id": "receipt-1", "confirmed_at": "2026-09-11T00:00:00Z", "revisions": []},
        "next_suggested": None,
        "workbench_url": "https://agentmesh360.com/workbench/#/resumes",
    }


def _selection(resume_ids=("resume-a", "resume-b")) -> dict[str, Any]:
    return {
        "ok": True,
        "selection": {"id": "selection-1", "context_id": "ctx-1", "status": "pending",
                      "expected_state_revision": 7, "options": []},
        "interaction": {
            "event": "interaction_required",
            "protocol": "agentmesh360.interaction_required",
            "protocol_version": 1,
            "interaction_id": "selection-1",
            "kind": "resume_selection",
            "title": "Select resume",
            "prompt": "Choose the confirmed resume for this context.",
            "required": True,
            "preferred_presentation": "card",
            "allow_text_fallback": True,
            "fields": [
                {"field_id": "resume_id", "type": "single", "label": "Resume",
                 "options": [
                     {"option_id": rid, "label": rid, "description": rid} for rid in resume_ids
                 ]}
            ],
            "fallback_text": "Choose one resume_id: resume-a; resume-b",
            "continuation": {"action": "jobagent interaction respond", "idempotency_key": "selection-1"},
        },
    }


def _binding(binding_id: str = "binding-1", resume_id: str = "resume-a") -> dict[str, Any]:
    return {
        "id": binding_id, "context_id": "ctx-1", "resume_id": resume_id,
        "resume_revision_id": "rev-5", "resume_revision_number": 2,
        "content_digest": "digest", "target_role": "数据产品经理",
        "resume_name": "数据方向", "confirmed_at": "2026-09-11T00:00:00Z",
    }


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    import jobagent.infra.state as state_mod
    from pathlib import Path

    monkeypatch.setattr(state_mod, "STATE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(state_mod, "PROFILE_DIR", tmp_path, raising=False)
    state_mod.STATE_DIR.mkdir(parents=True, exist_ok=True)
    profile_file = tmp_path / "profile.json"
    profile_file.write_text(__import__("json").dumps(_profile()), encoding="utf-8")
    monkeypatch.setattr(state_mod, "profile_path", lambda: profile_file)
    yield


def test_round_start_asks_for_resume_even_with_one_confirmed(monkeypatch):
    monkeypatch.setattr(
        cloud_client, "resume_center_preparation",
        lambda: _preparation(resumes=[_preparation()["resumes"][0]]),
    )
    monkeypatch.setattr(cloud_client, "resume_selection", lambda context_id, revision: _selection(("resume-a",)))
    result = _dispatch(_args("round start"))
    assert result["error"] == "interaction_required"
    interaction = result["interaction"]
    assert interaction["kind"] == "resume_selection"
    pending = load_pending_interaction()
    assert pending["stage"] == "resume_binding"
    assert pending["context"]["selection_id"] == "selection-1"
    assert {o["resume_id"] for o in pending["context"]["options"]} == {"resume-a"}


def test_resume_respond_stages_binding_and_round_start_uses_it(monkeypatch):
    monkeypatch.setattr(
        cloud_client, "resume_center_preparation", lambda: _preparation()
    )
    monkeypatch.setattr(cloud_client, "resume_selection", lambda context_id, revision: _selection())
    _dispatch(_args("round start"))
    monkeypatch.setattr(
        cloud_client, "resume_selection_respond",
        lambda selection_id, response_id, resume_id: {"ok": True, "binding": _binding(resume_id=resume_id)},
    )
    answer = _dispatch(_args("interaction respond --interaction-id selection-1 --resume-id resume-b"))
    assert answer["ok"] is True
    assert answer["resume_binding"]["resume_id"] == "resume-b"
    assert answer["next_suggested"] == "jobagent round start"
    # Second round start reuses the staged binding without a new selection.
    result = _dispatch(_args("round start --accept-suggested"))
    assert result["ok"] is True
    assert result["resume_binding"]["resume_id"] == "resume-b"


def test_resume_respond_rejects_unoffered_resume(monkeypatch):
    monkeypatch.setattr(cloud_client, "resume_center_preparation", lambda: _preparation())
    monkeypatch.setattr(cloud_client, "resume_selection", lambda context_id, revision: _selection())
    _dispatch(_args("round start"))
    result = _dispatch(_args("interaction respond --interaction-id selection-1 --resume-id resume-x"))
    assert result["error"] == "invalid_interaction_response"
    assert cloud_client.resume_selection_respond is not None


def test_no_resume_binding_keeps_classic_path(monkeypatch):
    called = []
    monkeypatch.setattr(cloud_client, "resume_center_preparation", lambda: (_ for _ in ()).throw(AssertionError("must not call")))
    result = _dispatch(_args("round start --no-resume-binding --accept-suggested"))
    assert result["ok"] is True
    assert result.get("resume_binding") is None
    assert called == []


def test_explicit_binding_is_adopted_after_material_check(monkeypatch):
    material = {"resume_binding": _binding(), "context_id": "ctx-1",
                "profile": _profile(), "profile_digest": "digest", "resume_text": "text"}
    monkeypatch.setattr(cloud_client, "resume_binding_material", lambda binding_id: material)
    result = _dispatch(_args("round start --resume-binding binding-1 --accept-suggested"))
    assert result["ok"] is True
    assert result["resume_binding"]["id"] == "binding-1"


def test_unreachable_resume_center_falls_back_to_classic_path(monkeypatch):
    def _down():
        raise CloudError("unavailable", status=503, code="resume_center_unavailable")

    monkeypatch.setattr(cloud_client, "resume_center_preparation", _down)
    result = _dispatch(_args("round start --accept-suggested"))
    assert result["ok"] is True
    assert result.get("resume_binding") is None
    assert "resume_center_skipped" in (result.get("resume_notice") or "")


def test_bound_discover_uses_material_profile_and_carries_binding(monkeypatch):
    from jobagent.application import discover as discover_mod
    from jobagent.infra import rounds as rounds_mod

    rounds_mod.clear_round_resume_binding()
    material = {
        "resume_binding": _binding(),
        "context_id": "ctx-1",
        "profile": {"basic": {"name": "云端画像"}},
        "profile_digest": "cloud-digest",
        "resume_text": "text",
    }
    captured: dict[str, Any] = {}

    def _fake_start(**kwargs):
        captured.update(kwargs)
        raise CloudError("stop-here", status=502, code="cloud_gateway_unavailable", retryable=False)

    active = {"round_id": "round-1", "status": "active", "resume_binding": _binding(),
              "intent": {"status": "confirmed", "target_roles": ["数据产品经理"]}, "platforms": {}}
    monkeypatch.setattr(rounds_mod, "ensure_current_round", lambda: active)
    monkeypatch.setattr(cloud_client, "resume_binding_material", lambda binding_id: material)
    monkeypatch.setattr(cloud_client, "discovery_start", _fake_start)
    monkeypatch.setattr(discover_mod, "_resume_pending_decision", lambda *a, **k: None)
    monkeypatch.setattr(discover_mod, "_start_context", lambda *a, **k: {"round_id": "round-1", "platform": "boss", "profile_digest": "cloud-digest"})
    monkeypatch.setattr(discover_mod, "save_pending_start", lambda *a, **k: None)
    monkeypatch.setattr(discover_mod, "load_collection_checkpoint", lambda platform: None)
    import jobagent.infra.state as state_mod
    monkeypatch.setattr(state_mod, "profile_path", lambda: __import__("pathlib").Path("/nonexistent"))
    with pytest.raises(CloudError):
        discover_mod.run_discover("boss")
    assert captured.get("resume_binding_id") == "binding-1"
    assert captured.get("context_id") == "ctx-1"
    assert captured.get("round_id") == "round-1"
    assert captured.get("profile") == {"basic": {"name": "云端画像"}}


def test_stale_binding_pauses_and_clears_for_reselection(monkeypatch):
    from jobagent.application import discover as discover_mod
    from jobagent.infra import rounds as rounds_mod

    material = {"resume_binding": _binding(), "context_id": "ctx-1",
                "profile": {"basic": {"name": "x"}}, "profile_digest": "d", "resume_text": "t"}
    active = {"round_id": "round-1", "status": "active", "resume_binding": _binding(),
              "intent": {"status": "confirmed", "target_roles": ["数据产品经理"]}, "platforms": {}}
    monkeypatch.setattr(rounds_mod, "ensure_current_round", lambda: active)
    monkeypatch.setattr(cloud_client, "resume_binding_material", lambda binding_id: material)

    def _stale(**kwargs):
        raise CloudError("Confirm resume preparation and explicitly select material for this task.",
                         status=409, code="preparation_required")

    monkeypatch.setattr(cloud_client, "discovery_start", _stale)
    monkeypatch.setattr(discover_mod, "_resume_pending_decision", lambda *a, **k: None)
    monkeypatch.setattr(discover_mod, "_start_context", lambda *a, **k: {"round_id": "round-1", "platform": "boss", "profile_digest": "d"})
    monkeypatch.setattr(discover_mod, "save_pending_start", lambda *a, **k: None)
    monkeypatch.setattr(discover_mod, "load_collection_checkpoint", lambda platform: None)
    import jobagent.infra.state as state_mod
    monkeypatch.setattr(state_mod, "profile_path", lambda: __import__("pathlib").Path("/nonexistent"))
    cleared: list[bool] = []
    monkeypatch.setattr(rounds_mod, "clear_round_resume_binding", lambda: cleared.append(True))
    with pytest.raises(CloudError) as exc:
        discover_mod.run_discover("boss")
    assert exc.value.details.get("resume_binding_paused") is True
    assert exc.value.details.get("next_suggested") == "jobagent round start"
    # The unusable binding is dropped so the next round start reselects.
    assert cleared == [True]
