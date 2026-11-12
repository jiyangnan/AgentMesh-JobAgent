"""Resolve observed native locations without replacing a preserved Discover."""
from __future__ import annotations

import shlex
from copy import deepcopy

from jobagent.infra import state
from jobagent.infra.account_state import current_account_ref
from jobagent.infra.discovery_state import (
    collection_scope_digest,
    load_collection_checkpoint,
    load_pending_start,
    save_collection_checkpoint,
)
from jobagent.infra.interaction_protocol import (
    build_host_presentations,
    build_interaction_required,
)
from jobagent.infra.interaction_state import (
    clear_pending_interaction,
    load_pending_interaction,
    save_pending_interaction,
)
from jobagent.infra.protocol import digest_payload
from jobagent.platforms.discovery import CollectionError

STAGE = "linkedin_location"
PREFIX = "linkedin-location:"
COPY = {
    "en": ("Confirm search location", "Choose the LinkedIn location that matches your target: {target}", "Keep paused"),
    "zh-CN": ("确认搜索地区", "请选择与你的目标对应的 LinkedIn 地区：{target}", "保留进度，暂不选择"),
    "ja": ("検索地域の確認", "希望地域に一致する LinkedIn の地域を選択してください：{target}", "進捗を保存して保留"),
    "ko": ("검색 지역 확인", "희망 지역에 맞는 LinkedIn 지역을 선택해 주세요: {target}", "진행 상황을 저장하고 대기"),
}


def _context(plan):
    request = load_pending_start("linkedin") or {}
    active = state.load_json(state.current_round_path()) or {}
    binding = active.get("resume_binding") or {}
    owner = current_account_ref()
    if (not owner or owner != request.get("account_ref") or owner != plan.get("account_ref")
            or active.get("status") != "active" or active.get("round_id") != request.get("round_id")
            or plan.get("round_id") != active.get("round_id")
            or (plan.get("resume_binding") and plan["resume_binding"].get("id") != binding.get("id"))
            or plan.get("request_id") != request.get("request_id")
            or plan.get("profile_digest") != request.get("profile_digest")
            or (not binding.get("id") and plan.get("profile_digest") != digest_payload(state.load_json(state.profile_path()) or {}))
            or plan.get("intent_digest") != request.get("intent_digest")
            or plan.get("intent_digest") != digest_payload(active.get("intent") or {})):
        raise CollectionError("linkedin_location_context_changed", "Keep the original account, round, resume and search intent",
                              details={"request_preserved": True, "retryable": False})
    return {"account_ref": owner, "round_id": active["round_id"], "request_id": request["request_id"],
            "discover_id": plan["discover_id"], "scope_digest": collection_scope_digest(plan),
            "resume_binding_digest": digest_payload(binding) if binding.get("id") else None}


def _checkpoint(plan):
    value = load_collection_checkpoint("linkedin")
    if not value or collection_scope_digest(value["plan"]) != collection_scope_digest(plan):
        raise CollectionError("linkedin_location_context_changed", "The preserved search scope changed")
    return value


def _save(checkpoint, progress):
    save_collection_checkpoint("linkedin", request_id=checkpoint["plan"]["request_id"],
                               plan=checkpoint["plan"], progress=progress)


def _present(prompt):
    card = prompt["interaction"]
    waiting = load_pending_interaction()
    if waiting and waiting.get("interaction_id") != card["interaction_id"]:
        raise CollectionError("interaction_already_pending", "Resolve the existing interaction before this location choice")
    if not waiting:
        save_pending_interaction(card, stage=STAGE, context=prompt["context"])
    return {"ok": False, "error": "interaction_required", "requires_user_action": True,
            "request_preserved": True, "no_charge": True, "billing_status": "not_charged",
            "interaction": card, "host_presentations": build_host_presentations(card), "user_prompt": card["fallback_text"],
            "next_suggested": f"jobagent interaction respond --interaction-id {shlex.quote(card['interaction_id'])} --location-id <location-id>"}


def pending(plan):
    checkpoint = load_collection_checkpoint("linkedin")
    radius = (checkpoint or {}).get("progress", {}).get("radius_prompt")
    if radius:
        if _context(plan) != radius["context"]:
            raise CollectionError("linkedin_location_context_changed", "The pending radius belongs to another search")
        _checkpoint(plan)
        from jobagent.application.linkedin_radius import present
        return present(radius)
    prompt = (checkpoint or {}).get("progress", {}).get("location_prompt")
    if not prompt:
        return None
    if _context(plan) != prompt["context"]:
        raise CollectionError("linkedin_location_context_changed", "The pending location belongs to another search")
    _checkpoint(plan)
    return _present(prompt)


def request(plan, details):
    context = _context(plan)
    checkpoint = _checkpoint(plan)
    index = details.get("query_index")
    if type(index) is not int or not 0 <= index < len(plan["queries"]):
        raise CollectionError("linkedin_location_evidence_invalid", "Location evidence must identify one signed query")
    query = plan["queries"][index]
    if details.get("target") != query["location"]:
        raise CollectionError("linkedin_location_evidence_invalid", "The location prompt must preserve the requested target")
    # Store labels/IDs, not a supplied navigation URL. The reader re-observes
    # matching native options for every keyword before clicking anything.
    choices = {}
    for item in details.get("location_options", []):
        geo, label = item.get("geo_id"), item.get("label")
        if not isinstance(geo, str) or not geo.isascii() or not geo.isdigit() or not isinstance(label, str) or not 0 < len(label) <= 300:
            raise CollectionError("linkedin_location_evidence_invalid", "Invalid native location option")
        if geo in choices and choices[geo]["label"] != label:
            raise CollectionError("linkedin_location_evidence_invalid", "Conflicting native location options")
        choices[geo] = {"geo_id": geo, "label": label}
    if not 1 <= len(choices) <= 20:
        raise CollectionError("linkedin_location_evidence_invalid", "No bounded native location choices were observed")
    target_key = digest_payload(query["location"])
    key = {**context, "target_key": target_key, "choices": choices}
    ident = PREFIX + digest_payload(key)[7:31]
    active = state.load_json(state.current_round_path()) or {}
    language = active.get("interaction_language", "en")
    title, question, pause = COPY.get(language, COPY["en"])
    question = question.format(target=query["location"]["label"])
    options = [{"option_id": geo, "label": item["label"]} for geo, item in choices.items()]
    options.append({"option_id": "pause_platform", "label": pause})
    card = build_interaction_required(interaction_id=ident, product_id="job_agent", kind=STAGE, title=title,
        prompt=question, fields=[{"field_id": "location", "type": "single", "label": title, "required": True,
                                 "default_option_ids": [], "allow_other": False, "options": options}],
        fallback_text=question + "\n" + "\n".join(f"{i}. {o['label']} ({o['option_id']})" for i, o in enumerate(options, 1)),
        continuation_action="jobagent.interaction.respond", idempotency_key=ident)
    progress = deepcopy(checkpoint["progress"])
    prompt = {"interaction": card, "context": context, "target_key": target_key, "choices": choices}
    progress["location_prompt"] = prompt
    _save(checkpoint, progress)
    return _present(prompt)


def respond(args):
    ident = str(getattr(args, "interaction_id", "") or "")
    if not ident.startswith(PREFIX):
        return None
    if any(getattr(args, key, None) for key in ("resume_id", "attachment_id", "target_role", "target_city", "target_location", "target_locations", "exclude_index")):
        return {"ok": False, "error": "invalid_interaction_response"}
    checkpoint = load_collection_checkpoint("linkedin")
    if not checkpoint:
        return {"ok": False, "error": "interaction_not_pending", "request_preserved": True}
    context = _context(checkpoint["plan"])
    progress = deepcopy(checkpoint["progress"])
    if getattr(args, "location_id", None) and getattr(args, "choice", None):
        return {"ok": False, "error": "invalid_interaction_response"}
    choice = str(getattr(args, "location_id", "") or getattr(args, "choice", "") or "")
    previous = progress.get("location_answers", {}).get(ident)
    if previous:
        if previous["context"] != context or previous["choice"] != choice:
            return {"ok": False, "error": "interaction_response_conflict", "request_preserved": True}
        waiting = load_pending_interaction()
        if waiting and waiting.get("interaction_id") == ident:
            clear_pending_interaction()
        return {"ok": True, "event": "linkedin_location_confirmed", "idempotent_replay": True,
                "request_preserved": True, "next_suggested": "jobagent linkedin discover"}
    prompt = progress.get("location_prompt") or {}
    if prompt.get("context") != context or (prompt.get("interaction") or {}).get("interaction_id") != ident:
        return {"ok": False, "error": "interaction_not_pending", "request_preserved": True}
    if choice == "pause_platform":
        return {**_present(prompt), "event": "linkedin_search_paused"}
    if choice not in prompt["choices"]:
        return {"ok": False, "error": "invalid_interaction_response", "request_preserved": True}
    value = {**prompt["choices"][choice], "target_digest": prompt["target_key"]}
    progress.setdefault("location_resolutions", {})[prompt["target_key"]] = value
    progress.setdefault("location_answers", {})[ident] = {"context": context, "choice": choice}
    progress.pop("location_prompt")
    _save(checkpoint, progress)  # Persist answer before clearing the host card.
    waiting = load_pending_interaction()
    if waiting and waiting.get("interaction_id") == ident:
        clear_pending_interaction()
    return {"ok": True, "event": "linkedin_location_confirmed", "request_preserved": True,
            "native_location": value, "no_charge": True, "next_suggested": "jobagent linkedin discover"}
