from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from jobagent.platforms.liepin.city_resolver import LiepinCityResolver
from jobagent.platforms.liepin.collect import LiepinReadOnlyCollector


def _evidence(city: str, code: str, query: str, *, card_count: int = 1) -> dict:
    return {
        "ok": True,
        "controlCity": city,
        "controlCode": code,
        "metaCity": city,
        "titleCity": city,
        "inputQuery": query,
        "urlQuery": query,
        "jobCardCount": card_count,
        "noResults": card_count == 0,
        "resultSurface": True,
    }


@pytest.mark.parametrize(
    ("city", "slug", "code"),
    [
        ("郑州", "zhengzhou", "150020"),
        ("杭州", "hz", "070020"),
    ],
)
def test_liepin_discovers_and_verifies_unbundled_city_before_caching(
    tmp_path: Path,
    city: str,
    slug: str,
    code: str,
):
    driver = DynamicCityDriver(city=city, slug=slug, code=code)
    cache = tmp_path / "liepin-cities.json"

    result = LiepinReadOnlyCollector(driver=driver, city_cache_path=cache).collect(
        query="高级产品经理",
        city=city,
        wait_seconds=1,
        page_delay=0,
    )

    assert result.ok is True
    assert [job.name for job in result.jobs] == ["高级产品经理"]
    assert result.snapshot["cityResolution"]["source"] == "platform_city_directory"
    assert result.snapshot["cityVerification"]["verified"] is True
    assert LiepinCityResolver(cache).lookup(city) == (code, "verified_cache")
    assert any("/citylist/" in url for url in driver.opened)
    assert any(f"/city-{slug}/zhaopin/" in url for url in driver.opened)
    assert any(f"city={code}" in url and f"dq={code}" in url for url in driver.opened)


def test_liepin_old_city_page_cannot_claim_requested_city_or_seed_cache(tmp_path: Path):
    cache = tmp_path / "liepin-cities.json"
    driver = ConflictingCityDriver()

    result = LiepinReadOnlyCollector(driver=driver, city_cache_path=cache).collect(
        query="高级产品经理",
        city="郑州",
        wait_seconds=1,
        page_delay=0,
    )

    assert result.ok is False
    assert result.error == "liepin_city_evidence_unverified"
    assert result.jobs == []
    assert result.to_payload()["city_resolution"] == {
        "city": "郑州",
        "source": "platform_city_directory",
        "code_resolved": False,
        "verified": False,
        "city_sources": [],
        "query_sources": ["input", "url"],
        "result_state": "jobs",
        "conflicts": ["city"],
    }
    assert LiepinCityResolver(cache).lookup("郑州") == (None, "none")


def test_liepin_city_cache_rejects_unverified_or_query_mismatched_evidence(tmp_path: Path):
    resolver = LiepinCityResolver(tmp_path / "liepin-cities.json")
    mismatched = resolver.verify_evidence(
        _evidence("郑州", "150020", "AI产品经理"),
        city="郑州",
        query="高级产品经理",
    )

    assert mismatched["verified"] is False
    assert "query" in mismatched["conflicts"]
    assert resolver.remember("郑州", "150020", mismatched) is False
    assert resolver.lookup("郑州") == (None, "none")


class DynamicCityDriver:
    def __init__(self, *, city: str, slug: str, code: str):
        self.city = city
        self.slug = slug
        self.code = code
        self.opened: list[str] = []
        self.page = "old_city"
        self.current_url = "https://www.liepin.com/zhaopin/?city=050090&dq=050090"

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        del wait_seconds
        self.opened.append(url)
        self.current_url = url
        if "/citylist/" in url:
            self.page = "city_list"
        elif f"/city-{self.slug}/zhaopin/" in url:
            self.page = "city_route"
        elif f"city={self.code}" in url:
            self.page = "verified_search"
        return {"ok": True, "url": url}

    def _exec_js(self, script: str):
        if "liepin_city_route_discovery" in script:
            return {
                "ok": True,
                "city": self.city,
                "route": f"https://www.liepin.com/city-{self.slug}/",
                "candidateCount": 300,
            }
        if "liepin_city_search_evidence" in script:
            if self.page == "old_city":
                return _evidence("深圳", "050090", "")
            if self.page in {"city_route", "verified_search"}:
                query = _query_from_url(self.current_url)
                return _evidence(self.city, self.code, query)
            return {"ok": False}
        if "placeholder" in script and "button" in script:
            query = _query_from_url(self.current_url)
            return {
                "ok": True,
                "href": unquote(self.current_url),
                "body": query,
                "input": None,
                "button": None,
            }
        return {
            "ok": True,
            "url": self.current_url,
            "title": f"【{self.city}招聘信息】-猎聘",
            "loginRequired": False,
            "loginPromptPresent": False,
            "cityEvidence": _evidence(self.city, self.code, _query_from_url(self.current_url)),
            "candidateCount": 1,
            "cardCount": 1,
            "cards": [
                {
                    "jobId": f"{self.slug}-1",
                    "jobTitle": "高级产品经理",
                    "salary": "30-50k",
                    "companyName": "Example Company",
                    "cityName": self.city,
                    "jobUrl": f"https://www.liepin.com/job/{self.slug}-1.shtml",
                }
            ],
        }


class ConflictingCityDriver(DynamicCityDriver):
    def __init__(self):
        super().__init__(city="郑州", slug="zhengzhou", code="150020")

    def _exec_js(self, script: str):
        if "liepin_city_route_discovery" in script:
            return {
                "ok": True,
                "city": "郑州",
                "route": "https://www.liepin.com/city-zhengzhou/",
                "candidateCount": 300,
            }
        if "liepin_city_search_evidence" in script:
            return _evidence("深圳", "050090", "高级产品经理")
        return super()._exec_js(script)


def _query_from_url(url: str) -> str:
    return parse_qs(urlparse(url).query).get("key", [""])[0]
