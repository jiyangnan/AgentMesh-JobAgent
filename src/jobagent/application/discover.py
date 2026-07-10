"""One-command Discover orchestration."""

from __future__ import annotations

import uuid
from typing import Any

from jobagent.infra import cloud_client
from jobagent.infra.activity import active_command
from jobagent.infra.discovery_state import save_manifest
from jobagent.infra.platform_lock import PlatformSessionLock
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
    request_id = f"{platform}:{uuid.uuid4().hex}"
    plan = cloud_client.discovery_start(
        platform=platform,
        profile=profile,
        request_id=request_id,
    )
    verified_plan = verify_search_plan(plan, platform=platform, profile=profile)
    with active_command(f"jobagent {platform} discover"):
        with PlatformSessionLock(platform=platform, command=f"jobagent {platform} discover"):
            candidates = collect_from_search_plan(
                verified_plan,
                wait_seconds=wait_seconds,
                page_delay=page_delay,
            )
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
        "next_suggested": (
            f"jobagent boss greet preview --input {path}"
            if platform == "boss"
            else f"jobagent {platform} apply review --input {path}"
        ),
    }
