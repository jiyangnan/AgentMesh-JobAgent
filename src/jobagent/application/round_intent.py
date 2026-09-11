"""Target-role confirmation for a new delivery round."""

from __future__ import annotations

from typing import Any

from jobagent.infra.interaction_protocol import (
    build_host_presentations,
    build_interaction_required,
)
from jobagent.infra.protocol import digest_payload
from jobagent.infra.rounds import utc_now

MAX_TARGET_ROLES = 4
MAX_TARGET_CITIES = 5
MAX_INTENT_TARGET_CITIES = 3
TARGET_ROLE_POLICY_VERSION = 2
TARGET_ROLE_CHOICES = {
    "accept_suggested",
    "append_roles",
    "replace_roles",
}
REBIND_RESUME_CHOICE = "rebind_resume"


def _binding_direction(resume_binding: dict[str, Any] | None) -> str | None:
    if not resume_binding or not resume_binding.get("id"):
        return None
    return str(resume_binding.get("target_role") or "").strip() or None


def confirmed_target_cities(profile: dict[str, Any]) -> list[str]:
    preferences = profile.get("preferences") or {}
    raw_cities = preferences.get("targetCities") or []
    ordered = sorted(
        (item for item in raw_cities if isinstance(item, dict)),
        key=lambda item: int(item.get("priority") or 999),
    )
    return _normalize_cities([str(item.get("city") or "") for item in ordered])


def with_target_cities(
    profile: dict[str, Any],
    target_cities: list[str],
) -> tuple[dict[str, Any], list[str]]:
    cities = _normalize_cities(target_cities)
    if not cities:
        raise ValueError("Enter at least one target city before starting a round.")
    updated = dict(profile)
    preferences = dict(updated.get("preferences") or {})
    preferences["targetCities"] = [
        {"city": city, "priority": index}
        for index, city in enumerate(cities, start=1)
    ]
    updated["preferences"] = preferences
    return updated, cities


def target_city_input_request(
    profile: dict[str, Any],
    *,
    previous_round_id: str | None,
) -> dict[str, Any]:
    profile_digest = digest_payload(profile)
    context = previous_round_id or "initial"
    interaction_key = digest_payload(
        {
            "product_id": "job_agent",
            "kind": "target_city_input",
            "profile_digest": profile_digest,
            "previous_round_id": context,
        }
    ).split(":", 1)[1][:20]
    interaction_id = f"jobagent:target-city:{interaction_key}"
    fallback = (
        "当前简历画像还没有目标城市。请告诉我本轮想看的城市，可以填写多个，"
        "例如：郑州、杭州。"
    )
    interaction = build_interaction_required(
        interaction_id=interaction_id,
        product_id="job_agent",
        kind="target_city_input",
        title="确认本轮目标城市",
        prompt="请确认本轮想看的目标城市，可以填写多个。",
        fields=[
            {
                "field_id": "target_cities",
                "type": "text",
                "label": "目标城市",
                "required": True,
                "placeholder": "例如：郑州、杭州",
            }
        ],
        fallback_text=fallback,
        continuation_action="jobagent.interaction.respond",
        idempotency_key=interaction_id,
    )
    return {
        "ok": False,
        "error": "interaction_required",
        "requires_user_action": True,
        "user_action": "confirm_target_cities",
        "user_prompt": fallback,
        "interaction": interaction,
        "host_presentations": build_host_presentations(interaction),
        "next_suggested": (
            f'jobagent interaction respond --interaction-id "{interaction_id}" '
            '--target-city "<city>"'
        ),
    }


def suggested_target_roles(profile: dict[str, Any]) -> list[str]:
    metadata = profile.get("_meta")
    if not isinstance(metadata, dict):
        return []
    try:
        policy_version = int(metadata.get("targetRolePolicyVersion") or 0)
    except (TypeError, ValueError):
        policy_version = 0
    if policy_version < TARGET_ROLE_POLICY_VERSION:
        return []
    preferences = profile.get("preferences") or {}
    raw_roles = preferences.get("targetRoles") or []
    ordered = sorted(
        (item for item in raw_roles if isinstance(item, dict)),
        key=lambda item: int(item.get("priority") or 999),
    )
    return _normalize_roles([str(item.get("title") or "") for item in ordered])[:3]


def build_round_intent(
    profile: dict[str, Any],
    *,
    accept_suggested: bool,
    target_roles: list[str] | None,
    resume_binding: dict[str, Any] | None = None,
    target_cities: list[str] | None = None,
) -> dict[str, Any]:
    direction = _binding_direction(resume_binding)
    suggested = suggested_target_roles(profile) if accept_suggested else []
    explicit = _normalize_roles(target_roles or [])
    if direction is not None:
        # Bound rounds deliver along the bound resume's confirmed direction
        # only; the server signs plans exclusively for that single role.
        if suggested and suggested != [direction]:
            suggested = [direction]
        if explicit and explicit != [direction]:
            raise ValueError(
                f"This round is bound to the resume "
                f"《{resume_binding.get('resume_name') or resume_binding.get('id')}》"
                f"（方向：{direction}），只能投递该方向的岗位。"
                "如需投递其他方向，请重新选择（或上传）对应方向的简历后再开轮：jobagent round start。"
            )
        roles = [direction]
    else:
        roles = _normalize_roles([*suggested, *explicit])
    if not roles:
        raise ValueError("At least one target role must be confirmed before starting a round.")
    if len(roles) > MAX_TARGET_ROLES:
        raise ValueError(f"A round supports at most {MAX_TARGET_ROLES} target roles.")
    if direction is None and suggested and explicit:
        source = "suggested_plus_explicit"
    elif explicit:
        source = "user_explicit"
    else:
        source = "suggested"
    intent: dict[str, Any] = {
        "status": "confirmed",
        "target_roles": roles,
        "source": source,
        "profile_digest": digest_payload(profile),
        "confirmed_at": utc_now(),
    }
    if direction is not None:
        cities = _normalize_cities(target_cities or [])[:MAX_INTENT_TARGET_CITIES]
        if not cities:
            raise ValueError(
                "A bound round must carry explicit target cities. "
                "Confirm target cities before starting the round."
            )
        intent["target_cities"] = cities
    return intent


def build_round_intent_from_choice(
    profile: dict[str, Any],
    *,
    choice: str,
    target_roles: list[str] | None,
    resume_binding: dict[str, Any] | None = None,
    target_cities: list[str] | None = None,
) -> dict[str, Any]:
    if choice not in TARGET_ROLE_CHOICES:
        raise ValueError(f"Unsupported target-role choice: {choice}")
    if _binding_direction(resume_binding) is not None and choice != "accept_suggested":
        raise ValueError(
            "This round is bound to a confirmed resume and can only deliver along "
            "that resume's direction. To aim at other roles, re-select (or upload) "
            "a resume in that direction, then start the round again: jobagent round start。"
        )
    explicit = _normalize_roles(target_roles or [])
    if choice == "accept_suggested":
        if explicit:
            raise ValueError("The suggested-role choice cannot include additional target roles.")
        return build_round_intent(
            profile,
            accept_suggested=True,
            target_roles=[],
            resume_binding=resume_binding,
            target_cities=target_cities,
        )
    if not explicit:
        raise ValueError("Enter at least one target role for this choice.")
    return build_round_intent(
        profile,
        accept_suggested=choice == "append_roles",
        target_roles=explicit,
        resume_binding=resume_binding,
        target_cities=target_cities,
    )


def target_role_confirmation(
    profile: dict[str, Any],
    *,
    previous_round_id: str | None,
    resume_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    direction = _binding_direction(resume_binding)
    if direction is not None:
        suggested = [direction]
    else:
        suggested = suggested_target_roles(profile)
    profile_digest = digest_payload(profile)
    context = previous_round_id or "initial"
    interaction_key = digest_payload(
        {
            "product_id": "job_agent",
            "kind": "target_role_confirmation",
            "profile_digest": profile_digest,
            "previous_round_id": context,
        }
    ).split(":", 1)[1][:20]
    interaction_id = f"jobagent:target-role:{interaction_key}"
    if direction is not None:
        resume_name = str(resume_binding.get("resume_name") or "") or "已确认简历"
        roles_text = direction
        prompt = (
            f"本轮已绑定简历《{resume_name}》（方向：{direction}），"
            f"投递将使用这份简历。本轮投递岗位：{roles_text}。"
            "如需投递其他方向，请换绑对应方向的简历。"
        )
        fallback = (
            f"本轮已绑定简历《{resume_name}》（方向：{direction}），"
            f"投递将使用这份简历。本轮投递岗位：{roles_text}。\n\n"
            "请选择：\n"
            "1. 按绑定方向开始（推荐）\n"
            "2. 换绑其他方向的简历\n"
        )
        fields = [
            {
                "field_id": "target_role_choice",
                "type": "single",
                "label": "本轮目标岗位",
                "required": True,
                "options": [
                    {
                        "option_id": "accept_suggested",
                        "label": "按绑定方向开始",
                        "description": f"投递 {roles_text}，并继续本轮流程。",
                    },
                    {
                        "option_id": REBIND_RESUME_CHOICE,
                        "label": "换绑其他方向的简历",
                        "description": (
                            "本轮不开始，重新选择（或上传）目标方向的简历后再开轮。"
                        ),
                    },
                ],
                "default_option_ids": ["accept_suggested"],
                "min_selections": 1,
                "max_selections": 1,
                "allow_other": False,
                "known_values": suggested,
            }
        ]
    elif suggested:
        roles_text = "、".join(suggested)
        prompt = (
            f"根据本机最近一次简历分析的结果，我建议本轮优先投递：{roles_text}。"
            "除此以外，你还想投递其他岗位吗？"
        )
        fallback = (
            f"根据本机最近一次简历分析的结果，我建议本轮优先投递：{roles_text}。\n\n"
            "请选择：\n"
            "1. 按建议岗位开始（推荐）\n"
            "2. 保留建议岗位，并追加其他岗位\n"
            "3. 只投你指定的其他岗位\n\n"
            "选择 2 或 3 后，请继续输入岗位名称。"
        )
        fields = [
            {
                "field_id": "target_role_choice",
                "type": "single",
                "label": "本轮目标岗位",
                "required": True,
                "options": [
                    {
                        "option_id": "accept_suggested",
                        "label": "按建议岗位",
                        "description": f"投递 {roles_text}，并继续本轮流程。",
                    },
                    {
                        "option_id": "append_roles",
                        "label": "追加其他岗位",
                        "description": "保留建议岗位，并输入还想投递的岗位。",
                    },
                    {
                        "option_id": "replace_roles",
                        "label": "只投其他岗位",
                        "description": "不采用建议岗位，改为输入你指定的岗位。",
                    },
                ],
                "default_option_ids": ["accept_suggested"],
                "min_selections": 1,
                "max_selections": 1,
                "allow_other": False,
                "known_values": suggested,
            }
        ]
    else:
        prompt = (
            "当前画像没有经过新版岗位方向校验，不能安全地替你默认选择岗位。"
            "请输入本轮想投递的目标岗位。"
        )
        fallback = (
            "当前画像没有经过新版岗位方向校验，不能安全地替你默认选择岗位。\n\n"
            "请输入本轮想投递的目标岗位。\n\n"
            "收到岗位后，Agent 应执行 jobagent round start --target-role <岗位名称>。"
        )
        fields = [
            {
                "field_id": "target_roles",
                "type": "text",
                "label": "本轮目标岗位",
                "required": True,
                "placeholder": "请输入岗位名称",
            }
        ]
    interaction = build_interaction_required(
        interaction_id=interaction_id,
        product_id="job_agent",
        kind="target_role_confirmation",
        title="确认本轮目标岗位",
        prompt=prompt,
        fields=fields,
        fallback_text=fallback,
        continuation_action="jobagent.interaction.respond",
        idempotency_key=interaction_id,
    )
    next_suggested = (
        f'jobagent interaction respond --interaction-id "{interaction_id}" '
        '--choice accept_suggested'
        if suggested
        else f'jobagent interaction respond --interaction-id "{interaction_id}" '
        '--target-role "<target role>"'
    )
    if direction is not None:
        next_suggested += (
            f'\n换绑简历：先取消当前选择（jobagent interaction respond '
            f'--interaction-id "{interaction_id}" --choice {REBIND_RESUME_CHOICE}），'
            "再执行 jobagent round start 重新选择简历。"
        )
    return {
        "ok": False,
        "error": "interaction_required",
        "interaction": interaction,
        "host_presentations": build_host_presentations(interaction),
        "suggested_roles": suggested,
        "next_suggested": next_suggested,
    }


def target_role_input_request(
    profile: dict[str, Any],
    *,
    root_interaction_id: str,
    choice: str,
) -> dict[str, Any]:
    if choice not in {"append_roles", "replace_roles"}:
        raise ValueError("A target-role input request requires append_roles or replace_roles.")
    interaction_id = f"{root_interaction_id}:roles:{choice}"
    if choice == "append_roles":
        title = "追加目标岗位"
        prompt = "请输入还想追加到本轮建议中的岗位名称。"
        fallback = "请输入要追加的岗位名称。"
    else:
        title = "指定其他岗位"
        prompt = "请输入本轮只想投递的岗位名称。"
        fallback = "请输入本轮只想投递的岗位名称。"
    interaction = build_interaction_required(
        interaction_id=interaction_id,
        product_id="job_agent",
        kind="target_role_input",
        title=title,
        prompt=prompt,
        fields=[
            {
                "field_id": "target_roles",
                "type": "text",
                "label": "目标岗位",
                "required": True,
                "placeholder": "请输入岗位名称",
            }
        ],
        fallback_text=fallback,
        continuation_action="jobagent.interaction.respond",
        idempotency_key=interaction_id,
    )
    return {
        "ok": False,
        "error": "interaction_required",
        "interaction": interaction,
        "host_presentations": build_host_presentations(interaction),
        "suggested_roles": suggested_target_roles(profile),
        "next_suggested": (
            f'jobagent interaction respond --interaction-id "{interaction_id}" '
            '--target-role "<target role>"'
        ),
    }


def _normalize_roles(values: list[str]) -> list[str]:
    roles: list[str] = []
    seen: set[str] = set()
    for value in values:
        role = " ".join(value.split()).strip()
        key = role.casefold()
        if not role or len(role) > 80 or not any(character.isalpha() for character in role):
            continue
        if key in seen:
            continue
        seen.add(key)
        roles.append(role)
    return roles


def _normalize_cities(values: list[str]) -> list[str]:
    cities: list[str] = []
    seen: set[str] = set()
    for value in values:
        city = " ".join(value.split()).strip().removesuffix("市")
        key = city.casefold()
        if (
            not city
            or len(city) > 30
            or any(ord(character) < 32 for character in city)
            or key in seen
        ):
            continue
        seen.add(key)
        cities.append(city)
    if len(cities) > MAX_TARGET_CITIES:
        raise ValueError(f"A round supports at most {MAX_TARGET_CITIES} target cities.")
    return cities
