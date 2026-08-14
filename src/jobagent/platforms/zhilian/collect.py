"""Zhilian live read-only collection spike."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jobagent.domain.models import Job
from jobagent.drivers.boss import create_driver

from .city_resolver import BUNDLED_CITY_CODES, ZhilianCityResolver, normalize_city_name
from .constants import ZHILIAN_LOGIN_USER_PROMPT
from .detail import (
    build_zhilian_detail_snapshot_script,
    merge_zhilian_detail_into_job,
    unwrap_zhilian_detail_js_result,
)
from .parser import parse_zhilian_job, zhilian_job_id
from .selectors import (
    build_zhilian_city_filter_script,
    build_zhilian_keyword_search_script,
    build_zhilian_pagination_script,
    build_zhilian_search_transition_script,
    build_zhilian_snapshot_script,
)
from .session_evidence import classify_zhilian_session_evidence

# Keep the bundled city codes only as post-submit verification evidence. Search
# itself starts from the public entry page and uses visible form controls.
_ZHILIAN_CITY_CODES = BUNDLED_CITY_CODES
ZHILIAN_SEARCH_ENTRY_URL = "https://www.zhaopin.com/"
ZHILIAN_PAGE_SETTLE_TIMEOUT_SECONDS = 75
ZHILIAN_SEARCH_NAVIGATION_TIMEOUT_SECONDS = 75
ZHILIAN_PAGE_POLL_INTERVAL_SECONDS = 0.5


def build_zhilian_search_url(
    query: str,
    city: str = "",
    page: int = 1,
    *,
    city_code: str | None = None,
) -> str:
    """Return the stable entry page used before visible keyword submission.

    Zhilian now owns the opaque ``kw...`` route encoding. The adapter must not
    feed that internal identifier back into a keyword parameter.
    """
    del query, city, page, city_code
    return ZHILIAN_SEARCH_ENTRY_URL


@dataclass
class ZhilianCollectResult:
    query: str
    city: str
    url: str
    jobs: list[Job]
    snapshot: dict[str, Any] = field(default_factory=dict)
    mode: str = "live_read_only"
    page: int = 1
    pages: int = 1
    ok: bool = True
    error: str = ""

    def to_payload(self, include_snapshot: bool = False) -> dict[str, Any]:
        payload = {
            "ok": self.ok,
            "platform": "zhilian",
            "mode": self.mode,
            "query": self.query,
            "city": self.city,
            "url": self.url,
            "page": self.page,
            "pages": self.pages,
            "count": len(self.jobs),
            "jobs": [job.to_dict() for job in self.jobs],
        }
        if self.error:
            payload["error"] = self.error
        if self.error == "zhilian_login_required":
            payload["message"] = "Zhilian live collect requires an active logged-in browser session."
            payload["requires_user_action"] = True
            payload["user_action"] = "login_zhilian"
            payload["user_prompt"] = ZHILIAN_LOGIN_USER_PROMPT
            payload["next_suggested"] = "jobagent zhilian login"
        elif self.ok:
            payload["next_suggested"] = "jobagent zhilian rank --input <zhilian.raw.json> --output <zhilian.ranked.json>"
        elif self.error == "zhilian_page_state_unknown":
            payload.update(
                {
                    "message": (
                        "Zhilian was still loading or showed conflicting session evidence; "
                        "the session was not classified as logged out and no credits were charged."
                    ),
                    "retryable": True,
                    "no_charge": True,
                }
            )
        elif self.error == "zhilian_job_cards_not_found":
            payload.update(
                {
                    "message": (
                        "Zhilian search results finished loading, but the current job-card "
                        "structure could not be collected; no credits were charged."
                    ),
                    "retryable": True,
                    "no_charge": True,
                    "diagnostics": _safe_selector_diagnostics(self.snapshot),
                }
            )
        elif self.error in {
            "zhilian_city_evidence_pending",
            "zhilian_city_selector_unavailable",
            "zhilian_city_evidence_conflict",
            "zhilian_city_resolution_unverified",
            "zhilian_search_navigation_pending",
        }:
            messages = {
                "zhilian_city_evidence_pending": (
                    "Zhilian had not finished exposing enough independently verifiable city evidence; "
                    "the preserved request can be resumed without another charge."
                ),
                "zhilian_city_selector_unavailable": (
                    "Zhilian search loaded, but the current city-control structure could not be "
                    "verified safely; no credits were charged."
                ),
                "zhilian_city_evidence_conflict": (
                    "Zhilian exposed city evidence that conflicts with the requested city; "
                    "collection stopped before returning candidates and no credits were charged."
                ),
                "zhilian_search_navigation_pending": (
                    "Zhilian did not finish the search-page transition in the safe wait window; "
                    "the preserved request can be resumed without another charge."
                ),
                "zhilian_city_resolution_unverified": (
                    "Zhilian city evidence could not be verified safely; no credits were charged."
                ),
            }
            payload.update(
                {
                    "message": messages[self.error],
                    "retryable": self.error != "zhilian_city_evidence_conflict",
                    "no_charge": True,
                    "diagnostics": _safe_city_diagnostics(self.snapshot),
                }
            )
        elif self.error in {
            "zhilian_keyword_input_not_found",
            "zhilian_keyword_submit_not_found",
            "zhilian_keyword_rejected",
            "zhilian_keyword_unverified",
            "zhilian_page_option_not_found",
        }:
            payload["message"] = (
                "Zhilian did not accept or verify the requested readable job keyword; "
                "no candidates were returned and no credits were charged."
            )
        if include_snapshot:
            payload["snapshot"] = self.snapshot
        return payload


class ZhilianReadOnlyCollector:
    """Collect Zhilian search cards without applying or sending messages."""

    def __init__(
        self,
        driver: Any | None = None,
        *,
        city_cache_path: Path | None = None,
        login_verification: dict[str, Any] | None = None,
    ):
        self.driver = driver or create_driver(platform="zhilian")
        self.city_resolver = ZhilianCityResolver(city_cache_path)
        self.login_verification = (
            dict(login_verification)
            if _valid_login_verification(login_verification, persisted_only=True)
            else None
        )

    def collect(
        self,
        query: str,
        city: str = "",
        limit: int = 20,
        wait_seconds: int = 8,
        page: int = 1,
        pages: int = 1,
        page_delay: float = 3.0,
        detail_limit: int = 0,
    ) -> ZhilianCollectResult:
        if not query:
            raise ValueError("query is required for live Zhilian read-only collect")

        start_page = max(1, int(page))
        page_count = max(1, int(pages))
        limit = max(1, int(limit))
        jobs: list[Job] = []
        seen: set[str] = set()
        snapshots: list[dict[str, Any]] = []
        detail_snapshots: list[dict[str, Any]] = []
        search_query = normalize_zhilian_keyword(query, city)
        resolved_city_code, city_code_source = self.city_resolver.lookup(city) if city else (None, "none")
        first_url = build_zhilian_search_url(
            search_query,
            city,
            page=start_page,
            city_code=resolved_city_code,
        )

        for index, current_page in enumerate(range(start_page, start_page + page_count)):
            url = build_zhilian_search_url(
                search_query,
                city,
                page=current_page,
                city_code=resolved_city_code,
            )
            open_result = self.driver.open_url_in_new_tab(url, wait_seconds=wait_seconds)
            if not open_result.get("ok"):
                return ZhilianCollectResult(
                    query=search_query,
                    city=city,
                    url=url,
                    jobs=jobs,
                    snapshot=_combined_snapshot(
                        snapshots,
                        {
                            "open_result": open_result,
                            "page": current_page,
                            "url": url,
                        },
                    ),
                    page=start_page,
                    pages=page_count,
                    ok=False,
                    error=str(open_result.get("error", "open_url_failed")),
                )
            keyword_search = self._submit_keyword(
                search_query,
                city=city,
                wait_seconds=wait_seconds,
            )
            if classify_zhilian_session_evidence(keyword_search) == "logged_in":
                self.login_verification = {
                    "valid": True,
                    "source": "current_collection_homepage",
                    "platform": "zhilian",
                    "session_scope": "collector_instance",
                    "age_seconds": 0,
                }
            if _login_required(keyword_search, self.login_verification):
                return ZhilianCollectResult(
                    query=search_query,
                    city=city,
                    url=str(keyword_search.get("url") or open_result.get("url") or url),
                    jobs=jobs,
                    snapshot=_combined_snapshot(
                        snapshots,
                        {
                            "error": "zhilian_login_required",
                            "keywordSearch": keyword_search,
                            "page": current_page,
                        },
                    ),
                    page=start_page,
                    pages=page_count,
                    ok=False,
                    error="zhilian_login_required",
                )
            if not keyword_search.get("ok"):
                failure = str(keyword_search.get("error") or "zhilian_keyword_submit_failed")
                return ZhilianCollectResult(
                    query=search_query,
                    city=city,
                    url=str(keyword_search.get("url") or open_result.get("url") or url),
                    jobs=jobs,
                    snapshot=_combined_snapshot(
                        snapshots,
                        {
                            "error": failure,
                            "keywordSearch": keyword_search,
                            "page": current_page,
                        },
                    ),
                    page=start_page,
                    pages=page_count,
                    ok=False,
                    error=failure,
                )
            search_transition = keyword_search.get("searchTransition")
            search_transition = (
                search_transition if isinstance(search_transition, dict) else {}
            )
            transition_candidate_code = str(
                search_transition.get("candidateCityCode") or ""
            )
            city_filter: dict[str, Any] = {}
            if city:
                city_filter = self._apply_city_filter(city, wait_seconds=wait_seconds)
                if transition_candidate_code and not city_filter.get("candidateCode"):
                    city_filter["candidateCode"] = transition_candidate_code
                    city_filter["candidateCodeSource"] = search_transition.get(
                        "candidateCitySource"
                    )
                if _login_required(city_filter, self.login_verification):
                    return ZhilianCollectResult(
                        query=search_query,
                        city=city,
                        url=str(city_filter.get("url") or open_result.get("url") or url),
                        jobs=jobs,
                        snapshot=_combined_snapshot(
                            snapshots,
                            {
                                "error": "zhilian_login_required",
                                "cityFilter": city_filter,
                                "page": current_page,
                            },
                        ),
                        page=start_page,
                        pages=page_count,
                        ok=False,
                        error="zhilian_login_required",
                    )
            pagination: dict[str, Any] = {}
            if current_page > 1:
                pagination = self._select_page(current_page, wait_seconds=wait_seconds)
                if pagination.get("exhausted"):
                    snapshots.append(
                        {
                            "ok": True,
                            "page": current_page,
                            "pagination": pagination,
                            "paginationExhausted": True,
                        }
                    )
                    break
                if not pagination.get("ok"):
                    failure = str(
                        pagination.get("error") or "zhilian_page_option_not_found"
                    )
                    return ZhilianCollectResult(
                        query=search_query,
                        city=city,
                        url=str(pagination.get("url") or open_result.get("url") or url),
                        jobs=jobs,
                        snapshot=_combined_snapshot(
                            snapshots,
                            {
                                "error": failure,
                                "keywordSearch": keyword_search,
                                "cityFilter": city_filter,
                                "pagination": pagination,
                                "page": current_page,
                            },
                        ),
                        page=start_page,
                        pages=page_count,
                        ok=False,
                        error=failure,
                    )

            remaining = max(1, limit - len(jobs))
            snapshot = self._extract_snapshot(limit=remaining, wait_seconds=wait_seconds)
            snapshot["page"] = current_page
            snapshot["requestedUrl"] = url
            snapshot["keywordSearch"] = keyword_search
            if transition_candidate_code:
                snapshot["candidateCityCode"] = transition_candidate_code
                snapshot["candidateCityCodeSource"] = search_transition.get(
                    "candidateCitySource"
                )
            if city_filter:
                snapshot["cityFilter"] = city_filter
                if city_filter.get("candidateCode"):
                    snapshot["candidateCityCode"] = city_filter["candidateCode"]
                snapshot.setdefault("visibleCity", city_filter.get("observedCity") or "")
                if not _city_filter_applied(city_filter):
                    snapshot["cityFilterDegraded"] = True
            if pagination:
                snapshot["pagination"] = pagination
            snapshots.append(snapshot)
            failure = _snapshot_failure(
                snapshot,
                login_verification=self.login_verification,
            )
            if failure:
                return ZhilianCollectResult(
                    query=search_query,
                    city=city,
                    url=str(snapshot.get("url") or open_result.get("url") or url),
                    jobs=jobs,
                    snapshot=_combined_snapshot(snapshots, {"error": failure, "page": current_page}),
                    page=start_page,
                    pages=page_count,
                    ok=False,
                    error=failure,
                )
            if not _query_verified(search_query, snapshot):
                return ZhilianCollectResult(
                    query=search_query,
                    city=city,
                    url=str(snapshot.get("url") or open_result.get("url") or url),
                    jobs=jobs,
                    snapshot=_combined_snapshot(
                        snapshots,
                        {
                            "error": "zhilian_keyword_unverified",
                            "expectedKeyword": search_query,
                            "observedKeyword": snapshot.get("searchKeyword"),
                            "page": current_page,
                        },
                    ),
                    page=start_page,
                    pages=page_count,
                    ok=False,
                    error="zhilian_keyword_unverified",
                )

            if city:
                city_resolution = self.city_resolver.verify_snapshot(
                    city,
                    snapshot,
                    expected_code=resolved_city_code,
                    source=city_code_source,
                )
                if not city_resolution["verified"] and resolved_city_code:
                    recovery_filter = self._apply_city_filter(city, wait_seconds=wait_seconds)
                    if _login_required(recovery_filter, self.login_verification):
                        return ZhilianCollectResult(
                            query=search_query,
                            city=city,
                            url=str(recovery_filter.get("url") or snapshot.get("url") or url),
                            jobs=jobs,
                            snapshot=_combined_snapshot(
                                snapshots,
                                {
                                    "error": "zhilian_login_required",
                                    "cityFilter": recovery_filter,
                                    "cityResolution": city_resolution,
                                    "page": current_page,
                                },
                            ),
                            page=start_page,
                            pages=page_count,
                            ok=False,
                            error="zhilian_login_required",
                        )
                    if _city_filter_applied(recovery_filter):
                        recovered_snapshot = self._extract_snapshot(
                            limit=remaining,
                            wait_seconds=wait_seconds,
                        )
                        recovered_snapshot["page"] = current_page
                        recovered_snapshot["requestedUrl"] = url
                        recovered_snapshot["cityFilter"] = recovery_filter
                        if recovery_filter.get("candidateCode"):
                            recovered_snapshot["candidateCityCode"] = recovery_filter[
                                "candidateCode"
                            ]
                        recovered_failure = _snapshot_failure(
                            recovered_snapshot,
                            login_verification=self.login_verification,
                        )
                        if not recovered_failure:
                            snapshot = recovered_snapshot
                            snapshots[-1] = snapshot
                            city_resolution = self.city_resolver.verify_snapshot(
                                city,
                                snapshot,
                                expected_code=None,
                                source="visible_filter_recovery",
                            )
                snapshot["cityResolution"] = city_resolution
                if not city_resolution["verified"]:
                    city_error = _city_resolution_error(city_resolution, city_filter)
                    return ZhilianCollectResult(
                        query=search_query,
                        city=city,
                        url=str(snapshot.get("url") or open_result.get("url") or url),
                        jobs=jobs,
                        snapshot=_combined_snapshot(
                            snapshots,
                            {
                                "error": city_error,
                                "cityFilter": city_filter,
                                "cityResolution": city_resolution,
                                "page": current_page,
                            },
                        ),
                        page=start_page,
                        pages=page_count,
                        ok=False,
                        error=city_error,
                    )
                resolution_source = str(city_resolution.get("source") or city_code_source)
                previous_city_code = resolved_city_code
                resolved_city_code = str(city_resolution["observedCode"])
                city_code_source = "verified_cache"
                if resolution_source != "bundled_seed" or resolved_city_code != previous_city_code:
                    self.city_resolver.remember(
                        city,
                        resolved_city_code,
                        evidence_url=str(city_resolution["observedUrl"]),
                        evidence_sources=list(city_resolution.get("evidenceSources") or []),
                        verification_source=str(
                            city_resolution.get("verificationSource") or "dynamic_multi_source"
                        ),
                    )

            cards = snapshot.get("cards", []) if isinstance(snapshot, dict) else []
            for card in cards:
                if not isinstance(card, dict):
                    continue
                job = parse_zhilian_job(card, city_name=city)
                if city and job.city and normalize_city_name(job.city) != normalize_city_name(city):
                    continue
                key = _job_dedupe_key(job, card)
                if key in seen:
                    continue
                seen.add(key)
                jobs.append(job)
                if len(jobs) >= limit:
                    break
            if len(jobs) >= limit:
                break
            if _query_page_exhausted(snapshot, current_page):
                snapshot["paginationExhausted"] = True
                break
            if index < page_count - 1 and page_delay > 0:
                time.sleep(page_delay)

        detail_limit = max(0, int(detail_limit))
        if detail_limit and jobs:
            detail_result = self._hydrate_from_details(jobs, detail_limit=detail_limit, wait_seconds=wait_seconds)
            jobs = detail_result["jobs"]
            detail_snapshots = detail_result["snapshots"]
            failure = detail_result.get("error", "")
            if failure:
                return ZhilianCollectResult(
                    query=search_query,
                    city=city,
                    url=str((snapshots[0].get("url") if snapshots else "") or first_url),
                    jobs=jobs,
                    snapshot=_combined_snapshot(snapshots, {"error": failure, "details": detail_snapshots}),
                    page=start_page,
                    pages=page_count,
                    ok=False,
                    error=failure,
                )

        combined_snapshot = _combined_snapshot(snapshots)
        if detail_snapshots:
            combined_snapshot["details"] = detail_snapshots

        return ZhilianCollectResult(
            query=search_query,
            city=city,
            url=str((snapshots[0].get("url") if snapshots else "") or first_url),
            jobs=jobs,
            snapshot=combined_snapshot,
            page=start_page,
            pages=page_count,
        )

    def _extract_snapshot(self, limit: int = 20, wait_seconds: int = 8) -> dict[str, Any]:
        js = build_zhilian_snapshot_script(limit=limit)
        return self._exec_ui_script_until_settled(
            js,
            wait_seconds=wait_seconds,
            parse_error="snapshot_parse_failed",
        )

    def _submit_keyword(
        self,
        keyword: str,
        *,
        city: str = "",
        wait_seconds: int = 8,
    ) -> dict[str, Any]:
        data = self._click_keyword_control(keyword, wait_seconds=wait_seconds)
        if not data.get("ok"):
            return data
        transition = self._await_search_transition(
            keyword,
            city=city,
            wait_seconds=wait_seconds,
        )
        result = {
            **data,
            "searchTransition": transition,
            "url": transition.get("url") or data.get("urlBefore"),
        }
        if not transition.get("ok"):
            return {
                **result,
                "ok": False,
                "error": transition.get("error") or "zhilian_search_navigation_pending",
                "loginRequired": transition.get("loginRequired", False),
            }
        return result

    def _click_keyword_control(
        self,
        keyword: str,
        *,
        wait_seconds: int,
    ) -> dict[str, Any]:
        data = self._exec_ui_script_until_settled(
            build_zhilian_keyword_search_script(
                keyword,
                allow_unknown_session=_valid_login_verification(
                    self.login_verification
                ),
            ),
            wait_seconds=wait_seconds,
            parse_error="zhilian_js_parse_failed",
        )
        if not data.get("ok"):
            return data
        if classify_zhilian_session_evidence(data) == "logged_in":
            self.login_verification = {
                "valid": True,
                "source": "current_collection_homepage",
                "platform": "zhilian",
                "session_scope": "collector_instance",
                "age_seconds": 0,
            }
        click_point = data.get("clickPoint") if isinstance(data.get("clickPoint"), dict) else None
        if not click_point or not hasattr(self.driver, "_click_at"):
            return {
                **data,
                "ok": False,
                "error": "zhilian_keyword_submit_not_found",
            }
        self.driver._click_at(click_point.get("x"), click_point.get("y"))
        data["nativeClicked"] = True
        dialog = _dismiss_javascript_dialog(self.driver)
        data["dialog"] = dialog
        if dialog.get("dismissed"):
            return {
                **data,
                "ok": False,
                "error": "zhilian_keyword_rejected",
                "url": data.get("urlBefore"),
            }
        return data

    def _await_search_transition(
        self,
        keyword: str,
        *,
        city: str,
        wait_seconds: int,
    ) -> dict[str, Any]:
        timeout = max(
            float(wait_seconds),
            float(ZHILIAN_SEARCH_NAVIGATION_TIMEOUT_SECONDS),
        )
        deadline = time.monotonic() + timeout
        script = build_zhilian_search_transition_script(keyword, city)
        last: dict[str, Any] = {}
        attempts = 0
        city_discovery_actions: set[tuple[str, str]] = set()
        while True:
            attempts += 1
            last = _unwrap_js_result_with_error(
                self.driver._exec_js(script),
                parse_error="zhilian_js_parse_failed",
            )
            if _strong_login_evidence(last):
                return {
                    **last,
                    "ok": False,
                    "error": "zhilian_login_required",
                    "settleAttempts": attempts,
                    "settleTimeoutSeconds": timeout,
                }
            if _search_transition_ready(last, keyword, city):
                return {
                    **last,
                    "ok": True,
                    "settleAttempts": attempts,
                    "settleTimeoutSeconds": timeout,
                }
            if _search_transition_city_discovery_ready(last, keyword, city):
                city_discovery = self._advance_city_discovery(
                    city,
                    attempted_actions=city_discovery_actions,
                    wait_seconds=wait_seconds,
                )
                last["cityDiscovery"] = city_discovery
                candidate_code = city_discovery.get("candidateCode")
                if candidate_code:
                    last["candidateCityCode"] = candidate_code
                    last["candidateCitySource"] = (
                        city_discovery.get("candidateCodeSource")
                        or city_discovery.get("source")
                        or "active_city_control"
                    )
                if _strong_login_evidence(city_discovery):
                    return {
                        **last,
                        "ok": False,
                        "error": "zhilian_login_required",
                        "settleAttempts": attempts,
                        "settleTimeoutSeconds": timeout,
                    }
                if city_discovery.get("navigated"):
                    bootstrap = self._await_city_bootstrap_destination(
                        keyword,
                        city=city,
                        expected_url=str(city_discovery.get("navigationUrl") or ""),
                        wait_seconds=wait_seconds,
                    )
                    last["cityBootstrap"] = bootstrap
                    if not bootstrap.get("ok"):
                        return {
                            **last,
                            "ok": False,
                            "error": bootstrap.get("error")
                            or "zhilian_city_evidence_pending",
                            "loginRequired": bootstrap.get("loginRequired", False),
                            "retryable": bootstrap.get("retryable", True),
                            "settleAttempts": attempts,
                            "settleTimeoutSeconds": timeout,
                        }
                    restart = self._click_keyword_control(
                        keyword,
                        wait_seconds=wait_seconds,
                    )
                    last["cityBootstrapKeyword"] = restart
                    if not restart.get("ok"):
                        return {
                            **last,
                            "ok": False,
                            "error": restart.get("error")
                            or "zhilian_keyword_submit_failed",
                            "loginRequired": restart.get("loginRequired", False),
                            "settleAttempts": attempts,
                            "settleTimeoutSeconds": timeout,
                        }
                    deadline = time.monotonic() + timeout
                    continue
                if _search_transition_ready(last, keyword, city):
                    return {
                        **last,
                        "ok": True,
                        "settleAttempts": attempts,
                        "settleTimeoutSeconds": timeout,
                    }
            if _search_transition_keyword_conflict(last, keyword):
                return {
                    **last,
                    "ok": False,
                    "error": "zhilian_keyword_unverified",
                    "loginRequired": False,
                    "settleAttempts": attempts,
                    "settleTimeoutSeconds": timeout,
                }
            if time.monotonic() >= deadline:
                error = _search_transition_failure(last, city)
                return {
                    **last,
                    "ok": False,
                    "error": error,
                    "loginRequired": False,
                    "retryable": True,
                    "settleAttempts": attempts,
                    "settleTimeoutSeconds": timeout,
                }
            time.sleep(ZHILIAN_PAGE_POLL_INTERVAL_SECONDS)

    def _advance_city_discovery(
        self,
        city: str,
        *,
        attempted_actions: set[tuple[str, str]],
        wait_seconds: int,
    ) -> dict[str, Any]:
        result = _unwrap_js_result_with_error(
            self.driver._exec_js(
                build_zhilian_city_filter_script(
                    city,
                    allow_unknown_session=_valid_login_verification(
                        self.login_verification
                    ),
                )
            ),
            parse_error="zhilian_js_parse_failed",
        )
        action = str(result.get("action") or "")
        if action == "navigate_city_homepage":
            navigation_url = _safe_zhilian_city_navigation_url(
                result.get("candidateNavigationUrl")
            )
            if not navigation_url:
                return {
                    **result,
                    "ok": False,
                    "error": "zhilian_city_navigation_unverified",
                    "navigationRejected": True,
                }
            signature = (action, navigation_url)
            if signature in attempted_actions:
                return {**result, "actionRepeated": True}
            attempted_actions.add(signature)
            open_result = self.driver.open_url_in_new_tab(
                navigation_url,
                wait_seconds=wait_seconds,
            )
            return {
                **result,
                "ok": bool(open_result.get("ok")),
                "navigated": bool(open_result.get("ok")),
                "navigationUrl": navigation_url,
                "navigationResult": open_result,
                "error": (
                    "" if open_result.get("ok")
                    else str(open_result.get("error") or "zhilian_city_navigation_failed")
                ),
            }
        click_point = (
            result.get("clickPoint")
            if isinstance(result.get("clickPoint"), dict)
            else None
        )
        if action not in {"expand_location", "select_city"} or not click_point:
            return result
        signature = (
            action,
            f"{int(click_point.get('x') or 0)}:{int(click_point.get('y') or 0)}",
        )
        if signature in attempted_actions:
            return {**result, "actionRepeated": True}
        attempted_actions.add(signature)
        if not hasattr(self.driver, "_click_at"):
            return result
        self.driver._click_at(click_point.get("x"), click_point.get("y"))
        return {**result, "nativeClicked": True}

    def _await_city_bootstrap_destination(
        self,
        keyword: str,
        *,
        city: str,
        expected_url: str,
        wait_seconds: int,
    ) -> dict[str, Any]:
        timeout = max(
            float(wait_seconds),
            float(ZHILIAN_SEARCH_NAVIGATION_TIMEOUT_SECONDS),
        )
        deadline = time.monotonic() + timeout
        script = build_zhilian_search_transition_script(keyword, city)
        attempts = 0
        last: dict[str, Any] = {}
        while True:
            attempts += 1
            last = _unwrap_js_result_with_error(
                self.driver._exec_js(script),
                parse_error="zhilian_js_parse_failed",
            )
            if _strong_login_evidence(last):
                return {
                    **last,
                    "ok": False,
                    "error": "zhilian_login_required",
                    "loginRequired": True,
                    "bootstrapAttempts": attempts,
                }
            if _city_bootstrap_destination_verified(
                last,
                city,
                self.login_verification,
                expected_url=expected_url,
            ):
                return {
                    **last,
                    "ok": True,
                    "bootstrapVerified": True,
                    "bootstrapAttempts": attempts,
                }
            if time.monotonic() >= deadline:
                return {
                    **last,
                    "ok": False,
                    "error": "zhilian_city_evidence_pending",
                    "loginRequired": False,
                    "retryable": True,
                    "bootstrapAttempts": attempts,
                }
            time.sleep(ZHILIAN_PAGE_POLL_INTERVAL_SECONDS)

    def _select_page(self, page: int, wait_seconds: int = 8) -> dict[str, Any]:
        result = self.driver._exec_js(build_zhilian_pagination_script(page))
        data = _unwrap_js_result(result)
        if not data.get("ok") or data.get("alreadySelected"):
            return data
        click_point = data.get("clickPoint") if isinstance(data.get("clickPoint"), dict) else None
        if not click_point or not hasattr(self.driver, "_click_at"):
            return {
                **data,
                "ok": False,
                "error": "zhilian_page_option_not_found",
            }
        self.driver._click_at(click_point.get("x"), click_point.get("y"))
        data["nativeClicked"] = True
        time.sleep(min(max(wait_seconds, 1), 5))
        return data

    def _apply_city_filter(self, city: str, wait_seconds: int = 8) -> dict[str, Any]:
        last: dict[str, Any] = {}
        for attempt in range(2):
            last = self._exec_ui_script_until_settled(
                build_zhilian_city_filter_script(
                    city,
                    allow_unknown_session=_valid_login_verification(
                        self.login_verification
                    ),
                ),
                wait_seconds=wait_seconds,
                parse_error="zhilian_js_parse_failed",
            )
            if last.get("loginRequired"):
                time.sleep(min(max(wait_seconds, 1), 4))
                return last
            click_point = last.get("clickPoint") if isinstance(last.get("clickPoint"), dict) else None
            if click_point and hasattr(self.driver, "_click_at"):
                self.driver._click_at(click_point.get("x"), click_point.get("y"))
                last["nativeClicked"] = True
                time.sleep(1.2 if last.get("action") == "expand_location" else min(max(wait_seconds, 1), 4))
                if last.get("action") == "select_city":
                    return last
            if _city_filter_applied(last):
                time.sleep(min(max(wait_seconds, 1), 4))
                return last
            if last.get("action") == "expand_location" and attempt == 0:
                time.sleep(0.8)
                continue
            return last
        return last

    def _exec_ui_script_until_settled(
        self,
        script: str,
        *,
        wait_seconds: int,
        parse_error: str,
    ) -> dict[str, Any]:
        timeout = max(float(wait_seconds), float(ZHILIAN_PAGE_SETTLE_TIMEOUT_SECONDS))
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {}
        attempts = 0
        while True:
            attempts += 1
            result = self.driver._exec_js(script)
            last = _unwrap_js_result_with_error(result, parse_error=parse_error)
            if not _page_state_pending(
                last,
                login_verification=self.login_verification,
            ):
                return _with_session_continuity(last, self.login_verification)
            if time.monotonic() >= deadline:
                if _search_page_ready_with_continuity(
                    last,
                    self.login_verification,
                ) and _zero_unexplained_candidates(last):
                    return _with_session_continuity(
                        {
                            **last,
                            "ok": False,
                            "error": "zhilian_job_cards_not_found",
                            "loginRequired": False,
                            "sessionState": "search_ready",
                            "retryable": True,
                            "settleAttempts": attempts,
                            "settleTimeoutSeconds": timeout,
                        },
                        self.login_verification,
                    )
                return {
                    **last,
                    "ok": False,
                    "error": "zhilian_page_state_unknown",
                    "loginRequired": False,
                    "sessionState": "unknown",
                    "retryable": True,
                    "settleAttempts": attempts,
                    "settleTimeoutSeconds": timeout,
                }
            time.sleep(ZHILIAN_PAGE_POLL_INTERVAL_SECONDS)

    def _hydrate_from_details(
        self,
        jobs: list[Job],
        detail_limit: int = 0,
        wait_seconds: int = 8,
    ) -> dict[str, Any]:
        hydrated = list(jobs)
        snapshots: list[dict[str, Any]] = []
        remaining = max(0, int(detail_limit))
        if remaining <= 0:
            return {"jobs": hydrated, "snapshots": snapshots}

        for index, job in _detail_hydration_order(hydrated):
            if remaining <= 0:
                break
            if not job.url:
                continue
            open_result = self.driver.open_url_in_new_tab(job.url, wait_seconds=wait_seconds)
            if not open_result.get("ok"):
                snapshots.append({
                    "ok": False,
                    "url": job.url,
                    "jobIndex": index,
                    "error": str(open_result.get("error", "detail_open_failed")),
                    "openResult": open_result,
                })
                remaining -= 1
                continue
            snapshot = self._extract_detail_snapshot()
            snapshot["jobIndex"] = index
            snapshot["requestedUrl"] = job.url
            snapshots.append(snapshot)
            failure = _snapshot_failure(snapshot)
            if failure:
                return {"jobs": hydrated, "snapshots": snapshots, "error": failure}
            if snapshot.get("ok") is not False:
                hydrated[index] = merge_zhilian_detail_into_job(job, snapshot)
            remaining -= 1
        return {"jobs": hydrated, "snapshots": snapshots}

    def _extract_detail_snapshot(self) -> dict[str, Any]:
        js = build_zhilian_detail_snapshot_script()
        return unwrap_zhilian_detail_js_result(self.driver._exec_js(js))


def write_zhilian_snapshot(path: str | Path, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _detail_hydration_order(jobs: list[Job]) -> list[tuple[int, Job]]:
    indexed = list(enumerate(jobs))
    missing_core = [item for item in indexed if _needs_zhilian_detail(item[1])]
    complete = [item for item in indexed if not _needs_zhilian_detail(item[1])]
    return missing_core + complete


def _needs_zhilian_detail(job: Job) -> bool:
    return not job.company or not job.boss


def _combined_snapshot(snapshots: list[dict[str, Any]], fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    if len(snapshots) == 1 and fallback is None:
        return snapshots[0]
    if snapshots:
        payload: dict[str, Any] = {"ok": True, "pages": snapshots}
        if fallback is not None:
            payload["ok"] = False
            payload["failure"] = fallback
        return payload
    return fallback or {}


def _job_dedupe_key(job: Job, raw: dict[str, Any]) -> str:
    job_id = zhilian_job_id(raw)
    if job_id:
        return f"id:{job_id}"
    if job.url:
        return f"url:{job.url}"
    return f"text:{job.name}|{job.company}|{job.city}"


def _snapshot_failure(
    snapshot: dict[str, Any],
    *,
    login_verification: dict[str, Any] | None = None,
) -> str:
    if snapshot.get("platformError"):
        return str(snapshot["platformError"])
    if _strong_login_evidence(snapshot):
        session_state = classify_zhilian_session_evidence(snapshot)
        return (
            "zhilian_page_state_unknown"
            if session_state == "unknown"
            else "zhilian_login_required"
        )
    if _search_page_ready_with_continuity(snapshot, login_verification):
        if _zero_unexplained_candidates(snapshot):
            return "zhilian_job_cards_not_found"
        if snapshot.get("ok") is False:
            error = str(snapshot.get("error") or "")
            if error not in {
                "zhilian_page_state_pending",
                "zhilian_page_state_unknown",
                "zhilian_login_required",
            }:
                return error or "zhilian_snapshot_failed"
        return ""
    session_state = classify_zhilian_session_evidence(snapshot)
    if session_state in {"loading", "unknown"}:
        return "zhilian_page_state_unknown"
    if session_state == "login_required":
        return "zhilian_login_required"
    url = str(snapshot.get("url", ""))
    title = str(snapshot.get("title", ""))
    if "passport" in url or "login" in url or "登录" in title:
        return "zhilian_login_required"
    if snapshot.get("ok") is False:
        return str(snapshot.get("error") or "zhilian_snapshot_failed")
    return ""


def _unwrap_js_result(result: Any) -> dict[str, Any]:
    return _unwrap_js_result_with_error(result, parse_error="zhilian_js_parse_failed")


def _unwrap_js_result_with_error(result: Any, *, parse_error: str) -> dict[str, Any]:
    if isinstance(result, dict) and "raw" in result:
        try:
            parsed = json.loads(result["raw"])
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {"ok": False, "error": parse_error}
    return result if isinstance(result, dict) else {"ok": False, "error": "zhilian_js_empty_result"}


def _page_state_pending(
    result: dict[str, Any],
    *,
    login_verification: dict[str, Any] | None = None,
) -> bool:
    if _strong_login_evidence(result):
        return False
    if _search_page_ready_with_continuity(result, login_verification):
        return _zero_unexplained_candidates(result)
    state = str(result.get("sessionState") or "")
    ready_state = str(result.get("readyState") or "")
    if state in {"loading", "unknown"}:
        return True
    if result.get("error") == "zhilian_page_state_pending":
        return True
    return bool(ready_state and ready_state != "complete")


def _valid_login_verification(
    value: dict[str, Any] | None,
    *,
    persisted_only: bool = False,
) -> bool:
    if not isinstance(value, dict) or value.get("valid") is not True:
        return False
    source = value.get("source")
    if source not in {"recent_login_check", "current_collection_homepage"}:
        return False
    if value.get("platform") != "zhilian":
        return False
    if source == "recent_login_check":
        if not value.get("round_id") or not value.get("browser_session_id"):
            return False
    elif persisted_only or value.get("session_scope") != "collector_instance":
        return False
    try:
        age_seconds = float(value.get("age_seconds"))
    except (TypeError, ValueError):
        return False
    return 0 <= age_seconds <= 30 * 60


def _strong_login_evidence(result: dict[str, Any]) -> bool:
    values = result.get("strongLoginEvidence")
    return bool(isinstance(values, list) and any(values))


def _search_page_ready_with_continuity(
    result: dict[str, Any],
    login_verification: dict[str, Any] | None,
) -> bool:
    if not _valid_login_verification(login_verification):
        return False
    if _strong_login_evidence(result):
        return False
    if str(result.get("readyState") or "") != "complete":
        return False
    url = str(result.get("url") or "").casefold()
    title = str(result.get("title") or "")
    if "passport" in url or "/login" in url or "登录" in title:
        return False
    evidence = {
        str(item)
        for item in (result.get("searchPageEvidence") or [])
        if item
    }
    return "search_route" in evidence and bool(
        evidence
        & {"search_title", "search_input", "job_surface", "job_action", "no_results"}
    )


def _zero_unexplained_candidates(result: dict[str, Any]) -> bool:
    if "candidateCount" not in result:
        return False
    try:
        count = int(result.get("candidateCount") or 0)
    except (TypeError, ValueError):
        count = 0
    return count == 0 and not bool(result.get("noResults"))


def _query_page_exhausted(snapshot: dict[str, Any], current_page: int) -> bool:
    """Return true only for an explicit, readable end-of-query boundary."""

    if snapshot.get("noResults") is True:
        return True
    if snapshot.get("paginationDetected") is not True:
        return False
    if snapshot.get("hasNextPage") is not False:
        return False
    available_pages: list[int] = []
    for value in snapshot.get("availablePages") or []:
        try:
            available_pages.append(int(value))
        except (TypeError, ValueError):
            continue
    observed_value = snapshot.get("currentPage")
    if observed_value in {None, ""} and not available_pages:
        return False
    try:
        observed_page = int(observed_value or current_page)
    except (TypeError, ValueError):
        return False
    return observed_page == current_page and (
        not available_pages or max(available_pages) <= current_page
    )


def _with_session_continuity(
    result: dict[str, Any],
    login_verification: dict[str, Any] | None,
) -> dict[str, Any]:
    if not _search_page_ready_with_continuity(result, login_verification):
        return result
    receipt = login_verification or {}
    return {
        **result,
        "loginRequired": False,
        "sessionContinuity": {
            "used": True,
            "source": receipt.get("source"),
            "ageSeconds": receipt.get("age_seconds"),
        },
    }


def _login_required(
    result: dict[str, Any],
    login_verification: dict[str, Any] | None,
) -> bool:
    if _strong_login_evidence(result):
        return True
    return bool(
        result.get("loginRequired")
        and not _search_page_ready_with_continuity(result, login_verification)
    )


def _safe_selector_diagnostics(snapshot: dict[str, Any]) -> dict[str, Any]:
    current = snapshot
    pages = snapshot.get("pages") if isinstance(snapshot, dict) else None
    if isinstance(pages, list) and pages and isinstance(pages[-1], dict):
        current = pages[-1]
    mapping = {
        "selectorVersion": "selector_version",
        "readyState": "ready_state",
        "sessionState": "session_state",
        "sessionReason": "session_reason",
        "candidateCount": "candidate_count",
        "jobLinkCount": "job_link_count",
        "jobSurfaceCount": "job_surface_count",
        "jobActionCount": "job_action_count",
        "noResults": "no_results",
        "navigationElapsedMs": "navigation_elapsed_ms",
        "searchPageEvidence": "search_page_evidence",
    }
    return {
        output: current[source]
        for source, output in mapping.items()
        if source in current
    }


def _safe_city_diagnostics(snapshot: dict[str, Any]) -> dict[str, Any]:
    current = snapshot if isinstance(snapshot, dict) else {}
    pages = current.get("pages")
    if isinstance(pages, list) and pages and isinstance(pages[-1], dict):
        current = pages[-1]
    failure = snapshot.get("failure") if isinstance(snapshot, dict) else None
    failure = failure if isinstance(failure, dict) else {}
    resolution = failure.get("cityResolution") or current.get("cityResolution") or {}
    city_filter = failure.get("cityFilter") or current.get("cityFilter") or {}
    keyword = failure.get("keywordSearch") or current.get("keywordSearch") or {}
    transition = keyword.get("searchTransition") if isinstance(keyword, dict) else {}
    transition = transition if isinstance(transition, dict) else {}
    page = transition or current
    diagnostics: dict[str, Any] = {}
    page_mapping = {
        "readyState": "ready_state",
        "navigationElapsedMs": "navigation_elapsed_ms",
        "titleCityMatch": "title_city_match",
        "observedCityCode": "observed_city_code",
        "candidateCityCode": "candidate_city_code",
        "candidateCitySource": "candidate_city_source",
        "searchPageEvidence": "search_page_evidence",
        "settleAttempts": "settle_attempts",
        "settleTimeoutSeconds": "settle_timeout_seconds",
    }
    for source, target in page_mapping.items():
        if source in page:
            diagnostics[target] = page[source]
    resolution_mapping = {
        "evidenceStatus": "evidence_status",
        "reason": "reason",
        "evidenceSources": "evidence_sources",
        "observedCode": "observed_city_code",
        "observedCodeSource": "observed_city_code_source",
        "candidateCode": "candidate_city_code",
        "codeEvidenceConflict": "code_evidence_conflict",
        "titleMatchesCity": "title_city_match",
        "visibleCityConflict": "visible_city_conflict",
        "allCardsOtherCity": "all_cards_other_city",
    }
    if isinstance(resolution, dict):
        for source, target in resolution_mapping.items():
            if source in resolution:
                diagnostics[target] = resolution[source]
    if isinstance(city_filter, dict):
        if city_filter.get("error"):
            diagnostics["city_selector_error"] = city_filter["error"]
        if city_filter.get("candidateCode"):
            diagnostics["candidate_city_code"] = city_filter["candidateCode"]
        if city_filter.get("source"):
            diagnostics["city_selector_source"] = city_filter["source"]
    return diagnostics


def _safe_zhilian_city_navigation_url(value: Any) -> str | None:
    try:
        parsed = urlparse(str(value or ""))
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() != "www.zhaopin.com":
        return None
    if parsed.username or parsed.password or port not in {None, 443}:
        return None
    if any(
        marker in parsed.path.casefold()
        for marker in ("passport", "login", "jobdetail", "companydetail")
    ):
        return None
    return parsed.geturl()


def _city_bootstrap_destination_verified(
    result: dict[str, Any],
    city: str,
    login_verification: dict[str, Any] | None,
    *,
    expected_url: str = "",
) -> bool:
    if _strong_login_evidence(result):
        return False
    if str(result.get("readyState") or "") != "complete":
        return False
    observed_url = _safe_zhilian_city_navigation_url(result.get("url"))
    if not observed_url:
        return False
    expected = _safe_zhilian_city_navigation_url(expected_url) if expected_url else None
    if expected:
        expected_path = urlparse(expected).path.rstrip("/") or "/"
        observed_path = urlparse(observed_url).path.rstrip("/") or "/"
        if observed_path != expected_path and not _is_zhilian_search_route(observed_url):
            return False
    state = str(result.get("sessionState") or "")
    if state == "login_required":
        return False
    if state == "unknown" and not _valid_login_verification(login_verification):
        return False
    normalized_city = normalize_city_name(city)
    title = normalize_city_name(str(result.get("title") or ""))
    title_matches = bool(normalized_city and normalized_city in title)
    evidence = {
        str(item)
        for item in (result.get("searchPageEvidence") or [])
        if item
    }
    return title_matches and "search_input" in evidence


def _search_transition_ready(
    result: dict[str, Any],
    keyword: str,
    city: str = "",
) -> bool:
    if _strong_login_evidence(result):
        return False
    ready_state = str(result.get("readyState") or "")
    if ready_state and ready_state != "complete":
        return False
    url = str(result.get("url") or "").casefold()
    expected = " ".join(keyword.split()).casefold()
    observed = " ".join(
        str(result.get("observedKeyword") or result.get("searchKeyword") or "").split()
    ).casefold()
    if observed and observed != expected:
        return False
    evidence = {
        str(item)
        for item in (result.get("searchPageEvidence") or [])
        if item
    }
    if _is_zhilian_search_route(url) and evidence:
        return "search_route" in evidence and bool(
            evidence
            & {"search_title", "search_input", "job_surface", "job_action", "no_results"}
        )
    if _is_zhilian_search_route(url):
        return bool(expected and observed == expected)
    return False


def _search_transition_city_discovery_ready(
    result: dict[str, Any],
    keyword: str,
    city: str,
) -> bool:
    if not city or _strong_login_evidence(result):
        return False
    ready_state = str(result.get("readyState") or "")
    if ready_state and ready_state != "complete":
        return False
    if _is_zhilian_search_route(str(result.get("url") or "")):
        return False
    expected = " ".join(keyword.split()).casefold()
    observed = " ".join(
        str(result.get("observedKeyword") or result.get("searchKeyword") or "").split()
    ).casefold()
    if not expected or observed != expected:
        return False
    normalized_city = normalize_city_name(city)
    title = normalize_city_name(str(result.get("title") or ""))
    if not (result.get("titleCityMatch") or (normalized_city and normalized_city in title)):
        return False
    evidence = {
        str(item)
        for item in (result.get("searchPageEvidence") or [])
        if item
    }
    return "search_input" in evidence and bool(
        evidence & {"job_surface", "job_action", "no_results"}
    )


def _search_transition_failure(result: dict[str, Any], city: str) -> str:
    normalized = normalize_city_name(city)
    title = normalize_city_name(str(result.get("title") or ""))
    title_matches = bool(
        result.get("titleCityMatch")
        or (normalized and normalized in title)
    )
    if title_matches and not (
        result.get("observedCityCode") or result.get("candidateCityCode")
    ):
        return "zhilian_city_evidence_pending"
    return "zhilian_search_navigation_pending"


def _search_transition_keyword_conflict(
    result: dict[str, Any],
    keyword: str,
) -> bool:
    ready_state = str(result.get("readyState") or "")
    if ready_state and ready_state != "complete":
        return False
    if not _is_zhilian_search_route(str(result.get("url") or "")):
        return False
    expected = " ".join(keyword.split()).casefold()
    observed = " ".join(
        str(result.get("observedKeyword") or result.get("searchKeyword") or "").split()
    ).casefold()
    return bool(expected and observed and observed != expected)


def _city_resolution_error(
    resolution: dict[str, Any],
    city_filter: dict[str, Any],
) -> str:
    if resolution.get("evidenceStatus") == "evidence_conflict":
        return "zhilian_city_evidence_conflict"
    selector_error = str(city_filter.get("error") or "")
    selector_unavailable = selector_error in {
        "zhilian_city_options_collapsed",
        "zhilian_city_option_not_found",
        "zhilian_city_filter_failed",
    }
    observed_url = str(resolution.get("observedUrl") or "")
    if selector_unavailable and _is_zhilian_search_route(observed_url):
        return "zhilian_city_selector_unavailable"
    if resolution.get("evidenceStatus") == "evidence_pending":
        return "zhilian_city_evidence_pending"
    return "zhilian_city_resolution_unverified"


def _is_zhilian_search_route(url: str) -> bool:
    lowered = str(url or "").casefold()
    return "/jobs" in lowered or "/sou/" in lowered or "sou.zhaopin.com" in lowered


def _city_filter_applied(result: dict[str, Any]) -> bool:
    if result.get("mode") != "zhilian_city_filter":
        return False
    return bool(result.get("ok") and (result.get("applied") or result.get("alreadySelected") or result.get("skipped")))


def _dismiss_javascript_dialog(driver: Any) -> dict[str, Any]:
    handler = getattr(driver, "dismiss_javascript_dialog", None)
    if not callable(handler):
        return {"ok": True, "dismissed": False, "supported": False}
    result = handler()
    return result if isinstance(result, dict) else {"ok": True, "dismissed": bool(result)}


def _query_verified(query: str, snapshot: dict[str, Any]) -> bool:
    expected = " ".join(query.split()).casefold()
    observed = " ".join(str(snapshot.get("searchKeyword") or "").split()).casefold()
    if observed:
        return observed == expected
    title = " ".join(str(snapshot.get("title") or "").split()).casefold()
    return bool(expected and expected in title)


def normalize_zhilian_keyword(query: str, city: str = "") -> str:
    value = query.strip()
    marker = city.strip()
    if marker and value.startswith(marker):
        stripped = value[len(marker):].strip()
        return stripped or value
    return value
