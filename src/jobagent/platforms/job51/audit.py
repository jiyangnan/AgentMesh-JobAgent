"""51Job platform event audit log."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from jobagent.domain.models import now_iso
from jobagent.infra.state import STATE_DIR, ensure_dirs


def job51_audit_log_path():
    ensure_dirs()
    return STATE_DIR / "job51_audit_log.json"


@dataclass
class Job51AuditEvent:
    action: str
    status: str
    job_url: str = ""
    job_name: str = ""
    company: str = ""
    error: str = ""
    message: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    platform: str = "51job"
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Job51AuditLog:
    def __init__(self, path=None):
        self.path = path or job51_audit_log_path()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def append(self, event: Job51AuditEvent) -> None:
        records = self._load()
        records.append(event.to_dict())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    def append_event(self, action: str, status: str, **kwargs) -> None:
        self.append(Job51AuditEvent(action=action, status=status, **kwargs))

    def delivered_apply_send_keys(self) -> set[str]:
        keys: set[str] = set()
        for record in self._load():
            if record.get("action") != "apply_send" or record.get("status") != "delivered":
                continue
            evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
            key = str(evidence.get("job_id") or record.get("job_url") or "").strip()
            if key:
                keys.add(key)
        return keys

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._load()[-max(1, int(limit)):]

    def summary(self) -> dict[str, Any]:
        summary = {
            "platform": "51job",
            "total": 0,
            "apply_open": {"total": 0, "opened": 0, "planned": 0, "failed": 0},
            "apply_send": {"total": 0, "delivered": 0, "planned": 0, "failed": 0, "skipped": 0},
        }
        for record in self._load():
            action = record.get("action")
            status = record.get("status")
            summary["total"] += 1
            if action == "apply_open":
                bucket = summary["apply_open"]
                bucket["total"] += 1
                if status in bucket:
                    bucket[status] += 1
            if action == "apply_send":
                bucket = summary["apply_send"]
                bucket["total"] += 1
                if status in bucket:
                    bucket[status] += 1
        return summary
