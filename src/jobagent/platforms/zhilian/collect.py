"""Zhilian live read-only collection spike."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from jobagent.domain.models import Job
from jobagent.drivers.boss import create_driver

from .constants import ZHILIAN_LOGIN_USER_PROMPT
from .detail import (
    build_zhilian_detail_snapshot_script,
    merge_zhilian_detail_into_job,
    unwrap_zhilian_detail_js_result,
)
from .parser import parse_zhilian_job, zhilian_job_id
from .selectors import build_zhilian_snapshot_script


def build_zhilian_search_url(query: str, city: str = "", page: int = 1) -> str:
    """Build a human-search URL for the Zhilian read-only spike."""
    url = f"https://sou.zhaopin.com/?kw={quote(query)}"
    if city:
        url += f"&jl={quote(city)}"
    if page > 1:
        url += f"&p={page}"
    return url


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
        if include_snapshot:
            payload["snapshot"] = self.snapshot
        return payload


class ZhilianReadOnlyCollector:
    """Collect Zhilian search cards without applying or sending messages."""

    def __init__(self, driver: Any | None = None):
        self.driver = driver or create_driver()

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
        first_url = build_zhilian_search_url(query, city, page=start_page)

        for index, current_page in enumerate(range(start_page, start_page + page_count)):
            url = build_zhilian_search_url(query, city, page=current_page)
            open_result = self.driver.open_url_in_new_tab(url, wait_seconds=wait_seconds)
            if not open_result.get("ok"):
                return ZhilianCollectResult(
                    query=query,
                    city=city,
                    url=url,
                    jobs=jobs,
                    snapshot=_combined_snapshot(snapshots, {"open_result": open_result, "page": current_page, "url": url}),
                    page=start_page,
                    pages=page_count,
                    ok=False,
                    error=str(open_result.get("error", "open_url_failed")),
                )

            remaining = max(1, limit - len(jobs))
            # When city filter is active, fetch 2× more cards per page to
            # compensate for non-matching ones that will be filtered out
            # (Zhilian's sou endpoint ignores URL city params server-side).
            fetch_limit = remaining * 2 if city else remaining
            snapshot = self._extract_snapshot(limit=fetch_limit)
            snapshot["page"] = current_page
            snapshot["requestedUrl"] = url
            snapshots.append(snapshot)
            failure = _snapshot_failure(snapshot)
            if failure:
                return ZhilianCollectResult(
                    query=query,
                    city=city,
                    url=str(snapshot.get("url") or open_result.get("url") or url),
                    jobs=jobs,
                    snapshot=_combined_snapshot(snapshots, {"error": failure, "page": current_page}),
                    page=start_page,
                    pages=page_count,
                    ok=False,
                    error=failure,
                )

            cards = snapshot.get("cards", []) if isinstance(snapshot, dict) else []
            for card in cards:
                if not isinstance(card, dict):
                    continue
                job = parse_zhilian_job(card, city_name=city)
                # Apply city post-filter (Zhilian URL params don't filter server-side)
                if not _city_matches(job.city, city):
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

        detail_limit = max(0, int(detail_limit))
        if detail_limit and jobs:
            detail_result = self._hydrate_from_details(jobs, detail_limit=detail_limit, wait_seconds=wait_seconds)
            jobs = detail_result["jobs"]
            detail_snapshots = detail_result["snapshots"]
            failure = detail_result.get("error", "")
            if failure:
                return ZhilianCollectResult(
                    query=query,
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
            query=query,
            city=city,
            url=str((snapshots[0].get("url") if snapshots else "") or first_url),
            jobs=jobs,
            snapshot=combined_snapshot,
            page=start_page,
            pages=page_count,
        )

    def _extract_snapshot(self, limit: int = 20) -> dict[str, Any]:
        js = build_zhilian_snapshot_script(limit=limit)
        result = self.driver._exec_js(js)
        if isinstance(result, dict) and "raw" in result:
            try:
                parsed = json.loads(result["raw"])
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {"ok": False, "error": "snapshot_parse_failed", "raw": result["raw"]}
        return result if isinstance(result, dict) else {}

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
    """Build a stable dedup key for a Zhilian job.

    Priority:
      1. Explicit job id attribute on the card.
      2. Job id extracted from URL path (/jobdetail/CCL...J....htm) — Zhilian
         decorates the same job's URL with different query params per page
         (refcode, srccode, preactionid, …).
      3. Full URL fallback.
      4. Name+company+city text fallback when no URL at all.
    """
    import re

    job_id = zhilian_job_id(raw)
    if job_id:
        return f"id:{job_id}"
    if job.url:
        # Zhilian job URLs look like:
        #   https://www.zhaopin.com/jobdetail/CCL1212903980J40854050007.htm?refcode=...
        m = re.search(r"/jobdetail/([A-Z0-9]+)\.htm", job.url)
        if m:
            return f"job:{m.group(1)}"
        return f"url:{job.url}"
    return f"text:{job.name}|{job.company}|{job.city}"


def _city_matches(job_city: str, requested_city: str) -> bool:
    """Check whether a job's parsed city matches the user-requested city.

    Zhilian's sou endpoint ignores URL city params server-side (jl= param is
    accepted but doesn't filter results), so search results are a mix of
    cities. This post-filter is the only reliable way to apply a city filter.

    Matches when:
      - No requested_city (filter disabled), OR
      - job_city starts with requested_city (handles "北京" and "北京·朝阳区"), OR
      - job_city is empty (permissive — better to over-include than drop).
    """
    if not requested_city:
        return True
    if not job_city:
        return True  # don't drop cards with missing city field
    # Zhilian uses "·" as city/area separator ("北京·朝阳区"), Liepin uses "-".
    # Split on both to get just the top-level city.
    for sep in ("·", "-"):
        if sep in job_city:
            job_city = job_city.split(sep)[0].strip()
            break
    for sep in ("·", "-"):
        if sep in requested_city:
            requested_city = requested_city.split(sep)[0].strip()
            break
    return job_city == requested_city


def _snapshot_failure(snapshot: dict[str, Any]) -> str:
    if snapshot.get("loginRequired"):
        return "zhilian_login_required"
    url = str(snapshot.get("url", ""))
    title = str(snapshot.get("title", ""))
    if "passport" in url or "login" in url or "登录" in title:
        return "zhilian_login_required"
    if snapshot.get("ok") is False:
        return str(snapshot.get("error") or "zhilian_snapshot_failed")
    return ""
