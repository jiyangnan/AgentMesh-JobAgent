"""Public search templates validated against synthetic signed plans, never UI.

These tests exercise the real presenter and page validator. The imported fixture
uses a temporary checkpoint, an in-memory work ledger, and a synthetic signing
key; no result is submitted to a recruiting platform or real cloud account.
"""

from __future__ import annotations

import copy
import json
import socket
from datetime import datetime, timezone

import pytest

from jobagent.application import native_discovery as discovery, native_work
from jobagent.infra import discovery_state as storage
from jobagent.platforms.discovery import CollectionError
from tests.test_native_discovery import candidate, env  # noqa: F401


PLATFORMS = ("boss", "liepin", "zhilian", "51job")
BRANCHES = ("results", "last_page", "no_results")
QUERY_SOURCES = {"search_input", "result_heading", "url_query", "search_history"}
CITY_SOURCES = {"city_control", "page_title", "page_metadata", "result_heading", "job_locations"}
SEARCH_URLS = {
    "boss": "https://www.zhipin.com/web/geek/job",
    "liepin": "https://www.liepin.com/zhaopin/",
    "zhilian": "https://www.zhaopin.com/jobs/",
    "51job": "https://we.51job.com/pc/search",
}


@pytest.fixture
def search_env(env, monkeypatch):
    def forbidden_network(*args, **kwargs):
        pytest.fail("native search contract tests must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden_network)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden_network)
    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    monkeypatch.setattr(native_work, "current_account_ref", lambda: "account-test")
    env.active["native_session"].update(
        window_reference="synthetic-search-window",
        profile_label="Synthetic search profile",
        group_reference="Synthetic search group",
        accounts={platform: f"Synthetic {platform} account" for platform in PLATFORMS},
    )
    return env


def presented_work(search_env, platform):
    work = discovery.start_discovery(platform, "session-test")["work"]
    assert work["action"] == "collect_search_page" and work["side_effect"] is False
    return native_work.present(work, execution=True)["work"]


def observed_example(work, branch):
    """Replace observation placeholders, keeping the advertised branch intact."""
    platform = work["binding"]["platform"]
    result = copy.deepcopy(work["task"]["result_examples"][branch])
    result["receipt_id"] = f"synthetic-search-{platform}-{branch}"
    evidence = result["evidence"]
    evidence.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        observation=f"Synthetic visible {branch} search state.",
        page_url=SEARCH_URLS[platform],
    )
    for field in ("query_evidence", "city_evidence"):
        for item in evidence[field]:
            item["text"] = f"Synthetic {item['source']} observation: {item['value']}"
    if evidence["exhaustion"] is not None:
        evidence["exhaustion"]["text"] = (
            "合成页面明确显示暂无符合条件的职位" if branch == "no_results"
            else "合成分页区域明确显示当前为最后一页"
        )
    if branch != "no_results":
        result["candidates"] = [candidate(platform)]
    assert "<" not in json.dumps(result, ensure_ascii=False), "unfilled observation placeholder"
    return result


def assert_validation_is_read_only(search_env, platform, before):
    assert storage.pending_start_path(platform).read_bytes() == before
    assert search_env.decisions == [] and search_env.renewals == []
    assert len(search_env.starts) == 1  # The fixture's synthetic signed plan only.


@pytest.mark.parametrize("platform", PLATFORMS)
def test_search_schema_exposes_state_sources_and_candidate_types(search_env, platform):
    schema = presented_work(search_env, platform)["task"]["result_schema"]
    evidence = schema["evidence"]
    assert evidence["page_state"]["type"] == "string"
    assert set(evidence["page_state"]["enum"]) == {"results", "no_results"}
    assert evidence["has_next_page"]["type"] == "boolean"
    assert evidence["search_transition_observed"] == {"type": "boolean", "const": True}
    for field, sources in (("query_evidence", QUERY_SOURCES), ("city_evidence", CITY_SOURCES)):
        declaration = evidence[field]
        assert declaration["type"] == "array"
        assert declaration["minItems"] == declaration["distinct_source_minimum"] == 2
        assert set(declaration["items"]["required"]) == {"source", "value", "text"}
        properties = declaration["items"]["properties"]
        assert set(properties["source"]["enum"]) == sources
        assert properties["value"]["type"] == properties["text"]["type"] == "string"
        assert properties["text"]["minLength"] == 1
    assert evidence["query_evidence"]["required_source"] == "search_input"
    assert set(evidence["exhaustion"]["properties"]["kind"]["enum"]) == {
        "last_page", "explicit_no_results",
    }
    properties = schema["candidate_properties"]
    assert set(properties) == set(schema["candidate_fields"])
    assert set(schema["candidate_required"]) == {"id", "title", "company", "area", "url"}
    for field, declaration in properties.items():
        if field == "skills":
            assert declaration == {
                "type": "array", "maxItems": 100,
                "items": {"type": "string", "maxLength": 200},
            }
        else:
            assert declaration["type"] == "string"
            assert declaration["maxLength"] == (20000 if field == "jd" else 2000)
        if field in schema["candidate_required"]:
            assert declaration["minLength"] == 1
    assert properties["id"]["pattern"] == "^[A-Za-z0-9_-]{1,160}$"
    assert schema["state_combinations"] == {
        "results": {"page_state": "results", "candidate_min_items": 1,
                    "has_next_page": True, "exhaustion": None},
        "last_page": {"page_state": "results", "candidate_min_items": 1,
                      "has_next_page": False, "exhaustion_kind": "last_page",
                      "exhaustion_text_required": True},
        "no_results": {"page_state": "no_results", "candidate_max_items": 0,
                       "has_next_page": False, "exhaustion_kind": "explicit_no_results",
                       "exhaustion_text_required": True},
    }
    assert set(BRANCHES) <= set(schema["combination_rules"])


@pytest.mark.parametrize("platform", PLATFORMS)
def test_default_search_template_contains_observation_placeholders_not_an_empty_result(search_env, platform):
    task = presented_work(search_env, platform)["task"]
    example = task["result_example"]
    assert example == task["result_examples"]["results"]
    assert example["outcome"] == "page_collected"
    assert example["evidence"]["page_state"] == "results"
    assert example["evidence"]["has_next_page"] is True
    assert example["evidence"]["exhaustion"] is None
    assert len(example["candidates"]) >= 1
    for field in task["result_schema"]["candidate_required"]:
        assert "<" in example["candidates"][0][field]


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("branch", BRANCHES)
def test_each_filled_public_search_branch_passes_real_validation(search_env, platform, branch):
    work = presented_work(search_env, platform)
    assert set(work["task"]["result_examples"]) == set(BRANCHES)
    example = work["task"]["result_examples"][branch]
    evidence = example["evidence"]
    assert evidence["page_state"] == ("no_results" if branch == "no_results" else "results")
    assert evidence["has_next_page"] is (branch == "results")
    if branch == "results":
        assert evidence["exhaustion"] is None and example["candidates"]
    else:
        assert evidence["exhaustion"]["kind"] == (
            "explicit_no_results" if branch == "no_results" else "last_page"
        )
        assert bool(example["candidates"]) is (branch == "last_page")
    result = observed_example(work, branch)
    before = storage.pending_start_path(platform).read_bytes()
    normalized = discovery.validate_page(work, result)
    assert normalized == {
        "duplicate": False,
        "candidates": [] if branch == "no_results" else [candidate(platform)],
        "page": [0, 1],
        "exhausted": branch != "results",
    }
    assert_validation_is_read_only(search_env, platform, before)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_present_refreshes_all_branch_identity_fields_without_mutating_work(search_env, platform):
    work = discovery.start_discovery(platform, "session-test")["work"]
    for revision in ("first", "second"):
        work["nonce"] = f"synthetic-{revision}-nonce"
        session = search_env.active["native_session"]
        session.update(window_reference=f"synthetic-{revision}-window",
                       profile_label=f"Synthetic {revision} profile")
        session["accounts"][platform] = f"Synthetic {revision} account"
        for sample in [work["task"]["result_example"], *work["task"]["result_examples"].values()]:
            sample.update(nonce="stale-nonce", binding={"account_ref": "stale-account"})
            sample["evidence"].update(window_reference="stale-window", profile_label="stale-profile",
                                      account_label="stale-account")
        frozen = copy.deepcopy(work)
        response = native_work.present(work, execution=True)
        assert work == frozen
        shown = response["work"]
        assert response["next_suggested"] == (
            f"jobagent work submit --work-id {work['work_id']} --result <result.json>"
        )
        for sample in [shown["task"]["result_example"], *shown["task"]["result_examples"].values()]:
            assert sample["nonce"] == work["nonce"]
            assert sample["binding"] == work["binding"]
            assert sample["evidence"]["window_reference"] == session["window_reference"]
            assert sample["evidence"]["profile_label"] == session["profile_label"]
            assert sample["evidence"]["account_label"] == session["accounts"][platform]


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("branch,mutation,code", [
    ("results", lambda r: r.update(candidates=[]), "native_empty_page_unverified"),
    ("last_page", lambda r: r.update(candidates=[]), "native_empty_page_unverified"),
    ("results", lambda r: r["evidence"].update(
        exhaustion={"kind": "last_page", "text": "Synthetic last page"}), "native_exhaustion_conflict"),
    ("last_page", lambda r: r["evidence"].update(exhaustion=None), "native_exhaustion_unverified"),
    ("no_results", lambda r: r["evidence"].update(has_next_page=True), "native_exhaustion_conflict"),
    ("no_results", lambda r: r["evidence"].update(page_state="results"), "native_empty_page_unverified"),
    ("results", lambda r: r["evidence"].update(page_state="no_results"), "native_result_state_conflict"),
    ("results", lambda r: r["evidence"].update(page_state="loading"), "native_search_state_unverified"),
])
def test_conflicting_search_branch_observations_still_fail_closed(search_env, platform, branch, mutation, code):
    work = presented_work(search_env, platform)
    result = observed_example(work, branch)
    mutation(result)
    before = storage.pending_start_path(platform).read_bytes()
    with pytest.raises(CollectionError) as error:
        discovery.validate_page(work, result)
    assert error.value.code == code
    assert_validation_is_read_only(search_env, platform, before)


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("field", ("query_evidence", "city_evidence"))
@pytest.mark.parametrize("invalid_source,code", [
    ("browser_script", "native_search_evidence_invalid"),
    (None, "native_search_evidence_missing"),
])
def test_source_contract_does_not_allow_hidden_or_duplicate_sources(search_env, platform, field, invalid_source, code):
    work = presented_work(search_env, platform)
    result = observed_example(work, "results")
    sources = result["evidence"][field]
    sources[1]["source"] = invalid_source or sources[0]["source"]
    before = storage.pending_start_path(platform).read_bytes()
    with pytest.raises(CollectionError) as error:
        discovery.validate_page(work, result)
    assert error.value.code == code
    assert_validation_is_read_only(search_env, platform, before)
