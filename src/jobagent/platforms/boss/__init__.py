"""Boss platform boundary.

This package owns Boss-specific selectors, parsers, and collection flows.
Browser transport still lives under ``jobagent.drivers.boss`` so future
platforms can reuse the runtime without sharing page logic.
"""

from .collect import BossDataDriver
from .parser import boss_job_id, parse_boss_job
from .send_flow import execute_boss_greeting_flow

__all__ = [
    "BossDataDriver",
    "boss_job_id",
    "execute_boss_greeting_flow",
    "parse_boss_job",
]
