"""Boss greeting send state machine."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from jobagent.domain.models import SendAttempt
from jobagent.drivers.boss.base import BossActionDriver


def execute_boss_greeting_flow(
    driver: BossActionDriver,
    job_url: str,
    message: str,
    *,
    verify: bool = True,
    retry_on_unverified: bool = True,
) -> SendAttempt:
    """Execute the verified Boss greeting flow for one job URL."""
    attempt = SendAttempt(
        job_url=job_url,
        message=message,
        delivered=False,
    )
    steps: list[dict[str, Any]] = []

    open_result = driver.open_url_in_new_tab(job_url, wait_seconds=6)
    steps.append({"step": "open_job_url", **open_result})
    if not open_result.get("ok"):
        err = open_result.get("error", "")
        attempt.error = "risk_control" if err == "risk_control" else "open_job_url_failed"
        attempt.steps = steps
        return attempt

    editor_result: dict[str, Any] | None = None
    chat_click = driver.click_chat_entry()
    steps.append({"step": "click_chat_entry", **chat_click})
    if not chat_click.get("ok"):
        err = chat_click.get("error", "")
        if err == "risk_control":
            attempt.error = "risk_control"
            attempt.steps = steps
            return attempt
        # Boss can finish navigating to the job-specific chat after the CDP
        # request itself times out. Wait for a writable editor, but recover only
        # when the chat URL still identifies this exact job.
        editor_result = driver.inspect_chat_editor()
        steps.append({"step": "inspect_chat_editor_after_entry_failure", **editor_result})
        expected_job_id = urlsplit(job_url).path.rsplit("/", 1)[-1].removesuffix(".html")
        same_job = (
            str(editor_result.get("jobId") or "") == expected_job_id
            or (
                bool(chat_click.get("clicked"))
                and str(chat_click.get("jobId") or "") == expected_job_id
            )
        )
        if (
            not editor_result.get("editorFound")
            or not same_job
        ):
            attempt.error = "chat_entry_failed"
            attempt.steps = steps
            return attempt
        steps.append({"step": "chat_entry_recovered", "ok": True, "jobId": expected_job_id})
    if chat_click.get("autoSent"):
        if chat_click.get("platformDefaultSent"):
            steps.append(
                {
                    "step": "platform_default_does_not_complete_custom_greeting",
                    "ok": True,
                }
            )
        else:
            verify_result = driver.verify_delivery(message)
            steps.append({"step": "verify_auto_sent", **verify_result})
            attempt.delivered = bool(verify_result.get("delivered"))
            if not attempt.delivered:
                attempt.error = "auto_sent_not_verified"
            attempt.steps = steps
            return attempt

    if editor_result is None:
        editor_result = driver.inspect_chat_editor()
        steps.append({"step": "inspect_chat_editor", **editor_result})
    if editor_result.get("error") == "risk_control":
        attempt.error = "risk_control"
        attempt.steps = steps
        return attempt
    if editor_result.get("loginDialog"):
        recovery = _recover_draft_delivery(driver, message)
        if recovery is not None:
            steps.append({"step": "recover_draft_delivery", **recovery})
            attempt.delivered = bool(recovery.get("delivered"))
            if attempt.delivered:
                attempt.steps = steps
                return attempt
        attempt.error = "login_required"
        attempt.steps = steps
        return attempt
    if editor_result.get("autoSent"):
        verify_result = driver.verify_delivery(message)
        steps.append({"step": "verify_auto_sent", **verify_result})
        attempt.delivered = bool(verify_result.get("delivered"))
        if not attempt.delivered:
            attempt.error = "auto_sent_not_verified"
        attempt.steps = steps
        return attempt
    # Re-runs should be idempotent: if this exact greeting is already visible
    # in the chat transcript with a delivery marker, do not fill and send again.
    pre_verify = driver.verify_delivery(message)
    steps.append({"step": "verify_pre_existing_delivery", **pre_verify})
    if pre_verify.get("delivered"):
        attempt.delivered = True
        attempt.steps = steps
        return attempt
    if not editor_result.get("editorFound"):
        # Editor selector didn't match. But if we have evidence the chat sidebar
        # is open (chatContainer or sendFound), the conversation IS established —
        # Boss's DOM may have changed and our selector is stale. Try fill+send
        # anyway: fill_chat_message has its own editor-finding logic.
        chat_open_signals = (
            editor_result.get("chatContainer")
            or editor_result.get("sendFound")
        )
        if not chat_open_signals:
            attempt.error = "chat_editor_not_found"
            attempt.steps = steps
            return attempt
        # Fall through and try fill+send with whatever editor fill_chat_message can find.

    fill_result = driver.fill_chat_message(message)
    steps.append({"step": "fill_chat_message", **fill_result})
    if not fill_result.get("ok"):
        attempt.error = "fill_message_failed"
        attempt.steps = steps
        return attempt

    send_result = driver.click_send()
    steps.append({"step": "click_send", **send_result})
    if not send_result.get("ok"):
        attempt.error = "click_send_failed"
        attempt.steps = steps
        return attempt

    if verify:
        verify_result = driver.verify_delivery(message)
        steps.append({"step": "verify_delivery", **verify_result})
        attempt.delivered = bool(verify_result.get("delivered"))
        if not attempt.delivered:
            recovery = _recover_draft_delivery(driver, message)
            if recovery is not None:
                steps.append({"step": "recover_draft_delivery", **recovery})
                attempt.delivered = bool(recovery.get("delivered"))
        if retry_on_unverified and not attempt.delivered:
            retry_fill = driver.fill_chat_message(message)
            steps.append({"step": "retry_fill_chat_message", **retry_fill})
            if retry_fill.get("ok"):
                retry_send = driver.click_send()
                steps.append({"step": "retry_click_send", **retry_send})
                if retry_send.get("ok"):
                    retry_verify = driver.verify_delivery(message)
                    steps.append({"step": "retry_verify_delivery", **retry_verify})
                    attempt.delivered = bool(retry_verify.get("delivered"))
        if not attempt.delivered:
            attempt.error = "delivery_not_verified"
    else:
        attempt.delivered = True

    attempt.steps = steps
    return attempt


def _recover_draft_delivery(driver: BossActionDriver, message: str) -> dict[str, Any] | None:
    recover = getattr(driver, "recover_draft_delivery", None)
    if not callable(recover):
        return None
    try:
        return recover(message)
    except Exception as e:
        return {"ok": False, "delivered": False, "error": str(e)}
