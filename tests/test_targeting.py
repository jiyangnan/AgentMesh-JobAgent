"""First-use geography must precede resume/search work and survive restarts."""
import base64
import copy
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jobagent import cli
from jobagent.application import targeting, workflow
from jobagent.application.interaction_answer import apply_answer_file
from jobagent.domain.targeting import normalize_targets
from jobagent.infra import account_state, browser_work, cloud_client, protocol, rounds, state
from jobagent.infra.interaction_state import load_pending_interaction
from jobagent.infra.workflow_protocol import with_contract

MAINLAND = {"scope": "mainland_china", "locations": [
    {"label": "深圳", "market": "mainland_china", "kind": "city", "country_code": "CN"}]}
TOKYO = {"locations": [{"label": "東京", "market": "outside_mainland_china", "kind": "city", "country_code": "JP"}]}
PROFILE = {"schema_version": 1, "_meta": {"targetRolePolicyVersion": 2},
           "basic": {"name": "Synthetic", "currentCity": "Tokyo"},
           "preferences": {"targetRoles": [{"title": "Product Manager", "priority": 1}],
                           "targetCities": [{"city": "北京", "priority": 1}]}}


@pytest.fixture
def local(tmp_path, monkeypatch):
    for name, path in {"APP_DIR": tmp_path, "STATE_DIR": tmp_path / "state",
                       "ROUNDS_DIR": tmp_path / "rounds", "LOG_DIR": tmp_path / "logs"}.items():
        monkeypatch.setattr(state, name, path)
    account = {"ref": "synthetic-target-account"}
    for module in (targeting, workflow, account_state):
        monkeypatch.setattr(module, "current_account_ref", lambda: account["ref"])
    monkeypatch.setattr(browser_work, "has_open", lambda: False)
    monkeypatch.setattr(browser_work, "list_work", lambda *_: [])
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", base64.urlsafe_b64encode(public).decode())
    calls = []

    def sign(body, order=None):
        # Synthetic server response, not production routing implementation.
        order = order or list(rounds.DEFAULT_PLATFORM_ORDER)
        targets = normalize_targets(body["targets"])
        value = {"signature_algorithm": "Ed25519", "manifest_type": "target_region_plan", "protocol_version": 2, "targeting_version": 1,
                 "account_ref": account["ref"], "request_id": body["request_id"], "targets": targets,
                 "targets_digest": protocol.digest_payload(targets), "authorization": "planning_only", "charged": False,
                 "routing": {"ready": True, "required_input": None, "platform_order": order, "groups": []}}
        value["signature"] = base64.urlsafe_b64encode(key.sign(protocol.canonical_json_bytes(value))).decode()
        return {"ok": True, "plan": value}

    def plan(body):
        calls.append(copy.deepcopy(body))
        return sign(body)

    def forbidden(*args, **kwargs):
        pytest.fail("Target setup must not call resume, paid discovery or recruiting platforms")

    monkeypatch.setattr(cloud_client, "target_region_plan", plan)
    monkeypatch.setattr(cloud_client, "me", lambda: {"server": {"capabilities": {"linkedin_easy_apply_version": 0}}})
    monkeypatch.setattr(cloud_client, "resume_center_preparation", forbidden)
    monkeypatch.setattr(cloud_client, "resume_analyze", forbidden)
    return {"path": tmp_path, "account": account, "calls": calls, "sign": sign}


def run(*argv):
    return cli._dispatch_unlocked(cli.build_parser().parse_args(list(argv)))


def answer(card, *argv):
    return run("interaction", "respond", "--interaction-id", card["interaction"]["interaction_id"], *argv)


def answer_file(local, card, body):
    file = local["path"] / "answer.json"
    file.write_text(json.dumps(body))
    args = cli.build_parser().parse_args(["interaction", "respond", "--interaction-id",
                                         card["interaction"]["interaction_id"], "--answer-file", str(file)])
    apply_answer_file(args)
    return targeting.respond(args)


@pytest.mark.parametrize("language,title", [("en", "Target work location"), ("zh-CN", "目标工作地区"),
                                             ("ja", "希望する勤務地"), ("ko", "희망 근무 지역")])
def test_first_card_is_localized_without_inferring_job_region(local, language, title):
    state.save_json(state.profile_path(), PROFILE)
    card = run("round", "start", "--language", language)
    assert card["interaction"]["kind"] == "target_region"
    assert card["interaction"]["title"] == title
    field = card["interaction"]["fields"][0]
    assert field["default_option_ids"] == []
    assert [item["option_id"] for item in field["options"]] == ["mainland_china", "outside_mainland_china"]
    assert with_contract(card)["action"]["type"] == "ask"
    assert not local["calls"] and not state.current_round_path().exists()


def test_explicit_city_survives_region_card_and_creates_signed_mainland_round(local):
    state.save_json(state.profile_path(), PROFILE)
    first = run("round", "start", "--target-role", "Product Manager", "--target-city", "深圳", "--no-resume-binding")
    assert first["interaction"]["kind"] == "target_region"
    assert run("round", "start")["interaction"] == first["interaction"]
    saved = answer(first, "--choice", "mainland_china")
    assert saved["targeting"]["locations"][0]["label"] == "深圳"
    result = run("round", "start")
    assert result["ok"]
    active = state.load_json(state.current_round_path())
    assert active["platform_order"] == ["boss", "liepin", "zhilian", "51job"]
    assert active["intent"]["target_cities"] == ["深圳"]
    assert active["intent"]["targeting"] == active["target_plan"]["targets"]
    assert len(local["calls"]) == 1
    assert answer(first, "--choice", "mainland_china")["idempotent_replay"]
    assert answer(first, "--choice", "outside_mainland_china")["error"] == "interaction_response_conflict"
    assert run("round", "start", "--no-resume-binding")["workflow"]["round_id"] == active["round_id"]
    assert len(local["calls"]) == 1


def test_missing_locations_asks_once_then_preserves_choice(local):
    first = run("round", "start")
    answer(first, "--choice", "mainland_china")
    second = targeting.ensure()
    assert second["interaction"]["kind"] == "target_locations"
    assert with_contract(second)["action"]["response_arguments"]["answer_flag"] == "--target-location"
    assert targeting.ensure()["interaction"] == second["interaction"]
    answer(second, "--target-location", "深圳")
    assert targeting.ensure() is None
    assert targeting.ensure() is None and len(local["calls"]) == 1


def test_structured_answer_skips_duplicate_region_question_and_does_not_open_boss(local, monkeypatch):
    card = run("round", "start")
    saved = answer_file(local, card, {"target_locations": TOKYO["locations"]})
    assert saved["ok"]
    monkeypatch.setattr(cloud_client, "target_region_plan", lambda body: local["sign"](body, ["linkedin"]))
    blocked = run("round", "start")
    assert blocked["error"] == "linkedin_unavailable"
    assert blocked["target_routing"]["platform_order"] == ["linkedin"]
    assert with_contract(blocked)["action"]["type"] == "blocked"
    assert not state.current_round_path().exists()
    monkeypatch.setattr(cloud_client, "me", lambda: {"server": {"capabilities": {"linkedin_easy_apply_version": 1}}})
    assert targeting.ensure() is None


def test_country_state_and_mixed_targets_need_no_forced_city_cascade(local, monkeypatch):
    monkeypatch.setattr(cloud_client, "target_region_plan", lambda body: local["sign"](body, ["linkedin", *rounds.DEFAULT_PLATFORM_ORDER]))
    file = local["path"] / "targets.json"
    file.write_text(json.dumps({"locations": [
        {"label": "California", "market": "outside_mainland_china", "kind": "subdivision", "country_code": "US"},
        *MAINLAND["locations"]]}))
    result = run("round", "start", "--targeting-file", str(file))
    assert result["error"] == "linkedin_unavailable"
    assert "interaction" not in result
    assert targeting.request()["targeting"]["locations"][0]["kind"] == "subdivision"


def test_incomplete_or_invalid_answer_cannot_consume_the_card(local):
    card = run("round", "start")
    ident = card["interaction"]["interaction_id"]
    assert answer(card, "--choice", "accept_suggested")["error"] == "invalid_interaction_response"
    assert answer(card, "--choice", "mainland_china", "--target-role", "Engineer")["error"] == "invalid_interaction_response"
    with pytest.raises(ValueError, match="conflict"):
        answer_file(local, card, {"choice": "mainland_china", "target_locations": TOKYO["locations"]})
    assert load_pending_interaction()["interaction_id"] == ident
    assert not state.current_round_path().exists() and not local["calls"]


def test_answer_file_and_flags_cannot_be_mixed(local):
    card = run("round", "start")
    file = local["path"] / "answer.json"
    file.write_text(json.dumps({"choice": "mainland_china"}))
    args = cli.build_parser().parse_args(["interaction", "respond", "--interaction-id", card["interaction"]["interaction_id"],
                                         "--answer-file", str(file), "--target-location", "Tokyo"])
    with pytest.raises(ValueError, match="either"):
        apply_answer_file(args)


def test_account_change_or_conflicting_replay_cannot_answer_another_card(local):
    card = run("round", "start")
    local["account"]["ref"] = "other-account"
    assert answer(card, "--choice", "mainland_china")["error"] == "interaction_context_changed"
    local["account"]["ref"] = "synthetic-target-account"
    assert answer(card, "--choice", "mainland_china")["ok"]
    assert answer(card, "--choice", "mainland_china")["idempotent_replay"]
    assert answer(card, "--choice", "outside_mainland_china")["error"] == "interaction_response_conflict"


def test_lost_target_plan_response_keeps_answers_and_reuses_request_id(local, monkeypatch):
    targeting.set_targets(MAINLAND)
    calls = []
    def request(body):
        calls.append(body)
        if len(calls) == 1:
            raise cloud_client.CloudError("Synthetic timeout", retryable=True)
        return local["sign"](body)
    monkeypatch.setattr(cloud_client, "target_region_plan", request)
    assert targeting.ensure()["request_preserved"]
    assert targeting.ensure() is None
    assert calls[0] == calls[1]
    assert not state.current_round_path().exists()


def test_tampered_or_wrong_account_plan_never_creates_round(local):
    targeting.set_targets(MAINLAND)
    targeting.ensure()
    plan = targeting.ready_plan()
    tampered = copy.deepcopy(plan)
    tampered["routing"]["platform_order"] = ["linkedin"]
    with pytest.raises(protocol.ProtocolError):
        rounds.start_new_round({"targeting": plan["targets"]}, target_plan=tampered)
    local["account"]["ref"] = "other-account"
    with pytest.raises(ValueError, match="account"):
        rounds.start_new_round({"targeting": plan["targets"]}, target_plan=plan)
    assert not state.current_round_path().exists()


def test_target_city_change_invalidates_the_prior_plan(local):
    targeting.set_targets(MAINLAND)
    targeting.ensure()
    args = cli.build_parser().parse_args(["round", "start", "--target-city", "杭州"])
    targeting.from_args(args)
    assert "target_plan" not in targeting.request()
    assert targeting.request()["targeting"]["locations"][0]["label"] == "杭州"
    targeting.ensure()
    assert len(local["calls"]) == 2


def test_legacy_active_round_preserves_intent_executors_and_unknown_send(local):
    original = rounds.start_new_round({"status": "confirmed", "target_roles": ["Engineer"], "target_cities": ["深圳"]})
    original["platforms"]["boss"] = {"status": "send_unknown", "delivery_nonce": "synthetic-unresolved"}
    rounds.save_round(original)
    assert targeting.ensure() is None
    assert state.load_json(state.current_round_path()) == original
    with pytest.raises(ValueError, match="existing round"):
        targeting.from_args(cli.build_parser().parse_args(["round", "start", "--target-region", "outside_mainland_china"]))
    assert state.load_json(state.current_round_path()) == original and not local["calls"]


def test_pre_upgrade_pending_role_card_is_not_replaced(local):
    state.save_json(state.pending_interaction_path(), {"stage": "choice", "interaction_id": "legacy-role", "interaction": {"kind": "target_role_choice"}})
    before = state.load_json(state.pending_interaction_path())
    assert targeting.ensure() is None
    assert state.load_json(state.pending_interaction_path()) == before


def test_installation_without_search_intent_does_not_ask_or_create_round(local):
    value = workflow._load()
    value["doctor"] = {"checked": True, "ready": True, "setup_healthy": True}
    workflow._save(value)
    assert workflow.next_action()["action"] == {"type": "done", "scope": "setup"}
    assert load_pending_interaction() is None and not local["calls"]


def test_completed_round_is_reported_without_starting_another_target_setup(local):
    active = rounds.start_new_round({"status": "confirmed", "target_roles": ["Engineer"], "target_cities": ["深圳"]})
    active["status"] = "completed"
    for item in active["platforms"].values():
        item["status"] = "completed"
    rounds.save_round(active)
    value = workflow._load()
    value.update(intent={"target_roles": ["Engineer"]}, doctor={"checked": True, "ready": True})
    workflow._save(value)
    result = workflow.next_action()
    assert result["action"]["type"] == "done" and result["action"]["scope"] == "round"
    assert load_pending_interaction() is None and not local["calls"]


def test_missing_resume_does_not_hide_region_card_after_account_verified(local):
    value = workflow._load()
    value.update(intent={"targeting": normalize_targets({})}, doctor={"checked": True, "ready": False, "setup_healthy": True})
    workflow._save(value)
    result = workflow.next_action()
    assert result["interaction"]["kind"] == "target_region"
    assert result["action"]["type"] == "ask"


def test_resume_analysis_preserves_its_exact_continuation_while_waiting(local):
    value = workflow._load()
    value["intent"] = {"target_roles": ["Product Manager"]}
    value["doctor"] = {"checked": True, "setup_healthy": True, "ready": False}
    workflow._save(value)
    source = str(local["path"] / "resume with spaces.pdf")
    result = run("resume", "analyze", "--file", source, "--target-role", "Product Manager", "--target-cities", "深圳")
    assert result["interaction"]["kind"] == "target_region"
    saved = answer(result, "--choice", "mainland_china")
    import shlex
    assert shlex.split(saved["next_suggested"]) == ["jobagent", "resume", "analyze", "--file", source, "--target-role", "Product Manager"]
    assert saved["targeting"]["locations"][0]["label"] == "深圳"
    action = workflow.next_action()["action"]
    assert action["business_argv"] == shlex.split(saved["next_suggested"])
    assert workflow.next_action()["action"] == action


@pytest.mark.parametrize("bad", [{"version": True}, {"scope": []}, {"locations": [{"label": "Tokyo", "market": []}]},
                                  {"locations": [{"label": "Tokyo", "market": "mainland_china", "country_code": "JP"}]},
                                  {"residence": "Tokyo"}, {"workplace_types": ["remote", "remote"]}])
def test_invalid_identity_or_target_fields_fail_before_cloud(local, bad):
    with pytest.raises(ValueError):
        targeting.set_targets(bad)
    assert not local["calls"]
