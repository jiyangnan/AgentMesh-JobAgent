"""51Job read-only parser."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jobagent.domain.models import Job

from .constants import JOB51_SEARCH_URL


def job51_job_id(raw: dict[str, Any]) -> str:
    sensors = _sensors(raw)
    return str(raw.get("jobId") or raw.get("job_id") or raw.get("id") or sensors.get("jobId") or "").strip()


def parse_job51_job(raw: dict[str, Any], city_name: str = "") -> Job:
    sensors = _sensors(raw)
    job_id = job51_job_id(raw)
    area_text = str(
        raw.get("cityName")
        or raw.get("jobArea")
        or raw.get("area")
        or sensors.get("jobArea")
        or city_name
        or ""
    ).strip()
    city, area = _split_city_area(area_text, city_name=city_name)
    tags = raw.get("tags") or raw.get("skills") or []
    if isinstance(tags, list):
        skills = ", ".join(str(item) for item in tags if str(item).strip())
    else:
        skills = str(tags or "")
    source_url = str(raw.get("sourceUrl") or raw.get("source_url") or JOB51_SEARCH_URL)
    url = str(raw.get("jobUrl") or raw.get("url") or "")
    if not url and job_id:
        url = f"{source_url}#jobId={job_id}"
    return Job(
        name=str(raw.get("jobTitle") or raw.get("name") or raw.get("title") or sensors.get("jobTitle") or ""),
        salary=str(raw.get("salary") or raw.get("jobSalary") or sensors.get("jobSalary") or ""),
        company=str(raw.get("companyName") or raw.get("company") or sensors.get("companyName") or ""),
        city=city,
        area=area,
        experience=str(raw.get("workYear") or raw.get("experience") or sensors.get("jobYear") or ""),
        degree=str(raw.get("education") or raw.get("degree") or sensors.get("jobDegree") or ""),
        skills=skills,
        boss=str(raw.get("hrName") or raw.get("boss") or ""),
        url=url,
        platform="51job",
        raw_data=raw,
    )


def collect_job51_fixture(path: str | Path, city_name: str = "") -> list[Job]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = _extract_rows(data)
    return [parse_job51_job(row, city_name=city_name) for row in rows if isinstance(row, dict)]


def _extract_rows(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("cards"), list):
        return data["cards"]
    if isinstance(data.get("jobs"), list):
        return data["jobs"]
    nested = data.get("data")
    if isinstance(nested, dict):
        for key in ("items", "results", "jobs", "cards"):
            if isinstance(nested.get(key), list):
                return nested[key]
    return []


def _sensors(raw: dict[str, Any]) -> dict[str, Any]:
    sensors = raw.get("sensors")
    if isinstance(sensors, dict):
        return sensors
    sensors_raw = raw.get("sensorsdata")
    if isinstance(sensors_raw, str) and sensors_raw.strip():
        try:
            parsed = json.loads(sensors_raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _split_city_area(value: str, city_name: str = "") -> tuple[str, str]:
    text = str(value or "").strip()
    if "·" in text:
        city, area = text.split("·", 1)
        return city.strip(), area.strip()
    if "-" in text:
        city, area = text.split("-", 1)
        return city.strip(), area.strip()
    if city_name and text.startswith(city_name):
        return city_name, text[len(city_name):].strip(" ·-")
    return text or city_name, ""
