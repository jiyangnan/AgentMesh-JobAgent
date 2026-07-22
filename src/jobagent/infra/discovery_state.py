"""Local storage for signed decisions and explicit review overrides."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jobagent.infra.state import discoveries_dir


def _platform_dir(platform: str) -> Path:
    path = discoveries_dir() / platform
    path.mkdir(parents=True, exist_ok=True)
    return path


def discovery_path(platform: str, discover_id: str) -> Path:
    return _platform_dir(platform) / f"{discover_id}.json"


def review_path(platform: str, discover_id: str) -> Path:
    return _platform_dir(platform) / f"{discover_id}.review.json"


def pending_decision_path(platform: str) -> Path:
    return _platform_dir(platform) / "pending-decision.json"


def save_pending_decision(
    platform: str,
    *,
    plan: dict[str, Any],
    jobs: list[dict[str, Any]],
) -> Path:
    path = pending_decision_path(platform)
    payload = {
        "schema_version": 1,
        "platform": platform,
        "discover_id": plan["discover_id"],
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "plan": plan,
        "jobs": jobs,
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def load_pending_decision(platform: str) -> dict[str, Any] | None:
    path = pending_decision_path(platform)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read pending discovery file: {path}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("platform") != platform
        or not isinstance(payload.get("plan"), dict)
        or not isinstance(payload.get("jobs"), list)
    ):
        raise ValueError(f"Invalid pending discovery file: {path}")
    return payload


def clear_pending_decision(platform: str, *, discover_id: str | None = None) -> None:
    path = pending_decision_path(platform)
    if not path.exists():
        return
    if discover_id is not None:
        payload = load_pending_decision(platform)
        if payload and str(payload.get("discover_id")) != discover_id:
            return
    path.unlink()


def save_manifest(manifest: dict[str, Any]) -> Path:
    platform = str(manifest["platform"])
    path = discovery_path(platform, str(manifest["discover_id"]))
    envelope = {
        "platform": platform,
        "discover_id": manifest["discover_id"],
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "manifest": manifest,
    }
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read discovery file: {path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("manifest"), dict):
        raise ValueError(f"Invalid discovery file: {path}")
    return payload


def latest_path(platform: str, *, reviewed: bool | None = None) -> Path:
    root = _platform_dir(platform)
    candidates = []
    for path in root.glob("*.json"):
        is_review = path.name.endswith(".review.json")
        if reviewed is True and not is_review:
            continue
        if reviewed is False and is_review:
            continue
        candidates.append(path)
    if not candidates:
        kind = "reviewed decision" if reviewed else "decision"
        raise ValueError(f"No {kind} found for {platform}. Run `jobagent {platform} discover` first.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def load_envelope(platform: str, input_path: str | None = None, *, reviewed: bool | None = None) -> dict[str, Any]:
    path = Path(input_path).expanduser() if input_path else latest_path(platform, reviewed=reviewed)
    payload = _load(path)
    if payload.get("platform") != platform:
        raise ValueError(f"Decision belongs to {payload.get('platform')}, not {platform}")
    payload["source_path"] = str(path)
    return payload


def _send_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "job_id": item.get("id"),
        "jobId": item.get("id"),
        "name": item.get("title"),
        "title": item.get("title"),
        "company": item.get("company"),
        "area": item.get("area"),
        "salary": item.get("salary"),
        "url": item.get("url"),
        "score": item.get("score"),
        "recommendation": item.get("classification"),
        "reason": item.get("reason"),
        "risk": item.get("risk"),
        "cloud_greeting": item.get("greeting"),
    }


def build_review(
    envelope: dict[str, Any],
    *,
    promoted_ids: list[str] | None = None,
    confirm_promote: bool = False,
) -> dict[str, Any]:
    manifest = envelope["manifest"]
    promoted_ids = list(dict.fromkeys(promoted_ids or []))
    review_by_id = {str(item["id"]): item for item in manifest.get("review", [])}
    unknown = [job_id for job_id in promoted_ids if job_id not in review_by_id]
    if unknown:
        raise ValueError("Only review jobs can be promoted: " + ", ".join(unknown))
    if promoted_ids and not confirm_promote:
        raise ValueError("Promoting review jobs requires --confirm-promote")
    selected = list(manifest.get("selected", []))
    promoted = [review_by_id[job_id] for job_id in promoted_ids]
    return {
        "platform": manifest["platform"],
        "discover_id": manifest["discover_id"],
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "manifest": manifest,
        "user_overrides": [
            {"job_id": item["id"], "from": "review", "to": "selected"}
            for item in promoted
        ],
        "send_candidates": [_send_item(item) for item in selected + promoted],
    }


def save_review(review: dict[str, Any], output_path: str | None = None) -> Path:
    path = (
        Path(output_path).expanduser()
        if output_path
        else review_path(str(review["platform"]), str(review["discover_id"]))
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
