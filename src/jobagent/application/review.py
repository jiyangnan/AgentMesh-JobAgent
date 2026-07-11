"""Signed decision review and explicit user override handling."""

from __future__ import annotations

from typing import Any

from jobagent.infra import rounds
from jobagent.infra.audit import AuditLog, boss_job_key
from jobagent.infra.discovery_state import build_review, load_envelope, save_review
from jobagent.infra.protocol import verify_stored_decision


def _exclude_delivered_boss_jobs(review: dict[str, Any]) -> None:
    delivered_keys = AuditLog().delivered_job_keys()
    if not delivered_keys:
        review["skipped_delivered"] = []
        return
    send_candidates = list(review.get("send_candidates") or [])
    skipped = [
        item for item in send_candidates if boss_job_key(str(item.get("url") or "")) in delivered_keys
    ]
    review["send_candidates"] = [
        item for item in send_candidates if boss_job_key(str(item.get("url") or "")) not in delivered_keys
    ]
    review["skipped_delivered"] = skipped


def review_decision(
    platform: str,
    *,
    input_path: str | None = None,
    promoted_ids: list[str] | None = None,
    confirm_promote: bool = False,
    output_path: str | None = None,
) -> dict[str, Any]:
    envelope = load_envelope(platform, input_path, reviewed=False if input_path is None else None)
    manifest = verify_stored_decision(envelope["manifest"], platform=platform)
    envelope["manifest"] = envelope["manifest"]
    review = build_review(
        envelope,
        promoted_ids=promoted_ids,
        confirm_promote=confirm_promote,
    )
    if platform == "boss":
        _exclude_delivered_boss_jobs(review)
        missing = [
            str(item.get("id"))
            for item in review["send_candidates"]
            if not str(item.get("cloud_greeting") or "").strip()
        ]
        if missing:
            raise ValueError("Boss decision is missing signed greetings for: " + ", ".join(missing))
    path = save_review(review, output_path)
    next_suggested = (
        f"jobagent boss greet send --input {path} --confirm-send"
        if platform == "boss"
        else f"jobagent {platform} apply send --input {path} --confirm-submit"
    )
    rounds.set_platform_status(
        platform,
        "reviewed",
        command=(
            "jobagent boss greet preview"
            if platform == "boss"
            else f"jobagent {platform} apply review"
        ),
        evidence={"discover_id": manifest["discover_id"], "send_count": len(review["send_candidates"])},
        next_suggested=next_suggested,
    )
    return {
        "ok": True,
        "platform": platform,
        "discover_id": manifest["discover_id"],
        "selected": manifest.get("selected", []),
        "review": manifest.get("review", []),
        "rejected": manifest.get("rejected", []),
        "promoted": review["user_overrides"],
        "skipped_delivered": review.get("skipped_delivered", []),
        "skipped_delivered_count": len(review.get("skipped_delivered", [])),
        "send_count": len(review["send_candidates"]),
        "review_file": str(path),
        "next_suggested": next_suggested,
        "workflow": rounds.round_status(),
    }
