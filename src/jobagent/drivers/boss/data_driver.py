"""Data driver — fetch jobs from Boss直聘 via AppleScript XHR."""

from __future__ import annotations

import json
import time
from urllib.parse import quote
from typing import Any

from jobagent.domain.models import Job
from jobagent.infra.exceptions import LoginRequiredError
from .base import BossActionDriver
from . import create_driver


class BossDataDriver:
    """Fetches job listings from Boss直聘 via browser-driven XHR.

    Works with any BossActionDriver implementation (CDP or AppleScript).
    """

    API_URL = "https://www.zhipin.com/wapi/zpgeek/search/joblist.json"

    def __init__(self, driver: BossActionDriver | None = None):
        self.driver = driver or create_driver()
        self._seen_ids: set[str] = set()

    def _ensure_on_zhipin(self) -> None:
        """Ensure the active Chrome tab is on zhipin.com before XHR."""
        js = """
        (function(){
            if (location.hostname.includes('zhipin.com')) return JSON.stringify({ok:true, status:'already'});
            location.href = 'https://www.zhipin.com/web/sou/?query=AI&city=101280600';
            return JSON.stringify({ok:true, status:'navigating'});
        })()
        """
        result = self.driver._exec_js(js)
        data = self.driver._unwrap(result)
        if data.get("status") == "navigating":
            time.sleep(4)

    def _fetch_page(self, query: str, city_code: str, page: int = 1, page_size: int = 15) -> dict[str, Any]:
        """Execute XHR in browser to fetch one page of job listings."""
        self._ensure_on_zhipin()

        url = (
            f"{self.API_URL}?scene=1&query={quote(query)}"
            f"&city={city_code}&page={page}&pageSize={page_size}"
        )
        js = f'''
        (function(){{
            var r = new XMLHttpRequest();
            r.open('GET', `{url}`, false);
            r.withCredentials = true;
            r.setRequestHeader('Accept', 'application/json');
            r.send(null);
            return r.responseText;
        }})()
        '''
        result = self.driver._exec_js(js)

        # _exec_js may return {"ok": True, "raw": "..."} or parsed JSON
        raw_text = result.get("raw", "")
        if raw_text:
            try:
                return json.loads(raw_text)
            except (json.JSONDecodeError, TypeError):
                return {}

        # If the response was already parsed as JSON dict
        if isinstance(result, dict) and "zpData" in result:
            return result
        return {}

    def _parse_job(self, raw: dict, city_name: str = "") -> Job:
        """Parse raw Boss API job dict to standardized Job model."""
        job_id = raw.get("encryptJobId", "")
        self._seen_ids.add(job_id)

        area_parts = []
        if raw.get("areaDistrict"):
            area_parts.append(raw["areaDistrict"])
        if raw.get("businessDistrict"):
            area_parts.append(raw["businessDistrict"])

        boss_name = raw.get("bossName", "")
        boss_title = raw.get("bossTitle", "")
        boss = f"{boss_name} · {boss_title}" if boss_name else boss_title

        return Job(
            name=raw.get("jobName", ""),
            salary=raw.get("salaryDesc", ""),
            company=raw.get("brandName", ""),
            area="·".join(area_parts) if area_parts else "",
            experience=raw.get("jobExperience", ""),
            degree=raw.get("jobDegree", ""),
            skills=", ".join(raw.get("skills", [])),
            boss=boss,
            city=city_name,
            url=f"https://www.zhipin.com/job_detail/{job_id}.html",
            platform="zhipin",
            raw_data=raw,
        )

    def _check_data_quality(self, raw_jobs: list[dict], page: int = 1) -> None:
        """Check if API response contains real data or degraded (logged-out) data.

        Boss API returns empty salaryDesc when user is not logged in.
        We detect this on the first page and fail fast with a clear message.
        """
        if page != 1:
            return
        if not raw_jobs:
            return
        # If the vast majority of jobs on the first page have empty salary,
        # it's almost certainly an unauthenticated / logged-out session.
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
        """Fetch a single page of jobs.

        Returns:
            List of Job models parsed from the API response.

        Raises:
            LoginRequiredError: If Boss login session is missing, causing
                salary and other fields to be empty.
        """
        data = self._fetch_page(query, city_code, page, page_size)
        if data.get("code") != 0:
            return []
        raw_jobs = data.get("zpData", {}).get("jobList", [])
        self._check_data_quality(raw_jobs, page=page)
        return [
            self._parse_job(j, city_name)
            for j in raw_jobs
            if j.get("encryptJobId") not in self._seen_ids
        ]

    def fetch_all(
        self,
        queries: list[str],
        cities: list[dict],
        max_pages: int = 5,
        delay: float = 3.0,
    ) -> list[Job]:
        """Fetch jobs across multiple queries, cities, and pages.

        Args:
            queries: Search keyword list.
            cities: List of dicts with 'name' and 'code' keys.
            max_pages: Max pages to fetch per query per city.
            delay: Seconds to wait between page fetches.

        Returns:
            Deduplicated list of Job models.
        """
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
                    print(f"  [Crawl] {query} @ {city_name} page {page} → {len(jobs)} jobs")
                    time.sleep(delay)
                time.sleep(delay + 2)
        return all_jobs
