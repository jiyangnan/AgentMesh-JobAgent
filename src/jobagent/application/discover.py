"""One-command Discover orchestration."""

from __future__ import annotations

import uuid
from typing import Any

from jobagent.infra import cloud_client, rounds
from jobagent.infra.activity import active_command
from jobagent.infra.discovery_state import (
    clear_pending_decision,
    load_pending_decision,
    save_manifest,
    save_pending_decision,
)
from jobagent.infra.diagnostics import emit_stage, progress_heartbeat
from jobagent.infra.platform_lock import PlatformSessionLock
from jobagent.infra.profile_contract import require_compatible_profile
from jobagent.infra.protocol import verify_decision_manifest, verify_search_plan
from jobagent.infra.state import load_json, profile_path
from jobagent.platforms.discovery import collect_from_search_plan


def _decision_result(
    platform: str,
    *,
    plan: dict[str, Any],
    candidates: list[dict[str, Any]],
    resumed: bool,
) -> dict[str, Any]:
    discover_id = str(plan["discover_id"])
    emit_stage(
        "cloud_decision_resumed" if resumed else "cloud_decision_requested",
        platform=platform,
        candidate_count=len(candidates),
        discover_id=discover_id,
    )
    try:
        with progress_heartbeat(
            "cloud_decision_in_progress",
            platform=platform,
            candidate_count=len(candidates),
            resumed=resumed,
        ):
            manifest = cloud_client.discovery_decide(
                discover_id=discover_id,
                jobs=candidates,
            )
    except cloud_client.CloudError as exc:
        exc.details.update(
            {
                "request_preserved": True,
                "discover_id": discover_id,
                "next_suggested": f"jobagent {platform} discover",
            }
        )
        raise

    verified_manifest = verify_decision_manifest(
        manifest,
        platform=platform,
        discover_id=discover_id,
        jobs=candidates,
    )
    path = save_manifest(manifest)
    clear_pending_decision(platform, discover_id=discover_id)
    emit_stage(
        "cloud_decision_completed",
        platform=platform,
        selected=len(verified_manifest.get("selected", [])),
        review=len(verified_manifest.get("review", [])),
        rejected=len(verified_manifest.get("rejected", [])),
        resumed=resumed,
    )
    next_suggested = (
        f"jobagent boss greet preview --input {path}"
        if platform == "boss"
        else f"jobagent {platform} apply review --input {path}"
    )
    rounds.set_platform_status(
        platform,
        "discovered",
        command=f"jobagent {platform} discover",
        evidence={"discover_id": verified_manifest["discover_id"]},
        next_suggested=next_suggested,
    )
    return {
        "ok": True,
        "platform": platform,
        "discover_id": verified_manifest["discover_id"],
        "candidate_count": len(candidates),
        "deduplicated_count": verified_manifest["deduplicated_count"],
        "selected": len(verified_manifest.get("selected", [])),
        "review": len(verified_manifest.get("review", [])),
        "rejected": len(verified_manifest.get("rejected", [])),
        "credits": verified_manifest.get("billing", {}).get("credits"),
        "decision_file": str(path),
        "resumed": resumed,
        "next_suggested": next_suggested,
        "workflow": rounds.round_status(),
    }


def _resume_pending_decision(
    platform: str,
    *,
    profile: dict[str, Any],
) -> dict[str, Any] | None:
    pending = load_pending_decision(platform)
    if pending is None:
        return None
    try:
        plan = verify_search_plan(pending["plan"], platform=platform, profile=profile)
    except ValueError as exc:
        if "expired" not in str(exc).casefold():
            raise
        clear_pending_decision(platform, discover_id=str(pending.get("discover_id") or ""))
        emit_stage("pending_decision_expired", platform=platform)
        return None
    candidates = pending["jobs"]
    try:
        return _decision_result(
            platform,
            plan=plan,
            candidates=candidates,
            resumed=True,
        )
    except cloud_client.CloudError as exc:
        if exc.code not in {"discover_failed_start_new", "search_plan_expired"}:
            raise
        clear_pending_decision(platform, discover_id=str(plan["discover_id"]))
        emit_stage(
            "pending_decision_replaced",
            platform=platform,
            reason=exc.code,
        )
        return None


def run_discover(
    platform: str,
    *,
    wait_seconds: int = 6,
    page_delay: float = 2.0,
) -> dict[str, Any]:
    profile = load_json(profile_path())
    if not profile:
        raise ValueError("No resume profile found. Run `jobagent resume analyze --file <resume>` first.")
    require_compatible_profile(profile)
    resumed = _resume_pending_decision(platform, profile=profile)
    if resumed is not None:
        return resumed
    request_id = f"{platform}:{uuid.uuid4().hex}"
    emit_stage("search_plan_requested", platform=platform)
    with progress_heartbeat("search_plan_waiting", platform=platform):
        plan = cloud_client.discovery_start(
            platform=platform,
            profile=profile,
            request_id=request_id,
        )
    verified_plan = verify_search_plan(plan, platform=platform, profile=profile)
    emit_stage(
        "search_plan_received",
        platform=platform,
        query_count=len(verified_plan.get("queries") or []),
    )
    emit_stage("browser_collection_started", platform=platform)
    with progress_heartbeat("browser_collection_in_progress", platform=platform):
        with active_command(f"jobagent {platform} discover"):
            with PlatformSessionLock(platform=platform, command=f"jobagent {platform} discover"):
                candidates = collect_from_search_plan(
                    verified_plan,
                    wait_seconds=wait_seconds,
                    page_delay=page_delay,
                )
    emit_stage("browser_collection_completed", platform=platform, candidate_count=len(candidates))
    save_pending_decision(platform, plan=plan, jobs=candidates)
    return _decision_result(
        platform,
        plan=verified_plan,
        candidates=candidates,
        resumed=False,
    )
