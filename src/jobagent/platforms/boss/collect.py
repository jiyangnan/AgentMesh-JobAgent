"""Boss collection flow.

The flow is platform-specific because endpoint shape, pagination, login
degradation, and response parsing are all Boss concerns. It depends on the
browser driver runtime but does not belong inside the driver package.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import quote

from jobagent.domain.models import Job
from jobagent.drivers.boss import create_driver
from jobagent.drivers.boss.base import BossActionDriver
from jobagent.infra.exceptions import LoginRequiredError, UserActionRequiredError

from .parser import boss_job_id, parse_boss_job
from .selectors import build_boss_snapshot_script


class BossDataDriver:
    """Fetch job listings from the visible Boss search results page."""

    API_URL = "https://www.zhipin.com/wapi/zpgeek/search/joblist.json"

    def __init__(self, driver: BossActionDriver | None = None):
        self.driver = driver or create_driver()
        self._seen_ids: set[str] = set()

    def _fetch_page(
        self,
        query: str,
        city_code: str,
        page: int = 1,
        page_size: int = 15,
    ) -> dict[str, Any]:
        """Open a real search page and extract its rendered job cards."""
        url = (
            f"https://www.zhipin.com/web/geek/jobs?query={quote(query)}"
            f"&city={quote(city_code)}&page={max(1, int(page))}"
        )
        open_result = self.driver.open_url_in_new_tab(url, wait_seconds=6)
        if not open_result.get("ok"):
            error = str(open_result.get("error") or "open_url_failed")
            if error == "verification_required":
                raise UserActionRequiredError(
                    "verification_required",
                    "Boss requires a visible security verification before Discover can continue",
                    "请在已经打开的 Boss 直聘页面完成安全验证，完成后回复我“已完成验证”。",
                )
            return {"ok": False, "error": error, "cards": []}

        result = self.driver._exec_js(build_boss_snapshot_script(limit=page_size))
        if isinstance(result, dict) and "raw" in result:
            try:
                parsed = json.loads(result["raw"])
                snapshot = parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                snapshot = {}
        else:
            snapshot = result if isinstance(result, dict) else {}
        if snapshot.get("loginRequired"):
            raise LoginRequiredError()
        if snapshot.get("verificationRequired"):
            raise UserActionRequiredError(
                "verification_required",
                "Boss requires a visible security verification before Discover can continue",
                "请在已经打开的 Boss 直聘页面完成安全验证，完成后回复我“已完成验证”。",
            )
        return snapshot

    def _parse_job(self, raw: dict[str, Any], city_name: str = "") -> Job:
        """Parse raw Boss API job dict to the standardized Job model."""
        job_id = boss_job_id(raw)
        self._seen_ids.add(job_id)
        return parse_boss_job(raw, city_name)

    def _check_data_quality(self, raw_jobs: list[dict[str, Any]], page: int = 1) -> None:
        """Fail fast when Boss returns degraded logged-out data."""
        if page != 1:
            return
        if not raw_jobs:
            return
        empty_salary_count = sum(1 for j in raw_jobs if not j.get("salaryDesc"))
        empty_ratio = empty_salary_count / len(raw_jobs)
        if empty_ratio >= 0.8:
            raise LoginRequiredError()

    def fetch_jobs(
        self,
        query: str,
        city_code: str,
        city_name: str = "",
        page: int = 1,
        page_size: int = 15,
    ) -> list[Job]:
        """Fetch a single page of Boss jobs."""
        data = self._fetch_page(query, city_code, page, page_size)
        if not data.get("ok"):
            return []
        raw_jobs = data.get("cards", [])
        self._check_data_quality(raw_jobs, page=page)
        return [
            self._parse_job(j, city_name)
            for j in raw_jobs
            if boss_job_id(j) not in self._seen_ids
        ]

    def fetch_all(
        self,
        queries: list[str],
        cities: list[dict[str, str]],
        max_pages: int = 5,
        delay: float = 3.0,
    ) -> list[Job]:
        """Fetch jobs across multiple queries, cities, and pages."""
        all_jobs: list[Job] = []
        for city in cities:
            city_code = city.get("code", "")
            city_name = city.get("name", "")
            for query in queries:
                for page in range(1, max_pages + 1):
                    jobs = self.fetch_jobs(query, city_code, city_name, page)
                    if not jobs:
                        break
                    all_jobs.extend(jobs)
                    print(f"  [Crawl] {query} @ {city_name} page {page} -> {len(jobs)} jobs")
                    time.sleep(delay)
                time.sleep(delay + 2)
        return all_jobs
