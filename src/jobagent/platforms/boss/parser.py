"""Boss API response parsers."""

from __future__ import annotations

import re
from typing import Any

from jobagent.domain.models import Job


def boss_job_id(raw: dict[str, Any]) -> str:
    """Return the stable Boss encrypted job id from API or DOM shapes."""
    direct = raw.get("encryptJobId") or raw.get("jobId") or raw.get("job_id") or raw.get("id")
    if direct:
        return str(direct)
    match = re.search(r"/job_detail/([^/?#]+)\.html", str(raw.get("jobUrl") or raw.get("url") or ""))
    return match.group(1) if match else ""


def parse_boss_job(raw: dict[str, Any], city_name: str = "") -> Job:
    """Parse a raw Boss API job dict into the shared Job model."""
    job_id = boss_job_id(raw)

    area_parts = []
    if raw.get("areaDistrict"):
        area_parts.append(raw["areaDistrict"])
    if raw.get("businessDistrict"):
        area_parts.append(raw["businessDistrict"])

    boss_name = raw.get("bossName", "") or raw.get("recruiterName", "")
    boss_title = raw.get("bossTitle", "") or raw.get("recruiterTitle", "")
    boss = f"{boss_name} · {boss_title}" if boss_name else boss_title

    skills = raw.get("skills", [])
    if isinstance(skills, str):
        skills_text = skills
    else:
        skills_text = ", ".join(str(item) for item in skills if str(item).strip())
    url = str(raw.get("jobUrl") or raw.get("url") or "")
    if not url and job_id:
        url = f"https://www.zhipin.com/job_detail/{job_id}.html"

    return Job(
        name=raw.get("jobName", "") or raw.get("jobTitle", "") or raw.get("title", ""),
        salary=raw.get("salaryDesc", "") or raw.get("salary", ""),
        company=raw.get("brandName", "") or raw.get("companyName", "") or raw.get("company", ""),
        area="·".join(area_parts) if area_parts else "",
        experience=raw.get("jobExperience", "") or raw.get("experience", ""),
        degree=raw.get("jobDegree", "") or raw.get("degree", ""),
        skills=skills_text,
        boss=boss,
        city=raw.get("cityName", "") or raw.get("city", "") or city_name,
        url=url,
        platform="boss",
        raw_data=raw,
    )
