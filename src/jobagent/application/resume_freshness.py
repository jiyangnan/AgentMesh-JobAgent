"""投递前平台简历新鲜度门禁 (pre-delivery platform resume freshness gate).

四平台的投递 = 浏览器触发平台自有动作，平台发送的是用户存在该平台后台的
简历附件；工作台档案只用于分析，永不外发。若用户在工作台改了简历却忘了
同步上传到平台后台，agent 触发投递时平台发出的就是旧简历，用户全程不知情。

本模块在每个平台的投递动作触发前比较:
  当前轮绑定简历的确认修订 (round ``resume_binding`` 快照)
  vs 该平台上次投递确认时的基线 (``resume_freshness_baselines.json``)

无基线或修订不一致 → 出一次三选项卡 (已同步 / 挂起本平台 / 整轮挂起);
一致 → 静默直投。同一平台同一轮同一修订最多问一次：应答"已同步"即更新
基线；绑定修订真实变更后的再次询问不属于重复打扰，正是本门禁要防的坑。

设计文档 (权威): 私有 job-agent-server 仓
docs/plans/2026-09-12-material-fallback-and-freshness-gate.md Part B。
"""

from __future__ import annotations

import shlex
from typing import Any

from jobagent.infra import cloud_client
from jobagent.infra.interaction_protocol import (
    build_host_presentations,
    build_interaction_required,
)
from jobagent.infra.interaction_state import (
    clear_pending_interaction,
    load_pending_interaction,
    save_pending_interaction,
)
from jobagent.infra.rounds import FRESHNESS_HOLD_STATUS, round_status, set_platform_status, utc_now
from jobagent.infra.state import current_round_path, load_json, resume_freshness_path, save_json

INTERACTION_KIND = "resume_freshness"
BASELINE_SCHEMA_VERSION = 1
PLATFORM_LABELS = {"boss": "Boss 直聘", "liepin": "猎聘", "zhilian": "智联招聘", "51job": "前程无忧 51Job"}
# 应答令牌: 卡片初始应答 A/B/C + B/C 挂起后的最终"传好了"应答共用 synced。
CHOICE_SYNCED = "synced"
CHOICE_PAUSE_PLATFORM = "pause_platform"
CHOICE_PAUSE_ROUND = "pause_round"
RESPOND_CHOICES = (CHOICE_SYNCED, CHOICE_PAUSE_PLATFORM, CHOICE_PAUSE_ROUND)
_HOLD_STATES = {"platform_hold", "round_hold"}


# ---------------------------------------------------------------- baselines


def load_baselines() -> dict[str, Any]:
    data = load_json(resume_freshness_path())
    if not isinstance(data, dict) or not isinstance(data.get("platforms"), dict):
        return {"schema_version": BASELINE_SCHEMA_VERSION, "platforms": {}}
    return data


def _fingerprint(binding: dict[str, Any]) -> dict[str, str]:
    return {
        "resume_id": str(binding.get("resume_id") or ""),
        "resume_revision_id": str(binding.get("resume_revision_id") or ""),
        "content_digest": str(binding.get("content_digest") or ""),
        "confirmed_at": str(binding.get("confirmed_at") or ""),
    }


def _same_revision(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return bool(
        left.get("resume_revision_id")
        and left.get("resume_revision_id") == right.get("resume_revision_id")
        and left.get("content_digest") == right.get("content_digest")
    )


def _save_baseline(platform: str, fingerprint: dict[str, Any]) -> None:
    data = load_baselines()
    data["platforms"][platform] = {**fingerprint, "updated_at": utc_now()}
    save_json(resume_freshness_path(), data)


def baseline_is_fresh(platform: str, binding: dict[str, Any]) -> bool:
    """True when the platform baseline already matches the bound revision."""
    baseline = load_baselines()["platforms"].get(platform)
    return bool(baseline and _same_revision(baseline, _fingerprint(binding)))


# ------------------------------------------------------------------- cards


def _month_day(confirmed_at: Any) -> str:
    from datetime import datetime

    text = str(confirmed_at or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return "最近"
    return f"{parsed.month} 月 {parsed.day} 日"


def _interaction_id(round_id: str, platform: str, fingerprint: dict[str, Any]) -> str:
    revision_tag = fingerprint["resume_revision_id"] or fingerprint["content_digest"]
    return f"freshness:{round_id}:{platform}:{revision_tag}"


def _respond_command(interaction_id: str, choice: str = CHOICE_SYNCED) -> str:
    return f'jobagent interaction respond --interaction-id "{interaction_id}" --choice {choice}'


def _build_card(platform: str, *, round_id: str, fingerprint: dict[str, Any], first_delivery: bool) -> dict[str, Any]:
    label = PLATFORM_LABELS.get(platform, platform)
    resume_name = fingerprint.get("resume_name") or "当前绑定简历"
    date = _month_day(fingerprint.get("confirmed_at"))
    if first_delivery:
        prompt = (
            f"本轮将首次通过 Job Agent 在 {label} 触发投递，{label} 发送的是你保存在其后台的简历附件。"
            f"当前绑定简历《{resume_name}》（{date} 确认）已经上传到 {label} 后台了吗？"
        )
    else:
        prompt = (
            f"检测到你在 {date} 确认了新版本简历《{resume_name}》，本轮投递会触发 {label} "
            f"发送你保存在其后台的简历。你已把这份最新简历同步上传到 {label} 后台了吗？"
        )
    return build_interaction_required(
        interaction_id=_interaction_id(round_id, platform, fingerprint),
        product_id="job_agent",
        kind=INTERACTION_KIND,
        title="平台简历新鲜度确认",
        prompt=prompt,
        fields=[
            {
                "field_id": "freshness_choice",
                "type": "single",
                "label": "同步状态",
                "options": [
                    {
                        "option_id": CHOICE_SYNCED,
                        "label": "已同步到该平台",
                        "description": f"{label} 后台已是这份最新简历，继续触发投递",
                    },
                    {
                        "option_id": CHOICE_PAUSE_PLATFORM,
                        "label": "还没传，挂起本平台",
                        "description": "本平台投递挂起，其余平台继续；同步完成后回来应答",
                    },
                    {
                        "option_id": CHOICE_PAUSE_ROUND,
                        "label": "还没传，整轮挂起",
                        "description": "全轮暂停；同步完成后回来应答，按原顺序继续",
                    },
                ],
            }
        ],
        fallback_text=(
            f"请应答 {_respond_command(_interaction_id(round_id, platform, fingerprint))}："
            "--choice synced（已同步）/ pause_platform（挂起本平台）/ pause_round（整轮挂起）。"
        ),
        continuation_action="jobagent.interaction.respond",
        idempotency_key=_interaction_id(round_id, platform, fingerprint),
    )


def _send_command(platform: str, source: dict[str, Any]) -> str:
    quoted = shlex.quote(str(source.get("input_path") or ""))
    base = (
        f"jobagent boss greet send --input {quoted}"
        if platform == "boss"
        else f"jobagent {platform} apply send --input {quoted}"
    )
    return (
        f"{base} --preview-id {source.get('preview_id')} "
        f"--authorization-id {source.get('authorization_id')} --limit {int(source.get('limit') or 100)}"
    )


# --------------------------------------------------------------- round help


def _active_round() -> dict[str, Any] | None:
    current = load_json(current_round_path())
    if not current or current.get("status") != "active" or not current.get("round_id"):
        return None
    return current


def _platform_record(active: dict[str, Any], platform: str) -> dict[str, Any] | None:
    item = (active.get("platforms") or {}).get(platform)
    record = (item or {}).get("resume_freshness") if isinstance(item, dict) else None
    return record if isinstance(record, dict) else None


def _save_record(platform: str, record: dict[str, Any] | None) -> dict[str, Any]:
    active = _active_round()
    if active is None:
        return {"ok": False, "error": "round_not_started",
                "message": "Start a Job Agent round before changing workflow state.",
                "next_suggested": "jobagent round start"}
    item = active.setdefault("platforms", {}).setdefault(platform, {"status": "pending"})
    if record is None:
        item.pop("resume_freshness", None)
    else:
        item["resume_freshness"] = record
    if record is None and active.get("resume_freshness_round_hold", {}).get("platform") == platform:
        active.pop("resume_freshness_round_hold", None)
    from jobagent.infra.rounds import save_round

    save_round(active)
    return active


def _set_status(platform: str, status: str, *, next_suggested: str, evidence_extra: dict[str, Any]) -> None:
    active = _active_round() or {}
    item = (active.get("platforms") or {}).get(platform) or {}
    evidence = dict(item.get("evidence") or {})
    evidence.update(evidence_extra)
    set_platform_status(platform, status, command="jobagent interaction respond",
                        evidence=evidence, next_suggested=next_suggested)


def _gate_response(interaction: dict[str, Any], platform: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": "interaction_required",
        "event": "resume_freshness_gate",
        "platform": platform,
        "interaction": interaction,
        "host_presentations": build_host_presentations(interaction),
        "requires_user_action": True,
        "request_preserved": True,
        "no_charge": True,
        "message": "投递已暂停：请先确认平台后台的简历与工作台最新确认版本一致。",
        "next_suggested": _respond_command(interaction["interaction_id"], CHOICE_SYNCED),
        "workflow": round_status(),
    }


def _invalid_response(pending_or_record: dict[str, Any], message: str) -> dict[str, Any]:
    interaction = pending_or_record.get("interaction") or {}
    return {
        "ok": False,
        "error": "invalid_interaction_response",
        "message": message,
        "requires_user_action": True,
        "interaction": interaction,
        "host_presentations": build_host_presentations(interaction) if isinstance(interaction, dict) and interaction else None,
        "next_suggested": _respond_command(str(pending_or_record.get("interaction_id") or "")),
    }


# --------------------------------------------------------------------- gate


def gate_delivery(platform: str, *, source: dict[str, Any], dry_run: bool = False) -> dict[str, Any] | None:
    """Return an interaction_required card when the platform resume may be stale.

    Called right before a platform's delivery actions would be triggered (native
    ``start_delivery`` and the legacy ``send_reviewed``). Returns None — silent
    pass — for dry runs, unbound rounds, and platforms whose baseline already
    matches the bound revision.
    """
    if dry_run or platform not in PLATFORM_LABELS:
        return None
    active = _active_round()
    if active is None:
        return None
    binding = active.get("resume_binding") or {}
    if not binding.get("id"):
        return None
    round_id = str(active["round_id"])
    record = _platform_record(active, platform)
    if record and record.get("round_id") == round_id:
        state = str(record.get("state") or "")
        if state == "awaiting":
            # Same unanswered card for this round: re-present it idempotently.
            save_pending_interaction(record["interaction"], stage=INTERACTION_KIND,
                                     context={"platform": platform, "round_id": round_id})
            return _gate_response(record["interaction"], platform)
        if state in _HOLD_STATES:
            return {
                "ok": False,
                "error": "resume_freshness_hold",
                "platform": platform,
                "requires_user_action": True,
                "request_preserved": True,
                "message": "该平台因简历新鲜度确认挂起：同步完成后应答 synced 恢复投递。",
                "next_suggested": _respond_command(str(record.get("interaction_id") or "")),
                "workflow": round_status(),
            }
    fingerprint = {**_fingerprint(binding), "resume_name": str(binding.get("resume_name") or "")}
    if baseline_is_fresh(platform, binding):
        return None
    first_delivery = platform not in load_baselines()["platforms"]
    interaction = _build_card(platform, round_id=round_id, fingerprint=fingerprint,
                              first_delivery=first_delivery)
    item = (active.get("platforms") or {}).get(platform) or {}
    record = {
        "interaction_id": interaction["interaction_id"],
        "interaction": interaction,
        "round_id": round_id,
        "state": "awaiting",
        "platform_label": PLATFORM_LABELS[platform],
        "resume_binding_id": str(binding.get("id") or ""),
        "resume_name": fingerprint["resume_name"],
        "revision": _fingerprint(binding),
        "pre_hold_status": str(item.get("status") or "reviewed"),
        "source": {**source, "limit": int(source.get("limit") or 100)},
        "asked_at": utc_now(),
    }
    from jobagent.infra.rounds import save_round

    item = active.setdefault("platforms", {}).setdefault(platform, {"status": "pending"})
    item["resume_freshness"] = record
    save_round(active)
    save_pending_interaction(interaction, stage=INTERACTION_KIND,
                             context={"platform": platform, "round_id": round_id})
    return _gate_response(interaction, platform)


# ------------------------------------------------------------------ respond


def respond(interaction_id: str, *, choice: str) -> dict[str, Any] | None:
    """Answer a freshness card, or the final sync confirmation of a held platform.

    Returns None when the id matches no freshness interaction so the CLI can
    fall through to the other interaction handlers.
    """
    pending = load_pending_interaction()
    if pending and str(pending.get("kind") or "") == INTERACTION_KIND \
            and str(pending.get("interaction_id") or "") == interaction_id:
        return _respond_initial(pending, interaction_id, choice)
    # A held platform answers by id even when another platform's card has
    # since taken the single pending-interaction slot: the hold itself lives
    # in the round state, not in the slot.
    active = _active_round()
    if active is not None:
        for platform, item in (active.get("platforms") or {}).items():
            record = (item or {}).get("resume_freshness") if isinstance(item, dict) else None
            if isinstance(record, dict) and str(record.get("interaction_id") or "") == interaction_id \
                    and str(record.get("state") or "") in _HOLD_STATES:
                return _respond_hold(platform, record, choice)
    if pending and str(pending.get("kind") or "") == INTERACTION_KIND:
        return {
            "ok": False,
            "error": "interaction_not_pending",
            "message": "This freshness interaction is no longer waiting for that answer.",
            "next_suggested": _respond_command(str(pending.get("interaction_id") or "")),
        }
    return None


def _respond_initial(pending: dict[str, Any], interaction_id: str, choice: str) -> dict[str, Any]:
    active = _active_round()
    if active is None:
        return _invalid_response(pending, "The delivery round is no longer active.")
    context = pending.get("context") or {}
    platform = str(context.get("platform") or "")
    record = _platform_record(active, platform) if platform else None
    if not record or str(record.get("interaction_id") or "") != interaction_id \
            or str(record.get("round_id") or "") != str(active.get("round_id") or ""):
        return _invalid_response(pending, "This freshness confirmation is no longer pending.")
    if choice not in RESPOND_CHOICES:
        return _invalid_response(pending, "Choose synced, pause_platform, or pause_round.")
    if choice == CHOICE_SYNCED:
        return _resolve_synced(platform, record, verified=None)
    record["state"] = "platform_hold" if choice == CHOICE_PAUSE_PLATFORM else "round_hold"
    record["hold_choice"] = choice
    record["held_at"] = utc_now()
    _save_record(platform, record)
    if choice == CHOICE_PAUSE_ROUND:
        active = _active_round() or {}
        active["resume_freshness_round_hold"] = {
            "platform": platform,
            "interaction_id": interaction_id,
            "held_at": utc_now(),
        }
        from jobagent.infra.rounds import save_round

        save_round(active)
    _set_status(platform, FRESHNESS_HOLD_STATUS,
                next_suggested=_respond_command(interaction_id),
                evidence_extra={"resume_freshness_hold": True, "interaction_id": interaction_id})
    clear_pending_interaction()
    message = (
        "整轮已挂起：请把最新简历上传到平台后台，完成后回来应答 synced，按原顺序继续本轮。"
        if choice == CHOICE_PAUSE_ROUND
        else "本平台已挂起，其余平台继续：请把最新简历上传到平台后台，完成后回来应答 synced 恢复本平台投递。"
    )
    return {
        "ok": True,
        "event": "resume_freshness_platform_hold" if choice == CHOICE_PAUSE_PLATFORM else "resume_freshness_round_hold",
        "platform": platform,
        "requires_user_action": True,
        "message": message,
        "next_suggested": _respond_command(interaction_id),
        "workflow": round_status(),
    }


def _respond_hold(platform: str, record: dict[str, Any], choice: str) -> dict[str, Any]:
    if choice != CHOICE_SYNCED:
        return _invalid_response(record, "This platform is already held; answer synced after uploading.")
    try:
        material = cloud_client.resume_binding_material(str(record.get("resume_binding_id") or ""))
    except cloud_client.CloudError as exc:
        if exc.code == "preparation_required":
            # The bound resume changed underneath; same unwind as discover.
            from jobagent.infra.rounds import clear_round_resume_binding

            clear_round_resume_binding()
            _save_record(platform, None)
            _set_status(platform, str(record.get("pre_hold_status") or "reviewed"),
                        next_suggested="jobagent round start",
                        evidence_extra={"resume_freshness_hold": False})
            return {
                "ok": False,
                "error": "resume_binding_paused",
                "platform": platform,
                "message": "绑定的简历已变更或不再可用，本平台挂起已解除。请重新执行 jobagent round start 选择简历后再继续。",
                "next_suggested": "jobagent round start",
            }
        return {
            "ok": False,
            "error": "resume_freshness_recheck_unavailable",
            "platform": platform,
            "requires_user_action": True,
            "request_preserved": True,
            "retryable": bool(exc.retryable),
            "message": "暂时无法复查工作台简历状态（网络或服务不可用），挂起保留。请稍后重新应答。",
            "next_suggested": _respond_command(str(record.get("interaction_id") or "")),
        }
    snapshot = material.get("binding") or material.get("resume_binding") or {}
    if _same_revision(snapshot, record.get("revision") or {}):
        return _resolve_synced(platform, record, verified=snapshot)
    # The user confirmed yet another revision while paused: keep the hold and
    # re-anchor the comparison to the newest revision. No baseline update —
    # the sync claim must match the CURRENT workbench revision to count.
    record["revision"] = _fingerprint(snapshot)
    record["resume_name"] = str(snapshot.get("resume_name") or record.get("resume_name") or "")
    record["interaction"] = _build_card(platform, round_id=str(record.get("round_id") or ""),
                                        fingerprint={**_fingerprint(snapshot),
                                                     "resume_name": record["resume_name"]},
                                        first_delivery=False)
    record["interaction_id"] = record["interaction"]["interaction_id"]
    record["reanchored_at"] = utc_now()
    _save_record(platform, record)
    date = _month_day((record.get("revision") or {}).get("confirmed_at"))
    return {
        "ok": False,
        "error": "resume_freshness_changed_again",
        "platform": platform,
        "requires_user_action": True,
        "request_preserved": True,
        "message": (
            f"复查发现你在 {date} 又确认了新版本简历《{record['resume_name']}》。"
            "请把这份最新版本同步上传到平台后台后，再应答 synced 恢复投递。"
        ),
        "next_suggested": _respond_command(str(record.get("interaction_id") or "")),
    }


def _resolve_synced(platform: str, record: dict[str, Any], *, verified: dict[str, Any] | None) -> dict[str, Any]:
    """User claims the platform now holds the bound revision: baseline + resume."""
    fingerprint = _fingerprint(verified) if verified else dict(record.get("revision") or {})
    if fingerprint.get("resume_revision_id"):
        _save_baseline(platform, fingerprint)
    _save_record(platform, None)
    _set_status(platform, str(record.get("pre_hold_status") or "reviewed"),
                next_suggested=_send_command(platform, record.get("source") or {}),
                evidence_extra={
                    "resume_freshness_hold": False,
                    "resume_freshness_synced": True,
                    "resume_revision_id": fingerprint.get("resume_revision_id"),
                })
    pending = load_pending_interaction()
    if pending and str(pending.get("kind") or "") == INTERACTION_KIND:
        clear_pending_interaction()
    return {
        "ok": True,
        "event": "resume_freshness_synced",
        "platform": platform,
        "message": "已记录该平台的简历基线，恢复投递流程。",
        "next_suggested": _send_command(platform, record.get("source") or {}),
        "workflow": round_status(),
    }


# ------------------------------------------------------------------ cleanup


def clear_hold(platform: str) -> None:
    """Drop a platform's freshness hold (e.g. the user explicitly skips it)."""
    active = _active_round()
    if active is None:
        return
    record = _platform_record(active, platform)
    if record is None:
        return
    _save_record(platform, None)
    _set_status(platform, str(record.get("pre_hold_status") or "reviewed"),
                next_suggested="jobagent round status",
                evidence_extra={"resume_freshness_hold": False})
    pending = load_pending_interaction()
    if pending and str(pending.get("kind") or "") == INTERACTION_KIND \
            and str(pending.get("interaction_id") or "") == str(record.get("interaction_id") or ""):
        clear_pending_interaction()


__all__ = [
    "INTERACTION_KIND",
    "RESPOND_CHOICES",
    "baseline_is_fresh",
    "clear_hold",
    "gate_delivery",
    "load_baselines",
    "respond",
]
