"""Liepin live read-only collection spike."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from jobagent.domain.models import Job
from jobagent.drivers.boss import create_driver

from .constants import LIEPIN_LOGIN_USER_PROMPT
from .parser import liepin_job_id, parse_liepin_job
from .selectors import build_liepin_snapshot_script


def build_liepin_search_url(query: str, city: str = "", page: int = 1) -> str:
    """Build a human-search URL for the live read-only spike.

    Path: /sojob/ (not /zhaopin/).
      The /zhaopin/ path is a SEO landing page that returns 0 results when
      accessed under a logged-in session (Liepin shows "非常抱歉！暂时没有
      合适的职位"). /sojob/ is the real search endpoint.

    City param: &city=<name>.
      Older formats (&dq=<name>) get silently dropped by Liepin's redirect.
    """
    url = f"https://www.liepin.com/sojob/?key={quote(query)}"
    if city:
        url += f"&city={quote(city)}"
    if page > 1:
        url += f"&curPage={page}"
    return url


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
        elif self.ok:
            payload["next_suggested"] = "jobagent liepin rank --input <liepin.raw.json> --output <liepin.ranked.json>"
        if include_snapshot:
            payload["snapshot"] = self.snapshot
        return payload


class LiepinReadOnlyCollector:
    """Collect Liepin search cards without applying or sending messages."""

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
    ) -> LiepinCollectResult:
        """Open one or more Liepin search pages and extract visible job cards.

        City filtering: Liepin's sojob endpoint silently ignores URL city
        params (city=, cityCode=, dq= all return the same mixed-city results).
        We apply a Python-side post-filter on the parsed ``city`` field instead.
        To compensate for cards dropped by the filter, we request more cards
        per page from the snapshot script (2× the user's limit).
        """
        if not query:
            raise ValueError("query is required for live Liepin read-only collect")

        start_page = max(1, int(page))
        page_count = max(1, int(pages))
        limit = max(1, int(limit))
        jobs: list[Job] = []
        seen: set[str] = set()
        snapshots: list[dict[str, Any]] = []
        first_url = build_liepin_search_url(query, city, page=start_page)

        for index, current_page in enumerate(range(start_page, start_page + page_count)):
            url = build_liepin_search_url(query, city, page=current_page)
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

            remaining = max(1, limit - len(jobs))
            # When city filter is active, fetch 2× more cards per page to
            # compensate for non-matching ones that will be filtered out.
            fetch_limit = remaining * 2 if city else remaining
            snapshot = self._extract_snapshot(limit=fetch_limit)
            snapshot["page"] = current_page
            snapshot["requestedUrl"] = url
            snapshots.append(snapshot)
            failure = _snapshot_failure(snapshot)
            if failure:
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

            cards = snapshot.get("cards", []) if isinstance(snapshot, dict) else []
            for card in cards:
                if not isinstance(card, dict):
                    continue
                job = parse_liepin_job(card, city_name=city)
                # Apply city post-filter (Liepin URL params don't filter server-side)
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

        return LiepinCollectResult(
            query=query,
            city=city,
            url=str((snapshots[0].get("url") if snapshots else "") or first_url),
            jobs=jobs,
            snapshot=_combined_snapshot(snapshots),
            page=start_page,
            pages=page_count,
        )

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
    """Build a stable dedup key for a Liepin job.

    Priority:
      1. Explicit jobId attribute on the card (rare on current Liepin DOM).
      2. Job ID extracted from URL path (/job/<id>.shtml). This is critical
         because Liepin decorates the same job's URL with different query
         params per page (d_posi, skId, fkId, ckId, curPage, index, …).
         Using the full URL as key treated the same job on page 1 and page 2
         as different, inflating counts and defeating pagination dedup.
      3. Full URL fallback (last resort).
      4. Name+company+city text fallback when no URL at all.
    """
    import re

    job_id = liepin_job_id(raw)
    if job_id:
        return f"id:{job_id}"
    if job.url:
        m = re.search(r"/job/(\d+)\.shtml", job.url)
        if m:
            return f"job:{m.group(1)}"
        return f"url:{job.url}"
    return f"text:{job.name}|{job.company}|{job.city}"


def _city_matches(job_city: str, requested_city: str) -> bool:
    """Check whether a job's parsed city matches the user-requested city.

    Liepin's sojob endpoint ignores URL city params server-side, so the
    search results are a mix of cities. This post-filter is the only
    reliable way to apply a city filter.

    Matches when:
      - No requested_city (filter disabled), OR
      - job_city starts with requested_city (handles "北京" and "北京-朝阳区"), OR
      - Either side is empty (permissive — better to over-include than drop).
    """
    if not requested_city:
        return True
    if not job_city:
        return True  # don't drop cards with missing city field
    return job_city.split("-")[0].strip() == requested_city.split("-")[0].strip()


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
