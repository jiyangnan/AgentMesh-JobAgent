"""Structured user confirmation between delivery preview and real platform action."""

from __future__ import annotations

import shlex
from typing import Any

from jobagent.infra import rounds
from jobagent.infra.account_state import current_account_ref
from jobagent.infra.delivery_authorization import build_delivery_authorization
from jobagent.infra.delivery_preview import (
    DeliveryPreviewError, build_delivery_preview, preview_required_payload,
    validate_delivery_preview,
)
from jobagent.infra.discovery_state import load_envelope, save_review
from jobagent.infra.interaction_protocol import (
    build_host_presentations,
    build_interaction_required,
)
from jobagent.infra.interaction_state import (
    clear_pending_interaction,
    save_pending_interaction,
)
from jobagent.infra.protocol import digest_payload, verify_stored_decision

DELIVERY_CHOICES = {"confirm_all", "exclude_jobs", "cancel_delivery"}


def _send_command(platform: str, review_path: str) -> str:
    source = shlex.quote(review_path)
    return (
        f"jobagent boss greet send --input {source}"
        if platform == "boss"
        else f"jobagent {platform} apply send --input {source}"
    )


def _review_command(platform: str, review_path: str) -> str:
    source = shlex.quote(review_path)
    return (
        f"jobagent boss greet preview --input {source}"
        if platform == "boss"
        else f"jobagent {platform} apply review --input {source}"
    )


def _pending_context(
    *,
    platform: str,
    review_path: str,
    review: dict[str, Any],
    preview: dict[str, Any],
    round_id: str,
    account_ref: str | None,
) -> dict[str, Any]:
    return {
        "platform": platform,
        "review_path": review_path,
        "discover_id": str(review.get("discover_id") or ""),
        "preview_id": str(preview.get("preview_id") or ""),
        "candidate_digest": digest_payload(list(review.get("send_candidates") or [])),
        "round_id": round_id,
        "account_ref": account_ref,
    }


def _interaction_response(
    *,
    preview: dict[str, Any],
    review_path: str,
) -> dict[str, Any]:
    interaction = preview["interaction"]
    interaction_id = str(interaction["interaction_id"])
    return {
        "ok": False,
        "error": "interaction_required",
        "event": "delivery_preview",
        "platform": preview["platform"],
        "discover_id": preview["discover_id"],
        "display_required": True,
        "requires_user_action": True,
        "delivery_preview": preview,
        "interaction": interaction,
        "host_presentations": preview["host_presentations"],
        "review_file": review_path,
        "next_suggested": (
            f'jobagent interaction respond --interaction-id "{interaction_id}" '
            "--choice <confirm_all|exclude_jobs|cancel_delivery>"
        ),
    }


def register_delivery_confirmation(
    *,
    platform: str,
    review_path: str,
    review: dict[str, Any],
    preview: dict[str, Any],
    round_id: str,
    account_ref: str | None,
) -> dict[str, Any]:
    validate_delivery_preview(
        preview,
        send_candidates=list(review.get("send_candidates") or []),
        expected_platform=platform,
        expected_discover_id=str(review.get("discover_id") or ""),
    )
    if not preview.get("requires_user_confirmation"):
        raise ValueError("A delivery confirmation cannot be registered for an empty preview.")
    interaction = preview["interaction"]
    context = _pending_context(
        platform=platform,
        review_path=review_path,
        review=review,
        preview=preview,
        round_id=round_id,
        account_ref=account_ref,
    )
    save_pending_interaction(
        interaction,
        stage="delivery_choice",
        root_interaction_id=str(interaction["interaction_id"]),
        context=context,
    )
    return _interaction_response(preview=preview, review_path=review_path)


def _invalid_response(pending: dict[str, Any], message: str) -> dict[str, Any]:
    interaction = pending.get("interaction") or {}
    return {
        "ok": False,
        "error": "invalid_interaction_response",
        "message": message,
        "requires_user_action": True,
        "interaction": interaction,
        "host_presentations": (
            build_host_presentations(interaction) if isinstance(interaction, dict) else None
        ),
        "next_suggested": str(interaction.get("fallback_text") or ""),
    }


def _load_context(
    pending: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, str]:
    context = pending.get("context")
    if not isinstance(context, dict):
        raise ValueError("The delivery confirmation context is missing.")
    platform = str(context.get("platform") or "")
    review_path = str(context.get("review_path") or "")
    if platform not in {"boss", "liepin", "zhilian", "51job"} or not review_path:
        raise ValueError("The delivery confirmation context is incomplete.")
    review = load_envelope(platform, review_path, reviewed=True)
    from jobagent.application.round_criteria import assert_current
    assert_current(review)
    verify_stored_decision(review["manifest"], platform=platform)
    preview = review.get("delivery_preview")
    candidates = list(review.get("send_candidates") or [])
    if not isinstance(preview, dict):
        raise ValueError("The reviewed delivery preview is missing.")
    workflow = rounds.round_status()
    round_id = str(workflow.get("round_id") or "")
    if not round_id or round_id != str(context.get("round_id") or ""):
        raise ValueError("The active delivery round changed after the list was shown.")
    account_ref = current_account_ref()
    expected_account_ref = str(context.get("account_ref") or "")
    if not account_ref:
        raise ValueError("The active AgentMesh account is not bound to local state.")
    if expected_account_ref and account_ref != expected_account_ref:
        raise ValueError("The AgentMesh account changed after the list was shown.")
    try:
        validate_delivery_preview(
            preview,
            send_candidates=candidates,
            expected_platform=platform,
            expected_discover_id=str(review.get("discover_id") or ""),
            expected_preview_id=str(context.get("preview_id") or ""),
        )
        if digest_payload(candidates) != str(context.get("candidate_digest") or ""):
            raise ValueError("The delivery candidate list changed after it was shown.")
    except ValueError as exc:
        # Exclusions are saved before their cloud acknowledgement. A lost
        # response must refresh this saved list, not replay an old confirmation.
        raise DeliveryPreviewError(preview_required_payload(platform, review_path)) from exc
    return context, review, preview, round_id, account_ref


def resume_pending_confirmation() -> dict[str, Any] | None:
    """Restore the same verified preview/answer step after a host restart."""
    from jobagent.infra.interaction_state import load_pending_interaction
    from jobagent.application.delivery_followup import pending as after_cancel_pending
    after_cancel = after_cancel_pending()
    if after_cancel:
        return after_cancel

    pending = load_pending_interaction()
    if not pending or pending.get("stage") not in {"delivery_choice", "delivery_exclusions"}:
        return None
    context, _review, preview, _round_id, _account = _load_context(pending)
    if pending["stage"] == "delivery_choice":
        return _interaction_response(preview=preview, review_path=context["review_path"])
    interaction = pending["interaction"]
    return {
        "ok": False, "error": "interaction_required", "event": "delivery_exclusions",
        "requires_user_action": True, "request_preserved": True,
        "platform": context["platform"], "interaction": interaction,
        "host_presentations": build_host_presentations(interaction),
        "next_suggested": f'jobagent interaction respond --interaction-id "{interaction["interaction_id"]}" --exclude-index <job number>',
    }


def _exclusion_request(
    pending: dict[str, Any],
    *,
    context: dict[str, Any],
    preview: dict[str, Any],
) -> dict[str, Any]:
    root_id = str(pending.get("root_interaction_id") or pending.get("interaction_id") or "")
    interaction_id = f"{root_id}:exclude:{preview['preview_id']}"
    fallback = (
        "请输入不想投递的岗位序号，多个序号用逗号分隔，例如：2, 5。\n"
        "Job Agent 会生成排除后的完整新清单，并再次等待你最终确认。"
    )
    interaction = build_interaction_required(
        interaction_id=interaction_id,
        product_id="job_agent",
        kind="delivery_exclusions",
        title="排除不想投递的岗位",
        prompt="请输入要从待投递清单中排除的岗位序号。",
        fields=[
            {
                "field_id": "exclude_indices",
                "type": "text",
                "label": "排除序号",
                "required": True,
                "placeholder": "例如 2, 5",
            }
        ],
        fallback_text=fallback,
        continuation_action="jobagent.interaction.respond",
        idempotency_key=interaction_id,
    )
    save_pending_interaction(
        interaction,
        stage="delivery_exclusions",
        root_interaction_id=root_id,
        choice="exclude_jobs",
        context=context,
    )
    return {
        "ok": False,
        "error": "interaction_required",
        "event": "delivery_exclusions",
        "platform": context["platform"],
        "interaction": interaction,
        "host_presentations": build_host_presentations(interaction),
        "next_suggested": (
            f'jobagent interaction respond --interaction-id "{interaction_id}" '
            "--exclude-index <job number>"
        ),
    }


def _cancel_delivery(
    *,
    platform: str,
    review: dict[str, Any],
    review_path: str,
    reason: str,
) -> dict[str, Any]:
    from jobagent.application.delivery_followup import register
    from jobagent.application.workflow_delivery import answer as server_answer
    server_answer(review, "cancel_delivery")
    response = register(platform, review, review_path, reason)
    review.pop("delivery_authorization", None)
    review["delivery_cancellation"] = {
        "reason": reason,
        "candidate_digest": digest_payload(list(review.get("send_candidates") or [])),
    }
    save_review(review, review_path)
    return response


def respond_delivery_confirmation(
    pending: dict[str, Any],
    *,
    choice: str,
    exclude_indices: list[int],
) -> dict[str, Any]:
    if not isinstance(pending, dict) or not str(pending.get("stage") or "").startswith(
        "delivery_"
    ):
        return _invalid_response(pending or {}, "This is not a pending delivery interaction.")
    if choice not in DELIVERY_CHOICES:
        return _invalid_response(pending, "Select confirm, exclude, or cancel before continuing.")
    try:
        context, review, preview, round_id, account_ref = _load_context(pending)
    except ValueError as exc:
        return _invalid_response(pending, str(exc))
    platform = str(context["platform"])
    review_path = str(context["review_path"])

    if choice == "cancel_delivery":
        return _cancel_delivery(
            platform=platform,
            review=review,
            review_path=review_path,
            reason="user_cancelled",
        )

    if choice == "exclude_jobs":
        if not exclude_indices:
            return _exclusion_request(pending, context=context, preview=preview)
        candidates = list(review.get("send_candidates") or [])
        unique_indices = sorted(set(exclude_indices))
        if any(index < 1 or index > len(candidates) for index in unique_indices):
            return _invalid_response(
                pending,
                f"岗位序号必须在 1 到 {len(candidates)} 之间。",
            )
        excluded = [candidates[index - 1] for index in unique_indices]
        excluded_set = set(unique_indices)
        remaining = [
            candidate
            for index, candidate in enumerate(candidates, start=1)
            if index not in excluded_set
        ]
        review["send_candidates"] = remaining
        review.setdefault("user_delivery_exclusions", []).extend(excluded)
        review.pop("delivery_authorization", None)
        if not remaining:
            return _cancel_delivery(
                platform=platform,
                review=review,
                review_path=review_path,
                reason="all_candidates_excluded",
            )
        summary = preview.get("summary") or {}
        refreshed = build_delivery_preview(
            platform=platform,
            discover_id=str(review["discover_id"]),
            send_candidates=remaining,
            send_command=_send_command(platform, review_path),
            selected_count=int(summary.get("selected") or 0),
            promoted_count=int(summary.get("promoted_from_review") or 0),
            review_count=int(summary.get("remaining_review") or 0)
            + int(summary.get("promoted_from_review") or 0),
            rejected_count=int(summary.get("rejected") or 0),
            skipped_delivered_count=int(summary.get("skipped_delivered") or 0),
        )
        review["delivery_preview"] = refreshed
        save_review(review, review_path)
        from jobagent.application.workflow_delivery import register as register_server_preview
        register_server_preview(review)
        save_review(review, review_path)
        response = register_delivery_confirmation(
            platform=platform,
            review_path=review_path,
            review=review,
            preview=refreshed,
            round_id=round_id,
            account_ref=account_ref,
        )
        rounds.set_platform_status(
            platform,
            "awaiting_delivery_confirmation",
            command="jobagent interaction respond",
            evidence={
                "discover_id": review["discover_id"],
                "preview_id": refreshed["preview_id"],
                "send_count": len(remaining),
                "excluded_count": len(review.get("user_delivery_exclusions") or []),
            },
            next_suggested=_review_command(platform, review_path),
        )
        response["excluded"] = excluded
        response["excluded_count"] = len(excluded)
        return response

    authorization = review.get("delivery_authorization")
    if isinstance(authorization, dict):
        authorization_id = str(authorization.get("authorization_id") or "")
        idempotent = True
    else:
        from jobagent.application.workflow_delivery import answer as server_answer
        server_authorization = server_answer(review, "confirm_all")
        authorization = build_delivery_authorization(
            account_ref=account_ref,
            round_id=round_id,
            platform=platform,
            discover_id=str(review["discover_id"]),
            preview=preview,
            send_candidates=list(review.get("send_candidates") or []),
            interaction_id=str(pending.get("interaction_id") or ""),
        )
        if server_authorization is not None:
            authorization["server_authorization"] = server_authorization
        authorization_id = str(authorization["authorization_id"])
        review["delivery_authorization"] = authorization
        review.pop("delivery_cancellation", None)
        save_review(review, review_path)
        idempotent = False
    next_suggested = (
        f"{_send_command(platform, review_path)} --preview-id {preview['preview_id']} "
        f"--authorization-id {authorization_id} --limit 100"
    )
    save_pending_interaction(
        pending["interaction"],
        stage="delivery_authorized",
        root_interaction_id=str(
            pending.get("root_interaction_id") or pending.get("interaction_id") or ""
        ),
        choice="confirm_all",
        context={**context, "authorization_id": authorization_id},
    )
    rounds.set_platform_status(
        platform,
        "reviewed",
        command="jobagent interaction respond",
        evidence={
            "discover_id": review["discover_id"],
            "preview_id": preview["preview_id"],
            "authorization_id": authorization_id,
            "send_count": len(review.get("send_candidates") or []),
        },
        next_suggested=next_suggested,
    )
    return {
        "ok": True,
        "event": "delivery_authorized",
        "platform": platform,
        "preview_id": preview["preview_id"],
        "authorization_id": authorization_id,
        "send_count": len(review.get("send_candidates") or []),
        "idempotent_replay": idempotent,
        "next_suggested": next_suggested,
        "workflow": rounds.round_status(),
    }
