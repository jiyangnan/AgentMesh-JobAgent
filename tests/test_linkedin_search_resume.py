"""Native geography answers resume the original signed Discover, never a new one."""
import json
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from types import SimpleNamespace

import pytest

from jobagent import cli
from jobagent.application import (
    discover,
    hybrid_discovery,
    linkedin_search,
    resume_freshness,
)
from jobagent.infra import account_state, cloud_client, rounds, state
from jobagent.infra import discovery_state as storage
from jobagent.infra.interaction_state import (
    clear_pending_interaction,
    load_pending_interaction,
)
from jobagent.infra.protocol import digest_payload
from jobagent.platforms.discovery import CollectedPage, CollectionError
from jobagent.platforms.linkedin.collect import choose_location, parse_detail
from tests.test_linkedin_collection import observation
from tests.test_linkedin_protocol import (
    signed_plan as signed_plan,  # noqa: PLC0414 - Register the shared pytest fixture.
)


@pytest.fixture
def search(tmp_path, monkeypatch, signed_plan):
    for name, path in {"APP_DIR": tmp_path, "STATE_DIR": tmp_path / "state",
                       "ROUNDS_DIR": tmp_path / "rounds", "LOG_DIR": tmp_path / "logs"}.items():
        monkeypatch.setattr(state, name, path)
    plan, _, raw_sign, profile, intent = deepcopy(signed_plan)
    def sign(value):
        return raw_sign({k: v for k, v in value.items() if k != "signature"})
    target = {"label": "San Francisco", "kind": "city", "country_code": "US", "market": "outside_mainland_china"}
    intent["targeting"]["locations"] = [target]
    intent["target_roles"] = ["Engineer", "Designer"]
    intent["targeting"]["scope"] = "outside_mainland_china"
    plan.update(discover_id="dis_synthetic", request_id="linkedin:original", round_id="round-synthetic",
                round_intent=intent, intent_digest=digest_payload(intent), candidate_limit=2,
                queries=[{**plan["queries"][0], "keyword": role, "location": target, "target_key": digest_payload(target)}
                         for role in intent["target_roles"]])
    owner = {"ref": plan["account_ref"]}
    for module in (linkedin_search, account_state):
        monkeypatch.setattr(module, "current_account_ref", lambda: owner["ref"])
    active = {"round_id": plan["round_id"], "status": "active", "intent": intent,
              "collection_executor": "cdp_readonly", "delivery_executor": "playwright",
              "platforms": {"linkedin": {"status": "login_verified"}}, "platform_order": ["linkedin"],
              "interaction_language": "en", "discovery_options": {"candidate_limit": 2, "recommendation_limit": None}}
    state.save_json(state.current_round_path(), active)
    state.save_json(state.profile_path(), profile)
    storage.save_pending_start("linkedin", request_id=plan["request_id"], round_id=plan["round_id"],
        profile_digest=plan["profile_digest"], intent_digest=plan["intent_digest"], account_ref=owner["ref"],
        delivery_executor="playwright")
    plan = sign(plan)
    progress = {"candidates": [], "completed_pages": [], "exhausted_queries": [], "elapsed_seconds": 0}
    storage.save_collection_checkpoint("linkedin", request_id=plan["request_id"], plan=plan, progress=progress)
    options = [{"geo_id": "102277331", "label": "San Francisco, California, United States"},
               {"geo_id": "90000084", "label": "San Francisco Bay Area"}]
    details = {"query_index": 0, "target": target, "location_options": options}
    return SimpleNamespace(plan=plan, progress=progress, owner=owner, active=active, details=details, sign=sign)


def answer(card, choice="102277331"):
    args = cli.build_parser().parse_args(["interaction", "respond", "--interaction-id",
                                        card["interaction"]["interaction_id"],
                                        "--choice" if choice == "pause_platform" else "--location-id", choice])
    return cli._interaction_respond(args)


def test_choice_survives_missing_host_card_and_conflicting_replay(search):
    card = linkedin_search.request(search.plan, search.details)
    assert card["interaction"]["fields"][0]["default_option_ids"] == []
    clear_pending_interaction()
    assert linkedin_search.pending(search.plan)["interaction"] == card["interaction"]
    assert answer(card, "pause_platform")["event"] == "linkedin_search_paused"
    assert load_pending_interaction()
    assert answer(card)["event"] == "linkedin_location_confirmed"
    assert not load_pending_interaction()
    assert answer(card)["idempotent_replay"]
    assert answer(card, "90000084")["error"] == "interaction_response_conflict"
    checkpoint = storage.load_collection_checkpoint("linkedin")
    assert checkpoint["plan"] == search.plan
    assert checkpoint["progress"]["candidates"] == []
    assert checkpoint["progress"]["location_resolutions"][digest_payload(search.details["target"])]["geo_id"] == "102277331"
    assert linkedin_search.pending(search.plan) is None


@pytest.mark.parametrize("change", ["account", "round", "profile", "intent", "request", "target", "binding"])
def test_stale_answers_cannot_rebind_a_search(search, change):
    card = linkedin_search.request(search.plan, search.details)
    if change == "account":
        search.owner["ref"] = "other"
    elif change in {"round", "intent", "binding"}:
        if change == "round":
            search.active["round_id"] = "other"
        elif change == "intent":
            search.active["intent"]["target_roles"] = ["Unconfirmed"]
        else:
            search.active["resume_binding"] = {"id": "other"}
        state.save_json(state.current_round_path(), search.active)
    elif change == "profile":
        state.save_json(state.profile_path(), {"changed": True})
    else:
        plan = deepcopy(search.plan)
        if change == "target":
            plan["queries"][0]["location"]["label"] = "San Diego"
        else:
            plan["discover_id"] = "other"
        storage.save_collection_checkpoint("linkedin", request_id=plan["request_id"], plan=plan,
                                          progress=storage.load_collection_checkpoint("linkedin")["progress"])
    before = storage.pending_start_path("linkedin").read_bytes()
    try:
        result = answer(card)
        assert result["ok"] is False
    except CollectionError as exc:
        assert exc.code == "linkedin_location_context_changed"
    assert storage.pending_start_path("linkedin").read_bytes() == before


def test_bound_resume_uses_confirmed_material_not_unrelated_global_profile(search):
    search.active["resume_binding"] = {"id": "binding-original", "resume_id": "resume-one"}
    state.save_json(state.current_round_path(), search.active)
    state.save_json(state.profile_path(), {"unrelated_global_resume": True})
    card = linkedin_search.request(search.plan, search.details)
    assert answer(card)["ok"]


def test_ttl_renewal_keeps_the_same_prompt_and_answer(search):
    card = linkedin_search.request(search.plan, search.details)
    renewed = search.sign({**search.plan, "expires_at": "2026-11-10T00:00:00Z", "reissued": 1,
                           "plan_revision": 2, "renewal": {"reason": "search_plan_expired"}})
    assert linkedin_search.pending(renewed)["interaction"] == card["interaction"]
    assert answer(card)["ok"]


def test_first_page_ambiguity_is_checkpointed_and_restored_before_opening_browser(search, monkeypatch):
    # Exercise run_discover + the real collector/checkpoint + the shared CLI answer.
    storage.pending_start_path("linkedin").unlink()
    monkeypatch.setattr(resume_freshness, "gate_search", lambda _: None)
    monkeypatch.setattr(discover, "require_compatible_profile", lambda _: None)
    monkeypatch.setattr(rounds, "ensure_current_round", lambda: state.load_json(state.current_round_path()))
    monkeypatch.setattr(rounds, "recent_platform_login_verification", lambda _: None)
    monkeypatch.setattr(discover, "PlatformSessionLock", lambda **_: nullcontext())
    monkeypatch.setattr(discover, "active_command", lambda *_: nullcontext())
    monkeypatch.setattr(discover, "_resume_pending_decision", lambda *a, **k: None)
    calls = []
    starts = []
    def start(**kwargs):
        starts.append(kwargs)
        return search.sign({**search.plan, "request_id": kwargs["request_id"]})
    monkeypatch.setattr(cloud_client, "discovery_start", start)
    def collect(q, page, limit, *, excluded_ids=None, location_resolution=None):
        calls.append((q["keyword"], page, deepcopy(location_resolution)))
        if location_resolution is None:
            raise CollectionError("linkedin_location_selection_required", "Native ambiguity", details={
                "target": q["location"], "location_options": search.details["location_options"]})
        assert location_resolution["geo_id"] == "102277331"
        identifier = "123" if q["keyword"] == "Engineer" else "456"
        return CollectedPage([parse_detail(observation(identifier))], exhausted=True)
    opens = []
    @contextmanager
    def reader(_):
        opens.append(True)
        yield SimpleNamespace(collect_page=collect)
    monkeypatch.setattr(hybrid_discovery, "collection_reader", reader)
    monkeypatch.setattr(discover, "_decision_result", lambda p, **kwargs: {"ok": True, "candidates": kwargs["candidates"]})
    card = discover.run_discover("linkedin", page_delay=0)
    assert card["error"] == "interaction_required" and card["no_charge"]
    assert storage.load_collection_checkpoint("linkedin")["progress"]["completed_pages"] == []
    assert discover.run_discover("linkedin", page_delay=0)["interaction"] == card["interaction"]
    assert len(opens) == 1 and len(starts) == 1 and len(calls) == 1
    assert answer(card)["ok"]
    result = discover.run_discover("linkedin", page_delay=0)
    assert result["ok"] and [j["id"] for j in result["candidates"]] == ["123", "456"]
    assert len(opens) == 2 and len(starts) == 1
    assert storage.load_pending_start("linkedin") is None
    assert storage.load_pending_decision("linkedin")["plan"]["request_id"] == starts[0]["request_id"]


def test_resolution_reobserves_fresh_options_and_cannot_change_signed_target(search):
    selected = {**search.details["location_options"][0], "target_digest": digest_payload(search.details["target"])}
    fresh = [{**selected, "url": "https://www.linkedin.com/jobs/search-results/?keywords=Designer"}]
    assert choose_location(fresh, search.details["target"], resolution=selected) == fresh[0]
    with pytest.raises(CollectionError) as exc:
        choose_location([search.details["location_options"][1]], search.details["target"], resolution=selected)
    assert exc.value.code == "linkedin_location_selection_required"
    with pytest.raises(CollectionError) as exc:
        choose_location(fresh, {**search.details["target"], "label": "San Diego"}, resolution=selected)
    assert exc.value.code == "linkedin_location_context_changed"


def test_portable_answer_file_uses_same_scoped_handler(search, tmp_path):
    from jobagent.application.interaction_answer import apply_answer_file
    card = linkedin_search.request(search.plan, search.details)
    file = tmp_path / "answer.json"
    file.write_text(json.dumps({"choice": "102277331"}))
    args = cli.build_parser().parse_args(["interaction", "respond", "--interaction-id",
        card["interaction"]["interaction_id"], "--answer-file", str(file)])
    apply_answer_file(args)
    assert cli._interaction_respond(args)["ok"]


def test_location_flags_cannot_answer_delivery_or_override_other_choices(search):
    args = cli.build_parser().parse_args(["interaction", "respond", "--interaction-id",
        "delivery:linkedin:some-other-card", "--location-id", "102277331", "--choice", "confirm_all"])
    assert cli._interaction_respond(args)["error"] == "invalid_interaction_response"
