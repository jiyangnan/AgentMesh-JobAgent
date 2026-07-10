"""Signed decision review and explicit user override handling."""

from __future__ import annotations

from typing import Any

from jobagent.infra.discovery_state import build_review, load_envelope, save_review
from jobagent.infra.protocol import verify_stored_decision


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
        missing = [
            str(item.get("id"))
            for item in review["send_candidates"]
            if not str(item.get("cloud_greeting") or "").strip()
        ]
        if missing:
            raise ValueError("Boss decision is missing signed greetings for: " + ", ".join(missing))
    path = save_review(review, output_path)
    return {
        "ok": True,
        "platform": platform,
        "discover_id": manifest["discover_id"],
        "selected": manifest.get("selected", []),
        "review": manifest.get("review", []),
        "rejected": manifest.get("rejected", []),
        "promoted": review["user_overrides"],
        "send_count": len(review["send_candidates"]),
        "review_file": str(path),
        "next_suggested": (
            f"jobagent boss greet send --input {path} --confirm-send"
            if platform == "boss"
            else f"jobagent {platform} apply send --input {path} --confirm-submit"
        ),
    }
