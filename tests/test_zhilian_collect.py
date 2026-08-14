"""Focused tests for Zhilian live collection helpers."""

import json

from jobagent.platforms.zhilian.city_resolver import (
    ZhilianCityResolver,
    city_code_from_url,
)
from jobagent.platforms.zhilian.collect import (
    ZhilianCollectResult,
    ZhilianReadOnlyCollector,
    build_zhilian_search_url,
)
from jobagent.platforms.zhilian.selectors import (
    build_zhilian_city_filter_script,
    build_zhilian_keyword_search_script,
    build_zhilian_search_control_activation_script,
    build_zhilian_search_form_submit_script,
    build_zhilian_search_transition_script,
    build_zhilian_snapshot_script,
)


def _recent_login_verification():
    return {
        "valid": True,
        "source": "recent_login_check",
        "platform": "zhilian",
        "round_id": "round-1",
        "browser_session_id": "local-cdp-19222",
        "age_seconds": 12,
    }


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

    assert "input.closest('.search-wrapper')" in script
    assert "search-wrapper__button" in script
    assert "searchDestinationReady" in script
    assert "button.setAttribute('target', '_self')" not in script
    assert "button.href =" not in script
    assert "/sou/" not in script


def test_search_control_activation_requeries_live_vue_anchor_and_bounds_methods():
    pointer = build_zhilian_search_control_activation_script(
        "AI产品经理",
        method="native_pointer",
    )
    dom_click = build_zhilian_search_control_activation_script(
        "AI产品经理",
        method="dom_click",
    )
    destination = build_zhilian_search_control_activation_script(
        "AI产品经理",
        method="official_destination",
    )

    for script in (pointer, dom_click, destination):
        assert "zhilian_search_control_activation" in script
        assert "input.closest('.search-wrapper')" in script
        assert "search-wrapper__button" in script
        assert "officialSearchDestination" in script
        assert "searchDestinationReady" in script
        assert "button.href =" not in script
    assert 'activationMethod = "native_pointer"' in pointer
    native_block = pointer.split("if (activationMethod === 'native_pointer')", 1)[1].split(
        "if (activationMethod === 'dom_click')", 1
    )[0]
    assert "button.setAttribute('target', '_self')" not in native_block
    assert "button.click()" in dom_click
    assert "location.assign" in destination


def test_keyword_search_emits_controlled_input_and_bounded_form_fallback():
    keyword_script = build_zhilian_keyword_search_script(
        "AI产品经理",
        allow_missing_submit_control=True,
    )
    form_script = build_zhilian_search_form_submit_script("AI产品经理")
    transition_script = build_zhilian_search_transition_script(
        "AI产品经理",
        "深圳",
    )

    assert "input._valueTracker.setValue(previousValue)" in keyword_script
    assert "new InputEvent('input'" in keyword_script
    assert "__jobagentZhilianSearchActionV1" in keyword_script
    assert "inputCandidateType" in keyword_script
    assert "buttonCandidateType" in keyword_script
    assert "formCandidateType" in keyword_script
    assert "zhilian_search_form_submit" in form_script
    assert "form.requestSubmit" in form_script
    assert "historyLength" in transition_script
    assert "documentTimeOriginMs" in transition_script
    assert "actionSignals" in transition_script


def test_snapshot_selector_supports_current_job_surfaces_and_safe_diagnostics():
    script = build_zhilian_snapshot_script(limit=5)

    assert "data-position-id" in script
    assert "data-job-id" in script
    assert "jobSurfaceCount" in script
    assert "jobActionCount" in script
    assert "noResults" in script
    assert "searchPageEvidence" in script
    assert "surfaceCardCandidates" in script
    assert "jobIdFromSurface" in script
    assert "jobUrlFromSurface" in script
    assert "collectableSurfaceCount" in script
    assert "explicitSurface" in script
    assert "root === explicitSurface" in script


def test_next_page_failure_is_not_reported_as_keyword_rejection():
    payload = ZhilianCollectResult(
        query="产品经理",
        city="深圳",
        url="https://www.zhaopin.com/jobs",
        jobs=[],
        ok=False,
        error="zhilian_page_option_not_found",
    ).to_payload()

    assert "next-page control" in payload["message"]
    assert "did not accept" not in payload["message"]
    assert payload["next_suggested"] == "jobagent browser diagnose --platform zhilian"


def test_city_filter_checks_visible_selected_city_before_expanding():
    script = build_zhilian_city_filter_script("深圳")
    selected_city_branch = script.index("source: 'visible_current_city'")
    expand_city_branch = script.index("findLocationHeader() || currentCityControl")

    assert selected_city_branch < expand_city_branch
    assert "currentCity === targetCity" in script
    assert "alreadySelected: true" in script


def test_city_filter_discovers_candidate_codes_from_current_dom_metadata():
    script = build_zhilian_city_filter_script("深圳")

    assert "candidateCode" in script
    assert "data-city-code" in script
    assert "data-city-id" in script
    assert "aria-label*=\"城市\"" in script


def test_search_transition_discovers_candidate_code_from_target_city_navigation():
    script = build_zhilian_search_transition_script("AI产品经理", "深圳")

    assert "candidateCityCode" in script
    assert "data-city-code" in script
    assert "data-city-id" in script
    assert "data-href" in script
    assert "querySelectorAll" in script


def test_city_code_parser_accepts_query_and_canonical_path():
    assert city_code_from_url("https://sou.zhaopin.com/?jl=489&kw=AI") == "489"
    assert city_code_from_url("https://www.zhaopin.com/sou/jl653/kwAI") == "653"
    assert city_code_from_url("https://sou.zhaopin.com/?kw=AI") is None


def test_city_resolver_persists_verified_dynamic_mapping(tmp_path):
    cache = tmp_path / "cities.json"
    resolver = ZhilianCityResolver(cache)
    snapshot = {
        "url": "https://sou.zhaopin.com/?jl=653&kw=AI",
        "title": "杭州热门职位招聘 - 智联招聘",
        "cards": [{"cityName": "杭州"}],
    }

    verified = resolver.verify_snapshot(
        "杭州市", snapshot, expected_code=None, source="visible_filter_recovery"
    )
    resolver.remember(
        "杭州市",
        verified["observedCode"],
        evidence_url=verified["observedUrl"],
        evidence_sources=verified["evidenceSources"],
        verification_source=verified["verificationSource"],
    )

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


def test_city_resolver_rejects_untrusted_url_code_without_semantic_evidence():
    verified = ZhilianCityResolver().verify_snapshot(
        "深圳",
        {
            "url": "https://www.zhaopin.com/jobs?jl=765&kw=产品经理",
            "title": "智联招聘",
            "cards": [],
        },
        expected_code="489",
        source="bundled_seed",
    )

    assert verified["verified"] is False
    assert verified["observedCode"] == "765"
    assert verified["evidenceSources"] == []
    assert verified["evidenceStatus"] == "evidence_pending"
    assert verified["reason"] == "dynamic_evidence_insufficient"


def test_city_resolver_accepts_changed_code_only_with_independent_city_evidence():
    verified = ZhilianCityResolver().verify_snapshot(
        "深圳",
        {
            "url": "https://www.zhaopin.com/jobs?jl=765&kw=产品经理",
            "title": "深圳热门职位招聘 - 智联招聘",
            "visibleCity": "深圳",
            "cards": [{"cityName": "深圳"}, {"cityName": "深圳"}],
        },
        expected_code="489",
        source="bundled_seed",
    )

    assert verified["verified"] is True
    assert verified["codeChanged"] is True
    assert verified["evidenceSources"] == ["job_cards", "page_title", "visible_city"]
    assert verified["evidenceStatus"] == "verified"


def test_city_resolver_reports_real_city_conflict_separately():
    resolution = ZhilianCityResolver().verify_snapshot(
        "深圳",
        {
            "url": "https://www.zhaopin.com/jobs?jl=538&kw=产品经理",
            "title": "上海热门职位招聘 - 智联招聘",
            "visibleCity": "上海",
            "cards": [{"cityName": "上海"}],
        },
        expected_code="489",
        source="bundled_seed",
    )

    assert resolution["verified"] is False
    assert resolution["evidenceStatus"] == "evidence_conflict"
    assert resolution["reason"] == "city_evidence_conflict"


def test_city_resolver_does_not_treat_cross_city_fallback_cards_as_route_conflict():
    resolution = ZhilianCityResolver().verify_snapshot(
        "深圳",
        {
            "url": "https://www.zhaopin.com/jobs?jl=765&kw=产品经理",
            "title": "深圳热门职位招聘 - 智联招聘",
            "visibleCity": "深圳",
            "cards": [{"cityName": "上海"}, {"cityName": "北京"}],
        },
        expected_code="765",
        source="verified_cache",
    )

    assert resolution["verified"] is True
    assert resolution["allCardsOtherCity"] is True
    assert resolution["crossCityFallbackOnly"] is True
    assert resolution["visibleCityConflict"] is False
    assert resolution["codeEvidenceConflict"] is False
    assert resolution["evidenceStatus"] == "verified"


def test_city_resolver_keeps_strong_visible_city_conflict_fail_closed():
    resolution = ZhilianCityResolver().verify_snapshot(
        "深圳",
        {
            "url": "https://www.zhaopin.com/jobs?jl=765&kw=产品经理",
            "title": "深圳热门职位招聘 - 智联招聘",
            "visibleCity": "上海",
            "cards": [{"cityName": "上海"}],
        },
        expected_code="765",
        source="verified_cache",
    )

    assert resolution["verified"] is False
    assert resolution["visibleCityConflict"] is True
    assert resolution["evidenceStatus"] == "evidence_conflict"


def test_city_resolver_rejects_conflicting_url_and_dom_candidate_codes():
    resolution = ZhilianCityResolver().verify_snapshot(
        "深圳",
        {
            "url": "https://www.zhaopin.com/jobs?jl=765&kw=产品经理",
            "title": "深圳热门职位招聘 - 智联招聘",
            "visibleCity": "深圳",
            "candidateCityCode": "489",
            "cards": [{"cityName": "深圳"}],
        },
        expected_code="489",
        source="bundled_seed",
    )

    assert resolution["verified"] is False
    assert resolution["codeEvidenceConflict"] is True
    assert resolution["evidenceStatus"] == "evidence_conflict"


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
            "title": "杭州热门职位招聘 - 智联招聘" if self.verified else "智联招聘",
            "loginRequired": False,
            "searchKeyword": "AI产品经理",
            "visibleCity": "杭州" if self.verified else "",
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
    assert result.error == "zhilian_city_evidence_conflict"
    assert result.jobs == []
    assert result.to_payload()["retryable"] is False


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


class _ChangedShenzhenCityDriver(_DynamicCityDriver):
    def __init__(self, *, corroborated: bool = True):
        super().__init__()
        self.corroborated = corroborated
        self.city_filter_calls = 0

    def _exec_js(self, script: str):
        if "zhilian_keyword_search" in script:
            return super()._exec_js(script)
        if "zhilian_city_filter" in script:
            self.city_filter_calls += 1
            return {
                "ok": False,
                "mode": "zhilian_city_filter",
                "error": "zhilian_city_option_not_found",
                "readyState": "complete",
                "sessionState": "logged_in",
                "url": "https://www.zhaopin.com/jobs?jl=765&kw=产品经理",
            }
        return {
            "ok": True,
            "url": "https://www.zhaopin.com/jobs?jl=765&kw=产品经理",
            "title": "深圳热门职位招聘 - 智联招聘" if self.corroborated else "智联招聘",
            "readyState": "complete",
            "sessionState": "logged_in",
            "loginRequired": False,
            "searchKeyword": "AI产品经理",
            "visibleCity": "深圳" if self.corroborated else "",
            "cards": [
                {
                    "positionId": "SZ-NEW-1",
                    "jobTitle": "AI产品经理",
                    "companyName": "深圳示例科技",
                    "cityName": "深圳" if self.corroborated else "",
                    "jobUrl": "https://www.zhaopin.com/jobdetail/SZ-NEW-1.htm",
                }
            ],
        }


class _SlowLoadingShenzhenDriver(_ChangedShenzhenCityDriver):
    def __init__(self, *, settle: bool = True):
        super().__init__()
        self.settle = settle
        self.keyword_probe_count = 0

    def _exec_js(self, script: str):
        if "zhilian_keyword_search" not in script:
            return super()._exec_js(script)
        self.keyword_probe_count += 1
        if not self.settle or self.keyword_probe_count == 1:
            return {
                "ok": False,
                "error": "zhilian_page_state_pending",
                "readyState": "loading",
                "sessionState": "loading",
                # A transient header control must not become login_required.
                "loginRequired": True,
                "loginEvidence": ["header_login_link"],
                "accountEvidence": [],
            }
        if self.keyword_probe_count == 2:
            return {
                "ok": False,
                "error": "zhilian_page_state_pending",
                "readyState": "complete",
                "sessionState": "unknown",
                "loginRequired": False,
                "loginEvidence": ["header_login_link"],
                "accountEvidence": ["account_navigation"],
            }
        return super()._exec_js(script)


class _SlowPostClickNavigationDriver(_ChangedShenzhenCityDriver):
    def __init__(self, *, eventually_ready: bool = True):
        super().__init__()
        self.eventually_ready = eventually_ready
        self.transition_probes = 0
        self.search_ready = False

    def _exec_js(self, script: str):
        if "zhilian_search_transition" in script:
            self.transition_probes += 1
            if self.eventually_ready and self.transition_probes >= 3:
                self.search_ready = True
                return {
                    "ok": True,
                    "mode": "zhilian_search_transition",
                    "url": "https://www.zhaopin.com/jobs?jl=765&kw=产品经理",
                    "title": "深圳热门职位招聘 2026年热门职位招聘信息-智联招聘",
                    "readyState": "complete",
                    "sessionState": "unknown",
                    "searchPageEvidence": ["search_route", "search_title"],
                    "observedKeyword": "AI产品经理",
                    "titleCityMatch": True,
                    "observedCityCode": "765",
                }
            return {
                "ok": True,
                "mode": "zhilian_search_transition",
                "url": "https://www.zhaopin.com/",
                "title": "深圳招聘网_深圳人才网_2026年深圳最新招聘信息-智联招聘",
                "readyState": "complete",
                "sessionState": "login_required",
                "loginRequired": True,
                "weakLoginEvidence": ["visible_login_control"],
                "strongLoginEvidence": [],
                "searchPageEvidence": ["search_input"],
                "observedKeyword": "AI产品经理",
                "titleCityMatch": True,
                "observedCityCode": None,
                "navigationElapsedMs": 50509,
            }
        if "zhilian_keyword_search" in script:
            return super()._exec_js(script)
        if not self.search_ready:
            if "zhilian_city_filter" in script:
                return {
                    "ok": False,
                    "mode": "zhilian_city_filter",
                    "error": "zhilian_city_options_collapsed",
                    "action": "expand_location",
                    "clickPoint": None,
                    "url": "https://www.zhaopin.com/",
                    "title": "深圳招聘网_深圳人才网_2026年深圳最新招聘信息-智联招聘",
                    "readyState": "complete",
                    "sessionState": "logged_in",
                }
            return {
                "ok": True,
                "url": "https://www.zhaopin.com/",
                "title": "深圳招聘网_深圳人才网_2026年深圳最新招聘信息-智联招聘",
                "readyState": "complete",
                "sessionState": "logged_in",
                "searchKeyword": "AI产品经理",
                "cards": [
                    {
                        "positionId": "HOME-1",
                        "jobTitle": "AI产品经理",
                        "companyName": "深圳示例科技",
                        "cityName": "深圳",
                        "jobUrl": "https://www.zhaopin.com/jobdetail/HOME-1.htm",
                    }
                ],
            }
        return super()._exec_js(script)


class _DomCandidateCityCodeDriver(_ChangedShenzhenCityDriver):
    def _exec_js(self, script: str):
        if "zhilian_search_transition" in script:
            return {
                "ok": True,
                "mode": "zhilian_search_transition",
                "url": "https://www.zhaopin.com/jobs?kw=产品经理",
                "title": "深圳热门职位招聘 - 智联招聘",
                "readyState": "complete",
                "sessionState": "page_ready",
                "searchPageEvidence": ["search_route", "search_title"],
                "observedKeyword": "AI产品经理",
                "titleCityMatch": True,
                "observedCityCode": None,
            }
        if "zhilian_keyword_search" in script:
            return super()._exec_js(script)
        if "zhilian_city_filter" in script:
            self.city_filter_calls += 1
            return {
                "ok": True,
                "mode": "zhilian_city_filter",
                "city": "深圳",
                "observedCity": "深圳",
                "candidateCode": "765",
                "alreadySelected": True,
                "source": "visible_current_city",
            }
        result = super()._exec_js(script)
        result["url"] = "https://www.zhaopin.com/jobs?kw=产品经理"
        return result


class _HomepageDynamicCityDiscoveryDriver(_ChangedShenzhenCityDriver):
    def __init__(self):
        super().__init__()
        self.transition_probes = 0
        self.city_discovery_steps = 0
        self.keyword_submissions = 0
        self.page = "entry"

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        result = super().open_url_in_new_tab(url, wait_seconds=wait_seconds)
        if url == "https://www.zhaopin.com/":
            self.page = "entry"
            self.keyword_submissions = 0
        elif url == "https://www.zhaopin.com/shenzhen/":
            self.page = "city_homepage"
        return result

    def _click_at(self, x, y):
        super()._click_at(x, y)
        self.keyword_submissions += 1

    def _exec_js(self, script: str):
        if "zhilian_keyword_search" in script:
            return super()._exec_js(script)
        if "zhilian_search_transition" in script:
            self.transition_probes += 1
            if self.page == "city_homepage" and self.keyword_submissions >= 2:
                self.page = "search_results"
                return {
                    "ok": True,
                    "mode": "zhilian_search_transition",
                    "url": "https://www.zhaopin.com/sou/jl765/kwOPAQUE/p1",
                    "title": "深圳热门职位招聘 2026年热门职位招聘信息-智联招聘",
                    "readyState": "complete",
                    "sessionState": "page_ready",
                    "searchPageEvidence": ["search_route", "search_input"],
                    "observedKeyword": "AI产品经理",
                    "titleCityMatch": True,
                    "observedCityCode": "765",
                }
            return {
                "ok": True,
                "mode": "zhilian_search_transition",
                "url": (
                    "https://www.zhaopin.com/shenzhen/"
                    if self.page == "city_homepage"
                    else "https://www.zhaopin.com/"
                ),
                "title": "深圳招聘网_深圳人才网_2026年深圳最新招聘信息-智联招聘",
                "readyState": "complete",
                "sessionState": "page_ready",
                "searchPageEvidence": ["search_input", "job_surface"],
                "observedKeyword": "AI产品经理",
                "titleCityMatch": True,
                "observedCityCode": None,
                "candidateCityCode": None,
            }
        if "zhilian_city_filter" in script:
            self.city_discovery_steps += 1
            if self.page == "entry":
                return {
                    "ok": False,
                    "mode": "zhilian_city_filter",
                    "error": "zhilian_city_route_navigation_required",
                    "action": "navigate_city_homepage",
                    "candidateNavigationUrl": "https://www.zhaopin.com/shenzhen/",
                    "candidateNavigationSource": "readable_city_anchor:href",
                    "url": "https://www.zhaopin.com/",
                    "title": "深圳招聘网_深圳人才网_2026年深圳最新招聘信息-智联招聘",
                    "readyState": "complete",
                    "sessionState": "page_ready",
                }
            return {
                "ok": True,
                "mode": "zhilian_city_filter",
                "city": "深圳",
                "observedCity": "深圳",
                "candidateCode": "765",
                "alreadySelected": True,
                "source": "search_route_city_code",
                "url": "https://www.zhaopin.com/sou/jl765/kwOPAQUE/p1",
                "title": "深圳热门职位招聘 2026年热门职位招聘信息-智联招聘",
                "readyState": "complete",
                "sessionState": "page_ready",
            }
        return {
            "ok": True,
            "url": "https://www.zhaopin.com/sou/jl765/kwOPAQUE/p1",
            "title": "深圳热门职位招聘 2026年热门职位招聘信息-智联招聘",
            "readyState": "complete",
            "sessionState": "page_ready",
            "loginRequired": False,
            "searchPageEvidence": ["search_route", "search_input", "job_surface"],
            "searchKeyword": "AI产品经理",
            "visibleCity": "深圳",
            "candidateCityCode": "765",
            "paginationDetected": True,
            "availablePages": [1],
            "currentPage": 1,
            "hasNextPage": False,
            "cards": [
                {
                    "positionId": "SZ-DYNAMIC-1",
                    "jobTitle": "AI产品经理",
                    "companyName": "深圳示例科技",
                    "cityName": "深圳",
                    "jobUrl": "https://www.zhaopin.com/jobdetail/SZ-DYNAMIC-1.htm",
                }
            ],
        }


def test_collector_learns_dom_candidate_code_only_after_multi_source_validation(tmp_path):
    cache = tmp_path / "cities.json"

    result = ZhilianReadOnlyCollector(
        driver=_DomCandidateCityCodeDriver(),
        city_cache_path=cache,
    ).collect(query="AI产品经理", city="深圳", limit=5, wait_seconds=1)

    assert result.ok is True
    assert result.snapshot["cityResolution"]["observedCodeSource"] == (
        "city_control_metadata"
    )
    assert result.snapshot["cityResolution"]["evidenceSources"] == [
        "job_cards",
        "page_title",
        "visible_city",
    ]
    assert ZhilianCityResolver(cache).lookup("深圳") == ("765", "verified_cache")


def test_collector_actively_discovers_city_code_and_converges_on_homepage(
    tmp_path,
    monkeypatch,
):
    driver = _HomepageDynamicCityDiscoveryDriver()
    cache = tmp_path / "cities.json"
    monkeypatch.setattr("jobagent.platforms.zhilian.collect.time.sleep", lambda _: None)
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_NAVIGATION_TIMEOUT_SECONDS",
        0,
    )

    result = ZhilianReadOnlyCollector(
        driver=driver,
        city_cache_path=cache,
    ).collect(query="AI产品经理", city="深圳", limit=5, wait_seconds=1)

    assert result.ok is True
    assert result.jobs[0].city == "深圳"
    assert driver.city_discovery_steps >= 1
    assert driver.calls.count("https://www.zhaopin.com/shenzhen/") == 1
    assert driver.keyword_submissions == 2
    assert result.snapshot["cityResolution"]["observedCode"] == "765"
    assert ZhilianCityResolver(cache).lookup("深圳") == ("765", "verified_cache")


def test_collector_waits_for_post_click_navigation_before_city_resolution(
    tmp_path,
    monkeypatch,
):
    driver = _SlowPostClickNavigationDriver()
    monkeypatch.setattr("jobagent.platforms.zhilian.collect.time.sleep", lambda _: None)

    result = ZhilianReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "cities.json",
    ).collect(query="AI产品经理", city="深圳", limit=5, wait_seconds=8)

    assert result.ok is True
    assert driver.transition_probes == 3
    assert result.jobs[0].city == "深圳"
    assert ZhilianCityResolver(tmp_path / "cities.json").lookup("深圳") == (
        "765",
        "verified_cache",
    )


def test_collector_reports_city_evidence_pending_when_search_stays_on_homepage(
    tmp_path,
    monkeypatch,
):
    driver = _SlowPostClickNavigationDriver(eventually_ready=False)
    monkeypatch.setattr("jobagent.platforms.zhilian.collect.time.sleep", lambda _: None)
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_NAVIGATION_TIMEOUT_SECONDS",
        0,
        raising=False,
    )

    result = ZhilianReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "cities.json",
    ).collect(query="AI产品经理", city="深圳", limit=5, wait_seconds=0)
    payload = result.to_payload()

    assert result.error == "zhilian_city_evidence_pending"
    assert payload["retryable"] is True
    assert payload["no_charge"] is True
    assert payload["diagnostics"]["title_city_match"] is True
    assert payload["diagnostics"]["navigation_elapsed_ms"] == 50509


def test_collector_waits_past_default_window_for_slow_loading_page(tmp_path, monkeypatch):
    driver = _SlowLoadingShenzhenDriver()
    monkeypatch.setattr("jobagent.platforms.zhilian.collect.time.sleep", lambda _: None)

    result = ZhilianReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "cities.json",
    ).collect(query="AI产品经理", city="深圳", limit=5, wait_seconds=8)

    assert result.ok is True
    assert driver.keyword_probe_count == 3
    assert result.jobs[0].city == "深圳"


def test_collector_reports_unknown_not_login_when_loading_never_settles(tmp_path, monkeypatch):
    driver = _SlowLoadingShenzhenDriver(settle=False)
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_PAGE_SETTLE_TIMEOUT_SECONDS",
        0,
    )

    result = ZhilianReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "cities.json",
    ).collect(query="AI产品经理", city="深圳", limit=5, wait_seconds=0)

    assert result.ok is False
    assert result.error == "zhilian_page_state_unknown"
    assert result.error != "zhilian_login_required"


class _SearchPageWithoutAccountUiDriver(_DynamicCityDriver):
    def __init__(
        self,
        *,
        strong_login: bool = False,
        cards: bool = True,
        homepage_logged_in: bool = False,
    ):
        super().__init__()
        self.strong_login = strong_login
        self.cards = cards
        self.homepage_logged_in = homepage_logged_in

    def _exec_js(self, script: str):
        if "zhilian_keyword_search" in script:
            result = super()._exec_js(script)
            if self.homepage_logged_in:
                result.update(
                    {
                        "readyState": "complete",
                        "sessionEvidenceVersion": 2,
                        "sessionState": "logged_in",
                        "weakLoginEvidence": ["visible_login_control"],
                        "strongLoginEvidence": [],
                        "accountEvidence": ["account_navigation"],
                        "strongAccountEvidence": ["resume_management"],
                    }
                )
            return result
        if "zhilian_city_filter" in script:
            return {
                "ok": False,
                "mode": "zhilian_city_filter",
                "error": "zhilian_city_option_not_found",
                "url": "https://www.zhaopin.com/jobs?jl=765&kw=产品经理",
                "title": "深圳热门职位招聘 - 智联招聘",
                "readyState": "complete",
                "sessionEvidenceVersion": 2,
                "sessionState": "login_required" if self.strong_login else "unknown",
                "weakLoginEvidence": ["visible_login_control"],
                "strongLoginEvidence": (
                    ["visible_credential_form"] if self.strong_login else []
                ),
                "searchPageEvidence": ["search_route", "search_title"],
            }
        cards = [
            {
                "positionId": "SZ-READY-1",
                "jobTitle": "产品经理",
                "companyName": "深圳示例科技",
                "cityName": "深圳",
                "jobUrl": "https://www.zhaopin.com/jobdetail/SZ-READY-1.htm",
            }
        ] if self.cards else []
        return {
            "ok": True,
            "url": "https://www.zhaopin.com/jobs?jl=765&kw=产品经理",
            "title": "深圳热门职位招聘 2026年热门职位招聘信息-智联招聘",
            "readyState": "complete",
            "sessionEvidenceVersion": 2,
            "sessionState": "login_required" if self.strong_login else "unknown",
            "weakLoginEvidence": ["visible_login_control"],
            "strongLoginEvidence": (
                ["visible_credential_form"] if self.strong_login else []
            ),
            "searchPageEvidence": ["search_route", "search_title"],
            "searchKeyword": "产品经理",
            "visibleCity": "深圳",
            "candidateCount": len(cards),
            "selectorVersion": "test-selector-v3",
            "jobSurfaceCount": 8 if self.cards else 0,
            "jobActionCount": 8 if self.cards else 0,
            "noResults": False,
            "cards": cards,
        }


def test_recent_login_verification_bridges_search_page_without_account_ui(tmp_path):
    result = ZhilianReadOnlyCollector(
        driver=_SearchPageWithoutAccountUiDriver(),
        city_cache_path=tmp_path / "cities.json",
        login_verification=_recent_login_verification(),
    ).collect(query="产品经理", city="深圳", limit=5, wait_seconds=1)

    assert result.ok is True
    assert result.jobs[0].name == "产品经理"
    assert result.snapshot["sessionContinuity"]["used"] is True


def test_current_homepage_login_evidence_bridges_legacy_round_without_receipt(tmp_path):
    result = ZhilianReadOnlyCollector(
        driver=_SearchPageWithoutAccountUiDriver(homepage_logged_in=True),
        city_cache_path=tmp_path / "cities.json",
    ).collect(query="产品经理", city="深圳", limit=5, wait_seconds=1)

    assert result.ok is True
    assert result.snapshot["sessionContinuity"] == {
        "used": True,
        "source": "current_collection_homepage",
        "ageSeconds": 0,
    }


def test_strong_login_form_overrides_recent_login_verification(tmp_path):
    result = ZhilianReadOnlyCollector(
        driver=_SearchPageWithoutAccountUiDriver(strong_login=True),
        city_cache_path=tmp_path / "cities.json",
        login_verification=_recent_login_verification(),
    ).collect(query="产品经理", city="深圳", limit=5, wait_seconds=1)

    assert result.ok is False
    assert result.error == "zhilian_login_required"


def test_ready_search_page_with_zero_parsed_cards_reports_selector_failure(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_PAGE_SETTLE_TIMEOUT_SECONDS",
        0,
    )
    result = ZhilianReadOnlyCollector(
        driver=_SearchPageWithoutAccountUiDriver(cards=False),
        city_cache_path=tmp_path / "cities.json",
        login_verification=_recent_login_verification(),
    ).collect(query="产品经理", city="深圳", limit=5, wait_seconds=0)

    payload = result.to_payload()
    assert result.ok is False
    assert result.error == "zhilian_job_cards_not_found"
    assert payload["retryable"] is True
    assert payload["no_charge"] is True
    assert payload["diagnostics"]["selector_version"]
    assert payload["diagnostics"]["ready_state"] == "complete"


def test_collector_learns_changed_shenzhen_code_after_city_dom_change(tmp_path):
    cache = tmp_path / "cities.json"
    driver = _ChangedShenzhenCityDriver()

    result = ZhilianReadOnlyCollector(driver=driver, city_cache_path=cache).collect(
        query="AI产品经理", city="深圳", limit=5, wait_seconds=1
    )

    assert result.ok is True
    assert result.jobs[0].city == "深圳"
    assert driver.city_filter_calls >= 1
    assert ZhilianCityResolver(cache).lookup("深圳") == ("765", "verified_cache")
    cached = json.loads(cache.read_text(encoding="utf-8"))["cities"]["深圳"]
    assert cached["verification_source"] == "dynamic_multi_source"
    assert cached["evidence_sources"] == ["job_cards", "page_title", "visible_city"]


def test_collector_fails_closed_after_city_dom_change_when_only_url_code_is_known(tmp_path):
    result = ZhilianReadOnlyCollector(
        driver=_ChangedShenzhenCityDriver(corroborated=False),
        city_cache_path=tmp_path / "cities.json",
    ).collect(query="AI产品经理", city="深圳", limit=5, wait_seconds=1)

    assert result.ok is False
    assert result.error == "zhilian_city_selector_unavailable"
    assert result.to_payload()["diagnostics"]["evidence_status"] == "evidence_pending"
    assert result.jobs == []


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
