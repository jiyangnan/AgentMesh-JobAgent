"""Signed decision review and explicit user override handling."""

from __future__ import annotations

import shlex
from typing import Any

from jobagent.application.delivery_confirmation import register_delivery_confirmation
from jobagent.infra import rounds
from jobagent.infra.account_state import current_account_ref
from jobagent.infra.audit import AuditLog, boss_job_key
from jobagent.infra.delivery_preview import build_delivery_preview
from jobagent.infra.discovery_state import build_review, load_envelope, save_review
from jobagent.infra.protocol import verify_stored_decision


def _candidate_identity(item: dict[str, Any]) -> str:
    for key in ("id", "job_id", "jobId", "url"):
        value = str(item.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    return ""


def _preserve_delivery_exclusions(
    envelope: dict[str, Any],
    review: dict[str, Any],
) -> None:
    exclusions = [
        item
        for item in (envelope.get("user_delivery_exclusions") or [])
        if isinstance(item, dict)
    ]
    if not exclusions:
        return
    excluded = {_candidate_identity(item) for item in exclusions}
    excluded.discard("")
    review["send_candidates"] = [
        item
        for item in review.get("send_candidates") or []
        if _candidate_identity(item) not in excluded
    ]
    review["user_delivery_exclusions"] = exclusions


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
    decision_repair: dict[str, Any] | None = None
    if platform == "zhilian":
        from jobagent.application.decision_repair import (
            repair_zhilian_decision_if_needed,
        )

        repair_result = repair_zhilian_decision_if_needed(envelope)
        envelope = repair_result["envelope"]
        if repair_result.get("repaired"):
            decision_repair = dict(repair_result.get("repair") or {})
    manifest = verify_stored_decision(envelope["manifest"], platform=platform)
    envelope["manifest"] = envelope["manifest"]
    existing_promoted_ids = [
        str(item.get("job_id"))
        for item in (envelope.get("user_overrides") or [])
        if isinstance(item, dict)
        and item.get("from") == "review"
        and item.get("to") == "selected"
        and item.get("job_id")
    ]
    requested_promoted_ids = list(promoted_ids or []) or existing_promoted_ids
    review = build_review(
        envelope,
        promoted_ids=requested_promoted_ids,
        confirm_promote=confirm_promote or bool(existing_promoted_ids),
    )
    _preserve_delivery_exclusions(envelope, review)
    if platform == "boss":
        _exclude_delivered_boss_jobs(review)
    if platform in {"boss", "liepin"}:
        missing = [
            str(item.get("id"))
            for item in review["send_candidates"]
            if not str(item.get("cloud_greeting") or "").strip()
        ]
        if missing:
            raise ValueError(
                f"{platform} decision is missing signed greetings for: " + ", ".join(missing)
            )
    path = save_review(review, output_path)
    send_command = (
        f"jobagent boss greet send --input {path}"
        if platform == "boss"
        else f"jobagent {platform} apply send --input {path}"
    )
    delivery_preview = build_delivery_preview(
        platform=platform,
        discover_id=str(manifest["discover_id"]),
        send_candidates=review["send_candidates"],
        send_command=send_command,
        selected_count=len(manifest.get("selected", [])),
        promoted_count=len(review["user_overrides"]),
        review_count=len(manifest.get("review", [])),
        rejected_count=len(manifest.get("rejected", [])),
        skipped_delivered_count=len(review.get("skipped_delivered", [])),
    )
    review["delivery_preview"] = delivery_preview
    path = save_review(review, str(path))
    workflow = rounds.round_status()
    requires_confirmation = bool(delivery_preview["requires_user_confirmation"])
    if requires_confirmation:
        confirmation = register_delivery_confirmation(
            platform=platform,
            review_path=str(path),
            review=review,
            preview=delivery_preview,
            round_id=str(workflow.get("round_id") or ""),
            account_ref=current_account_ref(),
        )
        source = shlex.quote(str(path))
        safe_resume = (
            f"jobagent boss greet preview --input {source}"
            if platform == "boss"
            else f"jobagent {platform} apply review --input {source}"
        )
        next_suggested = str(confirmation["next_suggested"])
    else:
        confirmation = {}
        safe_resume = str(delivery_preview["continuation"]["action"])
        next_suggested = safe_resume
    rounds.set_platform_status(
        platform,
        "awaiting_delivery_confirmation" if requires_confirmation else "reviewed",
        command=(
            "jobagent boss greet preview"
            if platform == "boss"
            else f"jobagent {platform} apply review"
        ),
        evidence={
            "discover_id": manifest["discover_id"],
            "send_count": len(review["send_candidates"]),
            "preview_id": delivery_preview["preview_id"],
        },
        next_suggested=safe_resume,
    )
    result = {
        "ok": not requires_confirmation,
        "event": "delivery_preview",
        "platform": platform,
        "discover_id": manifest["discover_id"],
        "selected": manifest.get("selected", []),
        "review": manifest.get("review", []),
        "rejected": manifest.get("rejected", []),
        "promoted": review["user_overrides"],
        "skipped_delivered": review.get("skipped_delivered", []),
        "skipped_delivered_count": len(review.get("skipped_delivered", [])),
        "send_count": len(review["send_candidates"]),
        "display_required": True,
        "requires_user_action": requires_confirmation,
        "delivery_preview": delivery_preview,
        "review_file": str(path),
        "next_suggested": next_suggested,
        "workflow": rounds.round_status(),
    }
    if decision_repair is not None:
        result["decision_repair"] = decision_repair
    if requires_confirmation:
        result.update(
            {
                "error": "interaction_required",
                "interaction": confirmation["interaction"],
                "host_presentations": confirmation["host_presentations"],
            }
        )
    return result
