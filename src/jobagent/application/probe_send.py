from __future__ import annotations

from jobagent.domain.models import SendAttempt
from jobagent.drivers.boss import create_driver
from jobagent.infra.state import last_probe_path, save_json
from jobagent.platforms.boss.send_flow import execute_boss_greeting_flow


def run_probe_send(job_url: str, message: str) -> SendAttempt:
    """Send a single greeting probe, aligned with boss-radar's verified flow.

    Handles verification redirects and verifies any auto-sent claim.
    """
    driver = create_driver()
    attempt = execute_boss_greeting_flow(
        driver,
        job_url,
        message,
        verify=True,
        retry_on_unverified=False,
    )
    save_json(last_probe_path(), attempt.to_dict())
    return attempt
