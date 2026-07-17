"""Automatic real delivery for signed and reviewed selected decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jobagent.infra import rounds
from jobagent.infra.activity import active_command
from jobagent.infra.audit import AuditLog, boss_job_key
from jobagent.infra.discovery_state import build_review, load_envelope
from jobagent.infra.diagnostics import emit_stage, progress_heartbeat
from jobagent.infra.platform_lock import PlatformSessionLock
from jobagent.infra.protocol import verify_stored_decision
from jobagent.infra.state import audit_log_path, load_json, save_json
from jobagent.infra.support import print_first_delivery_star_prompt_once
from jobagent.platforms.message_contract import validate_personalized_message


_SKIPPED_SEND_ERRORS = {"already_delivered", "job_unavailable"}


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


def _boss_send(
    jobs: list[dict[str, Any]],
    *,
    dry_run: bool,
    stop_on_failure: bool = True,
    on_attempt=None,
) -> list[Any]:
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
        for index, job in enumerate(jobs, 1):
            if boss_job_key(str(job.get("url") or "")) in delivered_keys:
                attempt = already_delivered(job)
            else:
                message = str(job.get("cloud_greeting") or "")
                validation = validate_personalized_message("boss", message)
                attempt = SendAttempt(
                    job_url=str(job.get("url") or ""),
                    message=message,
                    delivered=False,
                    error="dry_run" if validation["ok"] else str(validation["error"]),
                    steps=[
                        {"step": "validate_boss_personalized_message", **validation},
                        *([{"step": "plan_boss_greet_send", "ok": True}] if validation["ok"] else []),
                    ],
                )
            results.append(attempt)
            if callable(on_attempt):
                on_attempt(attempt, index, len(jobs))
        return results
    if not actionable:
        results = [already_delivered(job) for job in jobs]
        if callable(on_attempt):
            for index, attempt in enumerate(results, 1):
                on_attempt(attempt, index, len(jobs))
        return results
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
    for index, job in enumerate(jobs, 1):
        key = boss_job_key(str(job.get("url") or ""))
        if key in delivered_keys:
            attempt = already_delivered(job)
            results.append(attempt)
            if callable(on_attempt):
                on_attempt(attempt, index, len(jobs))
            continue
        validation = validate_personalized_message(
            "boss",
            str(job.get("cloud_greeting") or ""),
        )
        if not validation["ok"]:
            attempt = SendAttempt(
                job_url=str(job.get("url") or ""),
                message=str(job.get("cloud_greeting") or ""),
                delivered=False,
                error=str(validation["error"]),
                steps=[{"step": "validate_boss_personalized_message", **validation}],
            )
            results.append(attempt)
            if callable(on_attempt):
                on_attempt(attempt, index, len(jobs))
            if stop_on_failure:
                break
            continue
        attempt = execute_boss_greeting_flow(
            driver,
            str(job.get("url") or ""),
            str(job.get("cloud_greeting") or ""),
        )
        attempt.steps.insert(0, {"step": "validate_boss_personalized_message", **validation})
        results.append(attempt)
        if callable(on_attempt):
            on_attempt(attempt, index, len(jobs))
        if attempt.delivered and key:
            delivered_keys.add(key)
        platform_default_only = (
            not attempt.delivered
            and any(
                isinstance(step, dict) and step.get("platformDefaultSent")
                for step in attempt.steps
            )
        )
        if platform_default_only or (stop_on_failure and not attempt.delivered):
            break
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


def _apply_send(
    platform: str,
    jobs: list[dict[str, Any]],
    *,
    dry_run: bool,
    stop_on_failure: bool,
    on_attempt=None,
):
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
    attempts = sender.send_batch(
        jobs,
        limit=len(jobs),
        dry_run=dry_run,
        stop_on_failure=stop_on_failure,
        on_attempt=on_attempt,
    )
    if not dry_run:
        _raise_for_apply_user_intervention(platform, attempts)
    return attempts


def _raise_for_apply_user_intervention(platform: str, attempts: list[Any]) -> None:
    for attempt in attempts:
        for step in reversed(attempt.steps):
            if not isinstance(step, dict) or not step.get("requires_user_action"):
                continue
            code = str(step.get("user_action") or attempt.error or "user_action_required")
            prompt = str(step.get("user_prompt") or "请在已打开的平台页面完成所需操作。")
            raise UserInterventionRequired(
                code,
                prompt,
                details={
                    "platform": platform,
                    "job_url": attempt.job_url,
                    "step": step.get("step"),
                },
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
    all_jobs = list(reviewed.get("send_candidates") or [])
    jobs = all_jobs[: max(1, min(100, limit))]
    if not jobs:
        raise ValueError("The reviewed decision contains no send candidates")
    emit_stage("delivery_started", platform=platform, total=len(jobs), dry_run=dry_run)

    def on_attempt(attempt, index: int, total: int) -> None:
        if platform == "boss" and not dry_run and attempt.error != "already_delivered":
            _append_boss_audit([attempt])
        emit_stage(
            "delivery_item_completed",
            platform=platform,
            index=index,
            total=total,
            delivered=bool(attempt.delivered),
            outcome=attempt.error or "delivered",
        )

    with progress_heartbeat("delivery_in_progress", platform=platform, total=len(jobs)):
        with active_command(f"jobagent {platform} send"):
            with PlatformSessionLock(platform=platform, command=f"jobagent {platform} send"):
                attempts = (
                    _boss_send(
                        jobs,
                        dry_run=dry_run,
                        stop_on_failure=stop_on_failure,
                        on_attempt=on_attempt,
                    )
                    if platform == "boss"
                    else _apply_send(
                        platform,
                        jobs,
                        dry_run=dry_run,
                        stop_on_failure=stop_on_failure,
                        on_attempt=on_attempt,
                    )
                )
    delivered = sum(1 for attempt in attempts if attempt.delivered)
    failed = sum(
        1
        for attempt in attempts
        if not attempt.delivered and attempt.error not in _SKIPPED_SEND_ERRORS
    )
    skipped = sum(1 for attempt in attempts if attempt.error in _SKIPPED_SEND_ERRORS)
    emit_stage(
        "delivery_completed",
        platform=platform,
        attempted=len(attempts),
        delivered=delivered,
        failed=failed,
        skipped=skipped,
    )
    print_first_delivery_star_prompt_once(
        platform=platform,
        command=("greet send" if platform == "boss" else "apply send"),
        delivered=delivered,
        dry_run=dry_run,
    )
    complete_batch = failed == 0 and len(jobs) == len(all_jobs)
    next_suggested = (
        f"jobagent {platform} audit"
        if complete_batch
        else (
            f"jobagent boss greet send --input {input_path} --limit 100"
            if platform == "boss"
            else f"jobagent {platform} apply send --input {input_path} --limit 100"
        )
    )
    rounds.set_platform_status(
        platform,
        "sent" if complete_batch else "reviewed",
        command=(
            "jobagent boss greet send"
            if platform == "boss"
            else f"jobagent {platform} apply send"
        ),
        evidence={
            "discover_id": reviewed["discover_id"],
            "attempted": len(attempts),
            "delivered": delivered,
            "failed": failed,
            "skipped": skipped,
            "reviewed_count": len(all_jobs),
        },
        next_suggested=next_suggested,
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
        "next_suggested": next_suggested,
        "workflow": rounds.round_status(),
    }


def _audit_log(platform: str):
    if platform == "boss":
        return AuditLog()
    elif platform == "liepin":
        from jobagent.platforms.liepin.audit import LiepinAuditLog

        return LiepinAuditLog()
    elif platform == "zhilian":
        from jobagent.platforms.zhilian.audit import ZhilianAuditLog

        return ZhilianAuditLog()
    elif platform == "51job":
        from jobagent.platforms.job51.audit import Job51AuditLog

        return Job51AuditLog()
    raise ValueError(f"Unsupported audit platform: {platform}")


def _failed_record(record: dict[str, Any]) -> bool:
    status = str(record.get("status") or "")
    error = str(record.get("error") or "")
    if status == "failed":
        return True
    if record.get("delivered") is False and error not in {
        "",
        "already_delivered",
        "dry_run",
        "job_unavailable",
    }:
        return True
    return False


def _summary_failure_count(summary: dict[str, Any]) -> int:
    if isinstance(summary.get("failed"), int):
        return int(summary["failed"])
    send = summary.get("send") or summary.get("apply_send") or {}
    return int(send.get("failed") or 0) if isinstance(send, dict) else 0


def _audit_payload(
    platform: str,
    *,
    recent: int,
    details: bool,
    failures_only: bool,
) -> dict[str, Any]:
    log = _audit_log(platform)
    summary = log.summary()
    payload: dict[str, Any] = {
        "platform": platform,
        "summary": summary,
        "failure_count": _summary_failure_count(summary),
    }
    if details or failures_only:
        records = log.list_recent(max(1, min(100, recent)))
        if failures_only:
            records = [record for record in records if _failed_record(record)]
        payload["records"] = records
        payload["record_count"] = len(records)
    return payload


def audit_platform(
    platform: str,
    recent: int = 20,
    *,
    details: bool = False,
    failures_only: bool = False,
) -> dict[str, Any]:
    payload = _audit_payload(
        platform,
        recent=recent,
        details=details,
        failures_only=failures_only,
    )
    workflow = rounds.complete_platform_after_audit(platform)
    return {
        "ok": True,
        **payload,
        "workflow": workflow,
        "next_suggested": workflow.get("next_suggested"),
    }


def audit_round(
    *,
    platform: str | None = None,
    recent: int = 20,
    details: bool = False,
    failures_only: bool = False,
) -> dict[str, Any]:
    selected = [platform] if platform else list(rounds.DEFAULT_PLATFORM_ORDER)
    platform_reports = {
        item: _audit_payload(
            item,
            recent=recent,
            details=details,
            failures_only=failures_only,
        )
        for item in selected
    }
    return {
        "ok": True,
        "scope": "round",
        "platforms": platform_reports,
        "failure_count": sum(report["failure_count"] for report in platform_reports.values()),
        "workflow": rounds.round_status(),
        "next_suggested": rounds.round_status().get("next_suggested"),
    }
