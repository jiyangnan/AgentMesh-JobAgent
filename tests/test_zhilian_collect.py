"""Focused tests for Zhilian live collection helpers."""

import json

from jobagent.platforms.zhilian.city_resolver import ZhilianCityResolver, city_code_from_url
from jobagent.platforms.zhilian.collect import ZhilianReadOnlyCollector, build_zhilian_search_url


def test_build_zhilian_search_url_encodes_verified_beijing_city():
    url = build_zhilian_search_url("数据产品负责人", city="北京", page=1)

    assert url == (
        "https://sou.zhaopin.com/?jl=530&"
        "kw=%E6%95%B0%E6%8D%AE%E4%BA%A7%E5%93%81%E8%B4%9F%E8%B4%A3%E4%BA%BA"
    )


def test_build_zhilian_search_url_encodes_verified_shanghai_city_and_page():
    url = build_zhilian_search_url("AI 产品负责人", city="上海市", page=2)

    assert url == (
        "https://sou.zhaopin.com/?jl=538&"
        "kw=AI%20%E4%BA%A7%E5%93%81%E8%B4%9F%E8%B4%A3%E4%BA%BA&p=2"
    )


def test_build_zhilian_search_url_encodes_verified_shenzhen_city():
    url = build_zhilian_search_url("高级产品经理", city="深圳市", page=1)

    assert url == (
        "https://sou.zhaopin.com/?jl=489&"
        "kw=%E9%AB%98%E7%BA%A7%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86"
    )


def test_build_zhilian_search_url_keeps_ui_fallback_for_unknown_city():
    url = build_zhilian_search_url("BI负责人", city="杭州", page=1)

    assert url == "https://sou.zhaopin.com/?kw=BI%E8%B4%9F%E8%B4%A3%E4%BA%BA"


def test_city_code_parser_accepts_query_and_canonical_path():
    assert city_code_from_url("https://sou.zhaopin.com/?jl=489&kw=AI") == "489"
    assert city_code_from_url("https://www.zhaopin.com/sou/jl653/kwAI") == "653"
    assert city_code_from_url("https://sou.zhaopin.com/?kw=AI") is None


def test_city_resolver_persists_verified_dynamic_mapping(tmp_path):
    cache = tmp_path / "cities.json"
    resolver = ZhilianCityResolver(cache)
    snapshot = {
        "url": "https://sou.zhaopin.com/?jl=653&kw=AI",
        "cards": [{"cityName": "杭州"}],
    }

    verified = resolver.verify_snapshot(
        "杭州市", snapshot, expected_code=None, source="visible_filter_recovery"
    )
    resolver.remember("杭州市", verified["observedCode"], evidence_url=verified["observedUrl"])

    assert verified["verified"] is True
    assert resolver.lookup("杭州") == ("653", "verified_cache")
    assert json.loads(cache.read_text(encoding="utf-8"))["cities"]["杭州"]["code"] == "653"


def test_city_resolver_tolerates_recommendations_outside_verified_city():
    verified = ZhilianCityResolver().verify_snapshot(
        "杭州",
        {
            "url": "https://sou.zhaopin.com/?jl=653&kw=AI",
            "cards": [{"cityName": "杭州"}, {"cityName": "上海"}],
        },
        expected_code="653",
        source="verified_cache",
    )

    assert verified["verified"] is True
    assert verified["matchingCards"] == 1
    assert verified["mismatchedCardCities"] == ["上海"]


class _DynamicCityDriver:
    def __init__(self, *, verified: bool = True):
        self.verified = verified
        self.calls: list[str] = []

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        self.calls.append(url)
        return {"ok": True, "url": url}

    def _exec_js(self, script: str):
        if "zhilian_city_filter" in script:
            return {
                "ok": True,
                "mode": "zhilian_city_filter",
                "city": "杭州",
                "applied": True,
                "alreadySelected": True,
            }
        return {
            "ok": True,
            "url": (
                "https://sou.zhaopin.com/?jl=653&kw=AI"
                if self.verified
                else "https://sou.zhaopin.com/?kw=AI"
            ),
            "title": "智联招聘",
            "loginRequired": False,
            "cards": [
                {
                    "positionId": "HZ-1",
                    "jobTitle": "AI产品经理",
                    "companyName": "杭州示例科技",
                    "cityName": "杭州" if self.verified else "上海",
                    "jobUrl": "https://www.zhaopin.com/jobdetail/HZ-1.htm",
                }
            ],
        }


def test_collector_discovers_unknown_city_code_before_returning_jobs(tmp_path):
    driver = _DynamicCityDriver()
    cache = tmp_path / "cities.json"

    result = ZhilianReadOnlyCollector(driver=driver, city_cache_path=cache).collect(
        query="AI产品经理", city="杭州", limit=5, wait_seconds=1
    )

    assert result.ok is True
    assert result.jobs[0].city == "杭州"
    assert ZhilianCityResolver(cache).lookup("杭州") == ("653", "verified_cache")


def test_collector_fails_closed_when_city_cannot_be_verified(tmp_path):
    result = ZhilianReadOnlyCollector(
        driver=_DynamicCityDriver(verified=False),
        city_cache_path=tmp_path / "cities.json",
    ).collect(query="AI产品经理", city="杭州", limit=5, wait_seconds=1)

    assert result.ok is False
    assert result.error == "zhilian_city_resolution_unverified"
    assert result.jobs == []


class _StaleCityDriver(_DynamicCityDriver):
    def __init__(self):
        super().__init__()
        self.snapshot_count = 0

    def _exec_js(self, script: str):
        if "zhilian_city_filter" in script:
            return super()._exec_js(script)
        self.snapshot_count += 1
        if self.snapshot_count == 1:
            return {
                "ok": True,
                "url": "https://sou.zhaopin.com/?jl=999&kw=AI",
                "title": "智联招聘",
                "loginRequired": False,
                "cards": [{"cityName": "上海"}],
            }
        return super()._exec_js(script)


def test_collector_replaces_stale_cached_city_code_after_visible_recovery(tmp_path):
    cache = tmp_path / "cities.json"
    resolver = ZhilianCityResolver(cache)
    resolver.remember(
        "杭州",
        "999",
        evidence_url="https://sou.zhaopin.com/?jl=999",
    )
    driver = _StaleCityDriver()

    result = ZhilianReadOnlyCollector(driver=driver, city_cache_path=cache).collect(
        query="AI产品经理", city="杭州", limit=5, wait_seconds=1
    )

    assert result.ok is True
    assert "jl=999" in driver.calls[0]
    assert driver.snapshot_count == 2
    assert resolver.lookup("杭州") == ("653", "verified_cache")
