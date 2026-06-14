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
    """Build a human-search URL for the live read-only spike."""
    url = f"https://www.liepin.com/zhaopin/?key={quote(query)}"
    if city:
        url += f"&dq={quote(city)}"
    if page > 1:
        url += f"&currentPage={page}"
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
        """Open one or more Liepin search pages and extract visible job cards."""
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
            snapshot = self._extract_snapshot(limit=remaining)
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
