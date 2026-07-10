"""Cross-process guard for browser/discover/send activity."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone

from jobagent.infra.state import activity_lock_path


@contextmanager
def active_command(command: str):
    path = activity_lock_path()
    payload = {
        "pid": os.getpid(),
        "command": command,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        yield
    finally:
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if current.get("pid") == os.getpid():
                path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass
