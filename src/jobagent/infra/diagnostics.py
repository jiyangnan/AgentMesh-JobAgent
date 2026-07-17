"""Structured progress events and local crash diagnostics."""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import threading
import time
import traceback
from typing import Any

from jobagent.infra.state import LOG_DIR


def emit_stage(stage: str, **details: Any) -> None:
    payload = {
        "event": "jobagent_progress",
        "stage": stage,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **details,
    }
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)


@contextmanager
def progress_heartbeat(stage: str, *, interval_seconds: float = 15.0, **details: Any):
    """Emit bounded, non-sensitive heartbeats while a blocking boundary runs."""
    stop = threading.Event()
    started = time.monotonic()

    def pulse() -> None:
        while not stop.wait(max(0.05, interval_seconds)):
            emit_stage(
                stage,
                heartbeat=True,
                elapsed_seconds=round(time.monotonic() - started, 1),
                **details,
            )

    thread = threading.Thread(target=pulse, name=f"jobagent-{stage}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=max(0.1, interval_seconds + 0.1))


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
