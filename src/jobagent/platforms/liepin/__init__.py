"""Liepin platform boundary.

M2 starts in human-in-the-loop mode: parse saved samples, inspect login state,
collect visible cards, rank/greet preview, and open selected jobs for manual
review or perform controlled automatic send/apply.
"""

from .apply import LiepinApplyOpener, LiepinApplyOpenResult, LiepinApplySender
from .audit import LiepinAuditEvent, LiepinAuditLog, liepin_audit_log_path
from .collect import (
    LiepinCollectResult,
    LiepinReadOnlyCollector,
    build_liepin_search_url,
    write_liepin_snapshot,
)
from .parser import collect_liepin_fixture, liepin_job_id, parse_liepin_job
from .selectors import LIEPIN_CARD_SELECTORS, LIEPIN_SELECTOR_VERSION, build_liepin_snapshot_script
from .session import LIEPIN_LOGIN_URL, LiepinSessionGuide, LiepinSessionStatus

__all__ = [
    "LIEPIN_LOGIN_URL",
    "LiepinApplyOpener",
    "LiepinApplyOpenResult",
    "LiepinApplySender",
    "LiepinAuditEvent",
    "LiepinAuditLog",
    "LiepinCollectResult",
    "LiepinReadOnlyCollector",
    "LiepinSessionGuide",
    "LiepinSessionStatus",
    "LIEPIN_CARD_SELECTORS",
    "LIEPIN_SELECTOR_VERSION",
    "build_liepin_search_url",
    "build_liepin_snapshot_script",
    "collect_liepin_fixture",
    "liepin_job_id",
    "liepin_audit_log_path",
    "parse_liepin_job",
    "write_liepin_snapshot",
]
