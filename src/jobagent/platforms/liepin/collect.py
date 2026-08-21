"""Liepin live read-only collection spike."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlsplit, urlunsplit

from jobagent.domain.models import Job
from jobagent.drivers.boss import create_driver

from .constants import LIEPIN_LOGIN_USER_PROMPT
from .city_resolver import BUNDLED_CITY_CODES, LiepinCityResolver, normalize_city_name
from .parser import liepin_job_id, parse_liepin_job
from .selectors import build_liepin_snapshot_script

LIEPIN_CITY_CODES = BUNDLED_CITY_CODES

LIEPIN_SEARCH_URL = "https://www.liepin.com/zhaopin/"
LIEPIN_CITY_LIST_URL = "https://www.liepin.com/citylist/"


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


def build_liepin_city_route_search_url(route: str, query: str) -> str:
    """Build a search URL from an official city-directory route."""
    safe_route = _safe_liepin_city_route(route)
    if not safe_route:
        raise ValueError("Liepin city route is not an official city URL")
    parsed = urlsplit(safe_route)
    path = parsed.path.rstrip("/") + "/zhaopin/"
    query_string = urlencode(
        {
            "currentPage": 0,
            "pageSize": 40,
            "key": query,
            "scene": "input",
            "sfrom": "search_job_pc",
        }
    )
    return urlunsplit(("https", "www.liepin.com", path, query_string, ""))


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
        if self.error == "liepin_login_required":
            payload["message"] = "Liepin live collect requires an active logged-in session."
            payload["requires_user_action"] = True
            payload["user_action"] = "login_liepin"
            payload["user_prompt"] = LIEPIN_LOGIN_USER_PROMPT
            payload["next_suggested"] = "jobagent liepin login"
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
        city_resolution: dict[str, Any] = {
            "ok": True,
            "city": city,
            "code": city_code,
            "source": candidate_source,
        }
        dynamic_resolution_attempted = False
        if city and not city_code:
            dynamic_resolution_attempted = True
            city_resolution = self._resolve_city_code(
                city,
                query=query,
                wait_seconds=wait_seconds,
            )
            city_code = str(city_resolution.get("code") or "")
            if not city_code:
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
        first_url = build_liepin_search_url(
            query,
            city,
            page=start_page,
            city_code=city_code,
        )

        for index, current_page in enumerate(range(start_page, start_page + page_count)):
            url = build_liepin_search_url(
                query,
                city,
                page=current_page,
                city_code=city_code,
            )
            open_result = self.driver.open_url_in_new_tab(url, wait_seconds=wait_seconds)
            if not open_result.get("ok"):
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
                    error=str(open_result.get("error", "open_url_failed")),
                )

            self._submit_search_if_query_missing(query, wait_seconds=wait_seconds)

            remaining = max(1, limit - len(jobs))
            snapshot = self._extract_snapshot(limit=remaining)
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
                verification = self.city_resolver.verify_evidence(
                    _snapshot_city_evidence(snapshot),
                    city=city,
                    query=query,
                    expected_code=city_code,
                )
                if (
                    not verification["verified"]
                    and index == 0
                    and not dynamic_resolution_attempted
                ):
                    self.city_resolver.forget(city, code=city_code)
                    dynamic_resolution_attempted = True
                    city_resolution = self._resolve_city_code(
                        city,
                        query=query,
                        wait_seconds=wait_seconds,
                    )
                    replacement_code = str(city_resolution.get("code") or "")
                    if replacement_code:
                        city_code = replacement_code
                        url = build_liepin_search_url(
                            query,
                            city,
                            page=current_page,
                            city_code=city_code,
                        )
                        open_result = self.driver.open_url_in_new_tab(
                            url,
                            wait_seconds=wait_seconds,
                        )
                        if open_result.get("ok"):
                            self._submit_search_if_query_missing(
                                query,
                                wait_seconds=wait_seconds,
                            )
                            snapshot = self._extract_snapshot(limit=remaining)
                            snapshot["page"] = current_page
                            snapshot["requestedUrl"] = url
                            failure = _snapshot_failure(snapshot)
                            if not failure:
                                verification = self.city_resolver.verify_evidence(
                                    _snapshot_city_evidence(snapshot),
                                    city=city,
                                    query=query,
                                    expected_code=city_code,
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
                self.city_resolver.remember(city, city_code, verification)
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
            if len(jobs) >= limit:
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

    def _resolve_city_code(
        self,
        city: str,
        *,
        query: str,
        wait_seconds: int,
    ) -> dict[str, Any]:
        """Discover a city through Liepin's own directory and verify its result page."""
        current_evidence = self._extract_city_search_evidence()
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

        open_result = self.driver.open_url_in_new_tab(
            LIEPIN_CITY_LIST_URL,
            wait_seconds=wait_seconds,
        )
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
        route_url = _safe_liepin_city_route(str(route.get("route") or ""))
        if not route.get("ok") or not route_url:
            return {
                "ok": False,
                "city": city,
                "code": "",
                "source": "platform_city_directory",
                "url": str(open_result.get("url") or LIEPIN_CITY_LIST_URL),
                "error": "liepin_city_code_not_found",
                "candidate_count": int(route.get("candidateCount") or 0),
            }
        city_search_url = build_liepin_city_route_search_url(route_url, query)
        route_open = self.driver.open_url_in_new_tab(
            city_search_url,
            wait_seconds=wait_seconds,
        )
        if not route_open.get("ok"):
            return {
                "ok": False,
                "city": city,
                "code": "",
                "source": "platform_city_directory",
                "url": str(route_open.get("url") or city_search_url),
                "error": str(route_open.get("error") or "open_url_failed"),
            }
        evidence = self._extract_city_search_evidence()
        verification = self.city_resolver.verify_evidence(
            evidence,
            city=city,
            query=query,
        )
        return {
            "ok": bool(verification["verified"]),
            "city": city,
            "code": (
                str(verification.get("code") or "")
                if verification["verified"]
                else ""
            ),
            "source": "platform_city_directory",
            "url": str(route_open.get("url") or city_search_url),
            "verification": verification,
            "error": "" if verification["verified"] else "liepin_city_evidence_unverified",
        }

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
          return JSON.stringify({{
            ok: Boolean(route),
            mode: mode,
            city: normalize(expected),
            route: route,
            candidateCount: candidates.length
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
          const key = document.querySelector('input[name="key"]');
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
          const noResults = /暂无相关职位|暂时没有合适|没有找到相关职位|非常抱歉/.test(body);
          return JSON.stringify({
            ok: true,
            mode,
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

    def _submit_search_if_query_missing(self, query: str, wait_seconds: int = 8) -> None:
        """Use the visible Liepin search bar when the URL shortcut is ignored.

        Liepin's current React search page can redirect old `?key=` URLs to a
        generic list. CDP-native typing keeps the frontend state in sync.
        """
        cdp = getattr(self.driver, "cdp", None)
        click_at = getattr(self.driver, "_click_at", None)
        if cdp is None or not callable(click_at):
            return
        current = self._extract_search_state()
        href = unquote(str(current.get("href", "")))
        body = str(current.get("body", ""))
        no_results = "非常抱歉" in body or "暂时没有合适" in body
        if query and not no_results and (f"key={query}" in href or query in body[:300]):
            return
        input_target = current.get("input") if isinstance(current.get("input"), dict) else None
        button_target = current.get("button") if isinstance(current.get("button"), dict) else None
        if not input_target or not button_target:
            return

        click_at(input_target["x"], input_target["y"])
        _clear_visible_search_input(self.driver)
        _replace_focused_text(cdp, query)
        time.sleep(0.5)
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


def _snapshot_city_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    evidence = snapshot.get("cityEvidence")
    return evidence if isinstance(evidence, dict) else {}


def _redacted_city_diagnostic(snapshot: dict[str, Any]) -> dict[str, Any]:
    resolution = snapshot.get("cityResolution")
    if not isinstance(resolution, dict):
        return {}
    verification = resolution.get("verification")
    verification = verification if isinstance(verification, dict) else {}
    return {
        "city": str(resolution.get("city") or ""),
        "source": str(resolution.get("source") or ""),
        "code_resolved": bool(resolution.get("code")),
        "verified": verification.get("verified") is True,
        "city_sources": list(verification.get("city_sources") or []),
        "query_sources": list(verification.get("query_sources") or []),
        "result_state": str(verification.get("result_state") or "unknown"),
        "conflicts": list(verification.get("conflicts") or []),
    }


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
