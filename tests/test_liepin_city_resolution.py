from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from jobagent.platforms.liepin.city_resolver import LiepinCityResolver
from jobagent.platforms.liepin.collect import LiepinReadOnlyCollector


def _evidence(
    city: str,
    code: str,
    query: str,
    *,
    card_count: int = 1,
    url: str = "",
) -> dict:
    payload = {
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
    if url:
        payload["url"] = url
    return payload


def _route_evidence(
    city: str,
    query: str,
    url: str,
    *,
    card_count: int = 1,
) -> dict:
    return {
        "ok": True,
        "url": url,
        "controlCity": city,
        "controlCode": "",
        "metaCity": city,
        "titleCity": city,
        "visibleCity": city,
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
def test_liepin_discovers_and_verifies_unbundled_city_route_without_code(
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
    assert LiepinCityResolver(cache).lookup(city) == (None, "none")
    assert any("/citylist/" in url for url in driver.opened)
    assert any(f"/city-{slug}/zhaopin/" in url for url in driver.opened)
    assert all(f"city={code}" not in url and f"dq={code}" not in url for url in driver.opened)


@pytest.mark.parametrize(
    ("city", "slug", "code"),
    [
        ("郑州", "zhengzhou", "150020"),
        ("杭州", "hz", "070020"),
    ],
)
def test_liepin_uses_logged_in_search_page_city_links_before_redirecting_directory(
    tmp_path: Path,
    city: str,
    slug: str,
    code: str,
):
    driver = LoggedInSearchPageDirectoryRedirectDriver(
        city=city,
        slug=slug,
        code=code,
    )
    cache = tmp_path / "liepin-cities.json"

    result = LiepinReadOnlyCollector(driver=driver, city_cache_path=cache).collect(
        query="高级产品经理",
        city=city,
        pages=2,
        wait_seconds=1,
        page_delay=0,
    )

    assert result.ok is True
    assert [job.name for job in result.jobs] == ["高级产品经理"]
    first_snapshot = result.snapshot["pages"][0]
    assert first_snapshot["cityResolution"]["source"] == "current_platform_city_links"
    assert first_snapshot["cityResolution"]["verification"]["route_verified"] is True
    assert all("/citylist/" not in url for url in driver.opened)
    assert any(f"/city-{slug}/zhaopin/" in url for url in driver.opened)
    assert any("currentPage=1" in url for url in driver.opened)
    assert all(f"city={code}" not in url and f"dq={code}" not in url for url in driver.opened)
    assert LiepinCityResolver(cache).lookup(city) == (None, "none")


def test_liepin_uses_official_search_links_when_current_page_is_candidate_home(
    tmp_path: Path,
):
    driver = CandidateHomeSearchFallbackDriver(
        city="郑州",
        slug="zhengzhou",
        code="150020",
    )

    result = LiepinReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "liepin-cities.json",
    ).collect(
        query="高级产品经理",
        city="郑州",
        wait_seconds=1,
        page_delay=0,
    )

    assert result.ok is True
    assert result.snapshot["cityResolution"]["source"] == (
        "platform_search_city_links"
    )
    assert result.snapshot["cityVerification"]["route_changed"] is True
    assert not any("/citylist/" in url for url in driver.opened)


def test_liepin_reenters_search_route_when_current_page_is_already_target(
    tmp_path: Path,
):
    driver = CandidateHomeSearchFallbackDriver(
        city="郑州",
        slug="zhengzhou",
        code="150020",
        initial_page="city_route",
    )

    result = LiepinReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "liepin-cities.json",
    ).collect(
        query="高级产品经理",
        city="郑州",
        wait_seconds=1,
        page_delay=0,
    )

    assert result.ok is True
    assert driver.opened[0].startswith("https://www.liepin.com/zhaopin/")
    assert "/city-zhengzhou/zhaopin/" in driver.opened[1]
    assert result.snapshot["cityResolution"]["verification"]["route_changed"] is True


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
        "source": "current_platform_city_links",
        "code_resolved": False,
        "route_verified": True,
        "route_changed": True,
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


def test_liepin_city_home_recommendations_are_not_search_results(tmp_path: Path):
    resolver = LiepinCityResolver(tmp_path / "liepin-cities.json")

    verification = resolver.verify_route_evidence(
        _route_evidence(
            "郑州",
            "高级产品经理",
            "https://www.liepin.com/city-zhengzhou/",
        ),
        city="郑州",
        query="高级产品经理",
        expected_route="https://www.liepin.com/city-zhengzhou/",
        previous_url="https://www.liepin.com/zhaopin/?key=高级产品经理",
    )

    assert verification["verified"] is False
    assert verification["route_verified"] is False
    assert "route" in verification["conflicts"]


def test_liepin_route_must_change_before_candidate_extraction(tmp_path: Path):
    resolver = LiepinCityResolver(tmp_path / "liepin-cities.json")
    route_url = (
        "https://www.liepin.com/city-zhengzhou/zhaopin/"
        "?currentPage=0&pageSize=40&key=高级产品经理"
    )

    verification = resolver.verify_route_evidence(
        _route_evidence("郑州", "高级产品经理", route_url),
        city="郑州",
        query="高级产品经理",
        expected_route="https://www.liepin.com/city-zhengzhou/",
        previous_url=route_url,
    )

    assert verification["verified"] is False
    assert verification["route_verified"] is True
    assert verification["route_changed"] is False


def test_liepin_directory_redirect_reports_candidate_home_without_claiming_login(
    tmp_path: Path,
):
    driver = DirectoryRedirectWithoutCityLinkDriver()

    result = LiepinReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "liepin-cities.json",
    ).collect(
        query="高级产品经理",
        city="郑州",
        wait_seconds=1,
        page_delay=0,
    )

    assert result.ok is False
    assert result.error == "liepin_city_code_not_found"
    assert result.to_payload()["city_resolution"] == {
        "city": "郑州",
        "source": "platform_city_directory",
        "code_resolved": False,
        "route_verified": False,
        "route_changed": False,
        "verified": False,
        "city_sources": [],
        "query_sources": [],
        "result_state": "unknown",
        "conflicts": [],
        "discovery_outcome": "candidate_home_redirect",
    }


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
            if self.page != "city_list":
                return {
                    "ok": False,
                    "city": self.city,
                    "route": "",
                    "candidateCount": 0,
                    "pageKind": "search_result",
                }
            return {
                "ok": True,
                "city": self.city,
                "route": f"https://www.liepin.com/city-{self.slug}/",
                "candidateCount": 300,
                "pageKind": "city_directory",
            }
        if "liepin_city_search_evidence" in script:
            if self.page == "old_city":
                return _evidence(
                    "深圳",
                    "050090",
                    "",
                    url=self.current_url,
                )
            if self.page == "city_route":
                query = _query_from_url(self.current_url)
                return _route_evidence(self.city, query, self.current_url)
            if self.page == "verified_search":
                query = _query_from_url(self.current_url)
                return _evidence(self.city, self.code, query, url=self.current_url)
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
        city_evidence = (
            _route_evidence(
                self.city,
                _query_from_url(self.current_url),
                self.current_url,
            )
            if self.page == "city_route"
            else _evidence(
                self.city,
                self.code,
                _query_from_url(self.current_url),
                url=self.current_url,
            )
        )
        return {
            "ok": True,
            "url": self.current_url,
            "title": f"【{self.city}招聘信息】-猎聘",
            "loginRequired": False,
            "loginPromptPresent": False,
            "cityEvidence": city_evidence,
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
            if self.page == "old_city":
                return {
                    "ok": True,
                    "city": "郑州",
                    "route": "https://www.liepin.com/city-zhengzhou/",
                    "candidateCount": 134,
                    "pageKind": "search_result",
                }
            return {
                "ok": False,
                "city": "郑州",
                "route": "",
                "candidateCount": 0,
                "pageKind": "search_result",
            }
        if "liepin_city_search_evidence" in script:
            return _evidence(
                "深圳",
                "050090",
                "高级产品经理",
                url=self.current_url,
            )
        return super()._exec_js(script)


class LoggedInSearchPageDirectoryRedirectDriver(DynamicCityDriver):
    """Model the real v0.5.28 path without using a real account or browser."""

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        result = super().open_url_in_new_tab(url, wait_seconds=wait_seconds)
        if "/citylist/" in url:
            self.page = "candidate_home"
            self.current_url = "https://c.liepin.com/"
            return {"ok": True, "url": self.current_url, "title": "我的首页_猎聘"}
        return result

    def _exec_js(self, script: str):
        if "liepin_city_route_discovery" in script:
            if self.page == "old_city":
                return {
                    "ok": True,
                    "city": self.city,
                    "route": f"https://www.liepin.com/city-{self.slug}/",
                    "candidateCount": 48,
                    "pageKind": "search_result",
                }
            return {
                "ok": False,
                "city": self.city,
                "route": "",
                "candidateCount": 0,
                "pageKind": "candidate_home",
            }
        return super()._exec_js(script)


class DirectoryRedirectWithoutCityLinkDriver(
    LoggedInSearchPageDirectoryRedirectDriver
):
    def _exec_js(self, script: str):
        if "liepin_city_route_discovery" in script:
            return {
                "ok": False,
                "city": self.city,
                "route": "",
                "candidateCount": 0,
                "pageKind": (
                    "candidate_home" if self.page == "candidate_home" else "search_result"
                ),
            }
        return super()._exec_js(script)

    def __init__(self):
        super().__init__(city="郑州", slug="zhengzhou", code="150020")


class CandidateHomeSearchFallbackDriver(LoggedInSearchPageDirectoryRedirectDriver):
    def __init__(
        self,
        *,
        city: str,
        slug: str,
        code: str,
        initial_page: str = "candidate_home",
    ):
        super().__init__(city=city, slug=slug, code=code)
        self.page = initial_page
        self.current_url = (
            f"https://www.liepin.com/city-{slug}/zhaopin/"
            "?currentPage=0&pageSize=40&key=高级产品经理"
            if initial_page == "city_route"
            else "https://c.liepin.com/"
        )

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        result = super().open_url_in_new_tab(url, wait_seconds=wait_seconds)
        if url.startswith("https://www.liepin.com/zhaopin/?currentPage="):
            self.page = "generic_search"
        return result

    def _exec_js(self, script: str):
        if "liepin_city_route_discovery" in script:
            if self.page in {"generic_search", "city_route"}:
                return {
                    "ok": True,
                    "city": self.city,
                    "route": f"https://www.liepin.com/city-{self.slug}/",
                    "candidateCount": 134,
                    "pageKind": "search_result",
                }
            return {
                "ok": False,
                "city": self.city,
                "route": "",
                "candidateCount": 0,
                "pageKind": "candidate_home",
            }
        if "liepin_city_search_evidence" in script and self.page == "candidate_home":
            return {"ok": True, "url": self.current_url}
        if "liepin_city_search_evidence" in script and self.page == "generic_search":
            return {
                "ok": True,
                "url": self.current_url,
                "inputQuery": _query_from_url(self.current_url),
                "urlQuery": _query_from_url(self.current_url),
                "jobCardCount": 1,
                "noResults": False,
                "resultSurface": True,
            }
        return super()._exec_js(script)


def _query_from_url(url: str) -> str:
    return parse_qs(urlparse(url).query).get("key", [""])[0]
