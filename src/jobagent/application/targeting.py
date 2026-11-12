"""First-use geography cards and an account-bound, server-signed routing plan.

This setup has no browser, model, resume-analysis or delivery side effects.
It uses the existing staged round request so restarts and account switches
preserve the original answers without changing the user's resume profile.
"""
from __future__ import annotations

import json
import shlex
import uuid
from pathlib import Path

from jobagent.application.round_resume_binding import load_pending_binding, save_pending_binding
from jobagent.domain.targeting import MARKETS, normalize_targets
from jobagent.infra import cloud_client, protocol, state
from jobagent.infra.account_state import current_account_ref
from jobagent.infra.interaction_protocol import build_host_presentations, build_interaction_required
from jobagent.infra.interaction_state import clear_pending_interaction, load_pending_interaction, save_pending_interaction

STAGES = {"target_region", "target_locations"}
COPY = {
    "fr": ("Lieu de travail souhaité", "Où souhaitez-vous travailler cette fois-ci ?", "Chine continentale",
           "Hors de Chine continentale", "Indiquez le pays, l’État ou la région, la ville ou l’agglomération souhaitée.",
           "Vous pouvez aussi nommer le lieu directement ou inclure les deux zones.", "Lieux souhaités"),
    "en": ("Target work location", "Where would you like to work this time?", "Mainland China",
           "Outside mainland China", "Choose a country, state/province, city or metro area.",
           "You can also name the location directly, or include both regions.", "Target locations"),
    "zh-CN": ("目标工作地区", "你这次想找哪里的工作？", "中国大陆", "中国大陆以外",
              "请填写目标国家、州/省、城市或都会区。", "也可以直接说具体地区；两类地区都考虑时可以一起填写。", "具体目标地区"),
    "ja": ("希望する勤務地", "今回はどこで働きたいですか？", "中国本土", "中国本土以外",
           "希望する国・州・地域・都市・都市圏を教えてください。", "具体的な地域を直接指定したり、両方の地域を含めることもできます。", "具体的な勤務地"),
    "ko": ("희망 근무 지역", "이번에는 어느 지역에서 일하고 싶으신가요?", "중국 본토", "중국 본토 외 지역",
           "희망 국가, 주·도, 도시 또는 광역권을 알려 주세요.", "지역을 직접 지정하거나 두 지역을 함께 선택할 수도 있습니다.", "구체적인 근무 지역"),
}


def request():
    return dict((load_pending_binding() or {}).get("round_request") or {})


def _save_request(value):
    staged = load_pending_binding() or {}
    save_pending_binding({**staged, "round_request": value})


def set_targets(targets, *, language=None):
    normalized = normalize_targets(targets)
    value = request()
    if language is not None:
        if language not in COPY:
            raise ValueError("Supported interaction languages: en, zh-CN, ja, ko, fr")
        value["interaction_language"] = language
    previous = value.get("targeting") or {}
    if previous != normalized:
        value.pop("target_plan", None)
        value["targeting"] = normalized
        if normalized["locations"]:
            value["target_cities"] = [item["label"] for item in normalized["locations"]
                                      if item["market"] == "mainland_china" and item["kind"] in {"city", "unspecified"}]
        elif previous.get("scope") and previous.get("scope") != normalized.get("scope"):
            value["target_cities"] = []
    _save_request(value)
    waiting = load_pending_interaction()
    if waiting and waiting.get("stage") in STAGES and (waiting.get("context") or {}).get("targets_digest") != protocol.digest_payload(normalized):
        clear_pending_interaction()
    return normalized


def from_args(args):
    explicit = any(getattr(args, key, None) for key in ("target_region", "target_location", "targeting_file"))
    active = state.load_json(state.current_round_path()) or {}
    if active.get("status") != "active" and request().get("targeting", {}).get("scope") in MARKETS:
        explicit = explicit or bool(getattr(args, "target_city", None) or getattr(args, "target_cities", None))
    if not explicit:
        if getattr(args, "language", None):
            value = request(); value["interaction_language"] = args.language; _save_request(value)
        return False
    if active.get("status") == "active":
        raise ValueError("Finish or resume the existing round before changing its target geography")
    path = getattr(args, "targeting_file", None)
    if path:
        if getattr(args, "target_region", None) or getattr(args, "target_location", None):
            raise ValueError("Use a targeting file or explicit targeting flags, not both")
        source = Path(path).expanduser()
        if source.stat().st_size > 65536:
            raise ValueError("Targeting input exceeds 64 KiB")
        targets = json.loads(source.read_text(encoding="utf-8"))
    else:
        targets = dict(request().get("targeting") or {})
        scope = getattr(args, "target_region", None) or targets.get("scope")
        if getattr(args, "target_region", None) and scope != targets.get("scope"):
            targets["locations"] = []
        if scope:
            targets["scope"] = scope
        labels = getattr(args, "target_location", None) or getattr(args, "target_city", None) or getattr(args, "target_cities", None)
        if labels:
            if not scope:
                value = request()
                value["target_location_labels"] = list(labels)
                _save_request(value)
                set_targets(targets, language=getattr(args, "language", None))
                return True
            if scope not in MARKETS:
                raise ValueError("Use structured locations to assign each target to its region")
            targets["locations"] = [{"label": label, "market": scope,
                                      "kind": "city" if scope == "mainland_china" else "unspecified"} for label in labels]
    set_targets(targets, language=getattr(args, "language", None))
    return True


def _present(pending):
    interaction = pending["interaction"]
    flag = "--choice <choice>" if pending["stage"] == "target_region" else '--target-location "<location>"'
    return {"ok": False, "error": "interaction_required", "requires_user_action": True,
            "interaction": interaction, "host_presentations": build_host_presentations(interaction),
            "user_prompt": interaction["fallback_text"], "request_preserved": True,
            "onboarding": {"stage": pending["stage"]},
            "next_suggested": f"jobagent interaction respond --interaction-id {shlex.quote(interaction['interaction_id'])} {flag}"}


def pending():
    value = load_pending_interaction()
    return _present(value) if value and value.get("stage") in STAGES else None


def _ask(stage, targets, continuation):
    active = state.load_json(state.current_round_path()) or {}
    value = request()
    if not value.get("target_setup_id"):
        value["target_setup_id"] = str(uuid.uuid4())
        _save_request(value)
    language = value.get("interaction_language", "en")
    text = COPY.get(language, COPY["en"])
    context = {"targets_digest": protocol.digest_payload(targets), "account_ref": current_account_ref(),
               "setup_id": value["target_setup_id"],
               "previous_round_id": active.get("round_id"), "continuation": continuation}
    ident = "jobagent:geography:" + protocol.digest_payload({**context, "stage": stage}).split(":")[1][:24]
    if stage == "target_region":
        fields = [{"field_id": "target_region", "type": "single", "label": text[0], "required": True,
                   "allow_other": True, "default_option_ids": [], "options": [
                       {"option_id": "mainland_china", "label": text[2], "description": "Boss / 猎聘 / 智联招聘 / 51Job"},
                       {"option_id": "outside_mainland_china", "label": text[3], "description": "LinkedIn · Easy Apply"}]}]
        prompt = text[1]
        fallback = f"{prompt}\n1. {text[2]} — Boss / 猎聘 / 智联招聘 / 51Job\n2. {text[3]} — LinkedIn · Easy Apply\n{text[5]}"
    else:
        fields = [{"field_id": "target_locations", "type": "text", "label": text[6], "required": True}]
        prompt = fallback = text[4]
    card = build_interaction_required(interaction_id=ident, product_id="job_agent", kind=stage,
        title=text[0] if stage == "target_region" else text[6], prompt=prompt, fields=fields,
        fallback_text=fallback, continuation_action="jobagent.interaction.respond", idempotency_key=ident)
    saved = save_pending_interaction(card, stage=stage, context=context)
    return _present(saved)


def verify_plan(plan, targets):
    signed = protocol.verify_signed_payload(plan, public_key=protocol.DECISION_SIGNING_PUBLIC_KEY,
                                             expected_type="target_region_plan")
    account = current_account_ref()
    expected = normalize_targets(targets)
    request_id = "targets_" + protocol.digest_payload({"account": account, "targets": expected}).split(":")[1][:32]
    if (not account or signed.get("account_ref") != account or signed.get("protocol_version") != 2
            or signed.get("targeting_version") != 1 or signed.get("authorization") != "planning_only"
            or signed.get("charged") is not False or signed.get("targets") != expected
            or signed.get("request_id") != request_id
            or signed.get("targets_digest") != protocol.digest_payload(expected)):
        raise ValueError("Target plan account, version or confirmed inputs do not match")
    route = signed.get("routing")
    if not isinstance(route, dict) or type(route.get("ready")) is not bool:
        raise ValueError("Invalid target route")
    order = route.get("platform_order")
    if (not isinstance(order, list) or any(not isinstance(item, str) for item in order)
            or len(set(order)) != len(order) or (route["ready"] and not order)):
        raise ValueError("Invalid platform queue in target plan")
    return dict(plan)


def _sync_workflow(targets):
    """Finish recording a user's target answer in their existing cloud intent."""
    path = state.STATE_DIR / "workflow.json"
    value = state.load_json(path) or {}
    cloud = value.get("cloud_workflow") or {}
    if not cloud or value.get("intent") is None:
        return
    if value.get("account_ref") != current_account_ref():
        raise ValueError("Target setup belongs to another account")
    if normalize_targets((value["intent"].get("targeting") or {})) == targets:
        return
    criteria = {**value["intent"], "targeting": targets,
                "target_cities": request().get("target_cities") or []}
    request_id = "target_answer_" + protocol.digest_payload({"session": cloud["session_id"], "criteria": criteria}).split(":")[1][:24]
    result = cloud_client.workflow_submit({"request_id": request_id, "session_id": cloud["session_id"],
                                           "expected_revision": cloud["revision"], "criteria": criteria})
    if result.get("ok") is not True:
        raise ValueError("The target answer could not be recorded in the existing workflow")
    if result["workflow"]["criteria"].get("targeting") != targets:
        raise ValueError("The server did not preserve the confirmed target geography")
    value.update(intent=result["workflow"]["criteria"], cloud_workflow=result["workflow"],
                 revision=value["revision"] + 1)
    state.save_json(path, value)


def ensure(*, continuation="jobagent round start"):
    active = state.load_json(state.current_round_path()) or {}
    if active.get("status") == "active":
        return None
    value = request()
    waiting = load_pending_interaction()
    if waiting and waiting.get("stage") in STAGES:
        return _present(waiting)
    # A pre-upgrade setup card is already an in-flight domestic task. Keep
    # its original binding and answer contract rather than replacing it.
    if (waiting and waiting.get("stage") in {"resume_binding", "cities", "choice", "roles"}
            and "targeting" not in value):
        return None
    from jobagent.application.content_language import ensure as ensure_language
    language_setup = ensure_language(continuation)
    if language_setup:
        return language_setup
    targets = normalize_targets(value.get("targeting") or {})
    if not targets.get("scope") and not targets["locations"]:
        return _ask("target_region", targets, continuation)
    markets = {item["market"] for item in targets["locations"]}
    if not targets["locations"] or (targets.get("scope") == "mixed" and len(markets) < 2):
        return _ask("target_locations", targets, continuation)
    account = current_account_ref()
    if not account:
        return {"ok": False, "error": "account_verification_required", "request_preserved": True,
                "next_suggested": "jobagent doctor env"}
    plan = value.get("target_plan")
    try:
        if plan is None:
            _sync_workflow(targets)
            request_id = "targets_" + protocol.digest_payload({"account": account, "targets": targets}).split(":")[1][:32]
            response = cloud_client.target_region_plan({"request_id": request_id, "targets": targets})
            if response.get("ok") is not True:
                return response
            plan = response["plan"]
        plan = verify_plan(plan, targets)
    except cloud_client.CloudError as exc:
        return {"ok": False, "error": exc.code or "target_plan_unavailable", "request_preserved": True,
                "retryable": bool(exc.retryable), "requires_technical_recovery": not exc.retryable,
                "message": "The target regions are saved; no platform or paid task has started.",
                "next_suggested": continuation}
    value["targeting"] = targets
    value["target_plan"] = plan
    _save_request(value)
    route = plan["routing"]
    if not route["ready"]:
        return _ask(route["required_input"], targets, continuation)
    if route.get("execution_blockers"):
        return {"ok": False, "error": "target_execution_unavailable", "request_preserved": True,
                "requires_technical_recovery": True, "automatic_retry_allowed": False,
                "target_routing": route,
                "message": "The requested regions are saved. Some search conditions are not yet supported; no search, application or charge has started."}
    from jobagent.platforms.registry import list_platforms
    available = {item.key for item in list_platforms() if item.status == "available"}
    unsupported = [item for item in route["platform_order"] if item not in available]
    if unsupported:
        return {"ok": False, "error": "target_platform_unavailable", "request_preserved": True,
                "requires_technical_recovery": True, "automatic_retry_allowed": False,
                "target_routing": route, "unsupported_platforms": unsupported,
                "message": "Target regions are saved. This client cannot yet execute all planned platforms; no search, application or charge has started."}
    if "linkedin" in route["platform_order"]:
        try:
            cloud_client.require_linkedin_capability()
        except cloud_client.CloudError as exc:
            return {"ok": False, "error": exc.code or "linkedin_unavailable", "request_preserved": True,
                    "automatic_retry_allowed": False, "target_routing": route, "no_charge": True,
                    "message": "Your target regions are saved. LinkedIn is not available on the connected service yet.",
                    "next_suggested": continuation}
    return None


def ready_plan():
    value = request()
    if not value.get("targeting"):
        active = state.load_json(state.current_round_path()) or {}
        if active.get("status") == "active" and active.get("target_plan"):
            return verify_plan(active["target_plan"], active["intent"]["targeting"])
        return None
    if not value.get("target_plan"):
        raise ValueError("A verified target plan is required before creating a new round")
    return verify_plan(value["target_plan"], value["targeting"])


def attach_intent(intent):
    plan = ready_plan()
    return {**intent, "targeting": plan["targets"]} if plan else intent


def analysis_continuation(*, consume=False):
    value = request()
    continuation = value.get("target_continuation")
    if not isinstance(continuation, str) or not continuation.startswith("jobagent resume analyze "):
        return None
    if consume:
        value.pop("target_continuation", None)
        _save_request(value)
    return continuation


def respond(args):
    ident = str(args.interaction_id or "")
    if not ident.startswith("jobagent:geography:"):
        if getattr(args, "target_location", None) or getattr(args, "target_locations", None):
            return {"ok": False, "error": "invalid_interaction_response"}
        return None
    value = request()
    raw = {"choice": args.choice, "labels": list(getattr(args, "target_location", None) or []),
           "locations": getattr(args, "target_locations", None)}
    if any(getattr(args, field, None) for field in ("target_role", "target_city", "resume_id", "attachment_id", "exclude_index")):
        return {"ok": False, "error": "invalid_interaction_response"}
    active = state.load_json(state.current_round_path()) or {}
    recorded = value.get("target_answers", {}).get(ident) or active.get("target_answers", {}).get(ident)
    if recorded:
        if recorded.get("account_ref") != current_account_ref():
            return {"ok": False, "error": "interaction_context_changed", "request_preserved": True}
        if recorded["answer_digest"] != protocol.digest_payload(raw):
            return {"ok": False, "error": "interaction_response_conflict", "request_preserved": True}
        return {**recorded["result"], "idempotent_replay": True}
    waiting = load_pending_interaction()
    if not waiting or waiting.get("interaction_id") != ident or waiting.get("stage") not in STAGES:
        return {"ok": False, "error": "interaction_not_pending"}
    context = waiting.get("context") or {}
    targets = normalize_targets(value.get("targeting") or {})
    if (context.get("targets_digest") != protocol.digest_payload(targets)
            or context.get("account_ref") != current_account_ref()):
        return {"ok": False, "error": "interaction_context_changed", "request_preserved": True}
    scope = raw["choice"] or targets.get("scope")
    if raw["choice"] and (waiting["stage"] != "target_region" or raw["choice"] not in MARKETS | {"mixed"}):
        return {"ok": False, "error": "invalid_interaction_response"}
    if waiting["stage"] == "target_region" and not raw["choice"] and not raw["locations"]:
        return {"ok": False, "error": "invalid_interaction_response"}
    if raw["locations"] is not None and raw["labels"]:
        return {"ok": False, "error": "invalid_interaction_response"}
    if scope:
        targets["scope"] = scope
    if raw["locations"] is not None:
        targets["locations"] = raw["locations"]
    elif raw["labels"]:
        if scope not in MARKETS:
            return {"ok": False, "error": "target_region_required", "request_preserved": True}
        targets["locations"] = [{"label": label, "market": scope,
                                  "kind": "city" if scope == "mainland_china" else "unspecified"} for label in raw["labels"]]
    elif waiting["stage"] == "target_locations":
        return {"ok": False, "error": "invalid_interaction_response"}
    elif scope in MARKETS and (value.get("target_location_labels") or value.get("target_cities")):
        targets["locations"] = [{"label": label, "market": scope,
                                  "kind": "city" if value.get("target_cities") else "unspecified"}
                                 for label in value.get("target_location_labels") or value["target_cities"]]
    normalized = set_targets(targets)
    continuation = context.get("continuation") or "jobagent round start"
    result = {"ok": True, "event": "targeting_answer_saved", "targeting": normalized,
              "request_preserved": True, "next_suggested": continuation}
    value = request()
    value["target_continuation"] = continuation
    answers = value.setdefault("target_answers", {})
    answers[ident] = {"account_ref": current_account_ref(), "answer_digest": protocol.digest_payload(raw), "result": result}
    value["target_answers"] = dict(list(answers.items())[-16:])
    value.pop("target_location_labels", None)
    _save_request(value)
    clear_pending_interaction()
    return result
