"""Audit log — query and report greeting history."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jobagent.infra.state import audit_log_path


class AuditLog:
    """Read-only interface over the greeting audit log."""

    def __init__(self, path: Path | None = None):
        self.path = path or audit_log_path()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception):
            return []

    def list_recent(self, n: int = 20) -> list[dict[str, Any]]:
        """Return the most recent N send records (newest first)."""
        records = self._load()
        return list(reversed(records[-n:]))

    def summary(self) -> dict[str, Any]:
        """Return aggregate statistics over all send attempts."""
        records = self._load()
        total = len(records)
        delivered = sum(1 for r in records if r.get("delivered"))
        failed = total - delivered
        errors: dict[str, int] = {}
        for r in records:
            if not r.get("delivered"):
                err = r.get("error", "unknown")
                errors[err] = errors.get(err, 0) + 1

        # Group by date (using created_at field)
        daily: dict[str, dict[str, int]] = {}
        for r in records:
            ts = r.get("created_at", "")
            day = ts[:10] if len(ts) >= 10 else "unknown"
            if day not in daily:
                daily[day] = {"total": 0, "delivered": 0}
            daily[day]["total"] += 1
            if r.get("delivered"):
                daily[day]["delivered"] += 1

        return {
            "total": total,
            "delivered": delivered,
            "failed": failed,
            "success_rate": round(delivered / total, 2) if total else 0.0,
            "error_breakdown": errors,
            "daily_stats": daily,
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        }

    def latest_failed(self, n: int = 10) -> list[dict[str, Any]]:
        """Return the most recent failed attempts."""
        records = self._load()
        failed = [r for r in records if not r.get("delivered")]
        return list(reversed(failed[-n:]))
