"""Zhilian platform event audit log."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jobagent.infra.state import STATE_DIR, ensure_dirs


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def zhilian_audit_log_path() -> Path:
    ensure_dirs()
    return STATE_DIR / "zhilian_audit_log.json"


@dataclass(frozen=True)
class ZhilianAuditEvent:
    action: str
    status: str
    job_url: str = ""
    job_name: str = ""
    company: str = ""
    error: str = ""
    message: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    platform: str = "zhilian"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ZhilianAuditLog:
    def __init__(self, path: Path | None = None):
        self.path = path or zhilian_audit_log_path()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return data if isinstance(data, list) else []

    def append(self, event: ZhilianAuditEvent) -> None:
        records = self._load()
        records.append(event.to_dict())
        ensure_dirs()
        self.path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    def append_event(self, action: str, status: str, **kwargs: Any) -> None:
        self.append(ZhilianAuditEvent(action=action, status=status, **kwargs))

    def list_recent(self, n: int = 20) -> list[dict[str, Any]]:
        records = self._load()
        return list(reversed(records[-max(1, n):]))

    def delivered_apply_send_urls(self) -> set[str]:
        urls: set[str] = set()
        for record in self._load():
            if record.get("action") == "apply_send" and record.get("status") == "delivered":
                url = str(record.get("job_url") or "").strip().rstrip("/")
                if url:
                    urls.add(url)
        return urls

    def summary(self) -> dict[str, Any]:
        records = self._load()
        by_action: dict[str, int] = {}
        by_status: dict[str, int] = {}
        apply_open_total = 0
        apply_open_with_greeting = 0
        apply_open_missing_greeting = 0
        apply_send_total = 0
        apply_send_delivered = 0
        apply_send_failed = 0
        apply_send_planned = 0
        apply_send_skipped = 0
        apply_send_greeting_delivery: dict[str, int] = {}
        for record in records:
            action = str(record.get("action") or "unknown")
            status = str(record.get("status") or "unknown")
            by_action[action] = by_action.get(action, 0) + 1
            by_status[status] = by_status.get(status, 0) + 1
            evidence = record.get("evidence")
            evidence = evidence if isinstance(evidence, dict) else {}
            if action == "apply_open":
                apply_open_total += 1
                if evidence.get("has_greeting") or str(evidence.get("greeting") or "").strip():
                    apply_open_with_greeting += 1
                else:
                    apply_open_missing_greeting += 1
            elif action == "apply_send":
                apply_send_total += 1
                greeting_delivery = evidence.get("greeting_delivery")
                if isinstance(greeting_delivery, dict):
                    delivery_status = str(greeting_delivery.get("status") or "unknown")
                    apply_send_greeting_delivery[delivery_status] = apply_send_greeting_delivery.get(delivery_status, 0) + 1
                if status == "delivered":
                    apply_send_delivered += 1
                elif status == "failed":
                    apply_send_failed += 1
                elif status == "planned":
                    apply_send_planned += 1
                elif status == "skipped":
                    apply_send_skipped += 1
        return {
            "platform": "zhilian",
            "total": len(records),
            "by_action": by_action,
            "by_status": by_status,
            "handoff": {
                "apply_open_total": apply_open_total,
                "with_greeting": apply_open_with_greeting,
                "missing_greeting": apply_open_missing_greeting,
            },
            "send": {
                "apply_send_total": apply_send_total,
                "delivered": apply_send_delivered,
                "failed": apply_send_failed,
                "planned": apply_send_planned,
                "skipped": apply_send_skipped,
                "greeting_delivery": apply_send_greeting_delivery,
            },
            "last_updated": _now_iso(),
        }
