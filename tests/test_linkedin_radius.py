"""Native-radius choices retain identity and survive lost responses."""
from copy import deepcopy

import pytest

from jobagent import cli
from jobagent.application import bulk_decision, linkedin_radius, linkedin_search
from jobagent.infra import cloud_client, state
from jobagent.infra import discovery_state as storage
from jobagent.infra.interaction_state import load_pending_interaction
from jobagent.infra.protocol import digest_payload
from jobagent.platforms.discovery import (
    CollectedPage,
    CollectionError,
    collect_from_search_plan,
)
from tests.test_linkedin_search_resume import search as search  # noqa: PLC0414
from tests.test_linkedin_search_resume import (
    signed_plan as signed_plan,  # noqa: PLC0414
)


def radius_search(search):
    plan = deepcopy(search.plan)
    target = plan["queries"][0]["location"]
    target["radius_km"] = 27
    for query in plan["queries"]:
        query["location"] = target
        query["target_key"] = digest_payload(target)
    plan["round_intent"]["targeting"]["locations"] = [target]
    plan["intent_digest"] = digest_payload(plan["round_intent"])
    search.active["intent"] = plan["round_intent"]
    state.save_json(state.current_round_path(), search.active)
    storage.save_pending_start("linkedin", request_id=plan["request_id"], round_id=plan["round_id"],
        profile_digest=plan["profile_digest"], intent_digest=plan["intent_digest"], account_ref=search.owner["ref"], delivery_executor="playwright")
    plan = search.sign(plan)
    storage.save_collection_checkpoint("linkedin", request_id=plan["request_id"], plan=plan,
        progress={"candidates": [{"id": "old-scope"}], "completed_pages": [[0, 1]], "location_resolutions": {}})
    details = {"query_index": 0, "requested_radius_km": 27,
               "native_range": {"min": "0", "max": "100", "step": "5", "unit": "km", "value": "80"}}
    return plan, details


def answer(card, choice):
    return cli._interaction_respond(cli.build_parser().parse_args([
        "interaction", "respond", "--interaction-id", card["interaction"]["interaction_id"],
        "--choice" if choice == "pause_platform" else "--radius-id", choice]))


def test_radius_choice_replays_after_response_loss_and_keeps_original_intent(search, monkeypatch):
    plan, details = radius_search(search)
    card = linkedin_radius.request(plan, details)
    assert linkedin_search.pending(plan)["interaction"] == card["interaction"]
    assert answer(card, "pause_platform")["event"] == "linkedin_search_paused"
    assert answer(card, "distance-fake")["error"] == "invalid_interaction_response"
    prompt = storage.load_collection_checkpoint("linkedin")["progress"]["radius_prompt"]
    selected = next(key for key, value in prompt["choices"].items() if value == 25)
    saved, requests = {}, []
    def transport(method, discover_id, suffix, body):
        requests.append(body)
        if not saved:
            saved.update(plan=search.sign({**plan, "plan_revision": 2, "radius_resolutions": {
                plan["queries"][0]["target_key"]: {"radius_km": 25, "native_value": 25,
                                                    "native_range": body["native_range"]}}}))
            raise cloud_client.CloudError("lost response", code="timeout", retryable=True)
        return saved
    monkeypatch.setattr(bulk_decision, "request", transport)
    with pytest.raises(cloud_client.CloudError):
        answer(card, selected)
    assert load_pending_interaction()["interaction_id"] == card["interaction"]["interaction_id"]
    assert answer(card, selected)["event"] == "linkedin_radius_confirmed"
    assert requests[0] == requests[1]
    checkpoint = storage.load_collection_checkpoint("linkedin")
    assert checkpoint["plan"]["round_intent"] == plan["round_intent"]
    assert not checkpoint["progress"].get("candidates")
    assert list((state.STATE_DIR / "archive" / "linkedin-radius").glob("*.json"))
    assert answer(card, selected)["idempotent_replay"] is True
    assert answer(card, "distance-fake")["error"] == "interaction_response_conflict"
    assert load_pending_interaction() is None
    # The resumed collector uses the chosen radius for every role, preserves
    # the original target and retains the durable answer in later checkpoints.
    observed, checkpoints = [], []
    class Reader:
        def collect_page(self, query, page, limit, **kwargs):
            observed.append((query["location"]["radius_km"], kwargs["radius_resolution"]["radius_km"]))
            return CollectedPage([], exhausted=True)
    with pytest.raises(CollectionError, match="without matching jobs"):
        collect_from_search_plan(checkpoint["plan"], driver=Reader(), page_delay=0,
            resume_progress=checkpoint["progress"], checkpoint_callback=checkpoints.append)
    assert observed == [(27, 25), (27, 25)]
    assert checkpoints[-1]["radius_answers"] == checkpoint["progress"]["radius_answers"]


def test_radius_scope_change_and_bad_evidence_never_call_server(search, monkeypatch):
    plan, details = radius_search(search)
    for native in [{"min": 0, "max": 100, "step": 0, "unit": "km"},
                   {"min": 0, "max": 1000, "step": 1, "unit": "km"},
                   {"min": 0, "max": 100, "step": 5, "unit": "unknown"}]:
        with pytest.raises(CollectionError, match="bounded"):
            linkedin_radius.request(plan, {**details, "native_range": native})
    card = linkedin_radius.request(plan, details)
    search.owner["ref"] = "other-account"
    with pytest.raises(CollectionError, match="original account"):
        answer(card, "distance-0")


@pytest.mark.parametrize("capability", [None, 0, True, 2])
def test_formal_entry_rejects_missing_or_unsupported_service_capability(monkeypatch, capability):
    monkeypatch.setattr(cloud_client, "me", lambda: {"server": {"capabilities": {"linkedin_easy_apply_version": capability}}})
    with pytest.raises(cloud_client.CloudError) as error:
        cloud_client.require_linkedin_capability()
    assert error.value.code == "linkedin_unavailable" and error.value.details["no_charge"] is True


def test_formal_start_negotiates_capability_and_exact_executor(search, monkeypatch):
    calls = []
    monkeypatch.setattr(cloud_client, "me", lambda: {"server": {"capabilities": {"linkedin_easy_apply_version": 1}}})
    monkeypatch.setattr(cloud_client, "_workflow_price_stage", lambda *args: None)
    def post(method, path, body, **kwargs):
        calls.append(body)
        return {"linkedin_contract_version": 1, "discovery_contract_version": 2, "delivery_executor": "playwright"}
    monkeypatch.setattr(cloud_client, "_request", post)
    cloud_client.discovery_start(platform="linkedin", profile={}, request_id="test-start",
                                 discovery_contract_version=2, delivery_executor="playwright")
    assert calls[0]["linkedin_contract_version"] == 1
