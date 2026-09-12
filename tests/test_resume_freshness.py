"""投递前平台简历新鲜度门禁 — 设计文档 Part B (2026-09-12) 的行为测试。"""

from __future__ import annotations

import json

import pytest

from jobagent.application import resume_freshness as freshness
from jobagent.infra import rounds as rounds_mod
from jobagent.infra import state as state_mod
from jobagent.infra.interaction_state import load_pending_interaction

PLATFORMS = ["boss", "liepin", "zhilian", "51job"]


def binding_fixture(revision="rev-1", digest="sha256:aaa", name="后端简历",
                    confirmed_at="2026-09-08T10:00:00+00:00", resume_id="res-1"):
    return {
        "id": "bind-1",
        "context_id": "ctx-1",
        "resume_id": resume_id,
        "resume_revision_id": revision,
        "content_digest": digest,
        "target_role": "Engineer",
        "resume_name": name,
        "confirmed_at": confirmed_at,
        "resume_revision_number": 2,
    }


@pytest.fixture
def fresh_env(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "STATE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(state_mod, "ROUNDS_DIR", tmp_path / "rounds", raising=False)
    from jobagent.infra import account_state

    monkeypatch.setattr(account_state, "current_account_ref", lambda **kwargs: "acct_fresh")
    yield tmp_path


def write_round(binding, *, platforms=None, round_id="r-1", extra=None):
    state = {
        "schema_version": rounds_mod.ROUND_SCHEMA_VERSION,
        "round_id": round_id,
        "status": "active",
        "created_at": "2026-09-12T08:00:00+00:00",
        "updated_at": "2026-09-12T08:00:00+00:00",
        "platform_order": list(PLATFORMS),
        "platforms": platforms
        or {platform: {"status": "pending"} for platform in PLATFORMS},
        "resume_binding": binding,
    }
    if extra:
        state.update(extra)
    state_mod.save_json(state_mod.current_round_path(), state)
    return state


def set_baseline(platform, *, revision="rev-1", digest="sha256:aaa",
                 resume_id="res-1", confirmed_at="2026-09-01T00:00:00+00:00"):
    data = freshness.load_baselines()
    data["platforms"][platform] = {
        "resume_id": resume_id,
        "resume_revision_id": revision,
        "content_digest": digest,
        "confirmed_at": confirmed_at,
        "updated_at": "2026-09-12T00:00:00+00:00",
    }
    state_mod.save_json(state_mod.resume_freshness_path(), data)


def source_fixture():
    return {
        "input_path": "/tmp/reviewed.json",
        "preview_id": "preview-1",
        "authorization_id": "auth-1",
        "limit": 100,
        "stop_on_failure": True,
    }


# ------------------------------------------------------------ detection


def test_fresh_baseline_is_silent(fresh_env):
    write_round(binding_fixture())
    set_baseline("boss")
    assert freshness.gate_delivery("boss", source=source_fixture()) is None
    # No gate record was created and no pending interaction occupies the slot.
    active = state_mod.load_json(state_mod.current_round_path())
    assert "resume_freshness" not in active["platforms"]["boss"]
    assert load_pending_interaction() is None


def test_no_baseline_first_delivery_asks_once(fresh_env):
    write_round(binding_fixture(), platforms={p: {"status": "pending"} for p in PLATFORMS})
    card = freshness.gate_delivery("boss", source=source_fixture())
    assert card is not None and card["error"] == "interaction_required"
    interaction = card["interaction"]
    assert interaction["kind"] == freshness.INTERACTION_KIND
    assert "首次" in interaction["prompt"] and "Boss 直聘" in interaction["prompt"]
    options = interaction["fields"][0]["options"]
    assert [option["option_id"] for option in options] == ["synced", "pause_platform", "pause_round"]
    pending = load_pending_interaction()
    assert pending and pending["stage"] == freshness.INTERACTION_KIND


def test_changed_revision_cards_with_date_anchor(fresh_env):
    write_round(binding_fixture())
    set_baseline("boss", revision="rev-0", digest="sha256:old")
    card = freshness.gate_delivery("boss", source=source_fixture())
    prompt = card["interaction"]["prompt"]
    assert "9 月 8 日" in prompt and "后端简历" in prompt and "Boss 直聘" in prompt
    assert "首次" not in prompt


def test_unbound_round_and_dry_run_are_silent(fresh_env):
    write_round(None)
    assert freshness.gate_delivery("boss", source=source_fixture()) is None
    write_round(binding_fixture())
    assert freshness.gate_delivery("boss", source=source_fixture(), dry_run=True) is None


def test_gate_is_idempotent_while_awaiting(fresh_env):
    write_round(binding_fixture())
    first = freshness.gate_delivery("boss", source=source_fixture())
    second = freshness.gate_delivery("boss", source=source_fixture())
    assert second["interaction"]["interaction_id"] == first["interaction"]["interaction_id"]


# ------------------------------------------------------------- option A


def test_choice_synced_updates_baseline_and_restores_flow(fresh_env):
    platforms = {p: {"status": "pending"} for p in PLATFORMS}
    platforms["boss"] = {
        "status": "reviewed",
        "evidence": {"discover_id": "d-1", "preview_id": "preview-1", "authorization_id": "auth-1"},
    }
    write_round(binding_fixture(), platforms=platforms)
    card = freshness.gate_delivery("boss", source=source_fixture())
    result = freshness.respond(card["interaction"]["interaction_id"], choice="synced")
    assert result["ok"] is True and result["event"] == "resume_freshness_synced"
    assert "apply send" in result["next_suggested"] or "greet send" in result["next_suggested"]
    assert "--preview-id preview-1" in result["next_suggested"]
    baseline = freshness.load_baselines()["platforms"]["boss"]
    assert baseline["resume_revision_id"] == "rev-1" and baseline["content_digest"] == "sha256:aaa"
    active = state_mod.load_json(state_mod.current_round_path())
    assert "resume_freshness" not in active["platforms"]["boss"]
    assert active["platforms"]["boss"]["status"] == "reviewed"
    assert load_pending_interaction() is None
    # Same revision in the same round: silent now and forever after.
    assert freshness.gate_delivery("boss", source=source_fixture()) is None


def test_same_round_new_revision_asks_again(fresh_env):
    write_round(binding_fixture())
    set_baseline("boss", revision="rev-0", digest="sha256:old")
    card = freshness.gate_delivery("boss", source=source_fixture())
    freshness.respond(card["interaction"]["interaction_id"], choice="synced")
    # A genuinely newer confirmed revision mid-round is a new question.
    write_round(binding_fixture(revision="rev-2", digest="sha256:bbb"))
    again = freshness.gate_delivery("boss", source=source_fixture())
    assert again is not None
    assert again["interaction"]["interaction_id"] != card["interaction"]["interaction_id"]


# --------------------------------------------------------- options B and C


def test_pause_platform_holds_only_that_platform(fresh_env):
    platforms = {p: {"status": "pending"} for p in PLATFORMS}
    platforms["boss"] = {"status": "reviewed", "evidence": {"discover_id": "d-1"}}
    write_round(binding_fixture(), platforms=platforms)
    card = freshness.gate_delivery("boss", source=source_fixture())
    interaction_id = card["interaction"]["interaction_id"]
    result = freshness.respond(interaction_id, choice="pause_platform")
    assert result["ok"] is True and result["event"] == "resume_freshness_platform_hold"
    workflow = rounds_mod.round_status()
    assert workflow["platforms"]["boss"]["status"] == rounds_mod.FRESHNESS_HOLD_STATUS
    assert workflow["current_platform"] == "liepin"
    assert workflow["resume_freshness"]["held_platforms"] == ["boss"]
    assert workflow["resume_freshness"]["round_hold"] is None
    assert load_pending_interaction() is None
    # Other platforms keep working; the held one is rejected with a pointer.
    rounds_mod.assert_platform_turn("liepin")
    with pytest.raises(rounds_mod.RoundOrderError) as exc:
        rounds_mod.assert_platform_turn("boss")
    assert exc.value.payload["error"] == "platform_resume_freshness_hold"
    assert interaction_id in exc.value.payload["next_suggested"]


def test_hold_answers_by_id_even_when_slot_is_taken(fresh_env, monkeypatch):
    platforms = {p: {"status": "pending"} for p in PLATFORMS}
    platforms["boss"] = {"status": "reviewed"}
    write_round(binding_fixture(), platforms=platforms)
    card = freshness.gate_delivery("boss", source=source_fixture())
    interaction_id = card["interaction"]["interaction_id"]
    freshness.respond(interaction_id, choice="pause_platform")
    # Liepin's own delivery confirmation card later overwrites the slot.
    from jobagent.infra.interaction_state import save_pending_interaction

    save_pending_interaction(
        {
            "interaction_id": "liepin-card",
            "kind": "delivery_confirmation",
            "title": "t",
            "prompt": "p",
            "fallback_text": "f",
            "fields": [{"field_id": "x", "type": "single", "label": "l",
                        "options": [{"option_id": "confirm_all", "label": "y"}]}],
        },
        stage="delivery_choice",
    )
    snapshot = binding_fixture()
    monkeypatch.setattr(
        freshness.cloud_client,
        "resume_binding_material",
        lambda binding_id: {"binding": snapshot, "profile": {}, "profile_digest": "d"},
    )
    result = freshness.respond(interaction_id, choice="synced")
    assert result["ok"] is True
    assert freshness.load_baselines()["platforms"]["boss"]["resume_revision_id"] == "rev-1"
    workflow = rounds_mod.round_status()
    assert workflow["platforms"]["boss"]["status"] == "reviewed"
    assert workflow["current_platform"] == "boss"


def test_pause_round_blocks_the_whole_round(fresh_env):
    platforms = {p: {"status": "pending"} for p in PLATFORMS}
    platforms["boss"] = {"status": "reviewed"}
    write_round(binding_fixture(), platforms=platforms)
    card = freshness.gate_delivery("boss", source=source_fixture())
    interaction_id = card["interaction"]["interaction_id"]
    result = freshness.respond(interaction_id, choice="pause_round")
    assert result["ok"] is True and result["event"] == "resume_freshness_round_hold"
    workflow = rounds_mod.round_status()
    assert workflow["resume_freshness"]["round_hold"]["platform"] == "boss"
    assert workflow["resume_freshness"]["held_platforms"] == ["boss"]
    with pytest.raises(rounds_mod.RoundOrderError) as exc:
        rounds_mod.assert_platform_turn("liepin")
    assert exc.value.payload["error"] == "round_resume_freshness_hold"
    # next_suggested points at the respond command while everything is held.
    assert interaction_id in workflow["next_suggested"] or "interaction respond" in workflow["next_suggested"]


def test_round_hold_resumes_in_original_order(fresh_env, monkeypatch):
    platforms = {p: {"status": "pending"} for p in PLATFORMS}
    platforms["boss"] = {"status": "reviewed"}
    write_round(binding_fixture(), platforms=platforms)
    card = freshness.gate_delivery("boss", source=source_fixture())
    interaction_id = card["interaction"]["interaction_id"]
    freshness.respond(interaction_id, choice="pause_round")
    monkeypatch.setattr(
        freshness.cloud_client,
        "resume_binding_material",
        lambda binding_id: {"binding": binding_fixture(), "profile": {}, "profile_digest": "d"},
    )
    result = freshness.respond(interaction_id, choice="synced")
    assert result["ok"] is True
    workflow = rounds_mod.round_status()
    assert workflow["resume_freshness"]["round_hold"] is None
    assert workflow["platforms"]["boss"]["status"] == "reviewed"
    assert workflow["current_platform"] == "boss"
    rounds_mod.assert_platform_turn("boss")  # the held platform resumes its turn
    with pytest.raises(rounds_mod.RoundOrderError) as exc:
        rounds_mod.assert_platform_turn("liepin")
    # Liepin is blocked by ordinary ordering now, not by the freshness hold.
    assert exc.value.payload["error"] == "platform_out_of_order"


# ------------------------------------------------- B/C respond re-checks


def test_hold_respond_rechecks_workbench_and_reanchors(fresh_env, monkeypatch):
    platforms = {p: {"status": "pending"} for p in PLATFORMS}
    platforms["boss"] = {"status": "reviewed"}
    write_round(binding_fixture())
    card = freshness.gate_delivery("boss", source=source_fixture())
    interaction_id = card["interaction"]["interaction_id"]
    freshness.respond(interaction_id, choice="pause_platform")
    # The user confirmed yet another revision while uploading.
    monkeypatch.setattr(
        freshness.cloud_client,
        "resume_binding_material",
        lambda binding_id: {
            "binding": binding_fixture(revision="rev-9", digest="sha256:new",
                                       confirmed_at="2026-09-11T09:30:00+00:00"),
            "profile": {},
            "profile_digest": "d",
        },
    )
    result = freshness.respond(interaction_id, choice="synced")
    assert result["ok"] is False and result["error"] == "resume_freshness_changed_again"
    assert "9 月 11 日" in result["message"]
    # No baseline was written and the platform stays held, re-anchored.
    assert "boss" not in freshness.load_baselines()["platforms"]
    workflow = rounds_mod.round_status()
    assert workflow["platforms"]["boss"]["status"] == rounds_mod.FRESHNESS_HOLD_STATUS
    record = workflow["platforms"]["boss"]["resume_freshness"]
    assert record["revision"]["resume_revision_id"] == "rev-9"
    assert record["interaction_id"] != interaction_id
    # Answering the re-anchored id against the same revision resolves.
    result = freshness.respond(record["interaction_id"], choice="synced")
    assert result["ok"] is True
    assert freshness.load_baselines()["platforms"]["boss"]["resume_revision_id"] == "rev-9"


def test_hold_respond_material_unavailable_keeps_hold(fresh_env, monkeypatch):
    from jobagent.infra.cloud_client import CloudError

    platforms = {p: {"status": "pending"} for p in PLATFORMS}
    platforms["boss"] = {"status": "reviewed"}
    write_round(binding_fixture())
    card = freshness.gate_delivery("boss", source=source_fixture())
    interaction_id = card["interaction"]["interaction_id"]
    freshness.respond(interaction_id, choice="pause_platform")

    def unavailable(binding_id):
        raise CloudError("network down", status=503, code="upstream_unavailable", retryable=True)

    monkeypatch.setattr(freshness.cloud_client, "resume_binding_material", unavailable)
    result = freshness.respond(interaction_id, choice="synced")
    assert result["ok"] is False and result["error"] == "resume_freshness_recheck_unavailable"
    assert rounds_mod.round_status()["platforms"]["boss"]["status"] == rounds_mod.FRESHNESS_HOLD_STATUS


def test_hold_respond_preparation_required_unwinds_binding(fresh_env, monkeypatch):
    from jobagent.infra.cloud_client import CloudError

    platforms = {p: {"status": "pending"} for p in PLATFORMS}
    platforms["boss"] = {"status": "reviewed"}
    write_round(binding_fixture(), platforms=platforms)
    card = freshness.gate_delivery("boss", source=source_fixture())
    interaction_id = card["interaction"]["interaction_id"]
    freshness.respond(interaction_id, choice="pause_round")

    def changed(binding_id):
        raise CloudError("preparation required", status=409, code="preparation_required")

    monkeypatch.setattr(freshness.cloud_client, "resume_binding_material", changed)
    result = freshness.respond(interaction_id, choice="synced")
    assert result["ok"] is False and result["error"] == "resume_binding_paused"
    active = state_mod.load_json(state_mod.current_round_path())
    assert not active.get("resume_binding")
    assert "resume_freshness" not in active["platforms"]["boss"]
    assert "resume_freshness_round_hold" not in active


def test_invalid_choice_is_rejected_with_card(fresh_env):
    write_round(binding_fixture())
    card = freshness.gate_delivery("boss", source=source_fixture())
    result = freshness.respond(card["interaction"]["interaction_id"], choice="confirm_all")
    assert result["ok"] is False and result["error"] == "invalid_interaction_response"
    assert load_pending_interaction() is not None


# ------------------------------------------------- scope and isolation


def test_multi_platform_gates_are_independent(fresh_env):
    write_round(binding_fixture())
    set_baseline("boss")
    assert freshness.gate_delivery("boss", source=source_fixture()) is None
    card = freshness.gate_delivery("liepin", source=source_fixture())
    assert card is not None and "猎聘" in card["interaction"]["prompt"]


def test_baseline_file_is_account_owned(fresh_env):
    from jobagent.infra.account_state import _ACCOUNT_OWNED_PATHS

    assert "resume_freshness_baselines.json" in _ACCOUNT_OWNED_PATHS


def test_round_skip_is_the_hold_escape_hatch(fresh_env):
    platforms = {p: {"status": "pending"} for p in PLATFORMS}
    platforms["boss"] = {"status": "reviewed"}
    write_round(binding_fixture(), platforms=platforms)
    card = freshness.gate_delivery("boss", source=source_fixture())
    interaction_id = card["interaction"]["interaction_id"]
    freshness.respond(interaction_id, choice="pause_round")
    # The CLI round-skip path: clear the hold, then mark the platform skipped.
    freshness.clear_hold("boss")
    rounds_mod.set_platform_status("boss", "skipped_this_round", command="jobagent round skip")
    active = state_mod.load_json(state_mod.current_round_path())
    assert "resume_freshness" not in active["platforms"]["boss"]
    assert "resume_freshness_round_hold" not in active
    rounds_mod.assert_platform_turn("liepin")


# ------------------------------------------------------- wiring points


def test_native_start_delivery_consults_gate(fresh_env, monkeypatch):
    from jobagent.application import delivery as delivery_mod
    from jobagent.application import native_work

    active = write_round(binding_fixture())
    sentinel = {"ok": False, "error": "interaction_required", "sentinel": True}
    called = {}

    def fake_gate(platform, *, source, dry_run=False):
        called["args"] = (platform, dict(source), dry_run)
        return sentinel

    monkeypatch.setattr(freshness, "gate_delivery", fake_gate)
    monkeypatch.setattr(delivery_mod, "_load_reviewed", lambda *a, **k: {
        "source_path": "/tmp/reviewed.json", "discover_id": "d-1",
        "send_candidates": [{"id": "job-1", "title": "t", "company": "c", "url": "https://x.test/j"}],
    })
    monkeypatch.setattr(native_work, "current_account_ref", lambda: "acct_fresh")
    monkeypatch.setattr(native_work.rounds, "assert_platform_turn", lambda platform: {})
    monkeypatch.setattr(native_work.rounds, "ensure_current_round", lambda: active)
    monkeypatch.setattr(native_work.rounds, "save_round", lambda value: None)
    result = native_work.start_delivery("boss", input_path=None, preview_id="preview-1",
                                        authorization_id="auth-1")
    assert result == sentinel
    assert called["args"][0] == "boss"
    assert called["args"][1]["preview_id"] == "preview-1"
    assert called["args"][2] is False
    # The delivery was never staged on the platform.
    assert "native_delivery" not in active["platforms"]["boss"]


def test_native_start_delivery_dry_run_skips_gate(fresh_env, monkeypatch):
    from jobagent.application import delivery as delivery_mod
    from jobagent.application import native_work

    active = write_round(binding_fixture())
    monkeypatch.setattr(
        freshness, "gate_delivery",
        lambda *a, **k: pytest.fail("gate must not run for dry runs"),
    )
    monkeypatch.setattr(delivery_mod, "_load_reviewed", lambda *a, **k: {
        "source_path": "/tmp/reviewed.json", "discover_id": "d-1", "send_candidates": [],
    })
    monkeypatch.setattr(native_work, "current_account_ref", lambda: "acct_fresh")
    monkeypatch.setattr(native_work.rounds, "assert_platform_turn", lambda platform: {})
    monkeypatch.setattr(native_work.rounds, "ensure_current_round", lambda: active)
    monkeypatch.setattr(native_work.rounds, "save_round", lambda value: None)
    result = native_work.start_delivery("boss", input_path=None, preview_id="preview-1",
                                        authorization_id="auth-1", dry_run=True)
    assert result["dry_run"] is True


def test_legacy_send_reviewed_consults_gate(fresh_env, monkeypatch):
    from jobagent.application import delivery

    write_round(binding_fixture())
    envelope = {
        "platform": "liepin",
        "discover_id": "d-1",
        "source_path": "/tmp/reviewed.json",
        "send_candidates": [{"id": "job-1", "title": "t", "company": "c", "url": "https://x.test/j"}],
    }
    sentinel = {"ok": False, "error": "interaction_required", "sentinel": True}
    monkeypatch.setattr(freshness, "gate_delivery", lambda *a, **k: sentinel)
    monkeypatch.setattr(delivery, "_load_reviewed", lambda *a, **k: envelope)
    assert delivery.send_reviewed("liepin", input_path=None, preview_id="p-1",
                                  authorization_id="a-1") == sentinel


def test_next_work_with_everything_held_returns_respond_pointer(fresh_env, monkeypatch):
    from jobagent.application import native_work

    platforms = {
        "boss": {"status": "reviewed"},
        "liepin": {"status": "completed"},
        "zhilian": {"status": "completed"},
        "51job": {"status": "completed"},
    }
    write_round(binding_fixture(), platforms=platforms)
    card = freshness.gate_delivery("boss", source=source_fixture())
    interaction_id = card["interaction"]["interaction_id"]
    freshness.respond(interaction_id, choice="pause_platform")
    result = native_work.next_work()
    assert result["ok"] is True and result["requires_user_action"] is True
    assert interaction_id in result["next_suggested"]


def test_cli_interaction_respond_dispatches_freshness(fresh_env):
    from jobagent.cli import _dispatch, build_parser

    platforms = {p: {"status": "pending"} for p in PLATFORMS}
    platforms["boss"] = {"status": "reviewed"}
    write_round(binding_fixture(), platforms=platforms)
    card = freshness.gate_delivery("boss", source=source_fixture())
    parser = build_parser()
    args = parser.parse_args([
        "interaction", "respond",
        "--interaction-id", card["interaction"]["interaction_id"],
        "--choice", "synced",
    ])
    result = _dispatch(args)
    assert result["ok"] is True and result["event"] == "resume_freshness_synced"
    assert freshness.load_baselines()["platforms"]["boss"]["resume_revision_id"] == "rev-1"
