"""Boss API response parsers."""

from __future__ import annotations

from typing import Any

from jobagent.domain.models import Job


def boss_job_id(raw: dict[str, Any]) -> str:
    """Return the stable Boss encrypted job id from a raw API row."""
    return str(raw.get("encryptJobId", "") or "")


def parse_boss_job(raw: dict[str, Any], city_name: str = "") -> Job:
    """Parse a raw Boss API job dict into the shared Job model."""
    job_id = boss_job_id(raw)

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
        platform="boss",
        raw_data=raw,
    )
