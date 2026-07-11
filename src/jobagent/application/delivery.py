"""Explicitly confirmed real delivery for signed, reviewed decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jobagent.infra.activity import active_command
from jobagent.infra.audit import AuditLog, boss_job_key
from jobagent.infra.discovery_state import build_review, load_envelope
from jobagent.infra.platform_lock import PlatformSessionLock
from jobagent.infra.protocol import verify_stored_decision
from jobagent.infra.state import audit_log_path, load_json, save_json
from jobagent.infra.support import print_first_delivery_star_prompt_once


@dataclass
class UserInterventionRequired(RuntimeError):
    code: str
    prompt: str
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.prompt


def _load_reviewed(platform: str, input_path: str | None) -> dict[str, Any]:
    envelope = load_envelope(platform, input_path, reviewed=True if input_path is None else None)
    verify_stored_decision(envelope["manifest"], platform=platform)
    if "send_candidates" not in envelope:
        envelope = build_review(envelope)
    return envelope


def _append_boss_audit(attempts: list[Any]) -> None:
    records = load_json(audit_log_path()) or []
    records.extend({**attempt.to_dict(), "platform": "boss"} for attempt in attempts)
    save_json(audit_log_path(), records)


def _boss_send(jobs: list[dict[str, Any]], *, dry_run: bool) -> list[Any]:
    from jobagent.domain.models import SendAttempt

    delivered_keys = AuditLog().delivered_job_keys()
    results: list[SendAttempt] = []

    def already_delivered(job: dict[str, Any]) -> SendAttempt:
        return SendAttempt(
            job_url=str(job.get("url") or ""),
            message=str(job.get("cloud_greeting") or ""),
            delivered=False,
            error="already_delivered",
            steps=[{"step": "skip_boss_greet_send", "ok": True, "reason": "already_delivered"}],
        )

    actionable = [
        job for job in jobs if boss_job_key(str(job.get("url") or "")) not in delivered_keys
    ]
    if dry_run:
        for job in jobs:
            if boss_job_key(str(job.get("url") or "")) in delivered_keys:
                results.append(already_delivered(job))
            else:
                results.append(
                    SendAttempt(
                        job_url=str(job.get("url") or ""),
                        message=str(job.get("cloud_greeting") or ""),
                        delivered=False,
                        error="dry_run",
                        steps=[{"step": "plan_boss_greet_send", "ok": True}],
                    )
                )
        return results
    if not actionable:
        return [already_delivered(job) for job in jobs]
    from jobagent.drivers.boss import create_driver
    from jobagent.drivers.boss.cdp_driver import CDPBossDriver
    from jobagent.platforms.boss.send_flow import execute_boss_greeting_flow

    driver = create_driver(platform="boss")
    if isinstance(driver, CDPBossDriver) and not driver.check_login_status():
        driver.open_url_in_new_tab("https://www.zhipin.com/web/user/?ka=header-login", wait_seconds=2)
        raise UserInterventionRequired(
            "login_required",
            "请在已经打开的 Job Agent 浏览器中登录 Boss 直聘，完成后回复我“已登录”。",
        )
    for job in jobs:
        key = boss_job_key(str(job.get("url") or ""))
        if key in delivered_keys:
            results.append(already_delivered(job))
            continue
        attempt = execute_boss_greeting_flow(
            driver,
            str(job.get("url") or ""),
            str(job.get("cloud_greeting") or ""),
        )
        results.append(attempt)
        if attempt.delivered and key:
            delivered_keys.add(key)
    return results


def _check_apply_login(platform: str, job: dict[str, Any]) -> None:
    query = str(job.get("title") or job.get("name") or "产品经理")
    area = str(job.get("area") or "")
    city = area.split("·", 1)[0] if area else ""
    if platform == "liepin":
        from jobagent.platforms.liepin.session import LiepinSessionGuide

        status = LiepinSessionGuide().check(query=query, city=city)
    elif platform == "zhilian":
        from jobagent.platforms.zhilian.session import ZhilianSessionGuide

        status = ZhilianSessionGuide().check(query=query, city=city)
    elif platform == "51job":
        from jobagent.platforms.job51.session import Job51SessionGuide

        status = Job51SessionGuide().check(query=query, city=city)
    else:
        raise ValueError(f"Unsupported apply platform: {platform}")
    if status.login_required or not status.ok:
        payload = status.to_dict()
        raise UserInterventionRequired(
            str(payload.get("error") or "login_required"),
            str(payload.get("user_prompt") or f"请登录 {platform} 后回复我“已登录”。"),
            details=payload,
        )


def _apply_send(platform: str, jobs: list[dict[str, Any]], *, dry_run: bool, stop_on_failure: bool):
    if not dry_run:
        _check_apply_login(platform, jobs[0])
    if platform == "liepin":
        from jobagent.platforms.liepin.apply import LiepinApplySender

        sender = LiepinApplySender()
    elif platform == "zhilian":
        from jobagent.platforms.zhilian.apply import ZhilianApplySender

        sender = ZhilianApplySender()
    elif platform == "51job":
        from jobagent.platforms.job51.apply import Job51ApplySender

        sender = Job51ApplySender()
    else:
        raise ValueError(f"Unsupported apply platform: {platform}")
    return sender.send_batch(
        jobs,
        limit=len(jobs),
        dry_run=dry_run,
        stop_on_failure=stop_on_failure,
    )


def send_reviewed(
    platform: str,
    *,
    input_path: str | None = None,
    limit: int = 20,
    dry_run: bool = False,
    stop_on_failure: bool = True,
) -> dict[str, Any]:
    reviewed = _load_reviewed(platform, input_path)
    jobs = list(reviewed.get("send_candidates") or [])[: max(1, min(100, limit))]
    if not jobs:
        raise ValueError("The reviewed decision contains no send candidates")
    with active_command(f"jobagent {platform} send"):
        with PlatformSessionLock(platform=platform, command=f"jobagent {platform} send"):
            attempts = (
                _boss_send(jobs, dry_run=dry_run)
                if platform == "boss"
                else _apply_send(platform, jobs, dry_run=dry_run, stop_on_failure=stop_on_failure)
            )
    if platform == "boss":
        _append_boss_audit(attempts)
    delivered = sum(1 for attempt in attempts if attempt.delivered)
    failed = sum(1 for attempt in attempts if not attempt.delivered and attempt.error != "already_delivered")
    skipped = sum(1 for attempt in attempts if attempt.error == "already_delivered")
    print_first_delivery_star_prompt_once(
        platform=platform,
        command=("greet send" if platform == "boss" else "apply send"),
        delivered=delivered,
        dry_run=dry_run,
    )
    return {
        "ok": failed == 0,
        "platform": platform,
        "discover_id": reviewed["discover_id"],
        "attempted": len(attempts),
        "delivered": delivered,
        "failed": failed,
        "skipped": skipped,
        "dry_run": dry_run,
        "attempts": [attempt.to_dict() for attempt in attempts],
        "next_suggested": f"jobagent {platform} audit",
    }


def audit_platform(platform: str, recent: int = 20) -> dict[str, Any]:
    if platform == "boss":
        log = AuditLog()
        return {"platform": platform, "summary": log.summary(), "recent": log.list_recent(recent)}
    if platform == "liepin":
        from jobagent.platforms.liepin.audit import LiepinAuditLog

        log = LiepinAuditLog()
    elif platform == "zhilian":
        from jobagent.platforms.zhilian.audit import ZhilianAuditLog

        log = ZhilianAuditLog()
    elif platform == "51job":
        from jobagent.platforms.job51.audit import Job51AuditLog

        log = Job51AuditLog()
    else:
        raise ValueError(f"Unsupported audit platform: {platform}")
    return {"platform": platform, "summary": log.summary(), "recent": log.list_recent(recent)}
