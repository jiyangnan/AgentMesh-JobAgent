"""Public browser adapters that execute a signed SearchPlan serially."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from jobagent.domain.models import Job


@dataclass
class CollectionError(RuntimeError):
    code: str
    message: str
    user_prompt: str = ""
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


_BOSS_CITY_CODES = {
    "北京": "101010100",
    "上海": "101020100",
    "广州": "101280100",
    "深圳": "101280600",
    "成都": "101270100",
    "杭州": "101210100",
    "重庆": "101040100",
    "武汉": "101200100",
    "西安": "101110100",
    "苏州": "101190400",
    "天津": "101030100",
    "南京": "101190100",
    "长沙": "101250100",
    "郑州": "101180100",
    "东莞": "101281600",
    "青岛": "101120200",
    "沈阳": "101070100",
    "宁波": "101210400",
    "昆明": "101290100",
    "合肥": "101220100",
    "佛山": "101280800",
    "福州": "101230100",
    "厦门": "101230200",
    "济南": "101120100",
    "无锡": "101190200",
    "大连": "101070200",
    "南昌": "101240100",
    "贵阳": "101260100",
    "南宁": "101300100",
}


def _fallback_id(job: Job) -> str:
    seed = f"{job.platform}|{job.url}|{job.name}|{job.company}|{job.city}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _job_id(platform: str, job: Job) -> str:
    raw = job.raw_data if isinstance(job.raw_data, dict) else {}
    if platform == "boss":
        from jobagent.platforms.boss.parser import boss_job_id

        return boss_job_id(raw) or _fallback_id(job)
    if platform == "liepin":
        from jobagent.platforms.liepin.parser import liepin_job_id

        return liepin_job_id(raw) or _fallback_id(job)
    if platform == "zhilian":
        from jobagent.platforms.zhilian.parser import zhilian_job_id

        return zhilian_job_id(raw) or _fallback_id(job)
    if platform == "51job":
        from jobagent.platforms.job51.parser import job51_job_id

        return job51_job_id(raw) or _fallback_id(job)
    return _fallback_id(job)


def job_to_candidate(platform: str, job: Job) -> dict[str, Any]:
    raw = job.raw_data if isinstance(job.raw_data, dict) else {}
    area = "·".join(part for part in (job.city, job.area) if part)
    candidate = {
        "id": _job_id(platform, job),
        "title": job.name,
        "company": job.company or None,
        "area": area or None,
        "salary": job.salary or None,
        "experience": job.experience or None,
        "degree": job.degree or None,
        "skills": job.skills or None,
        "company_size": raw.get("companySize") or raw.get("companyScale"),
        "industry": raw.get("industry") or raw.get("industryName"),
        "finance_stage": raw.get("financeStage") or raw.get("financingStage"),
        "boss_name": job.boss or raw.get("bossName"),
        "boss_title": raw.get("bossTitle") or raw.get("recruiterTitle"),
        "url": job.url or None,
        "security_id": raw.get("securityId") or raw.get("security_id"),
        "jd": raw.get("jobDesc") or raw.get("description"),
    }
    return {key: value for key, value in candidate.items() if value is not None}


def _collect_boss(query: dict[str, Any], page: int, limit: int, driver) -> list[Job]:
    from jobagent.platforms.boss.collect import BossDataDriver

    city = str(query.get("city") or "").replace("市", "")
    code = _BOSS_CITY_CODES.get(city)
    if not code:
        raise CollectionError(
            "unsupported_city",
            f"Boss adapter does not have a city code for {city or 'empty city'}",
        )
    collector = BossDataDriver(driver=driver)
    return collector.fetch_jobs(
        str(query.get("keyword") or ""),
        code,
        city_name=city,
        page=page,
        page_size=min(15, limit),
    )


def _collect_web_platform(
    platform: str,
    query: dict[str, Any],
    page: int,
    limit: int,
    driver,
    wait_seconds: int,
) -> list[Job]:
    if platform == "liepin":
        from jobagent.platforms.liepin.collect import LiepinReadOnlyCollector

        collector = LiepinReadOnlyCollector(driver=driver)
    elif platform == "zhilian":
        from jobagent.platforms.zhilian.collect import ZhilianReadOnlyCollector

        collector = ZhilianReadOnlyCollector(driver=driver)
    elif platform == "51job":
        from jobagent.platforms.job51.collect import Job51ReadOnlyCollector

        collector = Job51ReadOnlyCollector(driver=driver)
    else:
        raise CollectionError("unsupported_platform", f"Unsupported platform: {platform}")
    result = collector.collect(
        query=str(query.get("keyword") or ""),
        city=str(query.get("city") or ""),
        limit=min(40, limit),
        wait_seconds=wait_seconds,
        page=page,
        pages=1,
        page_delay=0,
    )
    if not result.ok:
        payload = result.to_payload(include_snapshot=False)
        raise CollectionError(
            result.error or "collection_failed",
            str(payload.get("message") or result.error or "Collection failed"),
            user_prompt=str(payload.get("user_prompt") or ""),
            details=payload,
        )
    return result.jobs


def collect_from_search_plan(
    plan: dict[str, Any],
    *,
    wait_seconds: int = 6,
    page_delay: float = 2.0,
    driver=None,
) -> list[dict[str, Any]]:
    from jobagent.drivers.boss import create_driver
    from jobagent.infra.exceptions import (
        LoginRequiredError,
        PlatformEnvironmentRejectedError,
        UserActionRequiredError,
    )

    platform = str(plan["platform"])
    candidate_limit = min(100, int(plan.get("candidate_limit", 100)))
    queries = list(plan.get("queries") or [])
    if not queries:
        raise CollectionError("empty_search_plan", "SearchPlan contains no queries")
    driver = driver or create_driver(platform=platform)
    max_pages = max(int(query.get("page_limit", 1)) for query in queries)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    try:
        for page in range(1, max_pages + 1):
            for query in queries:
                if page > int(query.get("page_limit", 1)):
                    continue
                remaining = candidate_limit - len(candidates)
                if remaining <= 0:
                    return candidates
                if platform == "boss":
                    jobs = _collect_boss(query, page, remaining, driver)
                else:
                    jobs = _collect_web_platform(
                        platform,
                        query,
                        page,
                        remaining,
                        driver,
                        wait_seconds,
                    )
                for job in jobs:
                    candidate = job_to_candidate(platform, job)
                    if candidate["id"] in seen:
                        continue
                    seen.add(candidate["id"])
                    candidates.append(candidate)
                    if len(candidates) >= candidate_limit:
                        return candidates
                if page_delay > 0:
                    time.sleep(page_delay)
    except UserActionRequiredError as exc:
        raise CollectionError(
            exc.code,
            str(exc),
            user_prompt=exc.user_prompt,
        ) from exc
    except LoginRequiredError as exc:
        raise CollectionError(
            "login_required",
            f"{platform} requires login before Discover can continue",
            user_prompt=f"请在已经打开的浏览器中登录 {platform}，完成后回复我“已登录”。",
        ) from exc
    except PlatformEnvironmentRejectedError as exc:
        raise CollectionError(
            "platform_environment_rejected",
            str(exc),
            details={
                "platform": exc.platform,
                "upstream_code": exc.upstream_code,
            },
        ) from exc

    if not candidates:
        raise CollectionError("no_candidates", "No jobs were collected; no credits were charged")
    return candidates
