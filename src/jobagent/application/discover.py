"""One-command Discover orchestration."""

from __future__ import annotations

import uuid
from typing import Any

from jobagent.infra import cloud_client, rounds
from jobagent.infra.activity import active_command
from jobagent.infra.discovery_state import save_manifest
from jobagent.infra.diagnostics import emit_stage, progress_heartbeat
from jobagent.infra.platform_lock import PlatformSessionLock
from jobagent.infra.profile_contract import require_compatible_profile
from jobagent.infra.protocol import verify_decision_manifest, verify_search_plan
from jobagent.infra.state import load_json, profile_path
from jobagent.platforms.discovery import collect_from_search_plan


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
    emit_stage("cloud_decision_requested", platform=platform, candidate_count=len(candidates))
    with progress_heartbeat(
        "cloud_decision_in_progress",
        platform=platform,
        candidate_count=len(candidates),
    ):
        manifest = cloud_client.discovery_decide(
            discover_id=str(verified_plan["discover_id"]),
            jobs=candidates,
        )
    verified_manifest = verify_decision_manifest(
        manifest,
        platform=platform,
        discover_id=str(verified_plan["discover_id"]),
        jobs=candidates,
    )
    path = save_manifest(manifest)
    emit_stage(
        "cloud_decision_completed",
        platform=platform,
        selected=len(verified_manifest.get("selected", [])),
        review=len(verified_manifest.get("review", [])),
        rejected=len(verified_manifest.get("rejected", [])),
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
        "next_suggested": next_suggested,
        "workflow": rounds.round_status(),
    }
