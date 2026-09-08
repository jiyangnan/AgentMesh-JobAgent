from __future__ import annotations

import json

import pytest

from jobagent.cli import _dispatch, build_parser
from jobagent.infra import rounds, state
from jobagent.infra.interaction_protocol import validate_interaction_required
from jobagent.infra.protocol import digest_payload


def _profile() -> dict:
    return {
        "schema_version": 1,
        "_meta": {"targetRolePolicyVersion": 2},
        "basic": {"currentCity": "深圳", "totalExperience": 6},
        "career": {"currentJob": {"title": "数据分析师"}},
        "preferences": {
            "targetRoles": [
                {
                    "title": "数据分析师",
                    "confidence": "inferred",
                    "priority": 1,
                },
                {
                    "title": "商业分析师",
                    "confidence": "inferred",
                    "priority": 2,
                },
            ],
            "targetCities": [{"city": "深圳", "priority": 1}],
        },
    }


@pytest.fixture
def isolated_round_state(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.json"
    current_path = tmp_path / "current-round.json"
    pending_path = tmp_path / "pending-interaction.json"
    history_dir = tmp_path / "rounds"
    discoveries_dir = tmp_path / "discoveries"
    profile_path.write_text(json.dumps(_profile(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(state, "profile_path", lambda: profile_path)
    monkeypatch.setattr(state, "current_round_path", lambda: current_path)
    monkeypatch.setattr(state, "pending_interaction_path", lambda: pending_path)
    monkeypatch.setattr(rounds, "current_round_path", lambda: current_path)
    monkeypatch.setattr(rounds, "rounds_dir", lambda: history_dir)
    monkeypatch.setattr(
        "jobagent.infra.discovery_state.discoveries_dir",
        lambda: discoveries_dir,
    )
    return {
        "current": current_path,
        "pending": pending_path,
        "profile": profile_path,
    }


def test_round_start_without_answer_returns_shared_interaction(isolated_round_state):
    result = _dispatch(build_parser().parse_args(["round", "start"]))

    assert result["ok"] is False
    assert result["error"] == "interaction_required"
    interaction = validate_interaction_required(result["interaction"])
    assert interaction["protocol"] == "agentmesh360.interaction_required"
    assert interaction["preferred_presentation"] == "card"
    assert interaction["allow_text_fallback"] is True
    field = interaction["fields"][0]
    assert field["allow_other"] is False
    assert field["default_option_ids"] == ["accept_suggested"]
    assert [option["option_id"] for option in field["options"]] == [
        "accept_suggested",
        "append_roles",
        "replace_roles",
    ]
    assert all(option["description"] for option in field["options"])
    assert interaction["fields"][0]["known_values"] == ["数据分析师", "商业分析师"]
    assert "1. 按建议岗位开始（推荐）" in interaction["fallback_text"]
    assert "2. 保留建议岗位，并追加其他岗位" in interaction["fallback_text"]
    assert "3. 只投你指定的其他岗位" in interaction["fallback_text"]
    assert interaction["continuation"]["action"] == "jobagent.interaction.respond"
    codex = result["host_presentations"]["adapters"]["codex"]
    assert codex["supported"] is True
    question = codex["arguments"]["questions"][0]
    assert question["header"] == "本轮目标岗位"
    assert question["id"] == "target_role_choice"
    assert len(question["options"]) == 3
    assert question["options"][0]["label"].endswith("(Recommended)")
    assert codex["answer_mapping"]["追加其他岗位"] == "append_roles"
    assert codex["free_text_other"]["default_option_id"] == "replace_roles"
    assert not isolated_round_state["current"].exists()
    assert isolated_round_state["pending"].exists()


def test_round_start_requires_target_cities_before_role_confirmation(
    isolated_round_state,
):
    profile = _profile()
    profile["preferences"]["targetCities"] = []
    isolated_round_state["profile"].write_text(
        json.dumps(profile, ensure_ascii=False),
        encoding="utf-8",
    )

    result = _dispatch(build_parser().parse_args(["round", "start"]))

    assert result["ok"] is False
    assert result["error"] == "interaction_required"
    interaction = validate_interaction_required(result["interaction"])
    assert interaction["kind"] == "target_city_input"
    assert interaction["fields"] == [
        {
            "field_id": "target_cities",
            "type": "text",
            "label": "目标城市",
            "required": True,
            "placeholder": "例如：郑州、杭州",
        }
    ]
    assert "告诉我本轮想看的城市" in interaction["fallback_text"]
    assert result["requires_user_action"] is True
    assert result["user_prompt"] == interaction["fallback_text"]
    assert not isolated_round_state["current"].exists()
    assert isolated_round_state["pending"].exists()


def test_target_city_response_updates_profile_before_role_confirmation(
    isolated_round_state,
):
    profile = _profile()
    profile["preferences"]["targetCities"] = []
    isolated_round_state["profile"].write_text(
        json.dumps(profile, ensure_ascii=False),
        encoding="utf-8",
    )
    requested = _dispatch(build_parser().parse_args(["round", "start"]))

    result = _dispatch(
        build_parser().parse_args(
            [
                "interaction",
                "respond",
                "--interaction-id",
                requested["interaction"]["interaction_id"],
                "--target-city",
                "郑州",
                "--target-city",
                "杭州",
            ]
        )
    )

    saved = json.loads(isolated_round_state["profile"].read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["target_cities"] == ["郑州", "杭州"]
    assert result["next_suggested"] == "jobagent round start"
    assert saved["preferences"]["targetCities"] == [
        {"city": "郑州", "priority": 1},
        {"city": "杭州", "priority": 2},
    ]
    assert not isolated_round_state["current"].exists()
    assert not isolated_round_state["pending"].exists()


def test_legacy_profile_roles_are_not_presented_as_verified_suggestions(
    isolated_round_state,
):
    profile = _profile()
    profile.pop("_meta")
    isolated_round_state["profile"].write_text(
        json.dumps(profile, ensure_ascii=False),
        encoding="utf-8",
    )

    result = _dispatch(build_parser().parse_args(["round", "start"]))

    assert result["error"] == "interaction_required"
    assert result["suggested_roles"] == []
    assert result["interaction"]["fields"][0]["type"] == "text"
    assert "没有经过新版岗位方向校验" in result["interaction"]["fallback_text"]
    assert "AI产品经理" not in result["interaction"]["fallback_text"]
    assert isolated_round_state["pending"].exists()


def test_accept_suggested_creates_confirmed_round(isolated_round_state):
    result = _dispatch(
        build_parser().parse_args(["round", "start", "--accept-suggested"])
    )

    assert result["ok"] is True
    intent = result["workflow"]["intent"]
    assert intent["status"] == "confirmed"
    assert intent["source"] == "suggested"
    assert intent["target_roles"] == ["数据分析师", "商业分析师"]
    assert isolated_round_state["current"].exists()
    assert not isolated_round_state["pending"].exists()


def test_explicit_role_replaces_suggestions(isolated_round_state):
    result = _dispatch(
        build_parser().parse_args(
            ["round", "start", "--target-role", "数据运营经理"]
        )
    )

    assert result["workflow"]["intent"]["source"] == "user_explicit"
    assert result["workflow"]["intent"]["target_roles"] == ["数据运营经理"]


def test_accept_and_explicit_role_append_in_stable_order(isolated_round_state):
    result = _dispatch(
        build_parser().parse_args(
            [
                "round",
                "start",
                "--accept-suggested",
                "--target-role",
                "数据运营经理",
            ]
        )
    )

    assert result["workflow"]["intent"]["source"] == "suggested_plus_explicit"
    assert result["workflow"]["intent"]["target_roles"] == [
        "数据分析师",
        "商业分析师",
        "数据运营经理",
    ]


def test_active_round_is_idempotent_and_rejects_retargeting(isolated_round_state):
    first = _dispatch(
        build_parser().parse_args(["round", "start", "--target-role", "数据运营经理"])
    )
    repeated = _dispatch(build_parser().parse_args(["round", "start"]))

    assert repeated["workflow"]["round_id"] == first["workflow"]["round_id"]

    with pytest.raises(rounds.RoundOrderError) as error:
        _dispatch(
            build_parser().parse_args(
                ["round", "start", "--target-role", "AI产品经理"]
            )
        )
    assert error.value.payload["error"] == "round_intent_conflict"


def test_active_pre_delivery_round_rebinds_to_updated_profile(
    isolated_round_state,
):
    first = _dispatch(
        build_parser().parse_args(["round", "start", "--target-role", "数据运营经理"])
    )
    rounds.set_platform_status(
        "boss",
        "blocked",
        command="jobagent boss discover",
        evidence={
            "login": {
                "schema_version": 1,
                "logged_in": True,
                "platform": "boss",
                "round_id": first["workflow"]["round_id"],
                "browser_session_id": "local-cdp-19222",
                "verified_at": rounds.utc_now(),
                "requires_user_action": False,
                "error": None,
            },
            "error": "Boss adapter does not have a city code for empty city",
        },
        next_suggested="jobagent boss discover",
    )
    profile = _profile()
    profile["preferences"]["targetCities"] = [
        {"city": "郑州", "priority": 1},
        {"city": "杭州", "priority": 2},
    ]
    isolated_round_state["profile"].write_text(
        json.dumps(profile, ensure_ascii=False),
        encoding="utf-8",
    )

    result = _dispatch(
        build_parser().parse_args(["round", "start", "--target-role", "数据运营经理"])
    )

    assert result["workflow"]["round_id"] == first["workflow"]["round_id"]
    assert result["workflow"]["intent"]["profile_digest"] == digest_payload(profile)
    assert result["workflow"]["platforms"]["boss"]["status"] == "pending"
    assert result["workflow"]["next_suggested"] == "jobagent boss login --check"
    assert result["workflow"]["profile_reconciliation"]["reason"] == (
        "pre_delivery_profile_update"
    )


def test_active_progressed_round_rejects_profile_rebind(isolated_round_state):
    _dispatch(
        build_parser().parse_args(["round", "start", "--target-role", "数据运营经理"])
    )
    rounds.set_platform_status(
        "boss",
        "discovered",
        command="jobagent boss discover",
        evidence={"discover_id": "dis-existing"},
        next_suggested="jobagent boss greet preview",
    )
    profile = _profile()
    profile["preferences"]["targetCities"] = [{"city": "杭州", "priority": 1}]
    isolated_round_state["profile"].write_text(
        json.dumps(profile, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(rounds.RoundOrderError) as error:
        _dispatch(
            build_parser().parse_args(
                ["round", "start", "--target-role", "数据运营经理"]
            )
        )

    assert error.value.payload["error"] == "active_round_profile_changed"
    assert error.value.payload["next_suggested"] == "jobagent round status"


def test_native_card_accept_response_creates_round(isolated_round_state):
    requested = _dispatch(build_parser().parse_args(["round", "start"]))
    interaction_id = requested["interaction"]["interaction_id"]

    result = _dispatch(
        build_parser().parse_args(
            [
                "interaction",
                "respond",
                "--interaction-id",
                interaction_id,
                "--choice",
                "accept_suggested",
            ]
        )
    )

    assert result["ok"] is True
    assert result["workflow"]["intent"]["source"] == "suggested"
    assert result["workflow"]["intent"]["target_roles"] == ["数据分析师", "商业分析师"]
    assert result["interaction_receipt"]["choice"] == "accept_suggested"
    assert not isolated_round_state["pending"].exists()


def test_append_choice_returns_role_input_then_creates_round(isolated_round_state):
    requested = _dispatch(build_parser().parse_args(["round", "start"]))
    root_id = requested["interaction"]["interaction_id"]

    follow_up = _dispatch(
        build_parser().parse_args(
            [
                "interaction",
                "respond",
                "--interaction-id",
                root_id,
                "--choice",
                "append_roles",
            ]
        )
    )

    assert follow_up["error"] == "interaction_required"
    assert follow_up["interaction"]["kind"] == "target_role_input"
    assert follow_up["interaction"]["fields"][0]["type"] == "text"
    codex = follow_up["host_presentations"]["adapters"]["codex"]
    assert codex["supported"] is False
    assert codex["unsupported_reason"] == "current_interaction_requires_free_text"
    assert not isolated_round_state["current"].exists()

    result = _dispatch(
        build_parser().parse_args(
            [
                "interaction",
                "respond",
                "--interaction-id",
                follow_up["interaction"]["interaction_id"],
                "--target-role",
                "数据运营经理",
            ]
        )
    )

    assert result["workflow"]["intent"]["source"] == "suggested_plus_explicit"
    assert result["workflow"]["intent"]["target_roles"] == [
        "数据分析师",
        "商业分析师",
        "数据运营经理",
    ]
    assert result["interaction_receipt"]["interaction_ids"] == [
        root_id,
        follow_up["interaction"]["interaction_id"],
    ]


def test_replace_choice_can_include_role_in_first_response(isolated_round_state):
    requested = _dispatch(build_parser().parse_args(["round", "start"]))
    interaction_id = requested["interaction"]["interaction_id"]

    result = _dispatch(
        build_parser().parse_args(
            [
                "interaction",
                "respond",
                "--interaction-id",
                interaction_id,
                "--choice",
                "replace_roles",
                "--target-role",
                "数据运营经理",
            ]
        )
    )

    assert result["workflow"]["intent"]["source"] == "user_explicit"
    assert result["workflow"]["intent"]["target_roles"] == ["数据运营经理"]


def test_completed_interaction_response_is_idempotent(isolated_round_state):
    requested = _dispatch(build_parser().parse_args(["round", "start"]))
    command = [
        "interaction",
        "respond",
        "--interaction-id",
        requested["interaction"]["interaction_id"],
        "--choice",
        "replace_roles",
        "--target-role",
        "数据运营经理",
    ]
    first = _dispatch(build_parser().parse_args(command))
    repeated = _dispatch(build_parser().parse_args(command))

    assert repeated["ok"] is True
    assert repeated["idempotent_replay"] is True
    assert repeated["workflow"]["round_id"] == first["workflow"]["round_id"]


def test_changed_profile_invalidates_pending_interaction(isolated_round_state):
    requested = _dispatch(build_parser().parse_args(["round", "start"]))
    profile = _profile()
    profile["career"]["currentJob"]["title"] = "产品经理"
    isolated_round_state["profile"].write_text(
        json.dumps(profile, ensure_ascii=False),
        encoding="utf-8",
    )

    result = _dispatch(
        build_parser().parse_args(
            [
                "interaction",
                "respond",
                "--interaction-id",
                requested["interaction"]["interaction_id"],
                "--choice",
                "accept_suggested",
            ]
        )
    )

    assert result["error"] == "interaction_context_changed"
    assert not isolated_round_state["current"].exists()
    assert not isolated_round_state["pending"].exists()


def test_schema_v2_active_round_is_preserved_with_legacy_intent():
    payload = {
        "schema_version": 2,
        "round_id": "round-existing",
        "status": "active",
        "created_at": "2026-07-20T00:00:00+00:00",
        "updated_at": "2026-07-20T00:00:00+00:00",
        "platform_order": list(rounds.DEFAULT_PLATFORM_ORDER),
        "browser_session_id": "local-cdp-19222",
        "platforms": {
            "boss": {"status": "completed"},
            "liepin": {"status": "discovered"},
            "zhilian": {"status": "pending"},
            "51job": {"status": "pending"},
        },
    }

    migrated = rounds.migrate_round_payload(payload)

    assert migrated["schema_version"] == rounds.ROUND_SCHEMA_VERSION
    assert migrated["platforms"] == payload["platforms"]
    assert migrated["intent"]["status"] == "legacy_implicit"
    assert migrated["migration"]["reason"] == "preserve_active_round_and_mark_legacy_intent"
