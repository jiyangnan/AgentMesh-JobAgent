"""Liepin live read-only collection spike."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit

from jobagent.domain.models import Job
from jobagent.drivers.boss import create_driver

from .constants import LIEPIN_LOGIN_USER_PROMPT
from .city_resolver import BUNDLED_CITY_CODES, LiepinCityResolver, normalize_city_name
from .parser import liepin_job_id, parse_liepin_job
from .selectors import build_liepin_snapshot_script

LIEPIN_CITY_CODES = BUNDLED_CITY_CODES

LIEPIN_SEARCH_URL = "https://www.liepin.com/zhaopin/"
LIEPIN_CITY_LIST_URL = "https://www.liepin.com/citylist/"
LIEPIN_VERIFICATION_USER_PROMPT = (
    "猎聘当前需要安全验证。请在现有 Job Agent Chrome 标签页中完成验证，"
    "不要关闭浏览器或新建求职轮次；完成后继续原 Discover 请求。"
)


def build_liepin_search_url(
    query: str,
    city: str = "",
    page: int = 1,
    *,
    city_code: str | None = None,
) -> str:
    """Build a human-search URL for the live read-only spike."""
    city = city.strip().removesuffix("市") if city else ""
    resolved_city_code = (city_code or LIEPIN_CITY_CODES.get(city) or "").strip()
    current_page = max(0, int(page) - 1)
    if resolved_city_code:
        return (
            f"{LIEPIN_SEARCH_URL}?city={quote(resolved_city_code)}&dq={quote(resolved_city_code)}"
            f"&currentPage={current_page}&pageSize=40&key={quote(query)}"
            "&scene=input&sfrom=search_job_pc"
        )
    if city:
        raise ValueError(f"Liepin city code is unresolved for {city}")
    return (
        f"{LIEPIN_SEARCH_URL}?currentPage={current_page}&pageSize=40&key={quote(query)}"
        "&scene=input&sfrom=search_job_pc"
    )


def build_liepin_city_route_search_url(
    route: str,
    query: str,
    page: int = 1,
) -> str:
    """Build a search URL from an official city-directory route."""
    safe_route = _safe_liepin_city_route(route)
    if not safe_route:
        raise ValueError("Liepin city route is not an official city URL")
    parsed = urlsplit(safe_route)
    path = parsed.path.rstrip("/") + "/zhaopin/"
    query_string = urlencode(
        {
            "currentPage": max(0, int(page) - 1),
            "pageSize": 40,
            "key": query,
            "scene": "input",
            "sfrom": "search_job_pc",
        }
    )
    return urlunsplit(("https", "www.liepin.com", path, query_string, ""))


def _build_liepin_resolved_search_url(
    *,
    query: str,
    city: str,
    page: int,
    city_code: str,
    city_route: str,
) -> str:
    if city_route and not city_code:
        return build_liepin_city_route_search_url(city_route, query, page=page)
    return build_liepin_search_url(
        query,
        city,
        page=page,
        city_code=city_code,
    )


@dataclass
class LiepinCollectResult:
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
            "platform": "liepin",
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
        interruption = _interruption_payload(self.snapshot, error=self.error)
        for key in ("message", "retryable", "requires_user_action", "user_action", "user_prompt", "next_suggested"):
            if key in interruption:
                payload[key] = interruption[key]
        if self.error == "liepin_verification_required":
            payload["message"] = "Liepin requires user verification in the existing browser tab."
            payload["requires_user_action"] = True
            payload["user_action"] = "complete_liepin_verification"
            payload["user_prompt"] = (
                _verification_prompt(self.snapshot) or LIEPIN_VERIFICATION_USER_PROMPT
            )
        elif self.error == "liepin_login_required":
            payload["message"] = "Liepin live collect requires an active logged-in session."
            payload["requires_user_action"] = True
            payload["user_action"] = "login_liepin"
            payload["user_prompt"] = LIEPIN_LOGIN_USER_PROMPT
            payload["next_suggested"] = "jobagent liepin login"
        elif self.error == "liepin_search_state_unknown":
            payload.setdefault("message", "Liepin search page could not be verified before submission.")
            payload.setdefault("retryable", False)
            payload.setdefault("user_prompt", (
                "猎聘搜索页面状态暂时无法安全确认，已停止搜索操作。"
                "请保留当前浏览器和求职轮次，通过只读浏览器诊断确认页面状态。"
            ))
            payload.setdefault("next_suggested", "jobagent browser diagnose --platform liepin")
        elif self.error == "liepin_page_state_unknown":
            payload.setdefault("message", "Liepin page state could not be verified safely.")
            payload.setdefault("retryable", False)
            payload.setdefault("requires_user_action", True)
            payload.setdefault("user_prompt", (
                "猎聘当前页面状态暂时无法确认，已停止操作。"
                "请保留当前页面和求职轮次，先查看只读浏览器诊断结果。"
            ))
            payload.setdefault("next_suggested", "jobagent browser diagnose --platform liepin")
        elif self.error in {
            "liepin_city_code_not_found",
            "liepin_city_evidence_unverified",
        }:
            payload["message"] = (
                "Liepin city filter could not be resolved safely for "
                f"{self.city or 'the requested city'}."
            )
            payload["retryable"] = False
            city_diagnostic = _redacted_city_diagnostic(self.snapshot)
            if city_diagnostic:
                payload["city_resolution"] = city_diagnostic
        elif self.ok:
            payload["next_suggested"] = "jobagent liepin rank --input <liepin.raw.json> --output <liepin.ranked.json>"
        if include_snapshot:
            payload["snapshot"] = self.snapshot
        return payload


class LiepinReadOnlyCollector:
    """Collect Liepin search cards without applying or sending messages."""

    def __init__(self, driver: Any | None = None, *, city_cache_path: Path | None = None):
        self.driver = driver or create_driver(platform="liepin")
        self.city_resolver = LiepinCityResolver(city_cache_path)

    def collect(
        self,
        query: str,
        city: str = "",
        limit: int = 20,
        wait_seconds: int = 8,
        page: int = 1,
        pages: int = 1,
        page_delay: float = 3.0,
    ) -> LiepinCollectResult:
        """Open one or more Liepin search pages and extract visible job cards."""
        if not query:
            raise ValueError("query is required for live Liepin read-only collect")

        city = normalize_city_name(city)
        start_page = max(1, int(page))
        page_count = max(1, int(pages))
        limit = max(1, int(limit))
        jobs: list[Job] = []
        seen: set[str] = set()
        snapshots: list[dict[str, Any]] = []
        candidate_code, candidate_source = (
            self.city_resolver.lookup(city) if city else (None, "none")
        )
        city_code = str(candidate_code or "")
        city_route = ""
        city_resolution: dict[str, Any] = {
            "ok": True,
            "city": city,
            "code": city_code,
            "source": candidate_source,
        }
        dynamic_resolution_attempted = False
        if city and not city_code:
            dynamic_resolution_attempted = True
            city_resolution = self._resolve_city(
                city,
                query=query,
                wait_seconds=wait_seconds,
            )
            city_code = str(city_resolution.get("code") or "")
            city_route = _safe_liepin_city_route(
                str(city_resolution.get("route") or "")
            )
            if not city_code and not city_route:
                return LiepinCollectResult(
                    query=query,
                    city=city,
                    url=str(city_resolution.get("url") or LIEPIN_SEARCH_URL),
                    jobs=[],
                    snapshot={"cityResolution": city_resolution},
                    page=start_page,
                    pages=page_count,
                    ok=False,
                    error=str(
                        city_resolution.get("error")
                        or "liepin_city_code_not_found"
                    ),
                )
        first_url = _build_liepin_resolved_search_url(
            query=query,
            city=city,
            page=start_page,
            city_code=city_code,
            city_route=city_route,
        )

        for index, current_page in enumerate(range(start_page, start_page + page_count)):
            url = _build_liepin_resolved_search_url(
                query=query,
                city=city,
                page=current_page,
                city_code=city_code,
                city_route=city_route,
            )
            open_result = self.driver.open_url_in_new_tab(url, wait_seconds=wait_seconds)
            if not open_result.get("ok") or _requires_intervention(open_result):
                return LiepinCollectResult(
                    query=query,
                    city=city,
                    url=url,
                    jobs=jobs,
                    snapshot=_combined_snapshot(
                        snapshots,
                        {"open_result": open_result, "page": current_page, "url": url},
                    ),
                    page=start_page,
                    pages=page_count,
                    ok=False,
                    error=(_interruption_error(open_result) if _requires_intervention(open_result)
                           else str(open_result.get("error", "open_url_failed"))),
                )

            search_failure = self._submit_search_if_query_missing(query, wait_seconds=wait_seconds)

            remaining = max(1, limit - len(jobs))
            snapshot = search_failure or self._extract_snapshot(limit=remaining)
            snapshot["page"] = current_page
            snapshot["requestedUrl"] = url
            failure = _snapshot_failure(snapshot)
            if failure:
                if city:
                    snapshot["cityResolution"] = city_resolution
                snapshots.append(snapshot)
                snapshot_payload = (
                    snapshot
                    if len(snapshots) == 1
                    else _combined_snapshot(snapshots, {"error": failure, "page": current_page})
                )
                return LiepinCollectResult(
                    query=query,
                    city=city,
                    url=str(snapshot.get("url") or open_result.get("url") or url),
                    jobs=jobs,
                    snapshot=snapshot_payload,
                    page=start_page,
                    pages=page_count,
                    ok=False,
                    error=failure,
                )

            if city:
                verification = self._verify_snapshot_city(
                    snapshot,
                    city=city,
                    query=query,
                    city_code=city_code,
                    city_route=city_route,
                    city_resolution=city_resolution,
                )
                if (
                    not verification["verified"]
                    and index == 0
                    and not dynamic_resolution_attempted
                ):
                    self.city_resolver.forget(city, code=city_code)
                    dynamic_resolution_attempted = True
                    city_resolution = self._resolve_city(
                        city,
                        query=query,
                        wait_seconds=wait_seconds,
                    )
                    if _requires_intervention(city_resolution):
                        return LiepinCollectResult(
                            query=query, city=city,
                            url=str(city_resolution.get("url") or url), jobs=jobs,
                            snapshot=_combined_snapshot(snapshots, {"cityResolution": city_resolution}),
                            page=start_page, pages=page_count, ok=False,
                            error=_interruption_error(city_resolution),
                        )
                    replacement_code = str(city_resolution.get("code") or "")
                    replacement_route = _safe_liepin_city_route(
                        str(city_resolution.get("route") or "")
                    )
                    if replacement_code or replacement_route:
                        city_code = replacement_code
                        city_route = replacement_route
                        url = _build_liepin_resolved_search_url(
                            query=query,
                            city=city,
                            page=current_page,
                            city_code=city_code,
                            city_route=city_route,
                        )
                        open_result = self.driver.open_url_in_new_tab(
                            url,
                            wait_seconds=wait_seconds,
                        )
                        if _requires_intervention(open_result):
                            return LiepinCollectResult(
                                query=query, city=city, url=str(open_result.get("url") or url),
                                jobs=jobs, snapshot=_combined_snapshot(snapshots, {"open_result": open_result}),
                                page=start_page, pages=page_count, ok=False,
                                error=_interruption_error(open_result),
                            )
                        if open_result.get("ok"):
                            search_failure = self._submit_search_if_query_missing(
                                query,
                                wait_seconds=wait_seconds,
                            )
                            snapshot = search_failure or self._extract_snapshot(limit=remaining)
                            snapshot["page"] = current_page
                            snapshot["requestedUrl"] = url
                            failure = _snapshot_failure(snapshot)
                            if failure:
                                return LiepinCollectResult(
                                    query=query, city=city, url=str(snapshot.get("url") or url),
                                    jobs=jobs, snapshot=_combined_snapshot(snapshots, snapshot),
                                    page=start_page, pages=page_count, ok=False, error=failure,
                                )
                            if not failure:
                                verification = self._verify_snapshot_city(
                                    snapshot,
                                    city=city,
                                    query=query,
                                    city_code=city_code,
                                    city_route=city_route,
                                    city_resolution=city_resolution,
                                )
                snapshot["cityResolution"] = city_resolution
                snapshot["cityVerification"] = verification
                if not verification["verified"]:
                    snapshots.append(snapshot)
                    return LiepinCollectResult(
                        query=query,
                        city=city,
                        url=str(snapshot.get("url") or open_result.get("url") or url),
                        jobs=jobs,
                        snapshot=_combined_snapshot(snapshots),
                        page=start_page,
                        pages=page_count,
                        ok=False,
                        error="liepin_city_evidence_unverified",
                    )
                if city_code:
                    self.city_resolver.remember(city, city_code, verification)
                elif city_route:
                    numeric_verification = self.city_resolver.verify_evidence(
                        _snapshot_city_evidence(snapshot),
                        city=city,
                        query=query,
                    )
                    observed_code = str(numeric_verification.get("code") or "")
                    if numeric_verification["verified"] and observed_code:
                        self.city_resolver.remember(
                            city,
                            observed_code,
                            numeric_verification,
                        )
            termination_reason = _query_termination_reason(
                snapshot, query=query, city=city, page=current_page,
            )
            if termination_reason:
                snapshot["terminationReason"] = termination_reason
                snapshot["paginationExhausted"] = True
            snapshots.append(snapshot)

            cards = snapshot.get("cards", []) if isinstance(snapshot, dict) else []
            for card in cards:
                if not isinstance(card, dict):
                    continue
                job = parse_liepin_job(card, city_name=city)
                if city and job.city and job.city != city:
                    continue
                key = _job_dedupe_key(job, card)
                if key in seen:
                    continue
                seen.add(key)
                jobs.append(job)
                if len(jobs) >= limit:
                    break
            if termination_reason or len(jobs) >= limit:
                break
            if index < page_count - 1 and page_delay > 0:
                time.sleep(page_delay)

        return LiepinCollectResult(
            query=query,
            city=city,
            url=str((snapshots[0].get("url") if snapshots else "") or first_url),
            jobs=jobs,
            snapshot=_combined_snapshot(snapshots),
            page=start_page,
            pages=page_count,
        )

    def _verify_snapshot_city(
        self,
        snapshot: dict[str, Any],
        *,
        city: str,
        query: str,
        city_code: str,
        city_route: str,
        city_resolution: dict[str, Any],
    ) -> dict[str, Any]:
        evidence = _snapshot_city_evidence(snapshot)
        if city_route and not city_code:
            return self.city_resolver.verify_route_evidence(
                evidence,
                city=city,
                query=query,
                expected_route=city_route,
                previous_url=str(city_resolution.get("previous_url") or ""),
            )
        return self.city_resolver.verify_evidence(
            evidence,
            city=city,
            query=query,
            expected_code=city_code,
        )

    def _resolve_city(
        self,
        city: str,
        *,
        query: str,
        wait_seconds: int,
    ) -> dict[str, Any]:
        """Discover and verify a city code or official readable city route."""
        current_evidence = self._extract_city_search_evidence()
        if _requires_intervention(current_evidence):
            return _city_interruption_failure(current_evidence, city=city)
        current = self.city_resolver.verify_evidence(
            current_evidence,
            city=city,
            query=query,
        )
        if current["verified"]:
            return {
                "ok": True,
                "city": city,
                "code": current["code"],
                "source": "current_verified_result",
                "verification": current,
            }

        current_route = self._extract_city_route(city)
        if _requires_intervention(current_route):
            return _city_interruption_failure(current_route, city=city)
        current_route_url = _safe_liepin_city_route(
            str(current_route.get("route") or "")
        )
        current_url = str(current_evidence.get("url") or "")
        if (
            current_route.get("ok")
            and current_route_url
            and not _is_liepin_city_search_route(current_url, current_route_url)
        ):
            return self._verify_city_route(
                city,
                query=query,
                route_url=current_route_url,
                wait_seconds=wait_seconds,
                source="current_platform_city_links",
                route_evidence=current_route,
                previous_url=current_url,
            )

        search_directory_url = build_liepin_search_url(query)
        search_open = self.driver.open_url_in_new_tab(
            search_directory_url,
            wait_seconds=wait_seconds,
        )
        if _requires_intervention(search_open):
            return _city_interruption_failure(search_open, city=city)
        if search_open.get("ok"):
            search_route = self._extract_city_route(city)
            if _requires_intervention(search_route):
                return _city_interruption_failure(search_route, city=city)
            search_route_url = _safe_liepin_city_route(
                str(search_route.get("route") or "")
            )
            search_source_evidence = self._extract_city_search_evidence()
            if _requires_intervention(search_source_evidence):
                return _city_interruption_failure(search_source_evidence, city=city)
            search_source_url = str(
                search_source_evidence.get("url")
                or search_open.get("url")
                or search_directory_url
            )
            if search_route.get("ok") and search_route_url:
                return self._verify_city_route(
                    city,
                    query=query,
                    route_url=search_route_url,
                    wait_seconds=wait_seconds,
                    source="platform_search_city_links",
                    route_evidence=search_route,
                    previous_url=search_source_url,
                )

        open_result = self.driver.open_url_in_new_tab(
            LIEPIN_CITY_LIST_URL,
            wait_seconds=wait_seconds,
        )
        if _requires_intervention(open_result):
            return _city_interruption_failure(open_result, city=city)
        if not open_result.get("ok"):
            return {
                "ok": False,
                "city": city,
                "code": "",
                "source": "platform_city_directory",
                "url": str(open_result.get("url") or LIEPIN_CITY_LIST_URL),
                "error": str(open_result.get("error") or "open_url_failed"),
            }
        route = self._extract_city_route(city)
        if _requires_intervention(route):
            return _city_interruption_failure(route, city=city)
        directory_evidence = self._extract_city_search_evidence()
        if _requires_intervention(directory_evidence):
            return _city_interruption_failure(directory_evidence, city=city)
        directory_url = str(
            directory_evidence.get("url")
            or open_result.get("url")
            or LIEPIN_CITY_LIST_URL
        )
        route_url = _safe_liepin_city_route(str(route.get("route") or ""))
        if not route.get("ok") or not route_url:
            return {
                "ok": False,
                "city": city,
                "code": "",
                "source": "platform_city_directory",
                "url": directory_url,
                "error": "liepin_city_code_not_found",
                "candidate_count": int(route.get("candidateCount") or 0),
                "discovery_outcome": _city_directory_outcome(open_result, route),
            }
        return self._verify_city_route(
            city,
            query=query,
            route_url=route_url,
            wait_seconds=wait_seconds,
            source="platform_city_directory",
            route_evidence=route,
            previous_url=directory_url,
        )

    def _verify_city_route(
        self,
        city: str,
        *,
        query: str,
        route_url: str,
        wait_seconds: int,
        source: str,
        route_evidence: dict[str, Any],
        previous_url: str,
    ) -> dict[str, Any]:
        """Open one official city route and accept only cross-verified evidence."""
        city_search_url = build_liepin_city_route_search_url(route_url, query)
        route_open = self.driver.open_url_in_new_tab(
            city_search_url,
            wait_seconds=wait_seconds,
        )
        if _requires_intervention(route_open):
            return _city_interruption_failure(route_open, city=city)
        if not route_open.get("ok"):
            payload = {
                "ok": False,
                "city": city,
                "code": "",
                "source": source,
                "url": str(route_open.get("url") or city_search_url),
                "error": str(route_open.get("error") or "open_url_failed"),
                "candidate_count": int(route_evidence.get("candidateCount") or 0),
            }
            if route_evidence.get("discovery_actions"):
                payload["discovery_actions"] = list(
                    route_evidence.get("discovery_actions") or []
                )
            return payload
        evidence = self._extract_city_search_evidence()
        if _requires_intervention(evidence):
            return _city_interruption_failure(evidence, city=city)
        evidence.setdefault(
            "url",
            str(route_open.get("url") or city_search_url),
        )
        verification = self.city_resolver.verify_route_evidence(
            evidence,
            city=city,
            query=query,
            expected_route=route_url,
            previous_url=previous_url,
        )
        payload = {
            "ok": bool(verification["verified"]),
            "city": city,
            "code": "",
            "route": route_url if verification["verified"] else "",
            "source": source,
            "previous_url": previous_url,
            "url": str(route_open.get("url") or city_search_url),
            "verification": verification,
            "candidate_count": int(route_evidence.get("candidateCount") or 0),
            "error": "" if verification["verified"] else "liepin_city_evidence_unverified",
        }
        if route_evidence.get("discovery_actions"):
            payload["discovery_actions"] = list(
                route_evidence.get("discovery_actions") or []
            )
        return payload

    def _extract_city_route(self, city: str) -> dict[str, Any]:
        normalized_city = normalize_city_name(city)
        city_literal = json.dumps(normalized_city, ensure_ascii=False)
        js = rf"""
        (function(){{
          const mode = 'liepin_city_route_discovery';
          const expected = {city_literal};
          const normalize = function(value) {{
            return String(value || '').trim().replace(/市$/, '').replace(/猎聘|招聘网|招聘信息/g, '');
          }};
          const candidates = Array.from(document.querySelectorAll('a[href*="/city-"]'));
          const matched = candidates.find(function(el) {{
            const text = normalize(el.textContent || el.getAttribute('title') || '');
            const href = String(el.getAttribute('href') || '');
            return text === normalize(expected) && /\/city-[a-z0-9-]+\/?$/i.test(href);
          }});
          let route = '';
          try {{ route = matched ? new URL(matched.getAttribute('href'), location.origin).href : ''; }}
          catch (error) {{ route = ''; }}
          let pageKind = 'other';
          try {{
            const current = new URL(location.href);
            if (current.hostname === 'c.liepin.com') pageKind = 'candidate_home';
            else if (current.hostname === 'safe.liepin.com') pageKind = 'verification';
            else if (current.pathname.indexOf('/citylist') === 0) pageKind = 'city_directory';
            else if (current.pathname.indexOf('/zhaopin') >= 0) pageKind = 'search_result';
            else if (/^\/city-[a-z0-9-]+\/?$/i.test(current.pathname)) pageKind = 'city_landing';
          }} catch (error) {{ pageKind = 'unknown'; }}
          return JSON.stringify({{
            ok: Boolean(route),
            mode: mode,
            url: location.href || '',
            city: normalize(expected),
            route: route,
            candidateCount: candidates.length,
            pageKind: pageKind
          }});
        }})()
        """
        result = self.driver._exec_js(js)
        if isinstance(result, dict) and "raw" in result:
            try:
                parsed = json.loads(result["raw"])
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {
                    "ok": False,
                    "city": normalized_city,
                    "route": "",
                    "error": "liepin_city_route_parse_failed",
                }
        if isinstance(result, dict):
            return result
        return {
            "ok": False,
            "city": normalized_city,
            "route": "",
            "error": "liepin_city_code_not_found",
        }

    def _extract_city_search_evidence(self) -> dict[str, Any]:
        js = r"""
        (function(){
          const mode = 'liepin_city_search_evidence';
          const normalize = value => String(value || '').trim().replace(/市$/, '');
          const dq = document.querySelector('input[name="dq"]');
          const visible = el => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.display !== 'none'
              && style.visibility !== 'hidden'
              && Number(style.opacity || 1) > 0
              && rect.width > 0
              && rect.height > 0;
          };
          const key = Array.from(document.querySelectorAll(
            'input[name="key"], input[placeholder*="搜索职位"], input[placeholder*="搜职位"]'
          )).find(visible) || null;
          const meta = document.querySelector('meta[name="location"]');
          const metaContent = meta ? String(meta.getAttribute('content') || '') : '';
          const metaMatch = metaContent.match(/(?:^|;)\s*city=([^;]+)/);
          const title = document.title || '';
          const titleMatch = title.match(/【?([\u4e00-\u9fa5]{2,8})(?:招聘|人才)/);
          const visibleCityNode = Array.from(document.querySelectorAll('[data-name], .active, .selected')).find(el => {
            const value = normalize(el.getAttribute && el.getAttribute('data-name') || el.textContent);
            return value && dq && value === normalize(dq.getAttribute('data-name'));
          });
          let urlQuery = '';
          try { urlQuery = new URL(location.href).searchParams.get('key') || ''; }
          catch (error) { urlQuery = ''; }
          const jobCardCount = document.querySelectorAll('.job-card-pc-container, .job-card, .sojob-item-main, a[href*="/job/"]').length;
          const body = (document.body && (document.body.innerText || document.body.textContent) || '').slice(0, 3000);
          const noResults = jobCardCount === 0 && /暂无相关职位|暂时没有合适|没有找到相关职位|未找到相关职位/.test(body);
          return JSON.stringify({
            ok: true,
            mode,
            url: location.href || '',
            controlCity: dq ? normalize(dq.getAttribute('data-name')) : '',
            controlCode: dq ? String(dq.value || dq.getAttribute('value') || '').trim() : '',
            metaCity: metaMatch ? normalize(metaMatch[1]) : '',
            titleCity: titleMatch ? normalize(titleMatch[1]) : '',
            visibleCity: visibleCityNode ? normalize(visibleCityNode.getAttribute('data-name') || visibleCityNode.textContent) : '',
            inputQuery: key ? String(key.value || key.getAttribute('value') || key.getAttribute('data-name') || '').trim() : '',
            urlQuery,
            jobCardCount,
            noResults,
            resultSurface: jobCardCount > 0 || noResults
          });
        })()
        """
        result = self.driver._exec_js(js)
        if isinstance(result, dict) and "raw" in result:
            try:
                parsed = json.loads(result["raw"])
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        return result if isinstance(result, dict) else {}

    def _extract_snapshot(self, limit: int = 20) -> dict[str, Any]:
        """Extract visible job-card candidates from the current browser page."""
        js = build_liepin_snapshot_script(limit=limit)
        result = self.driver._exec_js(js)
        if isinstance(result, dict) and "raw" in result:
            try:
                parsed = json.loads(result["raw"])
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {"ok": False, "error": "snapshot_parse_failed", "raw": result["raw"]}
        return result if isinstance(result, dict) else {}

    def _submit_search_if_query_missing(self, query: str, wait_seconds: int = 8) -> dict[str, Any] | None:
        """Use the visible Liepin search bar when the URL shortcut is ignored.

        Liepin's current React search page can redirect old `?key=` URLs to a
        generic list. CDP-native typing keeps the frontend state in sync.
        """
        cdp = getattr(self.driver, "cdp", None)
        click_at = getattr(self.driver, "_click_at", None)
        if cdp is None or not callable(click_at):
            return
        current = self._extract_search_state()
        if _requires_intervention(current):
            return {**current, "ok": False, "error": _interruption_error(current),
                    "url": str(current.get("href") or current.get("url") or "")}
        original_path = _trusted_liepin_search_path(str(current.get("href") or ""))
        if current.get("ok") is False or not original_path:
            return {"ok": False, "error": "liepin_search_state_unknown"}
        try:
            parsed = urlsplit(str(current.get("href") or ""))
            url_query = parse_qs(parsed.query).get("key", [""])
        except ValueError:
            return None
        if parsed.scheme != "https" or parsed.hostname not in {"liepin.com", "www.liepin.com"}:
            return None
        # A verified no-result page is not evidence that submission failed.
        # Match the complete decoded parameter, never a body-text substring.
        if query and url_query == [query.strip()]:
            return
        input_target = current.get("input") if isinstance(current.get("input"), dict) else None
        button_target = current.get("button") if isinstance(current.get("button"), dict) else None
        if not input_target or not button_target:
            return

        click_at(input_target["x"], input_target["y"])
        _clear_visible_search_input(self.driver)
        _replace_focused_text(cdp, query)
        time.sleep(0.5)
        # Typing may trigger a redirect. Re-observe the page and the visible
        # control immediately before clicking instead of reusing coordinates.
        before_submit = self._extract_search_state()
        if _requires_intervention(before_submit):
            return {
                **before_submit, "ok": False, "error": _interruption_error(before_submit),
                "url": str(before_submit.get("href") or before_submit.get("url") or ""),
            }
        button_target = before_submit.get("button")
        if (
            before_submit.get("ok") is False
            or _trusted_liepin_search_path(str(before_submit.get("href") or "")) != original_path
            or not isinstance(before_submit.get("input"), dict)
            or not isinstance(button_target, dict)
            or not all(isinstance(button_target.get(key), (int, float)) for key in ("x", "y"))
        ):
            return {"ok": False, "error": "liepin_search_state_unknown"}
        if parse_qs(urlsplit(str(before_submit["href"])).query).get("key") == [query.strip()]:
            return None
        click_at(button_target["x"], button_target["y"])
        time.sleep(max(3, min(8, int(wait_seconds))))

    def _extract_search_state(self) -> dict[str, Any]:
        js = """
        (function(){
          function visible(el) {
            if (!el) return false;
            var style = window.getComputedStyle(el);
            var rect = el.getBoundingClientRect();
            return style.display !== 'none'
              && style.visibility !== 'hidden'
              && Number(style.opacity || 1) > 0
              && rect.width > 0
              && rect.height > 0;
          }
          function center(el) {
            var rect = el.getBoundingClientRect();
            return {
              x: Math.round(rect.left + rect.width / 2),
              y: Math.round(rect.top + rect.height / 2),
              w: Math.round(rect.width),
              h: Math.round(rect.height)
            };
          }
          var input = Array.from(document.querySelectorAll('input')).find(function(el) {
            return visible(el) && String(el.getAttribute('placeholder') || '').indexOf('搜索') >= 0;
          });
          var buttons = Array.from(document.querySelectorAll('span,button,a,div')).filter(function(el) {
            return visible(el) && (el.innerText || el.textContent || '').trim() === '搜索';
          }).map(function(el) {
            var data = center(el);
            data.tag = el.tagName;
            data.className = String(el.className || '');
            return data;
          }).sort(function(a, b) {
            return (a.w * a.h) - (b.w * b.h);
          });
          return JSON.stringify({
            ok: true,
            href: location.href || '',
            body: (document.body && (document.body.innerText || document.body.textContent) || '').slice(0, 600),
            input: input ? center(input) : null,
            button: buttons.length ? buttons[0] : null
          });
        })()
        """
        result = self.driver._exec_js(js)
        if isinstance(result, dict) and "raw" in result:
            try:
                parsed = json.loads(result["raw"])
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        return result if isinstance(result, dict) else {}


def write_liepin_snapshot(path: str | Path, payload: dict[str, Any]) -> None:
    """Persist a Liepin live-read snapshot or command payload."""
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _combined_snapshot(
    snapshots: list[dict[str, Any]],
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if len(snapshots) == 1 and fallback is None:
        return snapshots[0]
    if snapshots:
        payload: dict[str, Any] = {"ok": True, "pages": snapshots}
        if fallback is None and snapshots[-1].get("paginationExhausted") is True:
            payload["paginationExhausted"] = True
            payload["terminationReason"] = snapshots[-1]["terminationReason"]
        if fallback is not None:
            payload["ok"] = False
            payload["failure"] = fallback
        return payload
    return fallback or {}


def _job_dedupe_key(job: Job, raw: dict[str, Any]) -> str:
    job_id = liepin_job_id(raw)
    if job_id:
        return f"id:{job_id}"
    if job.url:
        return f"url:{job.url}"
    return f"text:{job.name}|{job.company}|{job.city}"


def _snapshot_failure(snapshot: dict[str, Any]) -> str:
    """Classify known live read-only collect blocking states."""
    if _requires_intervention(snapshot):
        return _interruption_error(snapshot)
    if snapshot.get("loginRequired"):
        return "liepin_login_required"
    if snapshot.get("loginPromptPresent"):
        return "liepin_login_required"
    url = str(snapshot.get("url", ""))
    title = str(snapshot.get("title", ""))
    if "/login" in url or "登录" in title:
        return "liepin_login_required"
    if snapshot.get("ok") is False:
        return str(snapshot.get("error") or "liepin_snapshot_failed")
    return ""


def _verification_required(payload: dict[str, Any]) -> bool:
    if payload.get("error") == "liepin_verification_required":
        return True
    try:
        parsed = urlsplit(str(payload.get("url") or payload.get("href") or ""))
        return bool(
            parsed.scheme == "https"
            and parsed.hostname == "safe.liepin.com"
            and parsed.username is None
            and parsed.password is None
            and parsed.port in {None, 443}
        )
    except ValueError:
        return False


def _trusted_liepin_search_path(value: str) -> str:
    try:
        parsed = urlsplit(value)
        path = parsed.path.rstrip("/")
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"liepin.com", "www.liepin.com"}
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in {None, 443}
        ):
            return ""
        if path == "/zhaopin":
            return path
        if path.endswith("/zhaopin"):
            route = "https://www.liepin.com" + path.removesuffix("/zhaopin") + "/"
            if _safe_liepin_city_route(route):
                return path
    except ValueError:
        pass
    return ""


def _verification_prompt(payload: dict[str, Any]) -> str:
    prompt = str(payload.get("user_prompt") or "").strip()
    if prompt:
        return prompt
    for key in ("failure", "open_result", "cityResolution"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            prompt = _verification_prompt(nested)
            if prompt:
                return prompt
    for page in reversed(payload.get("pages") or []):
        if isinstance(page, dict):
            prompt = _verification_prompt(page)
            if prompt:
                return prompt
    return ""


def _requires_intervention(payload: dict[str, Any]) -> bool:
    return bool(
        _verification_required(payload)
        or payload.get("requires_user_action") is True
        or payload.get("error") in {"liepin_page_state_unknown", "liepin_search_state_unknown"}
    )


def _interruption_error(payload: dict[str, Any]) -> str:
    if _verification_required(payload):
        return "liepin_verification_required"
    return str(payload.get("error") or "liepin_page_state_unknown")


def _interruption_payload(snapshot: dict[str, Any], *, error: str) -> dict[str, Any]:
    if _requires_intervention(snapshot) and _interruption_error(snapshot) == error:
        return snapshot
    nested_items = [snapshot.get(key) for key in ("failure", "open_result", "cityResolution")]
    nested_items.extend(reversed(snapshot.get("pages") or []))
    for nested in nested_items:
        if isinstance(nested, dict):
            result = _interruption_payload(nested, error=error)
            if result:
                return result
    return {}


def _city_interruption_failure(payload: dict[str, Any], *, city: str) -> dict[str, Any]:
    return {
        **payload,
        "ok": False, "city": city, "code": "", "route": "", "source": "page_intervention",
        "error": _interruption_error(payload), "requires_user_action": True,
    }


def _query_termination_reason(
    snapshot: dict[str, Any], *, query: str, city: str, page: int,
) -> str:
    """Accept terminal evidence only on this query's independently verified result page."""
    if _snapshot_failure(snapshot):
        return ""
    evidence = _snapshot_city_evidence(snapshot)
    if city and snapshot.get("cityVerification", {}).get("verified") is not True:
        return ""
    try:
        parsed = urlsplit(str(snapshot.get("url") or ""))
        requested = urlsplit(str(snapshot.get("requestedUrl") or ""))
        port = parsed.port
        params = parse_qs(parsed.query)
        requested_params = parse_qs(requested.query)
        current_page = params.get("currentPage", [])
        card_count = int(evidence.get("jobCardCount") or 0)
    except (TypeError, ValueError):
        return ""
    path = parsed.path.rstrip("/")
    official_result = (
        parsed.scheme == "https"
        and parsed.hostname in {"liepin.com", "www.liepin.com"}
        and parsed.username is None and parsed.password is None
        and port in {None, 443}
        and (path == "/zhaopin" or (
            path.startswith("/city-") and path.endswith("/zhaopin")
            and len(path.split("/")) == 3
        ))
        and path == requested.path.rstrip("/")
        and all(params.get(key) == value for key, value in requested_params.items()
                if key in {"city", "dq"})
    )
    if not (
        official_result
        and params.get("key") == [query.strip()]
        and str(evidence.get("inputQuery") or "").strip() == query.strip()
        and str(evidence.get("urlQuery") or "").strip() == query.strip()
        and evidence.get("resultSurface") is True
    ):
        return ""
    if evidence.get("noResults") is True and card_count == 0 and not snapshot.get("cards"):
        return "no_results"
    pagination = snapshot.get("paginationEvidence")
    if not isinstance(pagination, dict) or card_count <= 0:
        return ""
    # The visible active page and URL page must agree; absence of a next
    # control, empty parsed cards, or a stale disabled control proves nothing.
    if (
        pagination.get("source") == "visible_pagination"
        and pagination.get("currentPage") == page
        and current_page == [str(page - 1)]
        and pagination.get("nextControlPresent") is True
        and pagination.get("nextDisabled") is True
        and pagination.get("nextEnabled") is False
    ):
        return "last_page"
    return ""


def _snapshot_city_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    evidence = snapshot.get("cityEvidence")
    if not isinstance(evidence, dict):
        return {}
    payload = dict(evidence)
    payload.setdefault("url", str(snapshot.get("url") or ""))
    return payload


def _redacted_city_diagnostic(snapshot: dict[str, Any]) -> dict[str, Any]:
    resolution = snapshot.get("cityResolution")
    if not isinstance(resolution, dict):
        return {}
    verification = resolution.get("verification")
    verification = verification if isinstance(verification, dict) else {}
    diagnostic = {
        "city": str(resolution.get("city") or ""),
        "source": str(resolution.get("source") or ""),
        "code_resolved": bool(resolution.get("code")),
        "route_verified": verification.get("route_verified") is True,
        "route_changed": verification.get("route_changed") is True,
        "verified": verification.get("verified") is True,
        "city_sources": list(verification.get("city_sources") or []),
        "query_sources": list(verification.get("query_sources") or []),
        "result_state": str(verification.get("result_state") or "unknown"),
        "conflicts": list(verification.get("conflicts") or []),
    }
    discovery_outcome = str(resolution.get("discovery_outcome") or "")
    if discovery_outcome:
        diagnostic["discovery_outcome"] = discovery_outcome
    return diagnostic


def _city_directory_outcome(
    open_result: dict[str, Any],
    route_evidence: dict[str, Any],
) -> str:
    """Classify a failed official city-directory visit without exposing its URL."""
    page_kind = str(route_evidence.get("pageKind") or "")
    if page_kind == "candidate_home":
        return "candidate_home_redirect"
    if page_kind == "verification":
        return "verification_required"
    if page_kind == "city_directory":
        return "city_link_not_found"
    try:
        parsed = urlsplit(str(open_result.get("url") or ""))
    except ValueError:
        return "unknown_page"
    if parsed.hostname == "c.liepin.com":
        return "candidate_home_redirect"
    if parsed.hostname == "safe.liepin.com":
        return "verification_required"
    return "unexpected_official_page" if parsed.hostname else "unknown_page"


def _safe_liepin_city_route(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme != "https" or parsed.hostname not in {"liepin.com", "www.liepin.com"}:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 1 or not parts[0].startswith("city-"):
        return ""
    slug = parts[0].removeprefix("city-")
    if not slug or not all(char.isalnum() or char == "-" for char in slug):
        return ""
    return f"https://www.liepin.com/city-{slug}/"


def _is_liepin_city_search_route(value: str, route: str) -> bool:
    safe_route = _safe_liepin_city_route(route)
    if not safe_route:
        return False
    try:
        candidate = urlsplit(value)
        expected = urlsplit(safe_route)
    except ValueError:
        return False
    expected_path = expected.path.rstrip("/") + "/zhaopin"
    return bool(
        candidate.scheme == "https"
        and candidate.hostname in {"liepin.com", "www.liepin.com"}
        and candidate.path.rstrip("/") == expected_path
    )


def _replace_focused_text(cdp: Any, text: str) -> None:
    cdp.send(
        "Input.dispatchKeyEvent",
        {
            "type": "keyDown",
            "key": "Meta",
            "code": "MetaLeft",
            "windowsVirtualKeyCode": 91,
            "nativeVirtualKeyCode": 91,
            "modifiers": 4,
        },
    )
    cdp.send(
        "Input.dispatchKeyEvent",
        {
            "type": "keyDown",
            "key": "a",
            "code": "KeyA",
            "windowsVirtualKeyCode": 65,
            "nativeVirtualKeyCode": 65,
            "modifiers": 4,
        },
    )
    cdp.send(
        "Input.dispatchKeyEvent",
        {
            "type": "keyUp",
            "key": "a",
            "code": "KeyA",
            "windowsVirtualKeyCode": 65,
            "nativeVirtualKeyCode": 65,
            "modifiers": 4,
        },
    )
    cdp.send(
        "Input.dispatchKeyEvent",
        {
            "type": "keyUp",
            "key": "Meta",
            "code": "MetaLeft",
            "windowsVirtualKeyCode": 91,
            "nativeVirtualKeyCode": 91,
        },
    )
    cdp.send(
        "Input.dispatchKeyEvent",
        {
            "type": "keyDown",
            "key": "Backspace",
            "code": "Backspace",
            "windowsVirtualKeyCode": 8,
            "nativeVirtualKeyCode": 8,
        },
    )
    cdp.send(
        "Input.dispatchKeyEvent",
        {
            "type": "keyUp",
            "key": "Backspace",
            "code": "Backspace",
            "windowsVirtualKeyCode": 8,
            "nativeVirtualKeyCode": 8,
        },
    )
    cdp.send("Input.insertText", {"text": text})


def _clear_visible_search_input(driver: Any) -> None:
    js = """
    (function(){
      function visible(el) {
        if (!el) return false;
        var style = window.getComputedStyle(el);
        var rect = el.getBoundingClientRect();
        return style.display !== 'none'
          && style.visibility !== 'hidden'
          && Number(style.opacity || 1) > 0
          && rect.width > 0
          && rect.height > 0;
      }
      var input = Array.from(document.querySelectorAll('input')).find(function(el) {
        return visible(el) && String(el.getAttribute('placeholder') || '').indexOf('搜索') >= 0;
      });
      if (!input) return JSON.stringify({ok: false, error: 'search_input_not_found'});
      input.focus();
      var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      setter.call(input, '');
      input.dispatchEvent(new InputEvent('input', {
        bubbles: true,
        inputType: 'deleteContentBackward',
        data: null
      }));
      input.dispatchEvent(new Event('change', {bubbles: true}));
      try { input.setSelectionRange(0, 0); } catch (e) {}
      return JSON.stringify({ok: true, value: input.value || ''});
    })()
    """
    try:
        driver._exec_js(js)
    except Exception:
        return
