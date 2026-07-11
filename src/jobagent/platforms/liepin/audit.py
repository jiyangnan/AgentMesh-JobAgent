"""Liepin resume and personalized greeting audit log."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jobagent.infra.state import STATE_DIR, ensure_dirs


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def liepin_audit_log_path() -> Path:
    ensure_dirs()
    return STATE_DIR / "liepin_audit_log.json"


def _job_url_key(url: str) -> str:
    value = str(url or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
    return value.rstrip("/")


@dataclass(frozen=True)
class LiepinAuditEvent:
    action: str
    status: str
    job_url: str = ""
    job_name: str = ""
    company: str = ""
    error: str = ""
    message: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    platform: str = "liepin"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LiepinAuditLog:
    def __init__(self, path: Path | None = None):
        self.path = path or liepin_audit_log_path()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return data if isinstance(data, list) else []

    def append(self, event: LiepinAuditEvent) -> None:
        records = self._load()
        records.append(event.to_dict())
        ensure_dirs()
        self.path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_recent(self, n: int = 20) -> list[dict[str, Any]]:
        records = self._load()
        return list(reversed(records[-max(1, n):]))

    def resume_delivered_apply_send_urls(self) -> set[str]:
        records = self._load()
        urls: set[str] = set()
        for record in records:
            if record.get("action") != "apply_send":
                continue
            evidence = record.get("evidence")
            evidence = evidence if isinstance(evidence, dict) else {}
            resume_delivered = (
                record.get("status") == "delivered"
                or evidence.get("resume_delivered") is True
            )
            if not resume_delivered:
                continue
            url = _job_url_key(str(record.get("job_url") or ""))
            if url:
                urls.add(url)
        return urls

    def delivered_apply_send_urls(self) -> set[str]:
        records = self._load()
        urls: set[str] = set()
        for record in records:
            if record.get("action") != "apply_send" or record.get("status") != "delivered":
                continue
            evidence = record.get("evidence")
            evidence = evidence if isinstance(evidence, dict) else {}
            if not (
                evidence.get("resume_delivered") is True
                and evidence.get("greeting_delivered") is True
            ):
                continue
            url = _job_url_key(str(record.get("job_url") or ""))
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
        for record in records:
            action = str(record.get("action") or "unknown")
            status = str(record.get("status") or "unknown")
            by_action[action] = by_action.get(action, 0) + 1
            by_status[status] = by_status.get(status, 0) + 1
            evidence = record.get("evidence")
            evidence = evidence if isinstance(evidence, dict) else {}
            if action == "apply_open":
                apply_open_total += 1
                greeting = str(evidence.get("greeting") or "").strip()
                if evidence.get("has_greeting") is True or greeting:
                    apply_open_with_greeting += 1
                else:
                    apply_open_missing_greeting += 1
            elif action == "apply_send":
                apply_send_total += 1
                if status == "delivered":
                    apply_send_delivered += 1
                elif status == "planned":
                    apply_send_planned += 1
                elif status == "skipped":
                    apply_send_skipped += 1
                elif status == "failed":
                    apply_send_failed += 1
            else:
                continue
        return {
            "platform": "liepin",
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
            },
            "last_updated": _now_iso(),
        }
