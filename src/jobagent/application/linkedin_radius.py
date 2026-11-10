"""Confirm observed native distances and re-sign the preserved search."""
import math
import shlex
from copy import deepcopy

from jobagent.application import linkedin_search as locations
from jobagent.infra import protocol, state
from jobagent.infra.discovery_state import (
    load_collection_checkpoint,
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
from jobagent.platforms.discovery import CollectionError

PREFIX = "linkedin-radius:"
FACTORS = {"公里": 1, "Kilometers": 1, "km": 1, "キロメートル": 1, "킬로미터": 1,
           "Miles": 1.609344, "英里": 1.609344, "マイル": 1.609344, "마일": 1.609344}
COPY = {
    "en": ("Confirm search radius", "{target}: {radius:g} km is not available. Choose a supported distance. This restarts collection in the same search; no additional charge.", "Keep paused"),
    "zh-CN": ("确认搜索范围", "{target} 不支持 {radius:g} 公里。请选择平台支持的距离；将在原搜索内重新采集，不额外扣费。", "保留进度，暂不选择"),
    "ja": ("検索範囲の確認", "{target} では {radius:g} km を指定できません。対応する距離を選択すると、同じ検索内で収集を再開します。追加料金はありません。", "進捗を保存して保留"),
    "ko": ("검색 범위 확인", "{target}에서는 {radius:g} km를 지정할 수 없습니다. 지원하는 거리를 선택하면 같은 검색에서 다시 수집합니다. 추가 요금은 없습니다.", "진행 상황을 저장하고 대기"),
}


def scope_digest(plan):
    return protocol.digest_payload({"queries": plan["queries"], "radius_resolutions": plan.get("radius_resolutions", {})})


def native_choices(native, requested):
    try:
        data = {key: float(native[key]) for key in ("min", "max", "step")}
        data["unit"] = native["unit"]
        factor = FACTORS[data["unit"]]
        count = (data["max"] - data["min"]) / data["step"]
        if (not all(math.isfinite(data[k]) for k in ("min", "max", "step"))
                or data["min"] < 0 or data["step"] <= 0 or not 0 <= count <= 100
                or abs(count - round(count)) > 1e-6):
            raise ValueError("Invalid native range")
        values = [data["min"] + i * data["step"] for i in range(round(count) + 1)
                  if (data["min"] + i * data["step"]) * factor <= 500]
        values = sorted(sorted(values, key=lambda v: abs(v * factor - requested))[:5])
        if not values:
            raise ValueError("No supported distance")
        return data, {f"distance-{i}": value for i, value in enumerate(values)}
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise CollectionError("linkedin_radius_evidence_invalid", "No bounded native distance choices were observed") from exc


def present(prompt):
    card = prompt["interaction"]
    waiting = load_pending_interaction()
    if waiting and waiting.get("interaction_id") != card["interaction_id"]:
        raise CollectionError("interaction_already_pending", "Resolve the existing interaction first")
    if not waiting:
        save_pending_interaction(card, stage="linkedin_radius", context=prompt["context"])
    return {"ok": False, "error": "interaction_required", "requires_user_action": True,
            "request_preserved": True, "no_charge": True, "billing_status": "not_charged",
            "interaction": card, "host_presentations": build_host_presentations(card), "user_prompt": card["fallback_text"],
            "next_suggested": f"jobagent interaction respond --interaction-id {shlex.quote(card['interaction_id'])} --radius-id <option-id>"}


def request(plan, details):
    context = locations._context(plan)
    checkpoint = locations._checkpoint(plan)
    index = details.get("query_index")
    if type(index) is not int or not 0 <= index < len(plan["queries"]):
        raise CollectionError("linkedin_radius_evidence_invalid", "The native radius must identify one signed query")
    query = plan["queries"][index]
    target_key = query["target_key"]
    expected = plan.get("radius_resolutions", {}).get(target_key, {}).get("radius_km", query["location"].get("radius_km"))
    if expected is None or details.get("requested_radius_km") != expected:
        raise CollectionError("linkedin_radius_evidence_invalid", "The requested radius changed")
    native, choices = native_choices(details["native_range"], expected)
    key = {**context, "target_key": target_key, "native_range": native, "choices": choices}
    ident = PREFIX + protocol.digest_payload(key)[7:31]
    active = state.load_json(state.current_round_path()) or {}
    title, question, pause = COPY.get(active.get("interaction_language"), COPY["en"])
    question = question.format(target=query["location"]["label"], radius=expected)
    options = [{"option_id": key, "label": f"{value:g} {native['unit']}"} for key, value in choices.items()]
    options.append({"option_id": "pause_platform", "label": pause})
    card = build_interaction_required(interaction_id=ident, product_id="job_agent", kind="linkedin_radius", title=title,
        prompt=question, fields=[{"field_id": "radius", "type": "single", "label": title, "required": True,
                                 "default_option_ids": [], "allow_other": False, "options": options}],
        fallback_text=question + "\n" + "\n".join(f"{o['label']} ({o['option_id']})" for o in options),
        continuation_action="jobagent.interaction.respond", idempotency_key=ident)
    progress = deepcopy(checkpoint["progress"])
    prompt = {**key, "context": context, "interaction": card}
    progress["radius_prompt"] = prompt
    locations._save(checkpoint, progress)
    return present(prompt)


def respond(args):
    ident = str(getattr(args, "interaction_id", "") or "")
    if not ident.startswith(PREFIX):
        return None
    if any(getattr(args, k, None) for k in ("resume_id", "attachment_id", "target_role", "target_city", "target_location", "target_locations", "exclude_index", "location_id")):
        return {"ok": False, "error": "invalid_interaction_response"}
    checkpoint = load_collection_checkpoint("linkedin")
    if not checkpoint:
        return {"ok": False, "error": "interaction_not_pending", "request_preserved": True}
    old, progress = checkpoint["plan"], checkpoint["progress"]
    context = locations._context(old)
    choice = str(getattr(args, "radius_id", "") or getattr(args, "choice", "") or "")
    previous = progress.get("radius_answers", {}).get(ident)
    result = {"ok": True, "event": "linkedin_radius_confirmed", "request_preserved": True,
              "no_charge": True, "next_suggested": "jobagent linkedin discover"}
    if previous:
        if previous["choice"] != choice:
            return {"ok": False, "error": "interaction_response_conflict", "request_preserved": True}
        waiting = load_pending_interaction() or {}
        if waiting.get("interaction_id") == ident:
            clear_pending_interaction()
        return {**result, "idempotent_replay": True}
    prompt = progress.get("radius_prompt") or {}
    if prompt.get("context") != context or prompt.get("interaction", {}).get("interaction_id") != ident:
        return {"ok": False, "error": "interaction_not_pending", "request_preserved": True}
    if choice == "pause_platform":
        return {**present(prompt), "event": "linkedin_search_paused"}
    if choice not in prompt["choices"]:
        return {"ok": False, "error": "invalid_interaction_response", "request_preserved": True}
    from jobagent.application.bulk_decision import request as transport
    response = transport("POST", old["discover_id"], "linkedin-radius", {
        "request_id": ident, "expected_scope_digest": scope_digest(old), "target_key": prompt["target_key"],
        "native_range": prompt["native_range"], "native_value": prompt["choices"][choice], "confirm_scope_change": True})
    plan = response["plan"]
    protocol.verify_signed_payload(plan, public_key=protocol.DECISION_SIGNING_PUBLIC_KEY, expected_type="search_plan")
    allowed = {"signature", "key_id", "signature_algorithm", "issued_at", "expires_at", "plan_revision", "radius_resolutions"}
    if {k: v for k, v in old.items() if k not in allowed} != {k: v for k, v in plan.items() if k not in allowed}:
        raise protocol.ProtocolError("Radius revision changed another search condition")
    resolutions = deepcopy(old.get("radius_resolutions", {}))
    value = prompt["choices"][choice]
    resolutions[prompt["target_key"]] = {"native_range": prompt["native_range"], "native_value": value,
                                       "radius_km": round(value * FACTORS[prompt["native_range"]["unit"]], 6)}
    if plan.get("radius_resolutions") != resolutions:
        raise protocol.ProtocolError("Signed radius differs from the user's choice")
    # Persist the replaced evidence before replacing the active checkpoint.
    archive = state.STATE_DIR / "archive" / "linkedin-radius" / (protocol.digest_payload(checkpoint)[7:] + ".json")
    state.save_json(archive, checkpoint)
    fresh = {k: deepcopy(progress[k]) for k in ("location_answers", "location_resolutions", "radius_answers") if k in progress}
    fresh.update(candidates=[], completed_pages=[], exhausted_queries=[], elapsed_seconds=0)
    fresh.setdefault("radius_answers", {})[ident] = {"choice": choice}
    save_collection_checkpoint("linkedin", request_id=old["request_id"], plan=plan, progress=fresh)
    clear_pending_interaction()
    return {**result, "search_radius": resolutions[prompt["target_key"]]}
