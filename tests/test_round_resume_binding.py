"""Round resume binding (P2): user-confirmed resume rides the delivery chain."""
from __future__ import annotations

import argparse
import json
from typing import Any

import pytest

from jobagent.cli import _dispatch, build_parser
from jobagent.infra import cloud_client
from jobagent.infra.cloud_client import CloudError
from jobagent.infra.interaction_state import load_pending_interaction
from jobagent.infra.protocol import digest_payload


def _args(argv: str) -> argparse.Namespace:
    return build_parser().parse_args(argv.split())


def _profile() -> dict[str, Any]:
    return {
        "basic": {"name": "Synthetic"},
        "_meta": {"targetRolePolicyVersion": 2},
        "preferences": {"targetRoles": [{"title": "Engineer", "priority": 1}], "targetCities": [{"city": "Hangzhou", "priority": 1}]},
    }


def _material_profile(direction: str = "项目经理") -> dict[str, Any]:
    """The bound resume's own canonical profile: server pins its targetRoles
    to the resume's direction, unlike the stale local snapshot."""
    return {
        "basic": {"name": "Synthetic"},
        "_meta": {"targetRolePolicyVersion": 2},
        "preferences": {
            "targetRoles": [
                {"title": direction, "confidence": "explicit", "priority": 1},
            ],
            "targetCities": [{"city": "郑州", "preference": "must"}],
        },
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
    direction = "数据产品经理" if resume_id == "resume-a" else "项目经理"
    name = "数据方向" if resume_id == "resume-a" else "项目方向"
    return {
        "id": binding_id, "context_id": "ctx-1", "resume_id": resume_id,
        "resume_revision_id": "rev-5", "resume_revision_number": 2,
        "content_digest": "digest", "target_role": direction,
        "resume_name": name, "confirmed_at": "2026-09-11T00:00:00Z",
    }


def _material(binding: dict[str, Any] | None = None, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Real server material() shape: the binding snapshot rides under
    ``binding`` (with context_id inside) and there is no top-level
    ``context_id`` / ``resume_binding`` key."""
    binding = binding or _binding()
    profile = profile or _material_profile(binding["target_role"])
    return {
        "ok": True,
        "account_ref": "acct-test",
        "binding": binding,
        "profile": profile,
        "profile_digest": digest_payload(profile),
        "resume_text": "text",
        "checked_at": "2026-09-11T00:00:00Z",
        "offline": False,
        "stale": False,
    }


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    import jobagent.infra.state as state_mod

    monkeypatch.setattr(state_mod, "STATE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(state_mod, "PROFILE_DIR", tmp_path, raising=False)
    state_mod.STATE_DIR.mkdir(parents=True, exist_ok=True)
    profile_file = tmp_path / "profile.json"
    profile_file.write_text(json.dumps(_profile()), encoding="utf-8")
    monkeypatch.setattr(state_mod, "profile_path", lambda: profile_file)
    yield


def _current_round() -> dict[str, Any] | None:
    import jobagent.infra.state as state_mod

    path = state_mod.STATE_DIR / "current_round.json"
    return json.loads(path.read_text()) if path.exists() else None


def _stage_binding(monkeypatch, binding: dict[str, Any], material: dict[str, Any] | None = None) -> None:
    """Stage a binding as if the user had just confirmed the selection."""
    from jobagent.application.round_resume_binding import save_pending_binding

    save_pending_binding({"binding": binding})
    monkeypatch.setattr(
        cloud_client, "resume_binding_material",
        lambda binding_id: material or _material(binding),
    )


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
    # Second round start reuses the staged binding without a new selection and
    # derives the intent from the bound resume's own material.
    binding = _binding(resume_id="resume-b")
    material = _material(binding)
    monkeypatch.setattr(cloud_client, "resume_binding_material", lambda binding_id: material)
    result = _dispatch(_args("round start --accept-suggested"))
    assert result["ok"] is True
    assert result["resume_binding"]["resume_id"] == "resume-b"
    round_state = _current_round()
    intent = round_state["intent"]
    # The bound round's intent is the resume's direction, not the stale local
    # snapshot's suggestion (Engineer), with explicit cities and the material
    # digest — exactly what the server validates for bound rounds.
    assert intent["target_roles"] == ["项目经理"]
    assert intent["target_cities"] == ["郑州"]
    assert intent["profile_digest"] == material["profile_digest"]
    assert round_state["resume_binding"]["id"] == "binding-1"


def test_bound_round_start_card_suggests_binding_direction_only(monkeypatch):
    binding = _binding(resume_id="resume-b")
    _stage_binding(monkeypatch, binding)
    result = _dispatch(_args("round start"))
    assert result["error"] == "interaction_required"
    assert result["suggested_roles"] == ["项目经理"]
    prompt = result["interaction"]["prompt"]
    assert "项目方向" in prompt and "项目经理" in prompt
    assert "本机最近一次" not in prompt
    options = {
        option["option_id"]
        for option in result["interaction"]["fields"][0]["options"]
    }
    assert options == {"accept_suggested", "rebind_resume"}


def test_bound_respond_accept_creates_aligned_round(monkeypatch):
    binding = _binding(resume_id="resume-b")
    material = _material(binding)
    _stage_binding(monkeypatch, binding, material)
    card = _dispatch(_args("round start"))
    interaction_id = card["interaction"]["interaction_id"]
    answer = _dispatch(
        _args(f"interaction respond --interaction-id {interaction_id} --choice accept_suggested")
    )
    assert answer["ok"] is True
    round_state = _current_round()
    intent = round_state["intent"]
    assert intent["target_roles"] == ["项目经理"]
    assert intent["target_cities"] == ["郑州"]
    assert intent["profile_digest"] == material["profile_digest"]
    assert round_state["resume_binding"]["id"] == "binding-1"


def test_bound_rebind_choice_releases_staged_binding(monkeypatch):
    binding = _binding(resume_id="resume-b")
    _stage_binding(monkeypatch, binding)
    card = _dispatch(_args("round start"))
    interaction_id = card["interaction"]["interaction_id"]
    answer = _dispatch(
        _args(f"interaction respond --interaction-id {interaction_id} --choice rebind_resume")
    )
    assert answer["ok"] is True
    assert answer["rebind_requested"] is True
    assert _current_round() is None
    import jobagent.infra.state as state_mod

    assert not (state_mod.STATE_DIR / "pending_round_binding.json").exists()


def test_bound_replace_roles_is_refused_with_rebind_guidance(monkeypatch):
    binding = _binding(resume_id="resume-b")
    _stage_binding(monkeypatch, binding)
    card = _dispatch(_args("round start"))
    interaction_id = card["interaction"]["interaction_id"]
    result = _dispatch(
        _args(f"interaction respond --interaction-id {interaction_id} --choice replace_roles --target-role AI产品经理")
    )
    assert result["error"] == "invalid_interaction_response"
    assert "方向" in result["message"]
    assert _current_round() is None


def test_bound_direct_target_role_outside_direction_is_refused(monkeypatch):
    binding = _binding(resume_id="resume-b")
    _stage_binding(monkeypatch, binding)
    result = _dispatch(_args("round start --target-role AI产品经理"))
    assert result["error"] == "invalid_round_intent"
    assert "方向" in result["message"]
    assert _current_round() is None
    # The matching direction still goes through.
    ok = _dispatch(_args("round start --target-role 项目经理"))
    assert ok["ok"] is True
    assert _current_round()["intent"]["target_roles"] == ["项目经理"]


def test_direction_mismatch_refuses_attach_to_active_round(monkeypatch):
    classic = _dispatch(_args("round start --no-resume-binding --accept-suggested"))
    assert classic["ok"] is True
    assert _current_round()["intent"]["target_roles"] == ["Engineer"]
    binding = _binding(resume_id="resume-b")  # direction 项目经理
    _stage_binding(monkeypatch, binding)
    result = _dispatch(_args("round start"))
    assert result["error"] == "resume_binding_direction_mismatch"
    assert _current_round().get("resume_binding") is None


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
    material = _material(_binding())  # real server shape: ``binding`` key
    monkeypatch.setattr(cloud_client, "resume_binding_material", lambda binding_id: material)
    result = _dispatch(_args("round start --resume-binding binding-1 --accept-suggested"))
    assert result["ok"] is True
    assert result["resume_binding"]["id"] == "binding-1"
    assert _current_round()["intent"]["target_roles"] == ["数据产品经理"]


def test_staged_binding_material_unavailable_keeps_round_unstarted(monkeypatch):
    binding = _binding(resume_id="resume-b")
    _stage_binding(monkeypatch, binding)

    def _down(binding_id):
        raise CloudError("unavailable", status=503, code="resume_center_unavailable")

    monkeypatch.setattr(cloud_client, "resume_binding_material", _down)
    result = _dispatch(_args("round start"))
    assert result["error"] == "resume_binding_material_unavailable"
    assert _current_round() is None


def test_staged_binding_preparation_required_clears_and_reselects(monkeypatch):
    binding = _binding(resume_id="resume-b")
    _stage_binding(monkeypatch, binding)

    def _stale(binding_id):
        raise CloudError(
            "Confirm resume preparation and explicitly select material for this task.",
            status=409, code="preparation_required",
            details={"reason": "resume_not_in_prepared_set"},
        )

    monkeypatch.setattr(cloud_client, "resume_binding_material", _stale)
    result = _dispatch(_args("round start"))
    assert result["error"] == "resume_binding_paused"
    assert result["next_suggested"] == "jobagent round start"
    import jobagent.infra.state as state_mod

    assert not (state_mod.STATE_DIR / "pending_round_binding.json").exists()
    assert _current_round() is None


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
    material = _material(_binding())  # direction 数据产品经理
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
    monkeypatch.setattr(discover_mod, "_start_context", lambda *a, **k: {"round_id": "round-1", "platform": "boss", "profile_digest": material["profile_digest"]})
    monkeypatch.setattr(discover_mod, "save_pending_start", lambda *a, **k: None)
    monkeypatch.setattr(discover_mod, "load_collection_checkpoint", lambda platform: None)
    import jobagent.infra.state as state_mod
    monkeypatch.setattr(state_mod, "profile_path", lambda: __import__("pathlib").Path("/nonexistent"))
    with pytest.raises(CloudError):
        discover_mod.run_discover("boss")
    assert captured.get("resume_binding_id") == "binding-1"
    # context_id comes from the binding snapshot, not a popped material key.
    assert captured.get("context_id") == "ctx-1"
    assert captured.get("round_id") == "round-1"
    assert captured.get("profile") == material["profile"]


def test_stale_binding_pauses_and_clears_for_reselection(monkeypatch):
    from jobagent.application import discover as discover_mod
    from jobagent.infra import rounds as rounds_mod

    material = _material(_binding())
    active = {"round_id": "round-1", "status": "active", "resume_binding": _binding(),
              "intent": {"status": "confirmed", "target_roles": ["数据产品经理"]}, "platforms": {}}
    monkeypatch.setattr(rounds_mod, "ensure_current_round", lambda: active)
    monkeypatch.setattr(cloud_client, "resume_binding_material", lambda binding_id: material)

    def _stale(**kwargs):
        raise CloudError("Confirm resume preparation and explicitly select material for this task.",
                         status=409, code="preparation_required")

    monkeypatch.setattr(cloud_client, "discovery_start", _stale)
    monkeypatch.setattr(discover_mod, "_resume_pending_decision", lambda *a, **k: None)
    monkeypatch.setattr(discover_mod, "_start_context", lambda *a, **k: {"round_id": "round-1", "platform": "boss", "profile_digest": material["profile_digest"]})
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


def test_unbound_round_keeps_local_suggestion_copy(monkeypatch):
    result = _dispatch(_args("round start --no-resume-binding"))
    assert result["error"] == "interaction_required"
    prompt = result["interaction"]["prompt"]
    # Honest copy: unbound rounds suggest from the last local analysis.
    assert "本机最近一次简历分析" in prompt
    assert "当前简历中可验证" not in prompt
    assert result["suggested_roles"] == ["Engineer"]
