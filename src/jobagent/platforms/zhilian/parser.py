"""Zhilian read-only parser."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jobagent.domain.models import Job


def _first(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return ""


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    if isinstance(value, str) and value:
        return [value]
    return []


def zhilian_job_id(raw: dict[str, Any]) -> str:
    """Return a stable Zhilian job id from common response/snapshot shapes."""
    return str(_first(raw, "positionId", "jobId", "job_id", "number", "id") or "")


def parse_zhilian_job(raw: dict[str, Any], city_name: str = "") -> Job:
    """Parse a Zhilian job row into the shared Job model."""
    job_id = zhilian_job_id(raw)
    derived = _derive_from_raw_text(raw)
    title = _first(raw, "name", "title", "jobName", "jobTitle", "positionName")
    company = _valid_company(_first(raw, "company", "companyName", "company_name") or derived.get("company", ""))
    salary = _first(raw, "salary", "salaryDesc", "salaryText") or derived.get("salary", "")
    city = derived.get("city", "") or _first(raw, "city", "cityName", "workCity", "cityDisplay") or city_name
    area = _first(raw, "district", "area", "businessArea") or derived.get("area", "")
    experience = _first(raw, "experience", "workingExp", "workYear", "workExperience") or derived.get("experience", "")
    degree = _first(raw, "degree", "eduLevel", "education") or derived.get("degree", "")
    recruiter = _first(raw, "recruiterName", "hrName", "contactName")
    skills = _string_list(_first(raw, "skills", "skillLabels", "welfareLabel", "tags"))
    url = _first(raw, "url", "jobUrl", "positionURL", "pcUrl")
    if not url and job_id:
        url = f"https://www.zhaopin.com/jobdetail/{job_id}.htm"

    return Job(
        name=str(title),
        salary=str(salary),
        company=str(company),
        area=str(area),
        experience=str(experience),
        degree=str(degree),
        skills=", ".join(skills),
        boss=str(recruiter),
        city=str(city),
        url=str(url),
        platform="zhilian",
        raw_data=raw,
    )


def _derive_from_raw_text(raw: dict[str, Any]) -> dict[str, str]:
    raw_text = str(raw.get("rawText") or "")
    if not raw_text:
        return {}
    title = str(_first(raw, "name", "title", "jobName", "jobTitle", "positionName") or "")
    text = re.sub(r"\s+", " ", raw_text).strip()
    if title and text.startswith(title):
        text = text[len(title):].strip()
    tokens = [token for token in text.split(" ") if token]
    salary = _match_first(
        text,
        [
            r"\d+(?:\.\d+)?-\d+(?:\.\d+)?万(?:·\d+薪)?",
            r"\d+(?:\.\d+)?-\d+(?:\.\d+)?[kK](?:·\d+薪)?",
            r"\d+-\d+元(?:/月)?(?:·\d+薪)?",
            r"面议",
        ],
    )
    city_area = _match_first(
        text,
        [
            r"(?:北京|上海|深圳|广州|杭州|成都|武汉|南京|苏州|西安|郑州|天津|重庆|东莞|泉州|保定|沈阳|长沙|临沂|青岛|合肥|佛山|福州|厦门|济南|无锡|大连|长春|石家庄|南昌|贵阳|南宁)(?:·[\u4e00-\u9fa5A-Za-z0-9]+){0,3}",
        ],
    )
    city = city_area.split("·", 1)[0] if city_area else ""
    area = city_area.split("·", 1)[1] if "·" in city_area else ""
    experience = _match_first(text, [r"\d+-\d+年", r"\d+年以上", r"经验不限", r"应届"])
    degree = _match_first(text, [r"博士", r"硕士", r"本科", r"大专", r"学历不限"])
    company = ""
    if degree and degree in tokens:
        degree_index = tokens.index(degree)
        if degree_index + 1 < len(tokens):
            company = _valid_company(tokens[degree_index + 1])
    return {
        "salary": salary,
        "city": city,
        "area": area,
        "experience": experience,
        "degree": degree,
        "company": company,
    }


def _match_first(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return ""


def _valid_company(value: Any) -> str:
    company = str(value or "").strip()
    if not company:
        return ""
    invalid = {
        "立即投递",
        "立即沟通",
        "投递",
        "沟通",
        "高回复率",
        "刚刚活跃",
        "今日回复",
    }
    if company in invalid:
        return ""
    return company


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
    for key in ("jobs", "jobList", "list", "results", "cards", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("jobs", "jobList", "list", "results", "cards", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def collect_zhilian_fixture(path: str | Path, city_name: str = "") -> list[Job]:
    """Parse a saved Zhilian JSON fixture into shared Job objects."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        parse_zhilian_job(row, city_name=city_name)
        for row in _extract_job_rows(payload)
    ]
