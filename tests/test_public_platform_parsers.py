import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from jobagent.platforms.boss import boss_job_id, parse_boss_job
from jobagent.platforms.job51 import job51_job_id, parse_job51_job
from jobagent.platforms.liepin import (
    LiepinReadOnlyCollector,
    liepin_job_id,
    parse_liepin_job,
)
from jobagent.platforms.liepin.collect import build_liepin_search_url
from jobagent.platforms.zhilian import parse_zhilian_job, zhilian_job_id

FIXTURES = Path(__file__).parent / "fixtures"


def test_boss_public_parser_extracts_stable_identity():
    raw = json.loads((FIXTURES / "boss/search_joblist_page1.json").read_text())["zpData"][
        "jobList"
    ][0]
    job = parse_boss_job(raw, city_name="深圳")
    assert boss_job_id(raw) == "abc123"
    assert job.name == "AI产品经理"
    assert job.url.endswith("abc123.html")


def test_liepin_public_parser_extracts_stable_identity():
    payload = json.loads((FIXTURES / "liepin/search_joblist_page1.json").read_text())
    raw = payload["data"]["jobList"][0]
    job = parse_liepin_job(raw, city_name="深圳")
    assert liepin_job_id(raw)
    assert job.name and job.url


def test_liepin_public_adapter_uses_current_shenzhen_location_shape():
    url = build_liepin_search_url("AI产品经理", city="深圳")
    job = parse_liepin_job(
        {
            "jobId": "location-1",
            "jobTitle": "AI产品经理",
            "cityName": "深圳-南山区",
        }
    )

    assert "city=050090" in url and "dq=050090" in url
    assert "currentPage=0" in url
    assert job.city == "深圳"
    assert job.area == "南山区"


def test_liepin_search_pages_are_zero_based_for_all_cities():
    first = build_liepin_search_url("AI产品负责人", city="上海市", page=1)
    second = build_liepin_search_url("AI产品负责人", city="上海市", page=2)

    assert "city=020" in first
    assert "dq=020" in first
    assert "currentPage=0" in first
    assert "currentPage=1" in second


def test_liepin_search_uses_live_beijing_city_code():
    url = build_liepin_search_url("数据产品负责人", city="北京", page=1)

    assert "city=010" in url
    assert "dq=010" in url


def test_liepin_search_uses_live_guangzhou_city_code():
    url = build_liepin_search_url("AI产品经理", city="广州", page=1)

    assert "city=050020" in url
    assert "dq=050020" in url


class _LiepinCityResolutionDriver:
    def __init__(self, resolved_code: str):
        self.resolved_code = resolved_code
        self.opened: list[str] = []

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        self.opened.append(url)
        return {"ok": True, "url": url}

    def _exec_js(self, js_code: str):
        if "liepin_city_route_discovery" in js_code:
            return {
                "ok": bool(self.resolved_code),
                "city": "佛山",
                "route": (
                    "https://www.liepin.com/city-foshan/"
                    if self.resolved_code
                    else ""
                ),
                "candidateCount": 300,
            }
        if "liepin_city_search_evidence" in js_code:
            if not self.opened or not self.resolved_code:
                return {}
            query = parse_qs(urlparse(self.opened[-1]).query).get("key", [""])[0]
            return _liepin_city_evidence("佛山", self.resolved_code, query)
        query = parse_qs(urlparse(self.opened[-1]).query).get("key", [""])[0]
        return {
            "ok": True,
            "url": self.opened[-1],
            "candidateCount": 1,
            "cityEvidence": _liepin_city_evidence(
                "佛山", self.resolved_code, query
            ),
            "cards": [
                {
                    "jobId": "dynamic-city-1",
                    "jobTitle": "AI产品经理",
                    "companyName": "Dynamic City Example",
                    "cityName": "佛山",
                    "jobUrl": "https://www.liepin.com/job/dynamic-city-1.shtml",
                }
            ],
        }


def _liepin_city_evidence(city: str, code: str, query: str) -> dict:
    return {
        "controlCity": city,
        "controlCode": code,
        "metaCity": city,
        "titleCity": city,
        "visibleCity": city,
        "inputQuery": query,
        "urlQuery": query,
        "jobCardCount": 1,
        "noResults": False,
        "resultSurface": True,
    }


def test_liepin_collector_resolves_unbundled_city_from_page_metadata(tmp_path):
    driver = _LiepinCityResolutionDriver("0757")

    result = LiepinReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "liepin-cities.json",
    ).collect("AI产品经理", city="佛山市")

    assert result.ok is True
    assert "city=0757" in driver.opened[-1]
    assert result.jobs[0].city == "佛山"


def test_liepin_collector_rejects_unverified_city_without_searching_raw_text(tmp_path):
    driver = _LiepinCityResolutionDriver("")

    result = LiepinReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "liepin-cities.json",
    ).collect("AI产品经理", city="未收录城市")

    assert result.ok is False
    assert result.error == "liepin_city_code_not_found"
    assert all("%E6%9C%AA%E6%94%B6%E5%BD%95" not in url for url in driver.opened)


def test_liepin_parser_uses_canonical_url_identity_without_tracking_query():
    raw = {
        "jobTitle": "AI产品经理",
        "jobUrl": (
            "https://www.liepin.com/job/1984063863.shtml"
            "?skId=volatile&index=1"
        ),
    }

    job = parse_liepin_job(raw, city_name="深圳")

    assert liepin_job_id(raw) == "1984063863"
    assert job.url == "https://www.liepin.com/job/1984063863.shtml"


def test_zhilian_public_parser_extracts_stable_identity():
    payload = json.loads((FIXTURES / "zhilian/search_results_page1.json").read_text())
    raw = payload["data"]["results"][0]
    job = parse_zhilian_job(raw, city_name="深圳")
    assert zhilian_job_id(raw)
    assert job.name and job.url


def test_zhilian_public_parser_normalizes_official_links_to_https():
    job = parse_zhilian_job(
        {
            "jobTitle": "AI产品经理",
            "jobUrl": "http://www.zhaopin.com/jobdetail/CC1.htm",
            "cityName": "深圳",
        },
        city_name="深圳",
    )

    assert job.url == "https://www.zhaopin.com/jobdetail/CC1.htm"


def test_51job_public_parser_extracts_stable_identity():
    payload = json.loads((FIXTURES / "job51/search_results_page1.json").read_text())
    raw = payload["cards"][0]
    job = parse_job51_job(raw, city_name="深圳")
    assert job51_job_id(raw)
    assert job.name and job.url
