from __future__ import annotations

from jobagent.domain.models import SendAttempt
from jobagent.drivers.boss import create_driver
from jobagent.infra.state import last_probe_path, save_json


def run_probe_send(job_url: str, message: str) -> SendAttempt:
    """Send a single greeting probe, aligned with boss-radar's verified flow.

    Handles risk-control redirects and verifies any auto-sent claim.
    """
    driver = create_driver()
    attempt = SendAttempt(job_url=job_url, message=message, delivered=False)

    steps = []

    # Step 1: Open job URL
    open_result = driver.open_url_in_new_tab(job_url, wait_seconds=6)
    steps.append({"step": "open_job_url", **open_result})
    if not open_result.get("ok"):
        err = open_result.get("error", "")
        attempt.error = "risk_control" if err == "risk_control" else "open_job_url_failed"
        attempt.steps = steps
        save_json(last_probe_path(), attempt.to_dict())
        return attempt

    # Step 2: Click chat entry (includes 继续沟通 popup handling)
    chat_click = driver.click_chat_entry()
    steps.append({"step": "click_chat_entry", **chat_click})
    if not chat_click.get("ok"):
        err = chat_click.get("error", "")
        attempt.error = "risk_control" if err == "risk_control" else "chat_entry_failed"
        attempt.steps = steps
        save_json(last_probe_path(), attempt.to_dict())
        return attempt
    if chat_click.get("autoSent"):
        verify_result = driver.verify_delivery(message)
        steps.append({"step": "verify_auto_sent", **verify_result})
        attempt.delivered = bool(verify_result.get("delivered"))
        if not attempt.delivered:
            attempt.error = "auto_sent_not_verified"
        attempt.steps = steps
        save_json(last_probe_path(), attempt.to_dict())
        return attempt

    # Step 3: Wait for sidebar chat panel
    editor_result = driver.inspect_chat_editor()
    steps.append({"step": "inspect_chat_editor", **editor_result})
    if editor_result.get("error") == "risk_control":
        attempt.error = "risk_control"
        attempt.steps = steps
        save_json(last_probe_path(), attempt.to_dict())
        return attempt
    if editor_result.get("autoSent"):
        verify_result = driver.verify_delivery(message)
        steps.append({"step": "verify_auto_sent", **verify_result})
        attempt.delivered = bool(verify_result.get("delivered"))
        if not attempt.delivered:
            attempt.error = "auto_sent_not_verified"
        attempt.steps = steps
        save_json(last_probe_path(), attempt.to_dict())
        return attempt
    if not editor_result.get("editorFound"):
        attempt.error = "chat_editor_not_found"
        attempt.steps = steps
        save_json(last_probe_path(), attempt.to_dict())
        return attempt

    # Step 4: Fill message
    fill_result = driver.fill_chat_message(message)
    steps.append({"step": "fill_chat_message", **fill_result})
    if not fill_result.get("ok"):
        attempt.error = "fill_message_failed"
        attempt.steps = steps
        save_json(last_probe_path(), attempt.to_dict())
        return attempt

    # Step 5: Click send (native .click() per boss-radar findings)
    send_result = driver.click_send()
    steps.append({"step": "click_send", **send_result})
    if not send_result.get("ok"):
        attempt.error = "click_send_failed"
        attempt.steps = steps
        save_json(last_probe_path(), attempt.to_dict())
        return attempt

    # Step 6: Verify delivery
    verify_result = driver.verify_delivery(message)
    steps.append({"step": "verify_delivery", **verify_result})
    attempt.delivered = bool(verify_result.get("delivered"))
    if not attempt.delivered:
        attempt.error = "delivery_not_verified"
    attempt.steps = steps
    save_json(last_probe_path(), attempt.to_dict())
    return attempt
