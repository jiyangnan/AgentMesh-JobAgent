"""51Job live read-only collection."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from jobagent.domain.models import Job
from jobagent.drivers.boss import create_driver

from .constants import JOB51_LOGIN_USER_PROMPT, JOB51_SEARCH_URL
from .parser import parse_job51_job, job51_job_id
from .selectors import build_job51_snapshot_script


JOB51_CITY_CODES = {
    "北京": "010000",
    "上海": "020000",
    "广州": "030200",
    "深圳": "040000",
    "武汉": "180200",
    "西安": "200200",
    "杭州": "080200",
    "南京": "070200",
    "成都": "090200",
    "重庆": "060000",
    "东莞": "030800",
    "苏州": "070300",
}


def build_job51_search_url(query: str, city: str = "", page: int = 1) -> str:
    url = f"{JOB51_SEARCH_URL}?keyword={quote(query)}"
    city_code = _job51_city_code(city)
    if city_code:
        url += f"&jobArea={city_code}"
    if page > 1:
        url += f"&pageNum={int(page)}"
    return url


@dataclass
class Job51CollectResult:
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
            "platform": "51job",
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
        if self.error == "job51_login_required":
            payload["message"] = "51Job live collect requires an active logged-in browser session."
            payload["requires_user_action"] = True
            payload["user_action"] = "login_51job"
            payload["user_prompt"] = JOB51_LOGIN_USER_PROMPT
            payload["next_suggested"] = "jobagent 51job login"
        elif self.ok:
            payload["next_suggested"] = "jobagent 51job rank --input <51job.raw.json> --output <51job.ranked.json>"
        if include_snapshot:
            payload["snapshot"] = self.snapshot
        return payload


class Job51ReadOnlyCollector:
    """Collect 51Job search cards without applying or sending messages."""

    def __init__(self, driver: Any | None = None):
        self.driver = driver or create_driver(platform="51job")

    def collect(
        self,
        query: str,
        city: str = "",
        limit: int = 20,
        wait_seconds: int = 8,
        page: int = 1,
        pages: int = 1,
        page_delay: float = 3.0,
    ) -> Job51CollectResult:
        if not query:
            raise ValueError("query is required for live 51Job read-only collect")

        start_page = max(1, int(page))
        page_count = max(1, int(pages))
        limit = max(1, int(limit))
        jobs: list[Job] = []
        seen: set[str] = set()
        snapshots: list[dict[str, Any]] = []
        first_url = build_job51_search_url(query, city, page=start_page)

        for index, current_page in enumerate(range(start_page, start_page + page_count)):
            url = build_job51_search_url(query, city, page=current_page)
            open_result = self.driver.open_url_in_new_tab(url, wait_seconds=wait_seconds)
            if not open_result.get("ok"):
                return Job51CollectResult(
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
            snapshot = self._extract_snapshot(limit=remaining)
            snapshot["page"] = current_page
            snapshot["requestedUrl"] = url
            snapshots.append(snapshot)
            failure = _snapshot_failure(snapshot)
            if failure:
                return Job51CollectResult(
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
                job = parse_job51_job(card, city_name=city)
                if city and job.city and city not in job.city:
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

        return Job51CollectResult(
            query=query,
            city=city,
            url=str((snapshots[0].get("url") if snapshots else "") or first_url),
            jobs=jobs,
            snapshot=_combined_snapshot(snapshots),
            page=start_page,
            pages=page_count,
        )

    def _extract_snapshot(self, limit: int = 20) -> dict[str, Any]:
        result = self.driver._exec_js(build_job51_snapshot_script(limit=limit))
        if isinstance(result, dict) and "raw" in result:
            try:
                parsed = json.loads(result["raw"])
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {"ok": False, "error": "snapshot_parse_failed", "raw": result["raw"]}
        return result if isinstance(result, dict) else {}


def write_job51_snapshot(path: str | Path, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _job51_city_code(city: str) -> str:
    text = str(city or "").strip()
    if not text:
        return ""
    if text.isdigit() and len(text) == 6:
        return text
    normalized = text.replace("市", "")
    return JOB51_CITY_CODES.get(normalized, "")


def _combined_snapshot(snapshots: list[dict[str, Any]], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    combined = dict(snapshots[-1]) if snapshots else {}
    if snapshots:
        combined["pages"] = snapshots
    if extra:
        combined.update(extra)
    return combined


def _job_dedupe_key(job: Job, raw: dict[str, Any]) -> str:
    job_id = job51_job_id(raw)
    return job_id or job.url or f"{job.name}|{job.company}|{job.city}"


def _snapshot_failure(snapshot: dict[str, Any]) -> str:
    if snapshot.get("loginRequired"):
        return "job51_login_required"
    if snapshot.get("ok") is False:
        return str(snapshot.get("error") or "job51_snapshot_failed")
    return ""
