"""Focused tests for Zhilian live collection helpers."""

import json

from jobagent.platforms.zhilian.city_resolver import ZhilianCityResolver, city_code_from_url
from jobagent.platforms.zhilian.collect import ZhilianReadOnlyCollector, build_zhilian_search_url
from jobagent.platforms.zhilian.selectors import (
    build_zhilian_city_filter_script,
    build_zhilian_keyword_search_script,
)


def test_build_zhilian_search_url_encodes_verified_beijing_city():
    url = build_zhilian_search_url("数据产品负责人", city="北京", page=1)

    assert url == "https://www.zhaopin.com/"
    assert "kw=" not in url
    assert "jl=" not in url


def test_build_zhilian_search_url_encodes_verified_shanghai_city_and_page():
    url = build_zhilian_search_url("AI 产品负责人", city="上海市", page=2)

    assert url == "https://www.zhaopin.com/"


def test_build_zhilian_search_url_encodes_verified_shenzhen_city():
    url = build_zhilian_search_url("高级产品经理", city="深圳市", page=1)

    assert url == "https://www.zhaopin.com/"


def test_build_zhilian_search_url_keeps_ui_fallback_for_unknown_city():
    url = build_zhilian_search_url("BI负责人", city="杭州", page=1)

    assert url == "https://www.zhaopin.com/"


def test_keyword_search_keeps_platform_route_in_managed_tab():
    script = build_zhilian_keyword_search_script("AI产品经理")

    assert "originalTarget.toLowerCase() === '_blank'" in script
    assert "button.setAttribute('target', '_self')" in script
    assert "button.href =" not in script
    assert "/sou/" not in script


def test_city_filter_checks_visible_selected_city_before_expanding():
    script = build_zhilian_city_filter_script("深圳")
    selected_city_branch = script.index("source: 'visible_current_city'")
    expand_city_branch = script.index("findLocationHeader() || currentCityControl")

    assert selected_city_branch < expand_city_branch
    assert "currentCity === targetCity" in script
    assert "alreadySelected: true" in script


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

    def _click_at(self, x, y):
        self.calls.append(f"click:{x}:{y}")

    def dismiss_javascript_dialog(self):
        return {"ok": True, "dismissed": False}

    def _exec_js(self, script: str):
        if "zhilian_keyword_search" in script:
            return {
                "ok": True,
                "mode": "zhilian_keyword_search",
                "keyword": "AI产品经理",
                "observedValue": "AI产品经理",
                "clickPoint": {"x": 120, "y": 80},
            }
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
                "https://www.zhaopin.com/sou/jl653/kw01300K004004338VHKHKTEG/p1"
                if self.verified
                else "https://www.zhaopin.com/sou/kw01300K004004338VHKHKTEG/p1"
            ),
            "title": "智联招聘",
            "loginRequired": False,
            "searchKeyword": "AI产品经理",
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
    assert driver.calls[0] == "https://www.zhaopin.com/"
    assert any(call.startswith("click:") for call in driver.calls)
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
        if "zhilian_keyword_search" in script or "zhilian_city_filter" in script:
            return super()._exec_js(script)
        self.snapshot_count += 1
        if self.snapshot_count == 1:
            return {
                "ok": True,
                "url": "https://www.zhaopin.com/sou/jl999/kw01300K004004338VHKHKTEG/p1",
                "title": "智联招聘",
                "loginRequired": False,
                "searchKeyword": "AI产品经理",
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
    assert driver.calls[0] == "https://www.zhaopin.com/"
    assert driver.snapshot_count == 2
    assert resolver.lookup("杭州") == ("653", "verified_cache")


class _RejectedKeywordDriver(_DynamicCityDriver):
    def dismiss_javascript_dialog(self):
        return {"ok": True, "dismissed": True}


def test_collector_stops_when_platform_rejects_visible_keyword(tmp_path):
    driver = _RejectedKeywordDriver()

    result = ZhilianReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "cities.json",
    ).collect(query="财务总监", city="上海", limit=5, wait_seconds=1)

    assert result.ok is False
    assert result.error == "zhilian_keyword_rejected"
    assert result.jobs == []
    assert driver.calls == ["https://www.zhaopin.com/", "click:120:80"]


def test_collector_rejects_mismatched_visible_keyword_before_returning_jobs(tmp_path):
    class _MismatchedKeywordDriver(_DynamicCityDriver):
        def _exec_js(self, script: str):
            result = super()._exec_js(script)
            if "zhilian_keyword_search" not in script and "zhilian_city_filter" not in script:
                result["searchKeyword"] = "01300K004004338VHKHKTEG"
            return result

    result = ZhilianReadOnlyCollector(
        driver=_MismatchedKeywordDriver(),
        city_cache_path=tmp_path / "cities.json",
    ).collect(query="AI产品经理", city="杭州", limit=5, wait_seconds=1)

    assert result.ok is False
    assert result.error == "zhilian_keyword_unverified"
    assert result.jobs == []
