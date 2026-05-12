from __future__ import annotations

from jobagent.domain.models import SendAttempt
from jobagent.drivers.boss import create_driver
from jobagent.infra.state import last_probe_path, load_json, save_json


def run_verify_last_send(message: str | None = None) -> SendAttempt:
    previous = load_json(last_probe_path())
    if previous is None and not message:
        return SendAttempt(job_url="", message="", delivered=False, error="no_previous_probe_send")

    effective_message = message or previous.get("message", "")
    effective_job_url = previous.get("job_url", "") if previous else ""

    attempt = SendAttempt(job_url=effective_job_url, message=effective_message, delivered=False)
    driver = create_driver()
    verify_result = driver.verify_delivery(effective_message)
    attempt.steps = [{"step": "verify_delivery", **verify_result}]
    attempt.delivered = bool(verify_result.get("delivered"))
    if not attempt.delivered:
        attempt.error = "delivery_not_verified"
    save_json(last_probe_path(), attempt.to_dict())
    return attempt
