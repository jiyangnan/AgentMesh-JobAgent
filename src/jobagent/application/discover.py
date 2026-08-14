"""One-command Discover orchestration."""

from __future__ import annotations

import uuid
from typing import Any

from jobagent import __version__
from jobagent.infra import cloud_client, rounds
from jobagent.infra.activity import active_command
from jobagent.infra.discovery_state import (
    clear_pending_decision,
    clear_pending_start,
    load_pending_decision,
    load_pending_start,
    record_collection_recovery,
    save_manifest,
    save_pending_decision,
    save_pending_start,
)
from jobagent.infra.diagnostics import emit_stage, progress_heartbeat
from jobagent.infra.platform_lock import PlatformSessionLock
from jobagent.infra.profile_contract import require_compatible_profile
from jobagent.infra.protocol import (
    SearchPlanExpiredError,
    digest_payload,
    verify_decision_manifest,
    verify_search_plan,
)
from jobagent.infra.state import load_json, profile_path
from jobagent.platforms.discovery import CollectionError, collect_from_search_plan


_ZHILIAN_CITY_RECOVERY_CODES = {
    "zhilian_city_evidence_pending",
    "zhilian_city_selector_unavailable",
    "zhilian_city_resolution_unverified",
    "zhilian_search_navigation_pending",
}
_ZHILIAN_CITY_RECOVERY_BUDGET = 3


def _decision_result(
    platform: str,
    *,
    plan: dict[str, Any],
    candidates: list[dict[str, Any]],
    resumed: bool,
    profile: dict[str, Any] | None = None,
    round_intent: dict[str, Any] | None = None,
    request_id: str | None = None,
    expiry_recovery_attempted: bool = False,
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
        if (
            exc.code == "search_plan_expired"
            and profile is not None
            and not expiry_recovery_attempted
        ):
            renewed_plan = _renew_expired_plan(
                platform,
                expired_plan=plan,
                profile=profile,
                round_intent=round_intent,
                request_id=request_id,
            )
            renewed_request_id = (
                str(renewed_plan.get("request_id") or request_id or "") or None
            )
            save_pending_decision(
                platform,
                plan=renewed_plan,
                jobs=candidates,
                request_id=renewed_request_id,
            )
            return _decision_result(
                platform,
                plan=renewed_plan,
                candidates=candidates,
                resumed=True,
                profile=profile,
                round_intent=round_intent,
                request_id=renewed_request_id,
                expiry_recovery_attempted=True,
            )
        exc.details.update(
            {
                "request_preserved": True,
                "discover_id": discover_id,
                "next_suggested": f"jobagent {platform} discover",
                "billing_status": "response_pending_reconciliation",
                "billing": {
                    "status": "response_pending_reconciliation",
                    "retry_reuses_discover_id": True,
                    "additional_charge_on_retry": False,
                },
            }
        )
        raise

    verified_manifest = verify_decision_manifest(
        manifest,
        platform=platform,
        discover_id=discover_id,
        jobs=candidates,
        intent_digest=plan.get("intent_digest"),
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


def _expiry_recovery_details(
    platform: str,
    *,
    discover_id: str,
    request_id: str | None,
    retryable: bool,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "recovery_reason": "search_plan_expired",
        "request_preserved": True,
        "discover_id": discover_id,
        "next_suggested": f"jobagent {platform} discover",
        "no_charge": True,
        "billing_status": "not_charged",
        "billing": {
            "status": "not_charged",
            "renewal_charge": 0,
            "additional_charge_on_renewal": False,
            "eventual_decision_charge_unchanged": True,
        },
        "requires_user_action": not retryable,
    }
    if request_id:
        details["request_id"] = request_id
        details["billing"]["retry_reuses_request_id"] = True
    if not retryable:
        details["user_prompt"] = (
            "The signed SearchPlan expired and could not be safely renewed. "
            "Keep the current round and Job Agent Chrome profile; contact AgentMesh support "
            "with this error code. Do not create a new round or edit local state."
        )
    return details


def _renew_expired_plan(
    platform: str,
    *,
    expired_plan: dict[str, Any],
    profile: dict[str, Any],
    round_intent: dict[str, Any] | None,
    request_id: str | None,
) -> dict[str, Any]:
    discover_id = str(expired_plan.get("discover_id") or "")
    if not discover_id:
        raise ValueError("expired search plan is missing discover_id")
    intent_digest = (
        digest_payload(round_intent)
        if round_intent and round_intent.get("status") == "confirmed"
        else None
    )
    emit_stage(
        "search_plan_renewal_requested",
        platform=platform,
        discover_id=discover_id,
    )
    try:
        renewed = cloud_client.discovery_renew(
            discover_id=discover_id,
            platform=platform,
            profile_digest=digest_payload(profile),
            intent_digest=intent_digest,
        )
    except cloud_client.CloudError as exc:
        original_code = exc.code
        retryable = bool(exc.retryable)
        exc.code = (
            "search_plan_expired_recovery_pending"
            if retryable
            else "search_plan_expired_recovery_required"
        )
        exc.args = (
            "The signed SearchPlan expired; renewal is temporarily unavailable."
            if retryable
            else "The signed SearchPlan expired and could not be safely renewed.",
        )
        exc.details.update(
            {
                "renewal_failure_code": original_code,
                **_expiry_recovery_details(
                    platform,
                    discover_id=discover_id,
                    request_id=request_id,
                    retryable=retryable,
                ),
            }
        )
        raise
    from jobagent.infra.protocol import ProtocolError

    try:
        verified = verify_search_plan(
            renewed,
            platform=platform,
            profile=profile,
            round_intent=round_intent,
            request_id=request_id,
            require_request_id=True,
        )
        renewal = verified.get("renewal")
        if (
            not isinstance(renewal, dict)
            or renewal.get("reason") != "search_plan_expired"
            or renewal.get("request_id") != verified.get("request_id")
            or renewal.get("discover_id") != discover_id
            or renewal.get("request_preserved") is not True
            or renewal.get("same_request_id") is not True
            or renewal.get("same_discover_id") is not True
            or renewal.get("additional_charge_on_renewal") is not False
        ):
            raise ProtocolError("renewed search plan recovery binding is invalid")
    except ProtocolError as exc:
        raise cloud_client.CloudError(
            "The signed SearchPlan expired and the renewal response failed verification.",
            code="search_plan_expired_recovery_required",
            retryable=False,
            details={
                "renewal_failure_code": "renewed_plan_verification_failed",
                **_expiry_recovery_details(
                    platform,
                    discover_id=discover_id,
                    request_id=request_id,
                    retryable=False,
                ),
            },
        ) from exc
    emit_stage(
        "search_plan_renewed",
        platform=platform,
        discover_id=discover_id,
        request_id=verified["request_id"],
        no_charge=True,
    )
    return renewed


def _resume_pending_decision(
    platform: str,
    *,
    profile: dict[str, Any],
    round_intent: dict[str, Any] | None,
) -> dict[str, Any] | None:
    pending = load_pending_decision(platform)
    if pending is None:
        return None
    request_id = (
        str(pending.get("request_id") or pending["plan"].get("request_id") or "")
        or None
    )
    try:
        plan = verify_search_plan(
            pending["plan"],
            platform=platform,
            profile=profile,
            round_intent=round_intent,
            request_id=request_id,
        )
    except SearchPlanExpiredError as exc:
        plan = exc.signed_plan
    candidates = pending["jobs"]
    try:
        return _decision_result(
            platform,
            plan=plan,
            candidates=candidates,
            resumed=True,
            profile=profile,
            round_intent=round_intent,
            request_id=request_id,
        )
    except cloud_client.CloudError as exc:
        if exc.code not in {"discover_failed_start_new"}:
            raise
        clear_pending_decision(platform, discover_id=str(plan["discover_id"]))
        emit_stage(
            "pending_decision_replaced",
            platform=platform,
            reason=exc.code,
        )
        return None


def _start_context(
    platform: str,
    *,
    profile: dict[str, Any],
    active_round: dict[str, Any],
    round_intent: dict[str, Any] | None,
) -> dict[str, Any]:
    from jobagent.infra.account_state import current_account_ref

    intent_digest = (
        digest_payload(round_intent)
        if round_intent and round_intent.get("status") == "confirmed"
        else None
    )
    return {
        "platform": platform,
        "round_id": str(active_round["round_id"]),
        "profile_digest": digest_payload(profile),
        "intent_digest": intent_digest,
        "account_ref": current_account_ref(),
    }


def _preserved_request_id(context: dict[str, Any]) -> str:
    platform = str(context["platform"])
    pending = load_pending_start(platform)
    comparable = ("round_id", "profile_digest", "intent_digest", "account_ref")
    if pending and all(
        pending.get(field) == context.get(field) for field in comparable
    ):
        return str(pending["request_id"])
    if pending:
        clear_pending_start(platform, request_id=str(pending.get("request_id") or ""))
    request_id = f"{platform}:{uuid.uuid4().hex}"
    save_pending_start(
        platform,
        request_id=request_id,
        round_id=str(context["round_id"]),
        profile_digest=str(context["profile_digest"]),
        intent_digest=(
            str(context["intent_digest"]) if context.get("intent_digest") else None
        ),
        account_ref=(
            str(context["account_ref"]) if context.get("account_ref") else None
        ),
    )
    return request_id


def run_discover(
    platform: str,
    *,
    wait_seconds: int = 6,
    page_delay: float = 2.0,
) -> dict[str, Any]:
    profile = load_json(profile_path())
    if not profile:
        raise ValueError(
            "No resume profile found. Run `jobagent resume analyze --file <resume>` first."
        )
    require_compatible_profile(profile)
    active_round = rounds.ensure_current_round()
    round_intent = active_round.get("intent")
    resumed = _resume_pending_decision(
        platform,
        profile=profile,
        round_intent=round_intent,
    )
    if resumed is not None:
        return resumed
    context = _start_context(
        platform,
        profile=profile,
        active_round=active_round,
        round_intent=round_intent,
    )
    request_id = _preserved_request_id(context)
    emit_stage("search_plan_requested", platform=platform)
    try:
        with progress_heartbeat("search_plan_waiting", platform=platform):
            plan = cloud_client.discovery_start(
                platform=platform,
                profile=profile,
                request_id=request_id,
                round_intent=round_intent,
            )
    except cloud_client.CloudError as exc:
        exc.details.update(
            {
                "request_preserved": True,
                "request_id": request_id,
                "next_suggested": f"jobagent {platform} discover",
                "no_charge": True,
                "billing_status": "not_charged",
                "billing": {
                    "status": "not_charged",
                    "retry_reuses_request_id": True,
                    "additional_charge_on_retry": False,
                },
            }
        )
        raise
    try:
        verified_plan = verify_search_plan(
            plan,
            platform=platform,
            profile=profile,
            round_intent=round_intent,
            request_id=request_id,
        )
    except SearchPlanExpiredError as exc:
        plan = _renew_expired_plan(
            platform,
            expired_plan=exc.signed_plan,
            profile=profile,
            round_intent=round_intent,
            request_id=request_id,
        )
        verified_plan = verify_search_plan(
            plan,
            platform=platform,
            profile=profile,
            round_intent=round_intent,
            request_id=request_id,
            require_request_id=True,
        )
    emit_stage(
        "search_plan_received",
        platform=platform,
        query_count=len(verified_plan.get("queries") or []),
    )
    emit_stage("browser_collection_started", platform=platform)
    login_verification = rounds.recent_platform_login_verification(platform)
    try:
        with progress_heartbeat("browser_collection_in_progress", platform=platform):
            with active_command(f"jobagent {platform} discover"):
                with PlatformSessionLock(
                    platform=platform, command=f"jobagent {platform} discover"
                ):
                    candidates = collect_from_search_plan(
                        verified_plan,
                        wait_seconds=wait_seconds,
                        page_delay=page_delay,
                        login_verification=login_verification,
                    )
    except CollectionError as exc:
        details = dict(exc.details or {})
        details.update(
            {
                "request_preserved": True,
                "request_id": request_id,
                "no_charge": True,
                "billing_status": "not_charged",
                "billing": {
                    "status": "not_charged",
                    "retry_reuses_request_id": True,
                    "additional_charge_on_retry": False,
                },
            }
        )
        details.setdefault("next_suggested", f"jobagent {platform} discover")
        details.setdefault("retryable", _collection_error_retryable(exc.code))
        if platform == "zhilian" and exc.code in _ZHILIAN_CITY_RECOVERY_CODES:
            recovery = record_collection_recovery(
                platform,
                request_id=request_id,
                recovery_key="zhilian_city_convergence",
                client_version=__version__,
                budget=_ZHILIAN_CITY_RECOVERY_BUDGET,
            )
            details.update(
                {
                    "recovery_attempt": recovery["attempts"],
                    "recovery_budget": recovery["budget"],
                    "recovery_exhausted": recovery["exhausted"],
                    "recovery_scope": "request_id",
                }
            )
            if recovery["exhausted"]:
                exc.code = "zhilian_city_evidence_recovery_exhausted"
                exc.message = (
                    "Zhilian city evidence did not converge within the bounded recovery "
                    "window. The request remains preserved and was not charged."
                )
                details.update(
                    {
                        "retryable": False,
                        "requires_user_action": True,
                        "next_suggested": "jobagent browser diagnose --platform zhilian",
                    }
                )
        exc.details = details
        raise
    emit_stage(
        "browser_collection_completed",
        platform=platform,
        candidate_count=len(candidates),
    )
    save_pending_decision(
        platform,
        plan=plan,
        jobs=candidates,
        request_id=request_id,
    )
    clear_pending_start(platform, request_id=request_id)
    return _decision_result(
        platform,
        plan=verified_plan,
        candidates=candidates,
        resumed=False,
        profile=profile,
        round_intent=round_intent,
        request_id=request_id,
    )


def _collection_error_retryable(code: str) -> bool:
    return code in {
        "zhilian_page_state_unknown",
        "zhilian_job_cards_not_found",
        "zhilian_city_evidence_pending",
        "zhilian_city_selector_unavailable",
        "zhilian_city_resolution_unverified",
        "zhilian_search_navigation_pending",
        "page_state_unknown",
        "no_candidates",
    }
