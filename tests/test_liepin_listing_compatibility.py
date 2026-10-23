"""Regression coverage for modern Liepin cards and explicit preserved repair."""
from copy import deepcopy

import pytest

from jobagent.domain.models import Job
from jobagent.platforms import discovery
from jobagent.platforms.detail_reader import detail_url_allowed
from jobagent.platforms.liepin.parser import liepin_job_id, parse_liepin_job


@pytest.mark.parametrize("route", ["job", "a"])
def test_liepin_detail_identity_supports_both_official_listing_types(route):
    url = f"https://www.liepin.com/{route}/80900001.shtml?tracking=fixture#ignored"
    row = {"jobTitle": "数据产品经理", "companyName": "Fixture Company", "jobUrl": url}
    assert liepin_job_id(row) == "80900001"
    assert parse_liepin_job(row).url == f"https://www.liepin.com/{route}/80900001.shtml"
    assert detail_url_allowed("liepin", url)


@pytest.mark.parametrize("url", [
    "https://www.liepin.com.evil.test/a/80900001.shtml",
    "https://www.liepin.com@evil.test/a/80900001.shtml",
    "https://evil.test@www.liepin.com/a/80900001.shtml",
    "http://www.liepin.com/a/80900001.shtml",
    "https://www.liepin.com:8443/a/80900001.shtml",
    "https://www.liepin.com/a/80900001.shtml/extra",
    "https://www.liepin.com/a/",
])
def test_liepin_added_listing_type_keeps_official_url_boundary(url):
    assert not detail_url_allowed("liepin", url)


def job(index):
    return Job(name="数据产品经理", salary="20-30k", company="Fixture Company",
               city="深圳", platform="liepin",
               url=f"https://www.liepin.com/a/{80000000 + index}.shtml",
               raw_data={"jobId": str(80000000 + index)})


def preserved():
    plan = {"platform": "liepin", "discovery_contract_version": 2,
            "candidate_limit": 30, "page_budget": 6,
            "collection_time_budget_seconds": 1800,
            "queries": [{"keyword": "数据产品经理", "city": "深圳", "page_limit": 6}]}
    progress = {"candidates": [discovery.job_to_candidate("liepin", job(i)) for i in range(8)],
                "completed_pages": [[0, i] for i in range(1, 7)], "exhausted_queries": [],
                "page_fingerprints": {"0": ["old-page-1", "old-page-2"]},
                "elapsed_seconds": 75.0, "stop_reason": "collection_query_budget_reached"}
    return plan, progress


def test_ordinary_resume_never_repeats_completed_pages(monkeypatch):
    plan, original = preserved()
    before = deepcopy(original)
    monkeypatch.setattr(discovery, "_collect_web_platform", lambda *a, **k: pytest.fail("completed page repeated"))
    with pytest.raises(discovery.CollectionError, match="Search page limits"):
        discovery.collect_from_search_plan(plan, resume_progress=original, driver=object(), page_delay=0)
    assert original == before


def test_explicit_repair_preserves_original_prefix_pages_and_budget(monkeypatch):
    plan, original = preserved()
    before = deepcopy(original)
    calls, checkpoints = [], []

    def page(platform, query, number, limit, *args):
        calls.append((platform, number, limit))
        return discovery.CollectedPage([job(i) for i in range(40)])

    monkeypatch.setattr(discovery, "_collect_web_platform", page)
    result = discovery.collect_from_search_plan(plan, resume_progress=original,
        driver=object(), page_delay=0, repair_collection=True,
        checkpoint_callback=checkpoints.append)
    assert len(result) == len({row["id"] for row in result}) == 30
    assert result[:8] == before["candidates"]
    assert calls == [("liepin", 1, 22)]
    assert checkpoints[-1]["completed_pages"] == before["completed_pages"]
    assert checkpoints[-1]["page_fingerprints"] == before["page_fingerprints"]
    assert checkpoints[-1]["repair"]["completed_pages"] == [[0, 1]]
    assert checkpoints[-1]["repair"]["original_progress"] == before
    assert original == before


def test_interrupted_repair_resumes_missing_pages_without_reset(monkeypatch):
    plan, original = preserved()
    checkpoints, calls = [], []

    def interrupted(platform, query, page, limit, *args):
        calls.append(page)
        if page == 2:
            raise discovery.CollectionError("liepin_verification_required", "fixture interruption")
        return discovery.CollectedPage([job(i) for i in range(8, 11)])

    monkeypatch.setattr(discovery, "_collect_web_platform", interrupted)
    with pytest.raises(discovery.CollectionError):
        discovery.collect_from_search_plan(plan, resume_progress=original, driver=object(),
            repair_collection=True, page_delay=0, checkpoint_callback=checkpoints.append)
    assert calls == [1, 2]
    saved = deepcopy(checkpoints[-1])
    assert saved["repair"]["completed_pages"] == [[0, 1]]

    def resumed(platform, query, page, limit, *args):
        assert page == 2
        return discovery.CollectedPage([job(i) for i in range(11, 40)])

    monkeypatch.setattr(discovery, "_collect_web_platform", resumed)
    result = discovery.collect_from_search_plan(plan, resume_progress=saved,
        driver=object(), page_delay=0, checkpoint_callback=checkpoints.append)
    assert len(result) == 30 and result[:8] == original["candidates"]
    assert checkpoints[-1]["repair"]["completed_pages"] == [[0, 1], [0, 2]]
    assert checkpoints[-1]["repair"]["original_progress"] == original


def test_repair_cannot_change_original_candidate_prefix(monkeypatch):
    plan, original = preserved()
    checkpoints = []
    monkeypatch.setattr(discovery, "_collect_web_platform", lambda *a, **k: discovery.CollectedPage([job(i) for i in range(40)]))
    discovery.collect_from_search_plan(plan, resume_progress=original, driver=object(),
        repair_collection=True, page_delay=0, checkpoint_callback=checkpoints.append)
    saved = deepcopy(checkpoints[-1])
    saved["candidates"][0]["company"] = "Changed identity"
    with pytest.raises(discovery.CollectionError) as error:
        discovery.collect_from_search_plan(plan, resume_progress=saved, driver=object())
    assert error.value.code == "collection_repair_not_allowed"


def test_repair_has_one_extra_pass_and_repeating_flag_never_resets_it(monkeypatch):
    plan, original = preserved()
    calls, checkpoints = [], []

    def short_page(platform, query, page, limit, *args):
        calls.append(page)
        return discovery.CollectedPage([job(7 + page)])

    monkeypatch.setattr(discovery, "_collect_web_platform", short_page)
    with pytest.raises(discovery.CollectionError):
        discovery.collect_from_search_plan(plan, resume_progress=original, driver=object(),
            repair_collection=True, page_delay=0, checkpoint_callback=checkpoints.append)
    assert calls == list(range(1, 7))
    saved = deepcopy(checkpoints[-1])
    assert saved["repair"]["completed_pages"] == original["completed_pages"]
    with pytest.raises(discovery.CollectionError):
        discovery.collect_from_search_plan(plan, resume_progress=saved, driver=object(),
            repair_collection=True, page_delay=0)
    assert calls == list(range(1, 7))


def test_repair_shares_original_time_budget(monkeypatch):
    plan, progress = preserved()
    progress["elapsed_seconds"] = plan["collection_time_budget_seconds"]
    monkeypatch.setattr(discovery, "_collect_web_platform", lambda *a, **k: pytest.fail("time budget reset"))
    with pytest.raises(discovery.CollectionError) as error:
        discovery.collect_from_search_plan(plan, resume_progress=progress,
                                          driver=object(), repair_collection=True)
    assert error.value.code == "collection_budget_reached"


@pytest.mark.parametrize("change", ["boss", "legacy", "empty", "complete", "other_stop"])
def test_repair_is_not_a_general_restart_or_budget_override(change, monkeypatch):
    plan, progress = preserved()
    if change == "boss":
        plan["platform"] = "boss"
    elif change == "legacy":
        plan.pop("discovery_contract_version")
    elif change == "empty":
        progress["candidates"] = []
    elif change == "complete":
        plan["candidate_limit"] = 8
    else:
        progress["stop_reason"] = "platform_verification_required"
    monkeypatch.setattr(discovery, "_collect_web_platform", lambda *a, **k: pytest.fail("unsafe repair read"))
    with pytest.raises(discovery.CollectionError) as error:
        discovery.collect_from_search_plan(plan, resume_progress=progress,
                                          driver=object(), repair_collection=True)
    assert error.value.code == "collection_repair_not_allowed"
