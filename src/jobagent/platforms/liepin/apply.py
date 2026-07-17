"""Liepin resume and personalized greeting delivery."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from jobagent.domain.models import SendAttempt
from jobagent.drivers.boss import create_driver
from jobagent.platforms.message_contract import validate_personalized_message

from .audit import LiepinAuditEvent, LiepinAuditLog


@dataclass
class LiepinApplyOpenResult:
    ok: bool
    opened: int
    planned: int
    failed: int
    total: int
    events: list[dict[str, Any]] = field(default_factory=list)
    handoff: list[dict[str, Any]] = field(default_factory=list)
    mode: str = "manual_apply_open"
    platform: str = "liepin"
    next_suggested: str = "Review opened Liepin pages manually, then run `jobagent liepin audit`."
    requires_user_action: bool = False
    user_prompt: str = ""

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "ok": self.ok,
            "platform": self.platform,
            "mode": self.mode,
            "total": self.total,
            "planned": self.planned,
            "opened": self.opened,
            "failed": self.failed,
            "requires_user_action": self.requires_user_action,
            "handoff": self.handoff,
            "events": self.events,
            "next_suggested": self.next_suggested,
        }
        if self.user_prompt:
            payload["user_prompt"] = self.user_prompt
        return payload


class LiepinApplyOpener:
    def __init__(
        self,
        driver: Any | None = None,
        audit_log: LiepinAuditLog | None = None,
    ):
        self.driver = driver
        self.audit_log = audit_log or LiepinAuditLog()

    def open_jobs(
        self,
        jobs: list[dict[str, Any]],
        limit: int = 5,
        start: int = 0,
        wait_seconds: int = 3,
        dry_run: bool = False,
    ) -> LiepinApplyOpenResult:
        selected = jobs[max(0, start): max(0, start) + max(1, limit)]
        events: list[dict[str, Any]] = []
        handoff_items: list[dict[str, Any]] = []
        opened = 0
        planned = 0
        failed = 0

        for index, job in enumerate(selected, start=max(0, start)):
            url = str(job.get("url") or "")
            handoff = _handoff_evidence(job)
            event = self._event_from_job(
                job,
                status="planned" if dry_run else "opened",
                message="dry_run" if dry_run else "Opened for manual review.",
                evidence={"index": index, **handoff},
            )
            if not url:
                event = self._event_from_job(
                    job,
                    status="failed",
                    error="missing_job_url",
                    message="Cannot open Liepin job without url.",
                    evidence={"index": index, **handoff},
                )
                failed += 1
            elif dry_run:
                planned += 1
            else:
                driver = self.driver or create_driver(platform="liepin")
                self.driver = driver
                result = driver.open_url_in_new_tab(url, wait_seconds=wait_seconds)
                if result.get("ok"):
                    opened += 1
                    event = self._event_from_job(
                        job,
                        status="opened",
                        message="Opened for manual review.",
                        evidence={"index": index, **handoff, "open_result": result},
                    )
                else:
                    failed += 1
                    event = self._event_from_job(
                        job,
                        status="failed",
                        error=str(result.get("error") or "open_failed"),
                        message="Failed to open Liepin job page.",
                        evidence={"index": index, **handoff, "open_result": result},
                    )

            self.audit_log.append(event)
            events.append(event.to_dict())
            handoff_items.append(_handoff_item(job, index=index, status=event.status, error=event.error))

        if dry_run:
            next_suggested = (
                "Review handoff, then rerun without `--dry-run` to open Liepin pages for manual review."
            )
            user_prompt = ""
            requires_user_action = False
        else:
            next_suggested = (
                "Review opened Liepin pages manually, copy each handoff greeting, "
                "then run `jobagent liepin audit`."
            )
            user_prompt = (
                "请在已打开的猎聘页面中人工确认岗位，并复制 handoff 列表里对应的 greeting。"
                "完成后可运行 `jobagent liepin audit` 查看记录。"
            )
            requires_user_action = opened > 0

        return LiepinApplyOpenResult(
            ok=failed == 0,
            opened=opened,
            planned=planned,
            failed=failed,
            total=len(selected),
            events=events,
            handoff=handoff_items,
            next_suggested=next_suggested,
            requires_user_action=requires_user_action,
            user_prompt=user_prompt,
        )

    def _event_from_job(
        self,
        job: dict[str, Any],
        status: str,
        message: str = "",
        error: str = "",
        evidence: dict[str, Any] | None = None,
    ) -> LiepinAuditEvent:
        return LiepinAuditEvent(
            action="apply_open",
            status=status,
            job_url=str(job.get("url") or ""),
            job_name=str(job.get("name") or job.get("title") or ""),
            company=str(job.get("company") or ""),
            error=error,
            message=message,
            evidence=evidence or {},
        )


class LiepinApplySender:
    """Deliver both the account resume and signed personalized greeting.

    Some Liepin jobs expose only a chat entry. Opening it may send a
    platform-owned default message, which is never counted as the signed
    personalized greeting. A job is complete only when the active chat
    contains resume delivery evidence and the exact signed greeting.
    """
    def __init__(
        self,
        driver: Any | None = None,
        audit_log: LiepinAuditLog | None = None,
    ):
        self.driver = driver
        self.audit_log = audit_log or LiepinAuditLog()

    def send_batch(
        self,
        jobs: list[dict[str, Any]],
        limit: int = 5,
        start: int = 0,
        wait_seconds: int = 3,
        dry_run: bool = False,
        skip_delivered: bool = True,
        stop_on_failure: bool = True,
        on_attempt=None,
    ) -> list[SendAttempt]:
        selected = jobs[max(0, start): max(0, start) + max(1, limit)]
        attempts: list[SendAttempt] = []
        delivered_urls = self.audit_log.delivered_apply_send_urls() if skip_delivered else set()
        resume_delivered_urls = (
            self.audit_log.resume_delivered_apply_send_urls() if skip_delivered else set()
        )
        for index, job in enumerate(selected, start=max(0, start)):
            message = str(job.get("cloud_greeting") or job.get("greeting") or "")
            url = str(job.get("url") or "").strip()
            url_key = _normalize_liepin_url(url)
            if skip_delivered and url_key in delivered_urls:
                attempt = SendAttempt(job_url=url, message=message, delivered=False, error="already_delivered")
                attempt.steps = [
                    {
                        "step": "skip_liepin_apply_send",
                        "ok": True,
                        "reason": "already_delivered",
                        "url": url,
                    }
                ]
                status = "skipped"
                audit_message = "Skipped because this Liepin job URL was already delivered."
            else:
                attempt = self._send_one(
                    job,
                    message,
                    wait_seconds=wait_seconds,
                    dry_run=dry_run,
                    resume_already_delivered=url_key in resume_delivered_urls,
                )
                status = "planned" if dry_run else ("delivered" if attempt.delivered else "failed")
                audit_message = "dry_run" if dry_run else ("Delivered." if attempt.delivered else "Failed.")
                if attempt.delivered:
                    delivered_urls.add(url_key)
                    resume_delivered_urls.add(url_key)

            attempts.append(attempt)
            resume_delivered, greeting_delivered = _liepin_attempt_delivery_parts(attempt)
            greeting_contract = validate_personalized_message("liepin", message)
            self.audit_log.append(
                LiepinAuditEvent(
                    action="apply_send",
                    status=status,
                    job_url=attempt.job_url,
                    job_name=str(job.get("name") or job.get("title") or ""),
                    company=str(job.get("company") or ""),
                    error=attempt.error,
                    message=audit_message,
                    evidence={
                        "index": index,
                        "has_greeting": bool(message.strip()),
                        "greeting": message,
                        "greeting_contract": greeting_contract,
                        "score": job.get("score"),
                        "match_level": job.get("match_level") or job.get("recommendation") or job.get("cloud_recommendation"),
                        "resume_delivered": resume_delivered,
                        "greeting_delivered": greeting_delivered,
                        "steps": attempt.steps,
                    },
                )
            )
            if callable(on_attempt):
                on_attempt(attempt, index - max(0, start) + 1, len(selected))
            if stop_on_failure and not dry_run and status == "failed":
                break
        return attempts

    def _send_one(
        self,
        job: dict[str, Any],
        message: str,
        wait_seconds: int = 3,
        dry_run: bool = False,
        resume_already_delivered: bool = False,
    ) -> SendAttempt:
        url = str(job.get("url") or "")
        attempt = SendAttempt(job_url=url, message=message, delivered=False)
        steps: list[dict[str, Any]] = []
        if not url:
            attempt.error = "missing_job_url"
            attempt.steps = steps
            return attempt
        greeting_contract = validate_personalized_message("liepin", message)
        if not greeting_contract["ok"]:
            attempt.error = str(greeting_contract["error"])
            attempt.steps = [
                {"step": "validate_liepin_personalized_message", **greeting_contract}
            ]
            return attempt
        steps.append({"step": "validate_liepin_personalized_message", **greeting_contract})
        if dry_run:
            attempt.error = "dry_run"
            steps.append({"step": "plan_liepin_apply_send", "ok": True, "url": url})
            attempt.steps = steps
            return attempt

        driver = self.driver or create_driver(platform="liepin")
        self.driver = driver

        open_result = driver.open_url_in_new_tab(url, wait_seconds=wait_seconds)
        steps.append({"step": "open_job_url", **open_result})
        if not open_result.get("ok"):
            attempt.error = str(open_result.get("error") or "open_job_url_failed")
            attempt.steps = steps
            return attempt

        inspect_before = _exec_liepin_js(driver, _liepin_apply_inspect_script())
        steps.append({"step": "inspect_before_apply", **inspect_before})
        if _liepin_page_requires_login(inspect_before):
            attempt.error = "login_required"
            attempt.steps = steps
            return attempt
        if inspect_before.get("requires_user_action"):
            attempt.error = str(inspect_before.get("user_action") or "user_action_required")
            attempt.steps = steps
            return attempt
        if not inspect_before.get("chatOpen"):
            click_entry = _exec_liepin_js(driver, _liepin_apply_click_chat_script())
            steps.append({"step": "click_liepin_chat_entry", **click_entry})
            if not click_entry.get("ok"):
                attempt.error = str(click_entry.get("error") or "chat_entry_not_found")
                attempt.steps = steps
                return attempt
            chat_state = _poll_liepin_state(
                driver,
                attempts=20,
                require_chat=True,
            )
            if not chat_state.get("chatOpen"):
                # On a first contact Liepin may only create the conversation
                # and change the entry from 聊一聊 to 继续聊. A second click
                # opens that existing conversation; it does not create another
                # greeting. Poll again because the IM modal hydrates slowly.
                reopen = _exec_liepin_js(driver, _liepin_apply_click_chat_script())
                steps.append({"step": "reopen_liepin_chat", **reopen})
                if reopen.get("ok"):
                    chat_state = _poll_liepin_state(
                        driver,
                        attempts=20,
                        require_chat=True,
                    )
        else:
            chat_state = inspect_before

        steps.append({"step": "inspect_liepin_chat", **chat_state})
        if _liepin_page_requires_login(chat_state):
            attempt.error = "login_required"
            attempt.steps = steps
            return attempt
        if chat_state.get("requires_user_action"):
            attempt.error = str(chat_state.get("user_action") or "user_action_required")
            attempt.steps = steps
            return attempt
        if not chat_state.get("chatOpen"):
            attempt.error = "chat_editor_not_found"
            attempt.steps = steps
            return attempt

        terminal = self._drive_dialog(
            driver,
            message=message,
            steps=steps,
            state=chat_state,
            resume_already_delivered=resume_already_delivered,
        )
        if terminal.get("delivered"):
            attempt.delivered = True
        else:
            attempt.error = str(terminal.get("error") or "delivery_not_verified")
        attempt.steps = steps
        return attempt

    def _drive_dialog(
        self,
        driver: Any,
        message: str,
        steps: list[dict[str, Any]],
        state: dict[str, Any],
        resume_already_delivered: bool,
    ) -> dict[str, Any]:
        if state.get("requires_user_action"):
            return {
                "delivered": False,
                "error": state.get("user_action") or "user_action_required",
            }
        resume_delivered = bool(resume_already_delivered or state.get("resumeDelivered"))
        greeting_delivered = _liepin_greeting_delivery_detected(state, message)

        if resume_delivered:
            steps.append(
                {
                    "step": "resume_already_delivered",
                    "ok": True,
                    "delivered": True,
                    "source": "audit" if resume_already_delivered else "chat_transcript",
                }
            )
        else:
            if state.get("chatOpen") and state.get("canSendChatResume") is False:
                state = _poll_liepin_state(
                    driver,
                    attempts=20,
                    require_chat_resume=True,
                )
                if state.get("requires_user_action"):
                    return {
                        "delivered": False,
                        "error": state.get("user_action") or "user_action_required",
                    }
            if state.get("chatOpen") and state.get("canSendChatResume") is False:
                resume_action = {
                    "ok": False,
                    "error": "chat_resume_action_not_ready",
                }
                steps.append({"step": "click_liepin_resume_action", **resume_action})
            else:
                for resume_action_attempt in range(1, 4):
                    resume_action = _click_liepin_resume_action(driver)
                    steps.append({
                        "step": "click_liepin_resume_action",
                        "attempt": resume_action_attempt,
                        **resume_action,
                    })
                    if not resume_action.get("ok"):
                        break
                    state = _poll_liepin_state(
                        driver,
                        attempts=12,
                        require_resume=True,
                        allow_resume_confirm=True,
                    )
                    if state.get("requires_user_action"):
                        return {
                            "delivered": False,
                            "error": state.get("user_action") or "user_action_required",
                        }
                    if state.get("resumeDelivered") or state.get("canConfirmResume"):
                        break
                    if state.get("canSendChatResume") is False:
                        state = _poll_liepin_state(
                            driver,
                            attempts=20,
                            require_chat_resume=True,
                        )
                        if state.get("requires_user_action"):
                            return {
                                "delivered": False,
                                "error": state.get("user_action") or "user_action_required",
                            }
                        if state.get("canSendChatResume") is False:
                            break
            if (
                not state.get("resumeDelivered")
                and not state.get("canConfirmResume")
                and state.get("canSendChatResume") is True
            ):
                x = resume_action.get("x")
                y = resume_action.get("y")
                if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                    dom_fallback = _exec_liepin_js(
                        driver,
                        _liepin_apply_dom_click_script(x, y),
                    )
                    steps.append({
                        "step": "click_liepin_resume_action_dom_fallback",
                        **dom_fallback,
                    })
                    if dom_fallback.get("ok"):
                        state = _poll_liepin_state(
                            driver,
                            attempts=12,
                            require_resume=True,
                            allow_resume_confirm=True,
                        )
                    if state.get("requires_user_action"):
                        return {
                            "delivered": False,
                            "error": state.get("user_action") or "user_action_required",
                        }
            if state.get("canConfirmResume"):
                steps.append({"step": "inspect_liepin_resume_dialog", **state})
                if state.get("resumeAttachmentSelected") is False:
                    resume_confirm = {
                        "ok": False,
                        "error": "resume_attachment_not_selected",
                    }
                    steps.append({"step": "click_liepin_resume_confirm", **resume_confirm})
                else:
                    for confirm_attempt in range(1, 4):
                        resume_confirm = _click_liepin_resume_confirm(driver)
                        steps.append({
                            "step": "click_liepin_resume_confirm",
                            "attempt": confirm_attempt,
                            **resume_confirm,
                        })
                        if not resume_confirm.get("ok"):
                            break
                        state = _poll_liepin_state(
                            driver,
                            attempts=12,
                            require_resume=True,
                            allow_resume_confirm=True,
                        )
                        if state.get("requires_user_action"):
                            return {
                                "delivered": False,
                                "error": state.get("user_action") or "user_action_required",
                            }
                        if state.get("resumeDelivered") or not state.get("canConfirmResume"):
                            break
                if state.get("canConfirmResume") and not state.get("resumeDelivered"):
                    x = resume_confirm.get("x")
                    y = resume_confirm.get("y")
                    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                        dom_fallback = _exec_liepin_js(
                            driver,
                            _liepin_apply_dom_click_script(x, y),
                        )
                        steps.append({
                            "step": "click_liepin_resume_confirm_dom_fallback",
                            **dom_fallback,
                        })
                        if dom_fallback.get("ok"):
                            state = _poll_liepin_state(
                                driver,
                                attempts=20,
                                require_resume=True,
                            )
                        if state.get("requires_user_action"):
                            return {
                                "delivered": False,
                                "error": state.get("user_action") or "user_action_required",
                            }
            resume_delivered = bool(state.get("resumeDelivered"))
            steps.append(
                {
                    "step": "verify_liepin_resume_delivery",
                    **state,
                    "delivered": resume_delivered,
                }
            )

        if greeting_delivered:
            steps.append(
                {
                    "step": "verify_liepin_personalized_greeting",
                    "ok": True,
                    "delivered": True,
                    "pre_existing": True,
                }
            )
        else:
            fill = _exec_liepin_js(driver, _liepin_apply_fill_message_script(message))
            steps.append({"step": "fill_liepin_personalized_greeting", **fill})
            if fill.get("ok") and fill.get("filled"):
                send = _exec_liepin_js(driver, _liepin_apply_click_message_send_script())
            else:
                send = {"ok": False, "error": "message_editor_not_found"}
            steps.append({"step": "click_liepin_message_send", **send})
            if send.get("ok"):
                state = _poll_liepin_state(driver, attempts=4, expected_message=message)
            greeting_delivered = _liepin_greeting_delivery_detected(state, message)
            # The resume confirmation can finish asynchronously while the
            # personalized greeting is being sent. Reconcile against the
            # freshest chat state so a completed resume delivery is not
            # rejected because the earlier verification poll was premature.
            resume_delivered = bool(resume_delivered or state.get("resumeDelivered"))
            steps.append(
                {
                    "step": "verify_liepin_personalized_greeting",
                    **state,
                    "delivered": greeting_delivered,
                }
            )
            if state.get("requires_user_action"):
                return {
                    "delivered": False,
                    "error": state.get("user_action") or "user_action_required",
                }

        if greeting_delivered and not resume_delivered:
            state = _poll_liepin_state(
                driver,
                attempts=20,
                require_resume=True,
            )
            resume_delivered = bool(state.get("resumeDelivered"))
            steps.append(
                {
                    "step": "reconcile_liepin_resume_after_greeting",
                    **state,
                    "delivered": resume_delivered,
                }
            )
            if state.get("requires_user_action"):
                return {
                    "delivered": False,
                    "error": state.get("user_action") or "user_action_required",
                }

        if resume_delivered and greeting_delivered:
            return {"delivered": True}
        if not resume_delivered and not greeting_delivered:
            return {"delivered": False, "error": "resume_and_greeting_delivery_not_verified"}
        if not resume_delivered:
            return {"delivered": False, "error": "resume_delivery_not_verified"}
        return {"delivered": False, "error": "greeting_delivery_not_verified"}


def _handoff_evidence(job: dict[str, Any]) -> dict[str, Any]:
    greeting = str(job.get("cloud_greeting") or job.get("greeting") or "")
    return {
        "has_greeting": bool(greeting),
        "greeting": greeting,
        "score": job.get("score"),
        "match_level": job.get("match_level") or job.get("recommendation") or job.get("cloud_recommendation"),
    }


def _normalize_liepin_url(url: str) -> str:
    value = str(url or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
    return value.rstrip("/")


def _normalized_message(value: object) -> str:
    return "".join(str(value or "").split())


def _liepin_greeting_delivery_detected(state: dict[str, Any], message: str) -> bool:
    expected = _normalized_message(message)
    if not expected:
        return False
    outgoing = state.get("outgoingMessages")
    if not isinstance(outgoing, list):
        return False
    return any(_normalized_message(item) == expected for item in outgoing)


def _poll_liepin_state(
    driver: Any,
    attempts: int = 4,
    *,
    require_resume: bool = False,
    allow_resume_confirm: bool = False,
    expected_message: str = "",
    require_chat: bool = False,
    require_chat_resume: bool = False,
) -> dict[str, Any]:
    state: dict[str, Any] = {"ok": False, "error": "inspection_not_run"}
    for _ in range(max(1, attempts)):
        time.sleep(1)
        state = _exec_liepin_js(driver, _liepin_apply_inspect_script())
        if _liepin_page_requires_login(state):
            return state
        if require_chat and state.get("chatOpen"):
            return state
        if require_chat_resume and state.get("canSendChatResume"):
            return state
        if require_resume and state.get("resumeDelivered"):
            return state
        if require_resume and allow_resume_confirm and state.get("canConfirmResume"):
            return state
        if expected_message and _liepin_greeting_delivery_detected(state, expected_message):
            return state
        if (
            not require_resume
            and not expected_message
            and not require_chat
            and not require_chat_resume
        ):
            return state
    return state


def _liepin_attempt_delivery_parts(attempt: SendAttempt) -> tuple[bool, bool]:
    resume_delivered = False
    greeting_delivered = False
    for step in attempt.steps:
        resume_delivered = resume_delivered or step.get("resumeDelivered") is True
        if step.get("step") in {
            "resume_already_delivered",
            "verify_liepin_resume_delivery",
            "reconcile_liepin_resume_after_greeting",
        }:
            resume_delivered = resume_delivered or step.get("delivered") is True
        if step.get("step") == "verify_liepin_personalized_greeting":
            greeting_delivered = greeting_delivered or step.get("delivered") is True
    return resume_delivered, greeting_delivered


def _handoff_item(
    job: dict[str, Any],
    index: int,
    status: str,
    error: str = "",
) -> dict[str, Any]:
    evidence = _handoff_evidence(job)
    action = (
        "copy_greeting_to_liepin_page"
        if evidence["has_greeting"]
        else "run_liepin_greet_preview_before_contact"
    )
    return {
        "index": index,
        "status": status,
        "action": action,
        "job_name": str(job.get("name") or job.get("title") or ""),
        "company": str(job.get("company") or ""),
        "url": str(job.get("url") or ""),
        "error": error,
        **evidence,
    }


def _exec_liepin_js(driver: Any, script: str) -> dict[str, Any]:
    if not hasattr(driver, "_exec_js"):
        return {"ok": False, "error": "driver_js_not_supported"}
    result = driver._exec_js(script)
    if isinstance(result, dict) and isinstance(result.get("raw"), str):
        import json

        try:
            parsed = json.loads(result["raw"])
            return parsed if isinstance(parsed, dict) else {"ok": False, "error": "unexpected_js_result"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return result if isinstance(result, dict) else {"ok": False, "error": "unexpected_js_result"}


def _click_liepin_resume_action(driver: Any) -> dict[str, Any]:
    result = _exec_liepin_js(driver, _liepin_apply_click_resume_script())
    return _click_liepin_result(driver, result)


def _click_liepin_resume_confirm(driver: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "error": "resume_confirm_button_not_found"}
    previous: tuple[float, float] | None = None
    for _ in range(10):
        result = _exec_liepin_js(driver, _liepin_apply_click_resume_confirm_script())
        x = result.get("x")
        y = result.get("y")
        if not result.get("ok") or not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            previous = None
            time.sleep(0.5)
            continue
        current = (float(x), float(y))
        if previous is not None and abs(current[0] - previous[0]) <= 2 and abs(current[1] - previous[1]) <= 2:
            return _click_liepin_result(driver, result)
        previous = current
        time.sleep(0.5)
    return {**result, "ok": False, "error": "resume_confirm_button_not_stable"}


def _click_liepin_result(driver: Any, result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("ok"):
        return result
    x = result.get("x")
    y = result.get("y")
    native_click = getattr(driver, "_click_at", None)
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return result
    if not callable(native_click):
        fallback = _exec_liepin_js(driver, _liepin_apply_dom_click_script(x, y))
        return {**result, **fallback, "click_mode": "dom_fallback"}
    try:
        native_click(x, y)
    except Exception as exc:
        return {
            **result,
            "ok": False,
            "error": f"resume_native_click_failed: {exc}",
        }
    return {**result, "ok": True, "click_mode": "native_mouse"}


def _liepin_page_requires_login(state: dict[str, Any]) -> bool:
    if state.get("loginRequired") is True:
        return True
    text = f"{state.get('title') or ''}\n{state.get('bodySnippet') or ''}"
    return any(
        token in text
        for token in ("登录/注册", "扫码登录", "验证码登录", "手机验证码", "安全验证", "滑块")
    )


def _liepin_delivery_detected(state: dict[str, Any]) -> bool:
    if state.get("delivered") is True:
        return True
    text = f"{state.get('title') or ''}\n{state.get('bodySnippet') or ''}"
    return any(
        token in text
        for token in ("投递成功", "简历发送成功", "简历已发送", "已投递")
    )


def _liepin_apply_inspect_script() -> str:
    return r"""
    (function(){
      const text = (document.body && (document.body.innerText || document.body.textContent) || '').trim();
      const title = document.title || '';
      const href = location.href || '';
      const loginRequired = /\/login|passport|account/.test(href) || /登录\/注册|扫码登录|验证码登录|手机验证码|安全验证|滑块/.test(title + '\n' + text.slice(0, 800));
      const delivered = /\/job\/apply\/success/.test(href)
        || /投递成功/.test(title)
        || /投递成功|简历发送成功|简历已发送|已投递/.test(text);
      const visibleButtons = Array.from(document.querySelectorAll(
        'button,a,[role="button"],.im-ui-action-button,.action-resume'
      )).filter(el => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 1 && rect.height > 1;
      }).map(el => (el.innerText || el.textContent || '').trim());
      const canConfirmResume = visibleButtons.some(t => /立即投递|确认投递|投递/.test(t));
      const canSendResume = visibleButtons.some(t => /^(发简历|发送简历|投递简历|投简历)$/.test(t));
      const canSendChatResume = Array.from(document.querySelectorAll('.action-resume'))
        .some(el => {
          const style = window.getComputedStyle(el);
          const rect = el.getBoundingClientRect();
          const label = (el.innerText || el.textContent || '').trim();
          return /^(发简历|发送简历)$/.test(label)
            && style.display !== 'none' && style.visibility !== 'hidden'
            && rect.width > 1 && rect.height > 1;
        });
      const editor = Array.from(document.querySelectorAll(
        'textarea.im-ui-textarea,textarea,[contenteditable="true"]'
      )).find(el => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 1 && rect.height > 1;
      });
      const outgoingMessages = Array.from(new Set(
        Array.from(document.querySelectorAll(
          '.im-ui-txt.send .text, .im-ui-txt.send .im-ui-txt-content, .im-ui-txt.send'
        )).map(el => (el.innerText || el.textContent || '').trim()).filter(Boolean)
      ));
      const resumeDelivered = Boolean(
        document.querySelector('.im-ui-txt.send .im-ui-send-attachment-card')
      ) || outgoingMessages.some(message => /这是我的简历|简历已发送|已发送简历/.test(message));
      const resumeAttachmentSelected = Boolean(document.querySelector(
        'input[type="radio"]:checked,[role="radio"][aria-checked="true"],.ant-radio-checked'
      ));
      const requiresResume = /请选择简历|上传简历|完善简历|创建简历|附件简历/.test(text)
        && !canConfirmResume && !resumeDelivered;
      const requiresCaptcha = /验证码登录|手机验证码|安全验证|滑块/.test(text);
      const moderationPrompt = '你的发言疑似存在不良信息，请文明沟通。如需平台介入请及时反馈';
      const moderationNotice = text.includes('你的发言疑似存在不良信息')
        || text.includes('请文明沟通。如需平台介入请及时反馈');
      const moderationLeaves = Array.from(document.querySelectorAll('body *')).filter(el => {
        const own = (el.innerText || el.textContent || '').trim();
        if (!own.includes('你的发言疑似存在不良信息')) return false;
        const childContains = Array.from(el.children || []).some(child =>
          ((child.innerText || child.textContent || '').trim()).includes('你的发言疑似存在不良信息')
        );
        if (childContains) return false;
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden'
          && rect.width > 1 && rect.height > 1;
      });
      const requiresModeration = moderationLeaves.some(el =>
        !el.closest('.im-ui-system-tip,.im-ui-message-item')
      );
      const requiresUserAction = requiresResume || requiresCaptcha || requiresModeration;
      const userAction = requiresModeration
        ? 'message_moderation_required'
        : (requiresCaptcha ? 'captcha_required' : (requiresResume ? 'resume_selection_required' : ''));
      const userPrompt = requiresModeration
        ? moderationPrompt
        : (requiresCaptcha ? '请在猎聘页面完成安全验证。' : (requiresResume ? '请在猎聘页面选择或完善要发送的简历。' : ''));
      return JSON.stringify({
        ok: true,
        href,
        title,
        loginRequired,
        delivered,
        chatOpen: Boolean(editor),
        canSendResume,
        canSendChatResume,
        canConfirmResume,
        resumeAttachmentSelected,
        resumeDelivered,
        outgoingMessages,
        requires_user_action: requiresUserAction,
        user_action: userAction,
        user_prompt: userPrompt,
        moderation_notice: moderationNotice,
        moderation_notice_blocking: requiresModeration,
        bodySnippet: text.slice(0, 1200)
      });
    })()
    """


def _liepin_apply_click_chat_script() -> str:
    return r"""
    (function(){
      const labels = ['继续聊', '聊一聊', '立即沟通', '继续沟通', '沟通'];
      function visible(el){
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 1 && rect.height > 1;
      }
      const all = Array.from(document.querySelectorAll('button,a,[role="button"]'));
      for (const label of labels) {
        const el = all.find(node => visible(node) && (node.innerText || node.textContent || '').trim() === label);
        if (el) {
          el.click();
          return JSON.stringify({ok:true, clicked:label});
        }
      }
      return JSON.stringify({ok:false, error:'chat_entry_not_found'});
    })()
    """


def _liepin_apply_fill_message_script(message: str) -> str:
    import json

    msg = json.dumps(message)
    return f"""
    (function(){{
      const message = {msg};
      const selectors = [
        'textarea',
        '[contenteditable="true"]',
        '.chat-input',
        '.im-input',
        '.message-input'
      ];
      function visible(el){{
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 1 && rect.height > 1;
      }}
      let editor = null;
      for (const selector of selectors) {{
        editor = Array.from(document.querySelectorAll(selector)).find(visible);
        if (editor) break;
      }}
      if (!editor) return JSON.stringify({{ok:true, filled:false, reason:'editor_not_found'}});
      editor.focus();
      if (editor.tagName === 'TEXTAREA' || editor.tagName === 'INPUT') {{
        const prototype = editor.tagName === 'TEXTAREA'
          ? window.HTMLTextAreaElement.prototype
          : window.HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(prototype, 'value').set;
        setter.call(editor, message);
      }} else {{
        editor.innerText = message;
        editor.textContent = message;
      }}
      editor.dispatchEvent(new InputEvent('input', {{bubbles:true, inputType:'insertText', data:message}}));
      editor.dispatchEvent(new Event('change', {{bubbles:true}}));
      return JSON.stringify({{ok:true, filled:true, tag:editor.tagName, len:message.length}});
    }})()
    """


def _liepin_apply_click_message_send_script() -> str:
    return r"""
    (function(){
      const buttons = Array.from(document.querySelectorAll(
        'button.im-ui-basic-send-btn,button'
      ));
      const button = buttons.find(el => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        const text = (el.innerText || el.textContent || '').trim();
        return text === '发送' && !el.disabled && style.display !== 'none'
          && style.visibility !== 'hidden' && rect.width > 1 && rect.height > 1;
      });
      if (!button) {
        return JSON.stringify({ok:false, error:'message_send_button_not_found'});
      }
      button.click();
      return JSON.stringify({ok:true, clicked:'发送'});
    })()
    """


def _liepin_apply_click_resume_script() -> str:
    return r"""
    (function(){
      const labels = ['发简历', '发送简历', '投递简历', '投简历'];
      function visible(el){
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 1 && rect.height > 1;
      }
      function target(el, label){
        el.scrollIntoView({block:'center', inline:'center'});
        const rect = el.getBoundingClientRect();
        return JSON.stringify({
          ok:true,
          clicked:label,
          x:rect.left + rect.width / 2,
          y:rect.top + rect.height / 2
        });
      }
      const chatResume = Array.from(document.querySelectorAll('.action-resume'))
        .find(node => visible(node) && /^(发简历|发送简历)$/.test(
          (node.innerText || node.textContent || '').trim()
        ));
      if (chatResume) {
        return target(chatResume, (chatResume.innerText || chatResume.textContent || '').trim());
      }
      const chatEditor = Array.from(document.querySelectorAll(
        'textarea.im-ui-textarea,textarea,[contenteditable="true"]'
      )).find(visible);
      if (chatEditor) {
        return JSON.stringify({ok:false, error:'chat_resume_action_not_ready'});
      }
      const all = Array.from(document.querySelectorAll(
        'button,a,[role="button"],.im-ui-action-button,.action-resume'
      ));
      for (const label of labels) {
        const el = all.find(node => visible(node) && (node.innerText || node.textContent || '').trim() === label);
        if (el) {
          return target(el, label);
        }
      }
      return JSON.stringify({ok:false, error:'resume_action_not_found'});
    })()
    """


def _liepin_apply_dom_click_script(x: float, y: float) -> str:
    return f"""
    (function(){{
      const el = document.elementFromPoint({float(x)}, {float(y)});
      if (!el) return JSON.stringify({{ok:false, error:'resume_action_not_found_at_point'}});
      el.click();
      return JSON.stringify({{ok:true}});
    }})()
    """


def _liepin_apply_click_resume_confirm_script() -> str:
    return r"""
    (function(){
      const labels = ['立即投递', '确认投递'];
      function visible(el){
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 1 && rect.height > 1;
      }
      const all = Array.from(document.querySelectorAll('button,[role="button"],a'));
      for (const label of labels) {
        const el = all.find(node => visible(node) && (node.innerText || node.textContent || '').trim() === label);
        if (el) {
          el.scrollIntoView({block:'center', inline:'center'});
          const rect = el.getBoundingClientRect();
          return JSON.stringify({
            ok:true,
            clicked:label,
            x:rect.left + rect.width / 2,
            y:rect.top + rect.height / 2
          });
        }
      }
      return JSON.stringify({ok:false, error:'resume_confirm_button_not_found'});
    })()
    """
