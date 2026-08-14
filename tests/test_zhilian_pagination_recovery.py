"""Regression coverage for bounded Zhilian query pagination."""

from __future__ import annotations

import json
from contextlib import nullcontext

import pytest

from jobagent.domain.models import Job
from jobagent.platforms import discovery
from jobagent.platforms.zhilian.collect import (
    ZhilianReadOnlyCollector,
    _city_bootstrap_destination_verified,
    _query_page_exhausted,
    _safe_search_action_receipt,
    _safe_zhilian_city_navigation_url,
    parse_zhilian_snapshot_jobs,
    zhilian_candidate_collection_completed,
)
from jobagent.platforms.zhilian.selectors import (
    ZHILIAN_SELECTOR_VERSION,
    build_zhilian_city_filter_script,
    build_zhilian_pagination_script,
    build_zhilian_snapshot_script,
)


def _login_verification() -> dict:
    return {
        "valid": True,
        "source": "recent_login_check",
        "platform": "zhilian",
        "round_id": "round-1",
        "browser_session_id": "local-cdp-19222",
        "age_seconds": 10,
    }


def _snapshot(
    query: str,
    *,
    job_id: str | None = None,
    no_results: bool = False,
    available_pages: list[int] | None = None,
    has_next_page: bool | None = None,
    current_page: int | None = None,
) -> dict:
    cards = []
    if job_id:
        cards.append(
            {
                "positionId": job_id,
                "jobTitle": query,
                "companyName": f"{job_id} Company",
                "cityName": "",
                "jobUrl": f"https://www.zhaopin.com/jobdetail/{job_id}.htm",
            }
        )
    payload = {
        "ok": True,
        "url": "https://www.zhaopin.com/jobs?jl=dynamic&kw=01300K004004338VHKHKTEG",
        "title": "热门职位招聘 2026年热门职位招聘信息-智联招聘",
        "readyState": "complete",
        "sessionState": "page_ready",
        "loginRequired": False,
        "searchPageEvidence": [
            "search_route",
            "search_input",
            "no_results" if no_results else "job_surface",
        ],
        "searchKeyword": query,
        "searchKeywordVisible": True,
        "candidateCount": len(cards),
        "jobSurfaceCount": len(cards),
        "jobActionCount": len(cards),
        "noResults": no_results,
        "cards": cards,
    }
    if available_pages is not None:
        payload.update(
            {
                "paginationDetected": True,
                "availablePages": available_pages,
                "currentPage": (
                    current_page
                    if current_page is not None
                    else (available_pages[0] if available_pages else 1)
                ),
                "hasNextPage": bool(has_next_page),
            }
        )
    return payload


class _PagingDriver:
    def __init__(self, query: str, snapshots: list[dict], *, page_two_available: bool = False):
        self.query = query
        self.snapshots = list(snapshots)
        self.page_two_available = page_two_available
        self.pagination_calls: list[int] = []
        self.open_calls = 0

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        self.open_calls += 1
        return {"ok": True, "url": url}

    def _click_at(self, _x, _y):
        return None

    def dismiss_javascript_dialog(self):
        return {"ok": True, "dismissed": False}

    def _exec_js(self, script: str):
        if "zhilian_keyword_search" in script:
            return {
                "ok": True,
                "mode": "zhilian_keyword_search",
                "observedValue": self.query,
                "readyState": "complete",
                "sessionState": "page_ready",
                "clickPoint": {"x": 120, "y": 80},
            }
        if "zhilian_search_transition" in script:
            return {
                "ok": True,
                "mode": "zhilian_search_transition",
                "url": "https://www.zhaopin.com/jobs?jl=dynamic&kw=01300K004004338VHKHKTEG",
                "title": "热门职位招聘 2026年热门职位招聘信息-智联招聘",
                "readyState": "complete",
                "observedKeyword": self.query,
                "searchPageEvidence": ["search_route", "search_input"],
            }
        if "zhilian_pagination" in script:
            self.pagination_calls.append(2)
            if self.page_two_available:
                return {
                    "ok": True,
                    "mode": "zhilian_pagination",
                    "page": 2,
                    "action": "select_page",
                    "clickPoint": {"x": 500, "y": 900},
                }
            return {
                "ok": False,
                "mode": "zhilian_pagination",
                "error": "zhilian_page_option_not_found",
                "page": 2,
            }
        if not self.snapshots:
            raise AssertionError("unexpected extra Zhilian snapshot")
        return self.snapshots.pop(0)


class _AuthenticatedCityHomepageBootstrapDriver:
    def __init__(self):
        self.current_query = ""
        self.keyword_submissions: list[str] = []
        self.city_homepage_navigations: list[str] = []
        self.pagination_calls: list[int] = []
        self.page = "entry"
        self.submissions_for_query = 0

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        del wait_seconds
        if url == "https://www.zhaopin.com/":
            self.page = "entry"
            self.submissions_for_query = 0
        elif url == "https://www.zhaopin.com/shenzhen/":
            self.page = "city_homepage"
            self.city_homepage_navigations.append(url)
        return {"ok": True, "url": url}

    def _click_at(self, _x, _y):
        self.submissions_for_query += 1

    def dismiss_javascript_dialog(self):
        return {"ok": True, "dismissed": False}

    def _exec_js(self, script: str):
        if "zhilian_keyword_search" in script:
            self.current_query = (
                "数据产品经理" if "数据产品经理" in script else "产品经理"
            )
            self.keyword_submissions.append(self.current_query)
            return {
                "ok": True,
                "mode": "zhilian_keyword_search",
                "observedValue": self.current_query,
                "readyState": "complete",
                "sessionState": "logged_in",
                "accountEvidence": ["account_navigation"],
                "strongAccountEvidence": ["resume_management"],
                "clickPoint": {"x": 120, "y": 80},
            }
        if "zhilian_search_transition" in script:
            if self.page == "city_homepage" and self.submissions_for_query >= 2:
                self.page = "search_results"
                return {
                    "ok": True,
                    "mode": "zhilian_search_transition",
                    "url": "https://www.zhaopin.com/sou/jl765/kwOPAQUE/p1",
                    "title": "深圳热门职位招聘 2026年热门职位招聘信息-智联招聘",
                    "readyState": "complete",
                    "sessionState": "page_ready",
                    "observedKeyword": self.current_query,
                    "observedCityCode": "765",
                    "titleCityMatch": True,
                    "searchPageEvidence": ["search_route", "search_input"],
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
                "sessionState": "logged_in",
                "observedKeyword": self.current_query,
                "observedCityCode": None,
                "candidateCityCode": None,
                "titleCityMatch": True,
                "searchPageEvidence": ["search_input", "job_surface"],
            }
        if "zhilian_city_filter" in script:
            if self.page == "entry":
                return {
                    "ok": False,
                    "mode": "zhilian_city_filter",
                    "error": "zhilian_city_route_navigation_required",
                    "action": "navigate_city_homepage",
                    "candidateNavigationUrl": "https://www.zhaopin.com/shenzhen/",
                    "candidateNavigationSource": "readable_city_anchor:href",
                    "titleCityMatch": True,
                    "url": "https://www.zhaopin.com/",
                    "title": "深圳招聘网_深圳人才网_2026年深圳最新招聘信息-智联招聘",
                    "readyState": "complete",
                    "sessionState": "logged_in",
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
        if "zhilian_pagination" in script:
            self.pagination_calls.append(2)
            raise AssertionError("a verified single-page query must not request page 2")
        cards = []
        no_results = self.current_query == "产品经理"
        if not no_results:
            cards = [
                {
                    "positionId": "JOB-DATA-1",
                    "jobTitle": "数据产品经理",
                    "companyName": "深圳示例科技",
                    "cityName": "深圳",
                    "jobUrl": "https://www.zhaopin.com/jobdetail/JOB-DATA-1.htm",
                }
            ]
        return {
            "ok": True,
            "url": "https://www.zhaopin.com/sou/jl765/kwOPAQUE/p1",
            "title": "深圳热门职位招聘 2026年热门职位招聘信息-智联招聘",
            "readyState": "complete",
            "sessionState": "page_ready",
            "searchPageEvidence": [
                "search_route",
                "search_input",
                "no_results" if no_results else "job_surface",
            ],
            "searchKeyword": self.current_query,
            "visibleCity": "深圳",
            "candidateCityCode": "765",
            "candidateCount": len(cards),
            "jobSurfaceCount": len(cards),
            "noResults": no_results,
            "paginationDetected": True,
            "availablePages": [1],
            "currentPage": 1,
            "hasNextPage": False,
            "cards": cards,
        }


class _NativeEnterCDP:
    def __init__(self, driver):
        self.driver = driver

    def send(self, method: str, params: dict | None = None):
        params = params or {}
        if (
            method == "Input.dispatchKeyEvent"
            and params.get("type") == "keyUp"
            and params.get("key") == "Enter"
        ):
            self.driver.page = "search_results"
            self.driver.native_enter_queries.append(self.driver.current_query)
        return {}


class _SlugCityHomepageRequiresNativeSearchDriver:
    """Model the city homepage shape reported by the v0.5.17 customer run."""

    def __init__(self):
        self.cdp = _NativeEnterCDP(self)
        self.current_query = ""
        self.page = "entry"
        self.keyword_scripts: list[str] = []
        self.native_enter_queries: list[str] = []
        self.city_homepage_navigations: list[str] = []
        self.city_filter_calls_after_bootstrap = 0
        self.snapshot_pages: list[str] = []

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        del wait_seconds
        if url == "https://www.zhaopin.com/":
            self.page = "entry"
        elif url == "https://www.zhaopin.com/city-alpha/":
            self.page = "city_homepage"
            self.city_homepage_navigations.append(url)
        return {"ok": True, "url": url}

    def _click_at(self, _x, _y):
        # The current city homepage ignores the button click. It transitions
        # only after a native Enter event reaches the focused search input.
        return None

    def dismiss_javascript_dialog(self):
        return {"ok": True, "dismissed": False}

    def _exec_js(self, script: str):
        if "zhilian_keyword_search" in script:
            self.current_query = "第二查询" if "第二查询" in script else "第一查询"
            self.keyword_scripts.append(self.current_query)
            return {
                "ok": True,
                "mode": "zhilian_keyword_search",
                "observedValue": self.current_query,
                "readyState": "complete",
                "sessionState": "logged_in",
                "accountEvidence": ["account_navigation"],
                "strongAccountEvidence": ["resume_management"],
                "inputClickPoint": {"x": 280, "y": 96},
                "clickPoint": {"x": 520, "y": 96},
            }
        if "zhilian_search_transition" in script:
            if self.page == "search_results":
                return {
                    "ok": True,
                    "mode": "zhilian_search_transition",
                    "url": "https://www.zhaopin.com/jobs?kw=OPAQUE_PLATFORM_STATE",
                    "title": "目标城热门职位招聘 2026年热门职位招聘信息-智联招聘",
                    "readyState": "complete",
                    "sessionState": "page_ready",
                    "observedKeyword": self.current_query,
                    "observedCityCode": None,
                    "titleCityMatch": True,
                    "searchPageEvidence": ["search_route", "search_input", "job_surface"],
                }
            return {
                "ok": True,
                "mode": "zhilian_search_transition",
                "url": (
                    "https://www.zhaopin.com/city-alpha/"
                    if self.page == "city_homepage"
                    else "https://www.zhaopin.com/"
                ),
                "title": "目标城招聘网_目标城人才网_2026年最新招聘信息-智联招聘",
                "readyState": "complete",
                "sessionState": "logged_in",
                "observedKeyword": self.current_query,
                "observedCityCode": None,
                "candidateCityCode": None,
                "titleCityMatch": True,
                "searchPageEvidence": ["search_input", "job_surface"],
            }
        if "zhilian_city_filter" in script:
            if self.page == "entry":
                return {
                    "ok": False,
                    "mode": "zhilian_city_filter",
                    "error": "zhilian_city_route_navigation_required",
                    "action": "navigate_city_homepage",
                    "candidateNavigationUrl": "https://www.zhaopin.com/city-alpha/",
                    "candidateNavigationSource": "readable_city_anchor:href",
                    "url": "https://www.zhaopin.com/",
                    "title": "目标城招聘网_目标城人才网_2026年最新招聘信息-智联招聘",
                    "readyState": "complete",
                    "sessionState": "logged_in",
                }
            self.city_filter_calls_after_bootstrap += 1
            return {
                "ok": False,
                "mode": "zhilian_city_filter",
                "error": "zhilian_city_option_not_found",
                "url": "https://www.zhaopin.com/city-alpha/",
                "title": "目标城招聘网_目标城人才网_2026年最新招聘信息-智联招聘",
                "readyState": "complete",
                "sessionState": "logged_in",
            }
        if "zhilian_pagination" in script:
            raise AssertionError("each verified query is a single result page")

        self.snapshot_pages.append(self.page)
        if self.page != "search_results":
            return {
                "ok": True,
                "url": "https://www.zhaopin.com/city-alpha/",
                "title": "目标城招聘网_目标城人才网_2026年最新招聘信息-智联招聘",
                "readyState": "complete",
                "sessionState": "logged_in",
                "searchPageEvidence": ["search_input", "job_surface"],
                "searchKeyword": self.current_query,
                "visibleCity": "目标城",
                "candidateCount": 1,
                "jobSurfaceCount": 1,
                "noResults": False,
                "cards": [
                    {
                        "positionId": "RECOMMENDATION-ONLY",
                        "jobTitle": "首页推荐岗位",
                        "companyName": "推荐公司",
                        "cityName": "目标城",
                        "jobUrl": "https://www.zhaopin.com/jobdetail/RECOMMENDATION-ONLY.htm",
                    }
                ],
            }
        job_id = "JOB-SECOND" if self.current_query == "第二查询" else "JOB-FIRST"
        return {
            "ok": True,
            "url": "https://www.zhaopin.com/jobs?kw=OPAQUE_PLATFORM_STATE",
            "title": "目标城热门职位招聘 2026年热门职位招聘信息-智联招聘",
            "readyState": "complete",
            "sessionState": "page_ready",
            "searchPageEvidence": ["search_route", "search_input", "job_surface"],
            "searchKeyword": self.current_query,
            "visibleCity": "目标城",
            "candidateCityCode": None,
            "candidateCount": 1,
            "jobSurfaceCount": 1,
            "noResults": False,
            "paginationDetected": True,
            "availablePages": [1],
            "currentPage": 1,
            "hasNextPage": False,
            "cards": [
                {
                    "positionId": job_id,
                    "jobTitle": self.current_query,
                    "companyName": f"{self.current_query}公司",
                    "cityName": "目标城",
                    "jobUrl": f"https://www.zhaopin.com/jobdetail/{job_id}.htm",
                }
            ],
        }


class _NoTransitionCDP:
    def __init__(self, driver):
        self.driver = driver

    def send(self, method: str, params: dict | None = None):
        params = params or {}
        if (
            method == "Input.dispatchKeyEvent"
            and params.get("type") == "keyUp"
            and params.get("key") == "Enter"
        ):
            self.driver.native_enter_attempts += 1
        return {}


class _SlugCityHomepageNoTransitionDriver(
    _SlugCityHomepageRequiresNativeSearchDriver
):
    """Model a real city page that accepts input but swallows every submit path."""

    def __init__(self):
        super().__init__()
        self.cdp = _NoTransitionCDP(self)
        self.native_enter_attempts = 0
        self.form_submit_attempts = 0

    def _exec_js(self, script: str):
        if "zhilian_search_form_submit" in script:
            self.form_submit_attempts += 1
            return {
                "ok": True,
                "mode": "zhilian_search_form_submit",
                "formSubmitInvoked": True,
                "url": "https://www.zhaopin.com/city-alpha/",
                "title": "目标城招聘网_目标城人才网_2026年最新招聘信息-智联招聘",
                "observedKeyword": self.current_query,
            }
        result = super()._exec_js(script)
        if "zhilian_keyword_search" in script and result.get("ok"):
            result.update(
                {
                    "urlBefore": (
                        "https://www.zhaopin.com/city-alpha/"
                        if self.page == "city_homepage"
                        else "https://www.zhaopin.com/"
                    ),
                    "titleBefore": "目标城招聘网_目标城人才网_2026年最新招聘信息-智联招聘",
                    "historyLength": 4,
                    "documentTimeOriginMs": 1000,
                    "inputCandidateType": "input:search",
                    "buttonCandidateType": "button:search",
                    "formCandidateType": "form",
                    "focusedElementType": "input:search",
                }
            )
        if "zhilian_search_transition" in script:
            result.update(
                {
                    "historyLength": 4,
                    "documentTimeOriginMs": 1000,
                    "focusedElementType": "input:search",
                    "actionSignals": {
                        "buttonClicks": 1,
                        "inputEnterKeyups": self.native_enter_attempts,
                        "formSubmits": self.form_submit_attempts,
                    },
                }
            )
        return result


class _SlugCityHomepageInputResetDriver(_SlugCityHomepageNoTransitionDriver):
    def __init__(self):
        super().__init__()
        self.city_input_prepared = False

    def _exec_js(self, script: str):
        if "zhilian_keyword_search" in script and self.page == "city_homepage":
            self.city_input_prepared = True
        result = super()._exec_js(script)
        if (
            "zhilian_search_transition" in script
            and self.page == "city_homepage"
            and self.city_input_prepared
        ):
            result["observedKeyword"] = ""
        return result


class _SlugCityHomepageNoSubmitControlDriver(_SlugCityHomepageNoTransitionDriver):
    def _exec_js(self, script: str):
        if "zhilian_search_form_submit" in script:
            return {
                "ok": False,
                "mode": "zhilian_search_form_submit",
                "error": "zhilian_search_submit_control_not_activated",
                "formSubmitInvoked": False,
            }
        result = super()._exec_js(script)
        if "zhilian_keyword_search" in script and self.page == "city_homepage":
            result["clickPoint"] = None
            result["inputClickPoint"] = None
            result["buttonCandidateType"] = ""
            result["formCandidateType"] = ""
        return result


class _SlugCityHomepageFormSubmitDriver(_SlugCityHomepageNoTransitionDriver):
    def _exec_js(self, script: str):
        if "zhilian_search_form_submit" in script:
            self.form_submit_attempts += 1
            self.page = "search_results"
            return {
                "ok": True,
                "mode": "zhilian_search_form_submit",
                "formSubmitInvoked": True,
                "observedKeyword": self.current_query,
            }
        result = super()._exec_js(script)
        if "zhilian_keyword_search" in script and self.page == "city_homepage":
            result["clickPoint"] = None
            result["inputClickPoint"] = None
            result["buttonCandidateType"] = ""
        return result


class _LiveVueSearchAnchorDriver(_SlugCityHomepageNoTransitionDriver):
    """Model the live city-page Vue anchor observed by the v0.5.19 receipt."""

    def __init__(self, *, transition_method: str | None):
        super().__init__()
        self.transition_method = transition_method
        self.native_pointer_attempts = 0
        self.dom_click_attempts = 0
        self.official_destination_attempts = 0
        self.target_capture_attempts = 0
        self.target_adoption_attempts = 0
        self._exact_pointer_armed = False

    def _click_at(self, _x, _y):
        if self._exact_pointer_armed:
            self.native_pointer_attempts += 1
            self._exact_pointer_armed = False

    def capture_platform_target_state(self, platform: str):
        assert platform == "zhilian"
        self.target_capture_attempts += 1
        return {"platform": platform, "target_ids": ["target-old"]}

    def adopt_platform_target_transition(
        self,
        _before,
        *,
        platform: str,
        wait_seconds: float,
    ):
        assert platform == "zhilian"
        assert wait_seconds >= 0
        self.target_adoption_attempts += 1
        if self.transition_method == "new_target":
            self.page = "search_results"
            return {
                "ok": True,
                "outcome": "new_target_adopted",
                "new_target_count": 1,
                "previous_target_closed": True,
            }
        return {
            "ok": False,
            "outcome": "no_search_target_observed",
            "new_target_count": 0,
            "previous_target_closed": False,
        }

    def _exec_js(self, script: str):
        if "zhilian_search_control_activation" in script:
            if 'activationMethod = "native_pointer"' in script:
                self._exact_pointer_armed = True
                return {
                    "ok": True,
                    "mode": "zhilian_search_control_activation",
                    "activationMethod": "native_pointer",
                    "controlActivated": False,
                    "searchDestinationReady": True,
                    "searchDestinationKind": "official_search_with_opaque_keyword",
                    "buttonCandidateType": "a:search-anchor",
                    "buttonSameContainer": True,
                    "clickPoint": {"x": 520, "y": 96},
                }
            if 'activationMethod = "dom_click"' in script:
                self.dom_click_attempts += 1
                if self.transition_method == "dom_click":
                    self.page = "search_results"
                return {
                    "ok": True,
                    "mode": "zhilian_search_control_activation",
                    "activationMethod": "dom_click",
                    "controlActivated": True,
                    "searchDestinationReady": True,
                    "searchDestinationKind": "official_search_with_opaque_keyword",
                    "buttonCandidateType": "a:search-anchor",
                    "buttonSameContainer": True,
                }
            if 'activationMethod = "official_destination"' in script:
                self.official_destination_attempts += 1
                if self.transition_method == "official_destination":
                    self.page = "search_results"
                return {
                    "ok": True,
                    "mode": "zhilian_search_control_activation",
                    "activationMethod": "official_destination",
                    "controlActivated": True,
                    "searchDestinationReady": True,
                    "searchDestinationKind": "official_search_with_opaque_keyword",
                    "buttonCandidateType": "a:search-anchor",
                    "buttonSameContainer": True,
                }
        if "zhilian_search_form_submit" in script:
            return {
                "ok": False,
                "mode": "zhilian_search_form_submit",
                "error": "zhilian_search_submit_control_not_activated",
                "formSubmitInvoked": False,
            }
        result = super()._exec_js(script)
        if "zhilian_keyword_search" in script and result.get("ok"):
            result.update(
                {
                    "inputCandidateType": "input:text",
                    "buttonCandidateType": "a:search-anchor",
                    "formCandidateType": "",
                    "searchDestinationReady": True,
                    "searchDestinationKind": "official_search_with_opaque_keyword",
                    "targetNormalized": False,
                    "buttonSameContainer": True,
                }
            )
        if "zhilian_search_transition" in script:
            result.update(
                {
                    "searchDestinationReady": self.page == "city_homepage",
                    "searchDestinationKind": (
                        "official_search_with_opaque_keyword"
                        if self.page == "city_homepage"
                        else "search_results"
                    ),
                    "searchControlTarget": "_blank" if self.page == "city_homepage" else "",
                }
            )
        return result


class _SlugCityResultConflictDriver(_SlugCityHomepageRequiresNativeSearchDriver):
    def _exec_js(self, script: str):
        result = super()._exec_js(script)
        if (
            self.page == "search_results"
            and "zhilian_search_transition" not in script
            and "zhilian_keyword_search" not in script
            and "zhilian_city_filter" not in script
            and "zhilian_pagination" not in script
        ):
            result["visibleCity"] = "其他城"
            result["cards"][0]["cityName"] = "其他城"
        return result


def test_explicit_empty_first_page_does_not_attempt_second_page(tmp_path):
    driver = _PagingDriver("产品经理", [_snapshot("产品经理", no_results=True)])

    result = ZhilianReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "cities.json",
        login_verification=_login_verification(),
    ).collect(query="产品经理", pages=2, wait_seconds=0, page_delay=0)

    assert result.ok is True
    assert result.jobs == []
    assert result.snapshot["terminationReason"] == "no_results"
    assert result.snapshot["paginationExhausted"] is True
    assert driver.pagination_calls == []
    assert driver.open_calls == 1


def test_verified_single_page_with_jobs_does_not_attempt_second_page(tmp_path):
    driver = _PagingDriver(
        "产品经理",
        [
            _snapshot(
                "产品经理",
                job_id="JOB-1",
                available_pages=[1],
                has_next_page=False,
            )
        ],
    )

    result = ZhilianReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "cities.json",
        login_verification=_login_verification(),
    ).collect(query="产品经理", pages=2, wait_seconds=0, page_delay=0)

    assert result.ok is True
    assert [job.name for job in result.jobs] == ["产品经理"]
    assert result.snapshot["terminationReason"] == "pagination_exhausted"
    assert result.snapshot["paginationExhausted"] is True
    assert driver.pagination_calls == []
    assert driver.open_calls == 1


def test_verified_result_page_without_pagination_does_not_probe_page_two(tmp_path):
    driver = _PagingDriver(
        "产品经理",
        [_snapshot("产品经理", job_id="JOB-1")],
    )

    result = ZhilianReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "cities.json",
        login_verification=_login_verification(),
    ).collect(query="产品经理", pages=2, wait_seconds=0, page_delay=0)

    assert result.ok is True
    assert [job.url for job in result.jobs] == [
        "https://www.zhaopin.com/jobdetail/JOB-1.htm"
    ]
    assert result.snapshot["paginationExhausted"] is True
    assert result.snapshot["terminationReason"] == "pagination_exhausted"
    assert driver.pagination_calls == []
    assert driver.open_calls == 1


def test_snapshot_parser_and_page_boundary_share_production_logic():
    snapshot = _snapshot("产品经理", job_id="JOB-1")

    jobs = parse_zhilian_snapshot_jobs(
        snapshot,
        city_name="深圳",
        limit=5,
    )

    assert [job.url for job in jobs] == [
        "https://www.zhaopin.com/jobdetail/JOB-1.htm"
    ]
    assert _query_page_exhausted(snapshot, current_page=1) is True


def test_one_page_budget_never_probes_next_page_when_more_pages_exist(tmp_path):
    driver = _PagingDriver(
        "产品经理",
        [
            _snapshot(
                "产品经理",
                job_id="JOB-1",
                available_pages=[1, 2],
                has_next_page=True,
            )
        ],
        page_two_available=True,
    )

    result = ZhilianReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "cities.json",
        login_verification=_login_verification(),
    ).collect(query="产品经理", pages=1, wait_seconds=0, page_delay=0)

    assert result.ok is True
    assert [job.url for job in result.jobs] == [
        "https://www.zhaopin.com/jobdetail/JOB-1.htm"
    ]
    assert driver.pagination_calls == []
    assert driver.open_calls == 1
    assert result.snapshot["terminationReason"] == "page_limit_reached"
    assert result.snapshot["pageLimitReached"] is True
    assert result.snapshot.get("paginationExhausted") is not True


@pytest.mark.parametrize(
    "reason",
    [
        "candidate_limit_reached",
        "page_limit_reached",
        "pagination_exhausted",
    ],
)
def test_non_empty_collection_accepts_each_safe_termination_reason(reason):
    assert zhilian_candidate_collection_completed(reason) is True


@pytest.mark.parametrize("reason", ["", "no_results", "unknown"])
def test_non_empty_collection_rejects_empty_or_unknown_termination(reason):
    assert zhilian_candidate_collection_completed(reason) is False


def test_verified_second_page_is_collected_when_available(tmp_path, monkeypatch):
    driver = _PagingDriver(
        "产品经理",
        [
            _snapshot(
                "产品经理",
                job_id="JOB-1",
                available_pages=[1, 2],
                has_next_page=True,
            ),
            _snapshot(
                "产品经理",
                job_id="JOB-2",
                available_pages=[1, 2],
                has_next_page=False,
                current_page=2,
            ),
        ],
        page_two_available=True,
    )
    monkeypatch.setattr("jobagent.platforms.zhilian.collect.time.sleep", lambda _seconds: None)

    result = ZhilianReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "cities.json",
        login_verification=_login_verification(),
    ).collect(query="产品经理", pages=2, wait_seconds=0, page_delay=0)

    assert result.ok is True
    assert [job.url for job in result.jobs] == [
        "https://www.zhaopin.com/jobdetail/JOB-1.htm",
        "https://www.zhaopin.com/jobdetail/JOB-2.htm",
    ]
    assert driver.pagination_calls == [2]
    assert result.snapshot["terminationReason"] == "pagination_exhausted"
    assert result.snapshot["paginationExhausted"] is True


def test_search_plan_emits_sanitized_query_page_progress(monkeypatch):
    events: list[tuple[str, dict]] = []

    def collect_page(
        _platform,
        query,
        page,
        _limit,
        _driver,
        _wait_seconds,
        _login_verification,
        _progress_callback=None,
    ):
        assert query["keyword"] == "私密可读关键词"
        return discovery.CollectedPage(
            jobs=[
                Job(
                    name="公开职位标题",
                    salary="",
                    company="Example",
                    city="深圳",
                    url="https://www.zhaopin.com/jobdetail/JOB-1.htm",
                    platform="zhilian",
                    raw_data={"positionId": "JOB-1"},
                )
            ],
            exhausted=True,
        )

    monkeypatch.setattr(discovery, "_collect_web_platform", collect_page)
    plan = {
        "platform": "zhilian",
        "candidate_limit": 100,
        "queries": [
            {"keyword": "私密可读关键词", "city": "深圳", "page_limit": 1}
        ],
    }

    candidates = discovery.collect_from_search_plan(
        plan,
        driver=object(),
        page_delay=0,
        login_verification=_login_verification(),
        progress_callback=lambda stage, **details: events.append((stage, details)),
    )

    assert [candidate["id"] for candidate in candidates] == ["JOB-1"]
    assert [stage for stage, _details in events] == [
        "collection_query_page_started",
        "collection_query_page_completed",
    ]
    assert events[0][1] == {
        "platform": "zhilian",
        "query_index": 1,
        "query_count": 1,
        "page": 1,
        "page_limit": 1,
    }
    assert events[1][1]["candidate_count"] == 1
    assert all(
        "query" not in details and "keyword" not in details
        for _, details in events
    )
    assert "私密可读关键词" not in json.dumps(events, ensure_ascii=False)


def test_round_robin_continues_after_first_query_is_explicitly_empty(monkeypatch):
    calls: list[tuple[str, int]] = []

    def collect_page(
        platform,
        query,
        page,
        _limit,
        _driver,
        _wait_seconds,
        _login_verification,
    ):
        assert platform == "zhilian"
        calls.append((query["keyword"], page))
        if page > 1:
            pytest.fail("an exhausted query must not request page 2")
        if query["keyword"] == "产品经理":
            return []
        return [
            Job(
                name="数据产品经理",
                salary="20-30K",
                company="Example",
                city="深圳",
                url="https://www.zhaopin.com/jobdetail/JOB-2.htm",
                platform="zhilian",
                raw_data={"positionId": "JOB-2"},
            )
        ]

    monkeypatch.setattr(discovery, "_collect_web_platform", collect_page)
    plan = {
        "platform": "zhilian",
        "candidate_limit": 100,
        "queries": [
            {"keyword": "产品经理", "city": "深圳", "page_limit": 2},
            {"keyword": "数据产品经理", "city": "深圳", "page_limit": 1},
        ],
    }

    candidates = discovery.collect_from_search_plan(
        plan,
        driver=object(),
        page_delay=0,
        login_verification=_login_verification(),
    )

    assert calls == [("产品经理", 1), ("数据产品经理", 1)]
    assert [candidate["id"] for candidate in candidates] == ["JOB-2"]


def test_scheduler_bootstraps_authenticated_city_homepage_and_runs_both_queries(
    monkeypatch,
    tmp_path,
):
    driver = _AuthenticatedCityHomepageBootstrapDriver()
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.city_resolver.APP_DIR",
        tmp_path,
    )
    monkeypatch.setattr("jobagent.platforms.zhilian.collect.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_NAVIGATION_TIMEOUT_SECONDS",
        0,
    )
    plan = {
        "platform": "zhilian",
        "candidate_limit": 100,
        "queries": [
            {"keyword": "产品经理", "city": "深圳", "page_limit": 2},
            {"keyword": "数据产品经理", "city": "深圳", "page_limit": 2},
        ],
    }

    candidates = discovery.collect_from_search_plan(
        plan,
        driver=driver,
        wait_seconds=0,
        page_delay=0,
        login_verification=_login_verification(),
    )

    assert [candidate["id"] for candidate in candidates] == ["JOB-DATA-1"]
    assert driver.keyword_submissions == [
        "产品经理",
        "产品经理",
        "数据产品经理",
        "数据产品经理",
    ]
    assert driver.city_homepage_navigations == [
        "https://www.zhaopin.com/shenzhen/",
        "https://www.zhaopin.com/shenzhen/",
    ]
    assert driver.pagination_calls == []
    cache = json.loads(
        (tmp_path / "metadata" / "zhilian_city_codes.json").read_text(
            encoding="utf-8"
        )
    )
    assert cache["cities"]["深圳"]["code"] == "765"
    assert (
        cache["cities"]["深圳"]["verification_source"]
        == "verified_city_route_with_numeric_code"
    )


def test_scheduler_submits_both_queries_from_verified_city_slug_without_numeric_code(
    monkeypatch,
    tmp_path,
):
    driver = _SlugCityHomepageRequiresNativeSearchDriver()
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.city_resolver.APP_DIR",
        tmp_path,
    )
    monkeypatch.setattr("jobagent.platforms.zhilian.collect.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_NAVIGATION_TIMEOUT_SECONDS",
        0,
    )
    plan = {
        "platform": "zhilian",
        "candidate_limit": 100,
        "queries": [
            {"keyword": "第一查询", "city": "目标城", "page_limit": 1},
            {"keyword": "第二查询", "city": "目标城", "page_limit": 1},
        ],
    }

    candidates = discovery.collect_from_search_plan(
        plan,
        driver=driver,
        wait_seconds=0,
        page_delay=0,
        login_verification=_login_verification(),
    )

    assert [candidate["id"] for candidate in candidates] == ["JOB-FIRST", "JOB-SECOND"]
    assert driver.native_enter_queries == ["第一查询", "第二查询"]
    assert driver.city_homepage_navigations == [
        "https://www.zhaopin.com/city-alpha/",
        "https://www.zhaopin.com/city-alpha/",
    ]
    assert driver.city_filter_calls_after_bootstrap == 0
    assert driver.snapshot_pages == ["search_results", "search_results"]
    assert all(candidate["id"] != "RECOMMENDATION-ONLY" for candidate in candidates)
    assert not (tmp_path / "metadata" / "zhilian_city_codes.json").exists()


def test_verified_city_slug_reports_bounded_search_action_receipt_when_dom_swallows_submit(
    monkeypatch,
    tmp_path,
):
    driver = _SlugCityHomepageNoTransitionDriver()
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.city_resolver.APP_DIR",
        tmp_path,
    )
    monkeypatch.setattr("jobagent.platforms.zhilian.collect.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_NAVIGATION_TIMEOUT_SECONDS",
        0,
    )
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_ACTION_OBSERVE_SECONDS",
        0,
        raising=False,
    )

    result = ZhilianReadOnlyCollector(
        driver=driver,
        login_verification=_login_verification(),
    ).collect(
        query="第一查询",
        city="目标城",
        pages=1,
        wait_seconds=0,
        page_delay=0,
    )

    assert result.ok is False
    assert result.error == "zhilian_search_transition_not_observed"
    payload = result.to_payload()
    assert payload["retryable"] is False
    assert payload["no_charge"] is True
    receipt = payload["diagnostics"]["action_receipt"]
    assert receipt["receipt_version"] == 1
    assert receipt["input_candidate_type"] == "input:search"
    assert receipt["button_candidate_type"] == "button:search"
    assert receipt["form_candidate_type"] == "form"
    assert receipt["final_readable_input_value"] == "第一查询"
    assert receipt["focused_element_type"] == "input:search"
    assert [attempt["method"] for attempt in receipt["attempts"]] == [
        "controlled_input",
        "button_click",
        "input_enter",
        "form_submit",
    ]
    assert all(attempt["transition_observed"] is False for attempt in receipt["attempts"])
    assert receipt["url_changed"] is False
    assert receipt["title_changed"] is False
    assert receipt["history_changed"] is False
    assert receipt["navigation_changed"] is False
    assert receipt["form_submitted"] is True
    assert driver.native_enter_attempts == 1
    assert driver.form_submit_attempts == 1


@pytest.mark.parametrize(
    ("driver_type", "expected_error"),
    [
        (
            _SlugCityHomepageInputResetDriver,
            "zhilian_search_input_not_committed",
        ),
        (
            _SlugCityHomepageNoSubmitControlDriver,
            "zhilian_search_submit_control_not_activated",
        ),
    ],
)
def test_verified_city_slug_returns_exact_terminal_search_action_failure(
    monkeypatch,
    tmp_path,
    driver_type,
    expected_error,
):
    driver = driver_type()
    monkeypatch.setattr("jobagent.platforms.zhilian.city_resolver.APP_DIR", tmp_path)
    monkeypatch.setattr("jobagent.platforms.zhilian.collect.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_NAVIGATION_TIMEOUT_SECONDS",
        0,
    )
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_INPUT_COMMIT_TIMEOUT_SECONDS",
        0,
    )
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_ACTION_OBSERVE_SECONDS",
        0,
    )

    result = ZhilianReadOnlyCollector(
        driver=driver,
        login_verification=_login_verification(),
    ).collect(
        query="第一查询",
        city="目标城",
        pages=1,
        wait_seconds=0,
        page_delay=0,
    )

    assert result.ok is False
    assert result.error == expected_error
    payload = result.to_payload()
    assert payload["retryable"] is False
    assert payload["next_suggested"] == "jobagent browser diagnose --platform zhilian"
    assert payload["diagnostics"]["action_receipt"]["transition_observed"] is False


def test_verified_city_slug_can_fall_back_to_one_bounded_form_submit(
    monkeypatch,
    tmp_path,
):
    driver = _SlugCityHomepageFormSubmitDriver()
    monkeypatch.setattr("jobagent.platforms.zhilian.city_resolver.APP_DIR", tmp_path)
    monkeypatch.setattr("jobagent.platforms.zhilian.collect.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_NAVIGATION_TIMEOUT_SECONDS",
        0,
    )
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_ACTION_OBSERVE_SECONDS",
        0,
    )

    result = ZhilianReadOnlyCollector(
        driver=driver,
        login_verification=_login_verification(),
    ).collect(
        query="第一查询",
        city="目标城",
        pages=1,
        wait_seconds=0,
        page_delay=0,
    )

    assert result.ok is True
    assert [job.raw_data["positionId"] for job in result.jobs] == ["JOB-FIRST"]
    assert driver.native_enter_attempts == 0
    assert driver.form_submit_attempts == 1


def test_live_vue_anchor_requeries_current_control_and_verifies_dom_click_transition(
    monkeypatch,
    tmp_path,
):
    driver = _LiveVueSearchAnchorDriver(transition_method="dom_click")
    monkeypatch.setattr("jobagent.platforms.zhilian.city_resolver.APP_DIR", tmp_path)
    monkeypatch.setattr("jobagent.platforms.zhilian.collect.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_NAVIGATION_TIMEOUT_SECONDS",
        0,
    )
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_ACTION_OBSERVE_SECONDS",
        0,
    )

    result = ZhilianReadOnlyCollector(
        driver=driver,
        login_verification=_login_verification(),
    ).collect(
        query="第一查询",
        city="目标城",
        pages=1,
        wait_seconds=0,
        page_delay=0,
    )

    assert result.ok is True
    assert [job.raw_data["positionId"] for job in result.jobs] == ["JOB-FIRST"]
    assert driver.native_pointer_attempts == 1
    assert driver.dom_click_attempts == 1
    assert driver.official_destination_attempts == 0
    assert driver.native_enter_attempts == 0
    assert driver.form_submit_attempts == 0


def test_live_vue_anchor_adopts_new_cdp_target_before_result_collection(
    monkeypatch,
    tmp_path,
):
    driver = _LiveVueSearchAnchorDriver(transition_method="new_target")
    monkeypatch.setattr("jobagent.platforms.zhilian.city_resolver.APP_DIR", tmp_path)
    monkeypatch.setattr("jobagent.platforms.zhilian.collect.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_NAVIGATION_TIMEOUT_SECONDS",
        0,
    )
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_ACTION_OBSERVE_SECONDS",
        0,
    )

    result = ZhilianReadOnlyCollector(
        driver=driver,
        login_verification=_login_verification(),
    ).collect(
        query="第一查询",
        city="目标城",
        pages=1,
        wait_seconds=0,
        page_delay=0,
    )

    assert result.ok is True
    assert [job.raw_data["positionId"] for job in result.jobs] == ["JOB-FIRST"]
    assert driver.target_capture_attempts == 1
    assert driver.target_adoption_attempts == 1
    assert driver.native_pointer_attempts == 1
    assert driver.dom_click_attempts == 0
    assert driver.official_destination_attempts == 0
    receipt = result.snapshot["keywordSearch"]["searchTransition"][
        "searchActionReceipt"
    ]
    pointer = next(
        item for item in receipt["attempts"] if item["method"] == "exact_anchor_pointer"
    )
    assert pointer["targetOutcome"] == "new_target_adopted"
    assert pointer["newTargetCount"] == 1
    assert pointer["previousTargetClosed"] is True


def test_live_vue_anchor_uses_page_generated_official_destination_once(
    monkeypatch,
    tmp_path,
):
    driver = _LiveVueSearchAnchorDriver(transition_method="official_destination")
    monkeypatch.setattr("jobagent.platforms.zhilian.city_resolver.APP_DIR", tmp_path)
    monkeypatch.setattr("jobagent.platforms.zhilian.collect.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_NAVIGATION_TIMEOUT_SECONDS",
        0,
    )
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_ACTION_OBSERVE_SECONDS",
        0,
    )

    result = ZhilianReadOnlyCollector(
        driver=driver,
        login_verification=_login_verification(),
    ).collect(
        query="第一查询",
        city="目标城",
        pages=1,
        wait_seconds=0,
        page_delay=0,
    )

    assert result.ok is True
    assert [job.raw_data["positionId"] for job in result.jobs] == ["JOB-FIRST"]
    assert driver.native_pointer_attempts == 1
    assert driver.dom_click_attempts == 1
    assert driver.official_destination_attempts == 1
    assert driver.native_enter_attempts == 0
    assert driver.form_submit_attempts == 0


def test_live_vue_anchor_all_bounded_actions_keep_precise_failure_receipt(
    monkeypatch,
    tmp_path,
):
    driver = _LiveVueSearchAnchorDriver(transition_method=None)
    monkeypatch.setattr("jobagent.platforms.zhilian.city_resolver.APP_DIR", tmp_path)
    monkeypatch.setattr("jobagent.platforms.zhilian.collect.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_NAVIGATION_TIMEOUT_SECONDS",
        0,
    )
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_ACTION_OBSERVE_SECONDS",
        0,
    )

    result = ZhilianReadOnlyCollector(
        driver=driver,
        login_verification=_login_verification(),
    ).collect(
        query="第一查询",
        city="目标城",
        pages=1,
        wait_seconds=0,
        page_delay=0,
    )

    assert result.ok is False
    assert result.error == "zhilian_search_transition_not_observed"
    receipt = result.to_payload()["diagnostics"]["action_receipt"]
    assert receipt["button_candidate_type"] == "a:search-anchor"
    assert receipt["search_destination_ready"] is True
    assert receipt["search_destination_kind"] == "official_search_with_opaque_keyword"
    assert [attempt["method"] for attempt in receipt["attempts"]] == [
        "controlled_input",
        "exact_anchor_pointer",
        "exact_anchor_dom_click",
        "official_search_destination",
    ]
    assert all(attempt["transition_observed"] is False for attempt in receipt["attempts"])
    assert driver.native_pointer_attempts == 1
    assert driver.dom_click_attempts == 1
    assert driver.official_destination_attempts == 1
    assert driver.native_enter_attempts == 0
    assert driver.form_submit_attempts == 0


def test_search_action_receipt_redacts_unknown_page_and_account_fields():
    safe = _safe_search_action_receipt(
        {
            "receiptVersion": 1,
            "inputCandidateType": "input:search",
            "finalReadableInputValue": "产品经理",
            "urlBefore": "https://www.zhaopin.com/shenzhen/?token=secret",
            "urlAfter": "https://www.zhaopin.com/jobs?kw=opaque&account=private",
            "cookie": "session=secret",
            "bodySnippet": "private account page",
            "accountName": "private-user",
            "attempts": [
                {
                    "method": "button_click",
                    "controlActivated": True,
                    "transitionObserved": True,
                    "urlBefore": "https://www.zhaopin.com/shenzhen/?token=secret",
                    "urlAfter": "https://www.zhaopin.com/jobs?kw=opaque",
                    "bodySnippet": "private account page",
                }
            ],
        }
    )

    encoded = json.dumps(safe, ensure_ascii=False)
    assert "secret" not in encoded
    assert "private-user" not in encoded
    assert "private account page" not in encoded
    assert safe["url_before"].endswith("?token=<redacted>")
    assert safe["url_after"].endswith("?kw=<redacted>&account=<redacted>")


def test_verified_city_slug_does_not_override_result_city_conflict(monkeypatch, tmp_path):
    driver = _SlugCityResultConflictDriver()
    monkeypatch.setattr("jobagent.platforms.zhilian.city_resolver.APP_DIR", tmp_path)
    monkeypatch.setattr("jobagent.platforms.zhilian.collect.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "jobagent.platforms.zhilian.collect.ZHILIAN_SEARCH_NAVIGATION_TIMEOUT_SECONDS",
        0,
    )
    plan = {
        "platform": "zhilian",
        "candidate_limit": 100,
        "queries": [
            {"keyword": "第一查询", "city": "目标城", "page_limit": 1},
        ],
    }

    with pytest.raises(discovery.CollectionError) as error:
        discovery.collect_from_search_plan(
            plan,
            driver=driver,
            wait_seconds=0,
            page_delay=0,
            login_verification=_login_verification(),
        )

    assert error.value.code == "zhilian_city_evidence_conflict"
    assert error.value.details["no_charge"] is True


def test_search_plan_does_not_request_page_two_after_verified_single_page(tmp_path):
    driver = _PagingDriver(
        "产品经理",
        [
            _snapshot(
                "产品经理",
                job_id="JOB-1",
                available_pages=[1],
                has_next_page=False,
            )
        ],
    )
    plan = {
        "platform": "zhilian",
        "candidate_limit": 100,
        "queries": [
            {"keyword": "产品经理", "city": "", "page_limit": 2},
        ],
    }

    candidates = discovery.collect_from_search_plan(
        plan,
        driver=driver,
        wait_seconds=0,
        page_delay=0,
        login_verification=_login_verification(),
    )

    assert [candidate["id"] for candidate in candidates] == ["JOB-1"]
    assert driver.pagination_calls == []
    assert driver.open_calls == 1


def test_search_plan_collects_page_two_only_when_verified_available(tmp_path):
    driver = _PagingDriver(
        "产品经理",
        [
            _snapshot(
                "产品经理",
                job_id="JOB-1",
                available_pages=[1, 2],
                has_next_page=True,
            ),
            _snapshot(
                "产品经理",
                job_id="JOB-2",
                available_pages=[1, 2],
                has_next_page=False,
            ),
        ],
        page_two_available=True,
    )
    plan = {
        "platform": "zhilian",
        "candidate_limit": 100,
        "queries": [
            {"keyword": "产品经理", "city": "", "page_limit": 2},
        ],
    }

    candidates = discovery.collect_from_search_plan(
        plan,
        driver=driver,
        wait_seconds=0,
        page_delay=0,
        login_verification=_login_verification(),
    )

    assert [candidate["id"] for candidate in candidates] == ["JOB-1", "JOB-2"]
    assert driver.pagination_calls == [2]
    assert driver.open_calls == 2


def test_readable_query_validation_ignores_opaque_route_keyword(tmp_path):
    driver = _PagingDriver(
        "数据运营经理",
        [_snapshot("数据运营经理", job_id="JOB-READABLE")],
    )

    result = ZhilianReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "cities.json",
        login_verification=_login_verification(),
    ).collect(query="数据运营经理", pages=1, wait_seconds=0, page_delay=0)

    assert result.ok is True
    assert result.query == "数据运营经理"
    assert result.jobs[0].name == "数据运营经理"


def test_snapshot_script_emits_real_no_result_and_pagination_evidence():
    script = build_zhilian_snapshot_script(limit=5)
    pagination_script = build_zhilian_pagination_script(2)
    city_script = build_zhilian_city_filter_script(
        "深圳",
        allow_unknown_session=True,
    )

    assert "暂无.{0,12}职位" in script
    assert ".(0, 12)职位" not in script
    assert "paginationDetected" in script
    assert "availablePages" in script
    assert "hasNextPage" in script
    assert "exhausted" in pagination_script
    assert "allowUnknownSession = true" in city_script
    assert "readable_city_anchor:" in city_script
    assert "navigate_city_homepage" in city_script
    assert ZHILIAN_SELECTOR_VERSION == "2026-08-14.9"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://www.zhaopin.com/shenzhen/", True),
        ("https://www.zhaopin.com:443/shenzhen/", True),
        ("http://www.zhaopin.com/shenzhen/", False),
        ("https://evil.example/shenzhen/", False),
        ("https://www.zhaopin.com/passport/login", False),
        ("https://www.zhaopin.com/jobdetail/JOB-1.htm", False),
        ("https://user:pass@www.zhaopin.com/shenzhen/", False),
    ],
)
def test_city_bootstrap_navigation_accepts_only_safe_official_routes(value, expected):
    assert (_safe_zhilian_city_navigation_url(value) is not None) is expected


def test_city_bootstrap_destination_requires_independent_readable_evidence():
    destination = {
        "url": "https://www.zhaopin.com/shenzhen/",
        "title": "深圳招聘网_深圳人才网_2026年深圳最新招聘信息-智联招聘",
        "titleCityMatch": True,
        "readyState": "complete",
        "sessionState": "unknown",
        "strongLoginEvidence": [],
        "searchPageEvidence": ["search_input", "job_surface"],
    }

    assert _city_bootstrap_destination_verified(
        destination,
        "深圳",
        _login_verification(),
        expected_url="https://www.zhaopin.com/shenzhen/",
    ) is True
    assert _city_bootstrap_destination_verified(destination, "上海", _login_verification()) is False
    assert _city_bootstrap_destination_verified(destination, "深圳", None) is False
    assert _city_bootstrap_destination_verified(
        {**destination, "url": "https://www.zhaopin.com/"},
        "深圳",
        _login_verification(),
        expected_url="https://www.zhaopin.com/shenzhen/",
    ) is False
    assert _city_bootstrap_destination_verified(
        {**destination, "strongLoginEvidence": ["visible_credential_form"]},
        "深圳",
        _login_verification(),
    ) is False


def test_unparsed_pagination_container_does_not_claim_query_exhausted():
    assert (
        _query_page_exhausted(
            {
                "paginationDetected": True,
                "hasNextPage": False,
                "availablePages": [],
                "currentPage": None,
            },
            1,
        )
        is False
    )


def test_all_explicitly_empty_queries_return_terminal_no_charge_skip(monkeypatch):
    calls: list[tuple[str, int]] = []

    def collect_page(
        _platform,
        query,
        page,
        _limit,
        _driver,
        _wait_seconds,
        _login_verification,
    ):
        calls.append((query["keyword"], page))
        return []

    monkeypatch.setattr(discovery, "_collect_web_platform", collect_page)
    plan = {
        "platform": "zhilian",
        "candidate_limit": 100,
        "queries": [
            {"keyword": "产品经理", "city": "深圳", "page_limit": 2},
            {"keyword": "数据产品经理", "city": "深圳", "page_limit": 2},
        ],
    }

    with pytest.raises(discovery.CollectionError) as error:
        discovery.collect_from_search_plan(
            plan,
            driver=object(),
            page_delay=0,
            login_verification=_login_verification(),
        )

    assert calls == [("产品经理", 1), ("数据产品经理", 1)]
    assert error.value.code == "no_candidates"
    assert "confirm whether to skip zhilian" in error.value.user_prompt
    assert error.value.details == {
        "retryable": False,
        "requires_user_action": True,
        "search_exhausted": True,
        "next_suggested": "jobagent round skip --platform zhilian --confirm-skip",
    }


def test_non_retryable_page_error_never_suggests_same_discover(tmp_path, monkeypatch):
    import jobagent.application.discover as application
    from jobagent.infra import discovery_state

    profile = {
        "schema_version": 1,
        "preferences": {"targetRoles": [{"title": "产品经理"}]},
    }
    plan = {
        "platform": "zhilian",
        "discover_id": "dis_page_failure",
        "queries": [{"keyword": "产品经理", "city": "深圳", "page_limit": 2}],
    }
    monkeypatch.setattr(application, "profile_path", lambda: tmp_path / "profile.json")
    monkeypatch.setattr(application, "load_json", lambda _path: profile)
    monkeypatch.setattr(application, "require_compatible_profile", lambda _profile: None)
    monkeypatch.setattr(application, "load_pending_decision", lambda _platform: None)
    monkeypatch.setattr(discovery_state, "discoveries_dir", lambda: tmp_path)
    monkeypatch.setattr(
        application.rounds,
        "ensure_current_round",
        lambda: {
            "round_id": "round-1",
            "intent": {"status": "confirmed", "target_roles": ["产品经理"]},
        },
    )
    monkeypatch.setattr(
        "jobagent.infra.account_state.current_account_ref",
        lambda: "acct_account_a",
    )
    monkeypatch.setattr(application, "verify_search_plan", lambda value, **_kwargs: value)
    monkeypatch.setattr(application, "active_command", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(
        application,
        "PlatformSessionLock",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(application.cloud_client, "discovery_start", lambda **_kwargs: plan)
    monkeypatch.setattr(
        application,
        "collect_from_search_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            application.CollectionError(
                "zhilian_page_option_not_found",
                "Zhilian page option could not be verified",
            )
        ),
    )

    with pytest.raises(application.CollectionError) as error:
        application.run_discover("zhilian", page_delay=0)

    details = error.value.details
    assert details["retryable"] is False
    assert details["requires_user_action"] is True
    assert details["next_suggested"] == "jobagent browser diagnose --platform zhilian"
    assert details["next_suggested"] != "jobagent zhilian discover"
    assert details["request_preserved"] is True
    assert details["no_charge"] is True
    assert details["billing"]["additional_charge_on_retry"] is False
