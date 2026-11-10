"""Read-only LinkedIn collection boundaries; no live accounts in tests."""
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from jobagent.platforms.discovery import CollectedPage, CollectionError, collect_from_search_plan, job_to_candidate
from jobagent.platforms.linkedin.collect import LinkedInCollectionReader, choose_location, detail_id, location_options, parse_detail, radius_steps, same_native_location
from jobagent.platforms.registry import list_platforms


def observation(identifier="123", **changes):
    return {"requested_id": identifier, "url": f"https://www.linkedin.com/jobs/view/{identifier}/",
            "observed_at": "2026-11-09T06:00:00+00:00", "header": "Example Ltd\nEngineer\nTokyo · 1 day ago\nRemote\nEasy Apply",
            "jd": "Build APIs. SQL required; BI preferred.", "expanded": True, **changes}


def query(**location_changes):
    return {"keyword": "Engineer", "location": {"label": "United States", "kind": "country", "country_code": "US",
            "market": "outside_mainland_china", **location_changes}, "easy_apply_only": True, "page_limit": 2}


def test_full_description_and_observed_facts_survive_candidate_bridge():
    source = observation()
    candidate = job_to_candidate("linkedin", parse_detail(source))
    assert candidate["id"] == "123" and candidate["jd"] == source["jd"]
    assert candidate["title"] == "Engineer" and candidate["company"] == "Example Ltd"
    assert candidate["linkedin"]["jd_sha256"] == sha256(source["jd"].encode()).hexdigest()
    assert candidate["linkedin"]["workplace_type"] == "remote"
    assert candidate["linkedin"]["salary_disclosure"] == "not_disclosed"
    assert "country_code" not in candidate["linkedin"]  # Search scope is not job geography.


@pytest.mark.parametrize("url", ["http://www.linkedin.com/jobs/view/123/", "https://www.linkedin.com.evil/jobs/view/123/",
    "https://evil@www.linkedin.com/jobs/view/123/", "https://www.linkedin.com/jobs/search/?currentJobId=123",
    "https://www.linkedin.com/jobs/view/456/", "https://www.linkedin.com/jobs/view/123/#456"])
def test_redirect_or_unbound_identity_cannot_become_a_candidate(url):
    with pytest.raises(CollectionError) as error:
        parse_detail(observation(url=url))
    assert error.value.code == "linkedin_detail_identity_mismatch"
    job = parse_detail(observation())
    job.url = url
    with pytest.raises(CollectionError):
        job_to_candidate("linkedin", job)


@pytest.mark.parametrize("changes", [{"expanded": False}, {"jd": " "}, {"jd": "x" * 20001}])
def test_partial_or_oversize_description_is_not_marked_complete(changes):
    with pytest.raises(CollectionError) as error:
        parse_detail(observation(**changes))
    assert error.value.code == "linkedin_full_description_required"


@pytest.mark.parametrize("status,expected", [("No longer accepting applications", "closed"), ("Applied", "already_applied"),
    ("已停止接受申请", "closed"), ("申请已提交", "already_applied")])
def test_closed_and_applied_override_stale_easy_apply_text(status, expected):
    job = parse_detail(observation(header=observation()["header"] + "\n" + status))
    assert job.raw_data["linkedin"]["availability"] == expected


@pytest.mark.parametrize("mode,easy,applied", [("ハイブリッド", "Easy応募", "応募が送信されました"),
                                             ("재택대면혼합근무", "원클릭 지원", "지원서를 전송함")])
def test_observed_member_language_states_keep_workplace_and_prior_application(mode, easy, applied):
    header = f"Example Ltd\nEngineer\nLocal city\n{mode}\n{easy}"
    job = parse_detail(observation(header=header))
    assert job.raw_data["linkedin"]["application_type"] == "easy_apply"
    assert job.raw_data["linkedin"]["workplace_type"] == "hybrid"
    assert parse_detail(observation(header=header + "\n" + applied)).raw_data["linkedin"]["availability"] == "already_applied"


def test_background_easy_apply_and_mode_cannot_leak_into_current_job():
    job = parse_detail(observation(header="Example Ltd\nEngineer\nTokyo\nApply\nPeople you can reach out to\nRemote\nEasy Apply"))
    assert job.raw_data["linkedin"]["application_type"] == "external"
    assert job.raw_data["linkedin"]["workplace_type"] == "unknown"
    unknown = parse_detail(observation(header="Example Ltd\nEngineer\nTokyo"))
    assert unknown.raw_data["linkedin"]["availability"] == "unknown"


@pytest.mark.parametrize("text,expected", [("150K USD/yr - 200K USD/yr", "disclosed"),
    ("Salary range: $150,000 to $200,000 annually", "disclosed"), ("We raised $20 million", "ambiguous")])
def test_salary_text_is_preserved_without_currency_conversion(text, expected):
    job = parse_detail(observation(jd=text))
    assert job.salary == text
    assert job.raw_data["linkedin"]["salary_disclosure"] == expected


def option(label="美国", **params):
    return {"label": label, "url": "https://www.linkedin.com/jobs/search-results/?" + urlencode({
        "keywords": "Engineer", "origin": "JOB_SEARCH_PAGE_LOCATION_AUTOCOMPLETE", "geoId": "1234", **params})}


def test_only_fresh_native_choices_for_this_query_are_eligible():
    valid = option()
    rows = [option(origin="JOB_SEARCH_PAGE_LOCATION_HISTORY"), option(keywords="Different"),
            option(currentJobId="99"), {**valid, "url": valid["url"].replace("www.linkedin.com", "evil.example")}, valid, valid]
    choices = location_options(rows, "Engineer")
    assert len(choices) == 1
    assert choose_location(choices, query()["location"], "美国")["geo_id"] == "1234"


def test_native_label_order_can_change_without_changing_location():
    assert same_native_location("加利福尼亚, 美国", "美国 加利福尼亚")
    assert not same_native_location("Springfield, Illinois, United States", "Springfield, Massachusetts, United States")
    assert not same_native_location("California, United States", "United States")


@pytest.mark.parametrize("choices", [[], [option("California")], [option(), option(geoId="5678")]])
def test_unresolved_or_ambiguous_native_geography_never_chooses_first(choices):
    with pytest.raises(CollectionError) as error:
        choose_location(location_options(choices, "Engineer"), query()["location"], "美国")
    assert error.value.code == "linkedin_location_selection_required"
    assert error.value.details["requires_user_action"] is True


@pytest.mark.parametrize("changes,expected", [({"market": "mainland_china"}, "linkedin_search_scope_invalid")])
def test_unverified_scope_stops_before_browser_actions(changes, expected):
    reader = LinkedInCollectionReader(page=object(), manager=object())
    with pytest.raises(CollectionError) as error:
        reader.collect_page(query(**changes), 1, 2)
    assert error.value.code == expected


@pytest.mark.parametrize("unit", ["公里", "Kilometers", "km", "킬로미터"])
def test_exact_native_radius_uses_observed_unit_and_increment(unit):
    assert radius_steps(25, {"unit": unit, "min": "0", "max": "160", "step": "5"}) == (5, 25)


@pytest.mark.parametrize("radius,native", [
    (27, {"unit": "Kilometers", "min": "0", "max": "160", "step": "5"}),
    (200, {"unit": "公里", "min": "0", "max": "160", "step": "5"}),
    (25, {"unit": "Miles", "min": "0", "max": "100", "step": "5"}),
    (25, {"unit": "unknown", "min": "0", "max": "160", "step": "5"}),
    (25, {"unit": "km", "min": "0", "max": "160", "step": "0"}),
])
def test_native_radius_never_rounds_or_widens_requested_range(radius, native):
    with pytest.raises(CollectionError) as exc:
        radius_steps(radius, native)
    assert exc.value.code == "linkedin_radius_selection_required"
    assert exc.value.details["native_range"] == native


def test_browser_error_is_preserved_and_not_treated_as_empty_results(monkeypatch):
    reader = LinkedInCollectionReader(page=object(), manager=object())
    monkeypatch.setattr(reader, "_search", lambda query: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(CollectionError) as error:
        reader.collect_page(query(), 1, 2)
    assert error.value.code == "linkedin_browser_read_interrupted"
    assert error.value.details["request_preserved"] is True
    assert error.value.details["retryable"] is False


def test_page_checkpoint_resumes_without_repeating_completed_queries():
    plan = {"platform": "linkedin", "candidate_limit": 3, "queries": [query()]}
    calls, checkpoints = [], []
    def collect(q, page, limit, *, excluded_ids=None):
        calls.append(page)
        if page == 2:
            raise CollectionError("interrupted", "fixture")
        return CollectedPage([parse_detail(observation()), parse_detail(observation())], evidence={"geo_id": "1234"})
    with pytest.raises(CollectionError):
        collect_from_search_plan(plan, driver=SimpleNamespace(collect_page=collect), page_delay=0, checkpoint_callback=checkpoints.append)
    assert len(checkpoints[-1]["candidates"]) == 1
    assert checkpoints[-1]["query_observations"] == {"0:1": {"geo_id": "1234"}}
    def remaining(q, page, limit, *, excluded_ids=None):
        assert excluded_ids == {"123"}
        calls.append(page)
        return CollectedPage([parse_detail(observation("123")), parse_detail(observation("456"))], exhausted=True,
                             evidence={"geo_id": "1234", "page": 2})
    result = collect_from_search_plan(plan, driver=SimpleNamespace(collect_page=remaining), page_delay=0,
                                     resume_progress=deepcopy(checkpoints[-1]), checkpoint_callback=checkpoints.append)
    assert calls == [1, 2, 2]
    assert [j["id"] for j in result] == ["123", "456"]
    assert len(checkpoints[-1]["query_observations"]) == 2


def test_default_reader_disconnects_on_failure_and_invalid_plan_never_connects(monkeypatch):
    calls = []
    class Reader:
        def __init__(self):
            calls.append("open")
        def collect_page(self, *args, **kwargs):
            raise CollectionError("interrupted", "fixture")
        def disconnect(self):
            calls.append("disconnect")
    monkeypatch.setattr("jobagent.platforms.linkedin.collect.LinkedInCollectionReader", Reader)
    with pytest.raises(CollectionError):
        collect_from_search_plan({"platform": "linkedin", "queries": []})
    assert calls == []
    with pytest.raises(CollectionError):
        collect_from_search_plan({"platform": "linkedin", "queries": [query()]})
    assert calls == ["open", "disconnect"]


def test_already_seen_cards_do_not_consume_remaining_capacity(monkeypatch):
    page = SimpleNamespace(url="https://www.linkedin.com/jobs/search-results/", locator=lambda selector: SimpleNamespace(count=lambda: 0))
    reader = LinkedInCollectionReader(page=page, manager=object())
    reader.last_search_observation = {"geo_id": "1234"}
    monkeypatch.setattr(reader, "_search", lambda q: None)
    monkeypatch.setattr(reader, "_card_ids", lambda: ["123", "456", "789"])
    reads = []
    def read(identifier):
        reads.append(identifier)
        return parse_detail(observation(identifier))
    monkeypatch.setattr(reader, "read_job", read)
    result = reader.collect_page(query(), 1, 1, excluded_ids={"123"})
    assert reads == ["456"]
    assert result.jobs[0].raw_data["id"] == "456"


def test_linkedin_adapter_metadata_keeps_server_admission_explicit():
    platform = next(p for p in list_platforms() if p.key == "linkedin")
    assert platform.status == "available" and "linkedin_easy_apply_version=1" in platform.notes
    assert detail_id("https://www.linkedin.com/jobs/view/123/") == "123"
