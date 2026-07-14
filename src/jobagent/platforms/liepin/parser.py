"""Liepin read-only parser."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from jobagent.domain.models import Job


def _first(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return ""


def _join_non_empty(values: list[Any], sep: str = "·") -> str:
    return sep.join(str(value) for value in values if value not in (None, ""))


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    if isinstance(value, str) and value:
        return [value]
    return []


def liepin_job_id(raw: dict[str, Any]) -> str:
    """Return a stable Liepin job id from common saved response shapes."""
    explicit = _first(raw, "jobId", "job_id", "id", "positionId", "position_id")
    if explicit:
        return str(explicit)
    url = str(_first(raw, "url", "jobUrl", "pcUrl") or "")
    match = re.search(r"/job/([^/?#]+?)(?:\.shtml)?(?:[?#]|$)", url)
    return match.group(1) if match else ""


def _canonical_liepin_job_url(url: Any) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    parts = urlsplit(value)
    if not parts.netloc and parts.path.startswith("/"):
        parts = urlsplit(f"https://www.liepin.com{value}")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def parse_liepin_job(raw: dict[str, Any], city_name: str = "") -> Job:
    """Parse a Liepin job row into the shared Job model."""
    job_id = liepin_job_id(raw)
    company = _first(raw, "companyName", "company", "compName", "company_name")
    title = _first(raw, "title", "jobTitle", "jobName", "positionName")
    salary = _first(raw, "salary", "salaryText", "salaryDesc", "salaryLabel")
    location = str(_first(raw, "city", "cityName", "dq") or city_name)
    city, _, location_area = location.partition("-")
    area = _join_non_empty([
        location_area,
        _first(raw, "district", "districtName"),
        _first(raw, "businessArea", "businessDistrict"),
    ])
    experience = _first(raw, "experience", "workYear", "workYearText")
    degree = _first(raw, "degree", "education", "eduLevel")
    recruiter_name = _first(raw, "recruiterName", "hrName", "contactName")
    recruiter_title = _first(raw, "recruiterTitle", "hrTitle")
    recruiter = _join_non_empty([recruiter_name, recruiter_title], sep=" · ")
    skills = _string_list(_first(raw, "skills", "labels", "tags", "skillLabels"))
    url = _canonical_liepin_job_url(_first(raw, "url", "jobUrl", "pcUrl"))
    if not url and job_id:
        url = f"https://www.liepin.com/job/{job_id}.shtml"

    return Job(
        name=str(title),
        salary=str(salary),
        company=str(company),
        area=area,
        experience=str(experience),
        degree=str(degree),
        skills=", ".join(skills),
        boss=recruiter,
        city=str(city),
        url=str(url),
        platform="liepin",
        raw_data=raw,
    )


def _extract_job_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    pages = payload.get("pages")
    if isinstance(pages, list):
        rows: list[dict[str, Any]] = []
        for page in pages:
            rows.extend(_extract_job_rows(page))
        return rows
    for key in ("jobs", "jobList", "list", "cards", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("jobs", "jobList", "list", "cards", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def collect_liepin_fixture(path: str | Path, city_name: str = "") -> list[Job]:
    """Parse a saved Liepin JSON fixture into shared Job objects."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        parse_liepin_job(row, city_name=city_name)
        for row in _extract_job_rows(payload)
    ]
