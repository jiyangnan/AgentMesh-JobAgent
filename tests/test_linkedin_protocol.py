"""International transport contracts, without enabling a platform executor."""
import base64
import copy
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jobagent.domain.targeting import normalize_targets
from jobagent.infra import protocol


@pytest.fixture
def signed_plan(monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", base64.urlsafe_b64encode(public).decode())
    profile = {"preferences": {"targetCities": [{"city": "深圳"}]}}
    locations = [{"label": label, "market": "outside_mainland_china", "kind": kind, "country_code": code}
                 for label, kind, code in [("United States", "country", "US"), ("California", "subdivision", "US"),
                     ("San Francisco Bay Area", "metro", "US"), ("서울", "city", "KR"),
                     ("東京", "city", "JP"), ("Berlin", "city", "DE")]]
    targets = normalize_targets({"locations": locations, "workplace_types": ["remote", "hybrid"]})
    intent = {"status": "confirmed", "target_roles": ["Engineer", "Product Manager", "Designer", "Presales"],
              "targeting": targets, "source": "user_explicit", "profile_digest": protocol.digest_payload(profile),
              "confirmed_at": "2026-11-09T00:00:00Z"}
    queries = [{"keyword": role, "location": loc, "target_key": protocol.digest_payload(loc),
                "easy_apply_only": True, "workplace_types": targets["workplace_types"], "page_limit": 2}
               for role in intent["target_roles"] for loc in targets["locations"]]
    plan = {"manifest_type": "search_plan", "protocol_version": protocol.PROTOCOL_VERSION,
            "discover_id": "dis-synthetic", "platform": "linkedin", "account_ref": "synthetic-owner",
            "profile_digest": protocol.digest_payload(profile), "round_intent": intent,
            "intent_digest": protocol.digest_payload(intent), "queries": queries,
            "discovery_contract_version": 2, "candidate_limit": 30, "recommendation_limit": None,
            "collection_executor": "cdp_readonly", "delivery_executor": "playwright",
            "page_budget": 48, "collection_time_budget_seconds": 600,
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}

    def sign(payload):
        value = {**payload, "key_id": "synthetic", "signature_algorithm": "Ed25519"}
        return {**value, "signature": base64.urlsafe_b64encode(key.sign(protocol.canonical_json_bytes(value))).decode().rstrip("=")}

    def verify(payload):
        return protocol.verify_search_plan(sign(payload), platform="linkedin", profile=profile, round_intent=intent)
    return plan, verify, sign, profile, intent


def test_all_twenty_four_confirmed_queries_are_verified_without_city_cascade(signed_plan):
    plan, verify, _, _, _ = signed_plan
    checked = verify(plan)
    assert len(checked["queries"]) == 24
    assert all("city" not in query for query in checked["queries"])


@pytest.mark.parametrize("mutation", ["omit", "duplicate", "location", "role", "key", "external", "mode", "page", "extra", "legacy"])
def test_valid_signature_does_not_allow_scope_widening_or_missing_targets(signed_plan, mutation):
    original, verify, _, _, _ = signed_plan
    plan = copy.deepcopy(original)
    first = plan["queries"][0]
    if mutation == "omit":
        plan["queries"].pop()
    elif mutation == "duplicate":
        plan["queries"][1] = copy.deepcopy(first)
    elif mutation == "location":
        first["location"]["label"] = "Shenzhen"
    elif mutation == "role":
        first["keyword"] = "Unconfirmed role"
    elif mutation == "key":
        first["target_key"] = "sha256:" + "0" * 64
    elif mutation == "external":
        first["easy_apply_only"] = False
    elif mutation == "mode":
        first["workplace_types"] = []
    elif mutation == "page":
        first["page_limit"] = True
    elif mutation == "extra":
        first["apply"] = True
    else:
        plan["discovery_contract_version"] = 1
        plan["candidate_limit"] = 100
    with pytest.raises(protocol.ProtocolError):
        verify(plan)


def test_signature_and_original_intent_still_bind_linkedin(signed_plan):
    plan, _, sign, profile, intent = signed_plan
    signed = sign(plan)
    signed["queries"] = []
    with pytest.raises(protocol.ProtocolError):
        protocol.verify_search_plan(signed, platform="linkedin", profile=profile, round_intent=intent)
    changed = copy.deepcopy(intent)
    changed["targeting"]["workplace_types"] = ["onsite"]
    with pytest.raises(protocol.ProtocolError):
        protocol.verify_search_plan(sign(plan), platform="linkedin", profile=profile, round_intent=changed)
    with pytest.raises(protocol.ProtocolError):
        protocol.verify_search_plan(sign(plan), platform="linkedin", profile=profile)


def test_observed_job_facts_participate_in_signature_and_legacy_digest_is_preserved():
    old = {"id": "1", "title": "Engineer", "jd": "Original", "area": None, "ignored": "host annotation"}
    assert protocol.canonical_candidates([old]) == [{"id": "1", "title": "Engineer", "jd": "Original"}]
    observed = {**old, "linkedin": {"version": 1, "country_code": "JP", "subdivision": None,
                                    "application_type": "easy_apply", "salary_disclosure": "not_disclosed"}}
    digest = protocol.candidate_digest([observed])
    assert "subdivision" not in protocol.canonical_candidates([observed])[0]["linkedin"]
    for field, value in [("country_code", "KR"), ("application_type", "external"), ("salary_disclosure", "collection_missing")]:
        changed = copy.deepcopy(observed)
        changed["linkedin"][field] = value
        assert protocol.candidate_digest([changed]) != digest
