"""Structured progress events and local crash diagnostics."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import traceback
from typing import Any

from jobagent.infra.state import LOG_DIR


def emit_stage(stage: str, **details: Any) -> None:
    payload = {"event": "jobagent_progress", "stage": stage, **details}
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)


def write_exception_log(exc: BaseException, *, command: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    path = LOG_DIR / f"jobagent-error-{timestamp}.log"
    content = (
        f"timestamp={datetime.now(timezone.utc).isoformat()}\n"
        f"command={command}\n"
        + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    )
    path.write_text(content, encoding="utf-8")
    return path
