"""Liepin read-only parser."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    return str(_first(raw, "jobId", "job_id", "id", "positionId", "position_id") or "")


def parse_liepin_job(raw: dict[str, Any], city_name: str = "") -> Job:
    """Parse a Liepin job row into the shared Job model.

    ``city_name`` is the user-requested city (e.g., "北京"). It is used ONLY as
    a fallback when the card itself doesn't expose a city — Liepin's sojob
    endpoint ignores URL city params server-side, so cards frequently come
    from mixed cities. The parser prefers the card's own city field so the
    caller can post-filter accurately.
    """
    job_id = liepin_job_id(raw)
    company = _first(raw, "companyName", "company", "compName", "company_name")
    title = _first(raw, "title", "jobTitle", "jobName", "positionName")
    salary = _first(raw, "salary", "salaryText", "salaryDesc", "salaryLabel")
    # Card's own city wins over the user-requested city_name. The requested
    # city is only a fallback for cards that don't expose any location field.
    city = _first(raw, "city", "cityName", "dq") or city_name
    area = _join_non_empty([
        _first(raw, "district", "districtName"),
        _first(raw, "businessArea", "businessDistrict"),
    ])
    experience = _first(raw, "experience", "workYear", "workYearText")
    degree = _first(raw, "degree", "education", "eduLevel")
    recruiter_name = _first(raw, "recruiterName", "hrName", "contactName")
    recruiter_title = _first(raw, "recruiterTitle", "hrTitle")
    recruiter = _join_non_empty([recruiter_name, recruiter_title], sep=" · ")
    skills = _string_list(_first(raw, "skills", "labels", "tags", "skillLabels"))
    url = _first(raw, "url", "jobUrl", "pcUrl")
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
