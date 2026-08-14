"""User-visible delivery preview derived from a verified decision manifest."""

from __future__ import annotations

import shlex
from datetime import datetime, timezone
from typing import Any

from jobagent.domain.reviewability import delivery_reviewability_issues
from jobagent.infra.interaction_protocol import (
    build_host_presentations,
    build_interaction_required,
)
from jobagent.infra.protocol import digest_payload

DELIVERY_PREVIEW_PROTOCOL = "agentmesh360.delivery_preview"
DELIVERY_PREVIEW_PROTOCOL_VERSION = 1

_PLATFORM_LABELS = {
    "boss": "Boss直聘",
    "liepin": "猎聘",
    "zhilian": "智联招聘",
    "51job": "51Job / 前程无忧",
}
_DELIVERY_ACTIONS = {
    "boss": ["personalized_greeting"],
    "liepin": ["resume", "personalized_greeting"],
    "zhilian": ["resume"],
    "51job": ["resume"],
}


class DeliveryPreviewError(RuntimeError):
    def __init__(self, payload: dict[str, Any]):
        super().__init__(str(payload.get("message") or payload.get("error") or "delivery preview error"))
        self.payload = payload


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _preview_item(platform: str, item: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "index": index,
        "job_id": _clean(item.get("job_id") or item.get("id") or item.get("jobId")),
        "title": _clean(item.get("title") or item.get("name")) or "未命名岗位",
        "company": _clean(item.get("company")) or "公司未标注",
        "area": _clean(item.get("area")) or "地点未标注",
        "salary": _clean(item.get("salary")) or "薪资未标注",
        "url": _clean(item.get("url")),
        "score": item.get("score"),
        "reason": _clean(item.get("reason")),
        "risk": _clean(item.get("risk")),
        "delivery_actions": list(_DELIVERY_ACTIONS[platform]),
    }


def _preview_id(
    platform: str,
    discover_id: str,
    items: list[dict[str, Any]],
) -> str:
    digest = digest_payload(
        {
            "platform": platform,
            "discover_id": discover_id,
            "items": [
                {
                    "job_id": item["job_id"],
                    "url": item["url"],
                    "delivery_actions": item["delivery_actions"],
                }
                for item in items
            ],
        }
    )
    return "dpv_" + digest.removeprefix("sha256:")[:24]


def _fallback_text(
    *,
    platform: str,
    items: list[dict[str, Any]],
) -> str:
    label = _PLATFORM_LABELS[platform]
    lines = [
        f"{label}待投递岗位清单（共 {len(items)} 个）",
        "以下岗位已经完成云端匹配与签名审核。真实投递前必须由你最后确认。",
        "",
    ]
    if items:
        lines.extend(
            (
                f"{item['index']}. {item['title']}｜{item['company']}｜"
                f"{item['area']}｜{item['salary']}"
            )
            for item in items
        )
    else:
        lines.append("本平台没有待投递岗位。")
    if items:
        lines.extend(
            [
                "",
                "请选择：",
                "1. 确认全部投递",
                "2. 排除部分岗位",
                "3. 取消本平台投递",
                "",
                "在你明确选择前，Job Agent 不会执行任何真实投递。",
            ]
        )
    else:
        lines.extend(["", "没有真实平台动作需要确认，将继续进入审计。"])
    return "\n".join(lines)


def _confirmation_interaction(
    *,
    platform: str,
    discover_id: str,
    preview_id: str,
    fallback_text: str,
) -> dict[str, Any]:
    label = _PLATFORM_LABELS[platform]
    interaction_id = f"delivery:{platform}:{discover_id}:{preview_id}"
    return build_interaction_required(
        interaction_id=interaction_id,
        product_id="job_agent",
        kind="delivery_confirmation",
        title=f"确认{label}投递清单",
        prompt=f"请审阅上方 {label} 待投递岗位，并决定本次如何执行。",
        fields=[
            {
                "field_id": "delivery_choice",
                "type": "single",
                "label": "投递决定",
                "required": True,
                "options": [
                    {
                        "option_id": "confirm_all",
                        "label": "确认全部投递",
                        "description": "确认清单中的全部岗位并继续真实投递。",
                    },
                    {
                        "option_id": "exclude_jobs",
                        "label": "排除部分岗位",
                        "description": "输入不想投递的岗位序号，重新审阅调整后的清单。",
                    },
                    {
                        "option_id": "cancel_delivery",
                        "label": "取消本平台投递",
                        "description": "本轮不在这个平台投递任何岗位。",
                    },
                ],
                "default_option_ids": ["confirm_all"],
                "min_selections": 1,
                "max_selections": 1,
                "allow_other": False,
            }
        ],
        fallback_text=fallback_text,
        continuation_action="jobagent.interaction.respond",
        idempotency_key=interaction_id,
    )


def build_delivery_preview(
    *,
    platform: str,
    discover_id: str,
    send_candidates: list[dict[str, Any]],
    send_command: str,
    selected_count: int,
    promoted_count: int,
    review_count: int,
    rejected_count: int,
    skipped_delivered_count: int,
) -> dict[str, Any]:
    if platform not in _PLATFORM_LABELS:
        raise ValueError(f"Unsupported delivery preview platform: {platform}")
    if platform == "zhilian":
        invalid = [
            {
                "job_id": _clean(
                    item.get("job_id") or item.get("id") or item.get("jobId")
                ),
                "missing_or_invalid_fields": delivery_reviewability_issues(item),
            }
            for item in send_candidates
            if delivery_reviewability_issues(item)
        ]
        if invalid:
            raise DeliveryPreviewError(
                {
                    "ok": False,
                    "error": "delivery_candidate_unreviewable",
                    "message": (
                        "Zhilian delivery preview contains jobs without reviewable title, "
                        "company, or salary fields. The preserved signed decision must be "
                        "repaired before any delivery confirmation."
                    ),
                    "platform": platform,
                    "discover_id": discover_id,
                    "candidates": invalid,
                    "request_preserved": True,
                    "no_charge": True,
                    "billing": {"additional_credits": 0},
                    "requires_user_action": False,
                    "next_suggested": "jobagent zhilian apply review",
                }
            )
    items = [
        _preview_item(platform, item, index)
        for index, item in enumerate(send_candidates, start=1)
    ]
    preview_id = _preview_id(platform, discover_id, items)
    fallback_text = _fallback_text(platform=platform, items=items)
    requires_confirmation = bool(items)
    payload = {
        "event": "delivery_preview",
        "protocol": DELIVERY_PREVIEW_PROTOCOL,
        "protocol_version": DELIVERY_PREVIEW_PROTOCOL_VERSION,
        "preview_id": preview_id,
        "product_id": "job-agent",
        "platform": platform,
        "platform_label": _PLATFORM_LABELS[platform],
        "discover_id": discover_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "title": f"{_PLATFORM_LABELS[platform]}待投递岗位清单",
        "message": (
            f"以下 {len(items)} 个岗位已完成筛选。"
            + (
                "请完整展示清单并等待用户确认；确认前不得执行真实投递。"
                if requires_confirmation
                else "本平台没有待投递岗位，不会执行真实平台动作。"
            )
        ),
        "display_required": True,
        "requires_user_action": requires_confirmation,
        "requires_user_confirmation": requires_confirmation,
        "automatic_continuation": not requires_confirmation,
        "preferred_presentation": "table",
        "allow_text_fallback": True,
        "columns": [
            {"field": "index", "label": "序号"},
            {"field": "title", "label": "岗位"},
            {"field": "company", "label": "公司"},
            {"field": "area", "label": "地点"},
            {"field": "salary", "label": "薪资"},
        ],
        "summary": {
            "selected": selected_count,
            "promoted_from_review": promoted_count,
            "send_count": len(items),
            "remaining_review": max(0, review_count - promoted_count),
            "rejected": rejected_count,
            "skipped_delivered": skipped_delivered_count,
        },
        "items": items,
        "continuation": {
            "action": (
                "jobagent.interaction.respond"
                if requires_confirmation
                else f"{send_command} --preview-id {preview_id}"
            ),
            "automatic": not requires_confirmation,
            "requires_user_confirmation": requires_confirmation,
        },
    }
    payload["fallback_text"] = fallback_text
    if requires_confirmation:
        interaction = _confirmation_interaction(
            platform=platform,
            discover_id=discover_id,
            preview_id=preview_id,
            fallback_text=fallback_text,
        )
        payload["interaction"] = interaction
        payload["host_presentations"] = build_host_presentations(interaction)
    validate_delivery_preview(
        payload,
        send_candidates=send_candidates,
        expected_platform=platform,
        expected_discover_id=discover_id,
    )
    return payload


def validate_delivery_preview(
    payload: dict[str, Any],
    *,
    send_candidates: list[dict[str, Any]],
    expected_platform: str,
    expected_discover_id: str,
    expected_preview_id: str | None = None,
) -> dict[str, Any]:
    if payload.get("event") != "delivery_preview":
        raise ValueError("delivery preview event mismatch")
    if payload.get("protocol") != DELIVERY_PREVIEW_PROTOCOL:
        raise ValueError("delivery preview protocol mismatch")
    if payload.get("protocol_version") != DELIVERY_PREVIEW_PROTOCOL_VERSION:
        raise ValueError("delivery preview protocol version mismatch")
    platform = str(payload.get("platform") or "")
    discover_id = str(payload.get("discover_id") or "")
    if platform not in _PLATFORM_LABELS or not discover_id:
        raise ValueError("delivery preview binding is incomplete")
    if platform != expected_platform or discover_id != expected_discover_id:
        raise ValueError("delivery preview platform or Discover binding mismatch")
    items = [
        _preview_item(platform, item, index)
        for index, item in enumerate(send_candidates, start=1)
    ]
    preview_id = _preview_id(platform, discover_id, items)
    if payload.get("preview_id") != preview_id:
        raise ValueError("delivery preview candidate binding mismatch")
    if expected_preview_id is not None and expected_preview_id != preview_id:
        raise ValueError("delivery preview handoff id mismatch")
    if payload.get("items") != items:
        raise ValueError("delivery preview items do not match reviewed candidates")
    if payload.get("display_required") is not True:
        raise ValueError("delivery preview must be displayed")
    requires_confirmation = bool(items)
    if payload.get("requires_user_confirmation") is not requires_confirmation:
        raise ValueError("delivery preview confirmation requirement mismatch")
    if payload.get("requires_user_action") is not requires_confirmation:
        raise ValueError("delivery preview user-action requirement mismatch")
    if payload.get("automatic_continuation") is requires_confirmation:
        raise ValueError("delivery preview continuation mode mismatch")
    continuation = payload.get("continuation")
    if not isinstance(continuation, dict):
        raise ValueError("delivery preview continuation is missing")
    if requires_confirmation:
        if continuation.get("action") != "jobagent.interaction.respond":
            raise ValueError("delivery preview must pause for a structured interaction")
        if continuation.get("automatic") is not False:
            raise ValueError("delivery preview must not continue automatically")
        interaction = payload.get("interaction")
        if not isinstance(interaction, dict) or preview_id not in str(
            interaction.get("interaction_id") or ""
        ):
            raise ValueError("delivery preview interaction is not bound to the preview")
    elif preview_id not in str(continuation.get("action") or ""):
        raise ValueError("empty delivery preview continuation is not bound to the preview")
    return payload


def preview_required_payload(platform: str, input_path: str | None) -> dict[str, Any]:
    source = f" --input {shlex.quote(input_path)}" if input_path else ""
    review_command = (
        f"jobagent boss greet preview{source}"
        if platform == "boss"
        else f"jobagent {platform} apply review{source}"
    )
    return {
        "ok": False,
        "error": "delivery_preview_required",
        "platform": platform,
        "message": (
            "The reviewed delivery list must be regenerated before delivery. "
            "Run the review command, show its complete delivery_preview, then wait for the "
            "declared user confirmation."
        ),
        "request_preserved": True,
        "requires_user_action": False,
        "next_suggested": review_command,
    }
