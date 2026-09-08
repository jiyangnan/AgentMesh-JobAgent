"""Signed discovery handed to the host's visible UI, without a browser driver.

``validate_page`` runs before the work ledger accepts a receipt. ``accept_page``
runs afterwards and may be replayed after a crash. The ledger owns work closure;
this module owns the account-bound, signed-plan collection checkpoint.
"""

from __future__ import annotations

import copy
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

from jobagent.application import discover as existing
from jobagent.infra import discovery_state as storage
from jobagent.infra.protocol import (
    ProtocolError,
    SearchPlanExpiredError,
    canonical_candidates,
    digest_payload,
    verify_search_plan,
    verify_stored_decision,
)
from jobagent.platforms.discovery import CollectionError, _restore_progress

_ENTRY_URLS = {
    "boss": "https://www.zhipin.com/",
    "liepin": "https://www.liepin.com/",
    "zhilian": "https://www.zhaopin.com/",
    "51job": "https://we.51job.com/",
}
_PAGE_HOSTS = {
    "boss": {"www.zhipin.com"},
    "liepin": {"www.liepin.com"},
    "zhilian": {"www.zhaopin.com", "sou.zhaopin.com"},
    "51job": {"we.51job.com", "search.51job.com", "jobs.51job.com"},
}
_CITY_SOURCES = {"city_control", "page_title", "page_metadata", "result_heading", "job_locations"}
_QUERY_SOURCES = {"search_input", "result_heading", "url_query", "search_history"}


def _fail(code: str, message: str, *, platform: str = "", **details: Any) -> None:
    raise CollectionError(
        code, message,
        user_prompt="页面或保存的采集证据尚不能安全确认，已保留原请求与断点。请按下一步核验，不要重复投递、重建轮次或删除状态。",
        details={"retryable": False, "requires_user_action": True,
                 "request_preserved": True, "no_charge": True,
                 "billing_status": "not_charged",
                 "next_suggested": f"jobagent browser diagnose --platform {platform}" if platform else "jobagent work status",
                 **details},
    )


def _official_url(url: Any, hosts: set[str], *, platform: str) -> Any:
    if not isinstance(url, str) or not url or any(ord(c) < 32 for c in url):
        _fail("native_page_url_invalid", "A complete observed official URL is required", platform=platform)
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.hostname in hosts
                 and parsed.port in {None, 443} and not parsed.username and not parsed.password)
    except ValueError:
        valid = False
    if not valid:
        _fail("native_page_domain_mismatch", "Observed URL is not on the permitted official host", platform=platform)
    return parsed


def validate_job_url(platform: str, url: str, job_id: str) -> str:
    """Validate an observed exact job route; never manufacture a search URL/ID.

    Returns the observed route with irrelevant tracking query/fragment removed.
    IDs may be opaque strings; a route must still contain that exact identifier.
    """
    if platform not in _ENTRY_URLS or not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", job_id):
        _fail("native_candidate_id_invalid", "Candidate needs an exact platform job ID", platform=platform)
    hosts = {
        "boss": {"www.zhipin.com"}, "liepin": {"www.liepin.com"},
        "zhilian": {"www.zhaopin.com", "jobs.zhaopin.com"},
        "51job": {"jobs.51job.com", "we.51job.com"},
    }[platform]
    parsed = _official_url(url, hosts, platform=platform)
    identifier = re.escape(job_id)
    query = ""
    if platform == "boss":
        valid = re.fullmatch(rf"/job_detail/{identifier}\.html", parsed.path)
    elif platform == "liepin":
        valid = re.fullmatch(rf"/(?:job|a)/{identifier}\.shtml", parsed.path)
    elif platform == "zhilian":
        pattern = rf"/jobdetail/{identifier}\.htm" if parsed.hostname == "www.zhaopin.com" else rf"/{identifier}\.htm"
        valid = re.fullmatch(pattern, parsed.path)
    elif parsed.hostname == "jobs.51job.com":
        valid = re.fullmatch(rf"/[A-Za-z0-9_-]+/{identifier}\.html", parsed.path)
    else:
        params = parse_qs(parsed.query)
        valid = parsed.path == "/pc/jobdetail" and params.get("jobId") == [job_id]
        query = f"jobId={job_id}"
    if not valid:
        _fail("native_candidate_route_mismatch", "Candidate URL does not identify the exact observed job", platform=platform)
    return urlunsplit(("https", str(parsed.hostname), parsed.path, query, ""))


def _candidate(platform: str, raw: Any, city: str | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        _fail("native_candidate_invalid", "Candidate must be an object", platform=platform)
    # Ignore arbitrary page text/instructions and ranking fields, never pass them
    # through to the cloud as decision metadata.
    item = canonical_candidates([raw])[0]
    identifier = str(item.get("id") or raw.get("job_id") or raw.get("jobId") or "").strip()
    for alias in ("id", "job_id", "jobId"):
        if raw.get(alias) is not None and str(raw[alias]).strip() != identifier:
            _fail("native_candidate_id_conflict", "Candidate ID aliases disagree", platform=platform)
    item["id"] = identifier
    item["url"] = validate_job_url(platform, item.get("url"), identifier)
    for field in ("title", "company", "area"):
        if not isinstance(item.get(field), str) or not item[field].strip():
            _fail("native_candidate_fields_missing", f"Candidate needs observed {field}", platform=platform)
        item[field] = item[field].strip()
    if city and not _same_city(item["area"].split("·")[0].split("-")[0].strip(), city):
        _fail("native_candidate_city_mismatch", "Candidate is outside the verified query city", platform=platform)
    for field, value in item.items():
        if field == "skills":
            if not isinstance(value, list) or len(value) > 100 or not all(isinstance(v, str) and len(v) <= 200 for v in value):
                _fail("native_candidate_invalid", "Candidate skills are invalid", platform=platform)
        elif not isinstance(value, str) or len(value) > (20000 if field == "jd" else 2000):
            _fail("native_candidate_invalid", "Candidate field type or length is invalid", platform=platform)
    # security_id is optional, but retain it if genuinely observed. Native UI
    # never reads hidden browser state just to manufacture this legacy field.
    return item


def _same_city(left: str, right: str) -> bool:
    return left.strip().removesuffix("市") == right.strip().removesuffix("市")


def _context(platform: str, session_id: str) -> tuple[dict, dict, dict]:
    if platform not in _ENTRY_URLS or not isinstance(session_id, str) or not session_id:
        _fail("native_discovery_context_invalid", "Platform and host session are required", platform=platform)
    profile = existing.load_json(existing.profile_path())
    if not profile:
        raise ValueError("No resume profile found. Run `jobagent resume analyze --file <resume>` first.")
    existing.require_compatible_profile(profile)
    active = existing.rounds.ensure_current_round()
    existing.rounds.assert_platform_turn(platform)
    session = active.get("native_session") or {}
    if session.get("id") != session_id:
        _fail("native_session_mismatch", "Host session does not match the current round", platform=platform)
    context = existing._start_context(platform, profile=profile, active_round=active, round_intent=active.get("intent"))
    if not context.get("account_ref"):
        _fail("native_account_binding_required", "A verified account binding is required", platform=platform)
    if (session.get("account_ref") != context["account_ref"]
            or session.get("round_id") != context["round_id"]):
        _fail("native_session_mismatch", "Host session account or round binding does not match", platform=platform)
    return profile, active, context


def _binding(context: dict, session_id: str, request_id: str, plan: dict) -> dict:
    return {"account_ref": context["account_ref"], "round_id": context["round_id"],
            "platform": context["platform"], "session_id": session_id,
            "request_id": request_id, "discover_id": str(plan["discover_id"]),
            "plan_digest": storage.collection_plan_digest(plan)}


def _progress(platform: str, checkpoint: dict, session_id: str) -> dict:
    plan = checkpoint["plan"]
    saved = copy.deepcopy(checkpoint["progress"])
    candidates, pages, exhausted = _restore_progress(saved, queries=plan["queries"], candidate_limit=min(100, int(plan["candidate_limit"])))
    candidates = [_candidate(platform, item) for item in candidates]
    native = saved.get("native")
    if native is None:
        native = {"schema_version": 1, "session_id": session_id, "receipts": {}}
    if (not isinstance(native, dict) or native.get("schema_version") != 1
            or native.get("session_id") != session_id or not isinstance(native.get("receipts"), dict)):
        _fail("native_checkpoint_session_mismatch", "Saved native checkpoint has a different or invalid host session", platform=platform)
    for key, receipt in native["receipts"].items():
        if (not isinstance(key, str) or not isinstance(receipt, dict)
                or not isinstance(receipt.get("digest"), str)
                or tuple(receipt.get("page", [])) not in pages):
            _fail("native_checkpoint_invalid", "Saved native receipt is invalid", platform=platform)
    return {"candidates": candidates, "completed_pages": [list(p) for p in sorted(pages)],
            "exhausted_queries": sorted(exhausted), "native": native}


def _verify_checkpoint(platform: str, *, profile: dict, active: dict, context: dict,
                       session_id: str, renew: bool) -> tuple[dict, dict, dict]:
    pending = storage.load_pending_start(platform)
    if not pending or any(pending.get(k) != context.get(k) for k in ("account_ref", "round_id", "profile_digest", "intent_digest")):
        _fail("native_discovery_context_mismatch", "Saved discovery is not bound to the current account and round", platform=platform)
    checkpoint = storage.load_collection_checkpoint(platform)
    if checkpoint is None:
        _fail("native_checkpoint_missing", "Saved signed SearchPlan is missing", platform=platform)
    plan = checkpoint["plan"]
    kwargs = dict(platform=platform, profile=profile, round_intent=active.get("intent"), request_id=pending["request_id"], require_request_id=True)
    try:
        verified = verify_search_plan(plan, **kwargs)
    except SearchPlanExpiredError as exc:
        if not renew:
            _fail("native_search_plan_expired", "Renew the preserved SearchPlan before accepting another page", platform=platform,
                  next_suggested=f"jobagent {platform} discover", requires_user_action=False, retryable=True)
        plan = existing._renew_expired_plan(platform, expired_plan=exc.signed_plan, profile=profile,
                    round_intent=active.get("intent"), request_id=pending["request_id"])
        verified = verify_search_plan(plan, **kwargs)
        if storage.collection_plan_digest(plan) != checkpoint["plan_digest"]:
            _fail("collection_checkpoint_plan_mismatch", "Renewal changed the saved collection scope", platform=platform)
    if any(not str(query.get("city") or "").strip() for query in verified["queries"]):
        _fail("native_target_city_required", "Every native query requires a signed readable target city", platform=platform)
    progress = _progress(platform, checkpoint, session_id)
    binding = _binding(context, session_id, pending["request_id"], plan)
    if renew and (plan != checkpoint["plan"] or progress != checkpoint["progress"]):
        storage.save_collection_checkpoint(platform, request_id=pending["request_id"], plan=plan, progress=progress)
    return plan, progress, binding


def _next_page(plan: dict, progress: dict) -> tuple[int, int] | None:
    if len(progress["candidates"]) >= min(100, int(plan["candidate_limit"])):
        return None
    completed = {tuple(p) for p in progress["completed_pages"]}
    for page in range(1, max(int(q["page_limit"]) for q in plan["queries"]) + 1):
        for index, query in enumerate(plan["queries"]):
            if index not in progress["exhausted_queries"] and page <= int(query["page_limit"]) and (index, page) not in completed:
                return index, page
    return None


def _task(plan: dict, progress: dict, index: int, page: int) -> dict:
    query = plan["queries"][index]
    example = {
        "receipt_id": "<new unique receipt ID>", "nonce": "<work nonce>", "binding": "<copy complete work.binding>",
        "outcome": "page_collected", "candidates": [{
            "id": "<exact observed platform job ID>", "title": "<observed job title>",
            "company": "<observed company>", "area": "<observed location in the requested city>",
            "url": "<exact observed official detail URL identifying this job ID>"}],
        "evidence": {"source": "host_ui_observation", "page_url": "<observed official search URL>",
            "observed_at": "<current ISO-8601 timestamp with timezone>",
            "observation": "<visible search and result state observed in the bound session>",
            "window_reference": "<bound session window_reference>",
            "profile_label": "<bound session profile_label>",
            "account_label": "<bound platform account_label>",
            "query": query["keyword"], "city": query["city"], "query_index": index, "page": page,
            "page_state": "results", "search_transition_observed": True,
            "query_evidence": [{"source": "search_input", "value": query["keyword"], "text": "<exact readback>"},
                               {"source": "result_heading", "value": query["keyword"], "text": "<independent query evidence>"}],
            "city_evidence": [{"source": "city_control", "value": query["city"], "text": "<visible city>"},
                              {"source": "page_title", "value": query["city"], "text": "<independent city evidence>"}],
            "has_next_page": True, "exhaustion": None},
    }
    last_page = copy.deepcopy(example)
    last_page["evidence"].update(has_next_page=False,
        exhaustion={"kind": "last_page", "text": "<actual visible final-page evidence>"})
    no_results = copy.deepcopy(example)
    no_results["candidates"] = []
    no_results["evidence"].update(page_state="no_results", has_next_page=False,
        exhaustion={"kind": "explicit_no_results", "text": "<actual visible explicit no-results notice>"})
    candidate_fields = ["id", "title", "company", "area", "salary", "experience", "degree", "skills",
                        "company_size", "industry", "finance_stage", "boss_name", "boss_title", "url", "security_id", "jd"]
    candidate_properties = {field: {"type": "string", "maxLength": 20000 if field == "jd" else 2000}
                            for field in candidate_fields if field != "skills"}
    candidate_properties["skills"] = {"type": "array", "maxItems": 100,
        "items": {"type": "string", "maxLength": 200}}
    candidate_properties["id"].update(pattern="^[A-Za-z0-9_-]{1,160}$")
    for field in ("id", "title", "company", "area", "url"):
        candidate_properties[field]["minLength"] = 1
    return {"query": query["keyword"], "city": query["city"], "query_index": index,
            "page": page, "page_limit": int(query["page_limit"]),
            "candidate_limit": min(100, int(plan["candidate_limit"])) - len(progress["candidates"]),
            "official_entry_url": _ENTRY_URLS[plan["platform"]],
            "allowed_actions": ["reuse_bound_tab", "visible_city_selection", "visible_query_input_and_readback", "visible_search", "visible_pagination", "read_job_cards_and_details"],
            "forbidden_actions": ["CDP", "page_script", "hidden_api", "invent_city_code", "apply", "send_message", "upload_resume", "solve_verification"],
            "required_evidence": ["official_search_page", "search_input_and_independent_query", "two_independent_readable_city_sources", "verified_result_state", "exact_observed_job_id_and_detail_url", "explicit_no_results_or_last_page_to_retire_query"],
            "result_schema": {"type": "object", "required": ["receipt_id", "nonce", "binding", "outcome", "evidence", "candidates"],
                "evidence_required": ["source", "observed_at", "observation", "window_reference", "profile_label", "account_label", "page_url", "query", "city", "query_index", "page", "page_state", "search_transition_observed", "query_evidence", "city_evidence", "has_next_page"],
                "evidence": {
                    "query": {"type": "string", "const": query["keyword"]},
                    "city": {"type": "string", "const": query["city"]},
                    "query_index": {"type": "integer", "const": index}, "page": {"type": "integer", "const": page},
                    "page_state": {"type": "string", "enum": ["results", "no_results"]},
                    "search_transition_observed": {"type": "boolean", "const": True},
                    "query_evidence": {"type": "array", "minItems": 2, "distinct_source_minimum": 2,
                        "required_source": "search_input", "items": {"type": "object", "required": ["source", "value", "text"],
                            "properties": {"source": {"enum": sorted(_QUERY_SOURCES)},
                                "value": {"type": "string", "description": "Exact readable signed query"},
                                "text": {"type": "string", "minLength": 1}}}},
                    "city_evidence": {"type": "array", "minItems": 2, "distinct_source_minimum": 2,
                        "items": {"type": "object", "required": ["source", "value", "text"],
                            "properties": {"source": {"enum": sorted(_CITY_SOURCES)},
                                "value": {"type": "string", "description": "Exact readable signed city; optional trailing 市"},
                                "text": {"type": "string", "minLength": 1}}}},
                    "has_next_page": {"type": "boolean"},
                    "exhaustion": {"type": ["object", "null"], "required_when_no_next_page": ["kind", "text"],
                        "properties": {"kind": {"enum": ["explicit_no_results", "last_page"]},
                            "text": {"type": "string", "minLength": 1}}}},
                "candidate_fields": candidate_fields,
                "candidate_required": ["id", "title", "company", "area", "url"],
                "candidate_properties": candidate_properties,
                "candidate_rules": "Use observed strings and a list of strings for skills; omit unobserved optional fields, never use null. Required fields must be non-empty after trimming. IDs must be unique on the page, the official detail URL must identify that exact ID, and area must agree with the signed city. Examples contain placeholders, not candidate evidence.",
                "state_combinations": {
                    "results": {"page_state": "results", "candidate_min_items": 1, "has_next_page": True, "exhaustion": None},
                    "last_page": {"page_state": "results", "candidate_min_items": 1, "has_next_page": False,
                        "exhaustion_kind": "last_page", "exhaustion_text_required": True},
                    "no_results": {"page_state": "no_results", "candidate_max_items": 0, "has_next_page": False,
                        "exhaustion_kind": "explicit_no_results", "exhaustion_text_required": True}},
                "combination_rules": {
                    "results": "page_state=results, at least one actual candidate, has_next_page=true, exhaustion=null.",
                    "last_page": "page_state=results, at least one actual candidate, has_next_page=false, exhaustion.kind=last_page with actual visible evidence.",
                    "no_results": "page_state=no_results, candidates=[], has_next_page=false, exhaustion.kind=explicit_no_results with actual visible notice.",
                    "signed_limit": "page_limit is only an upper bound, not final-page evidence. Report the actual next-page state; the CLI enforces the signed bound.",
                    "inconclusive": "Missing cards, loading, missing required identity or incomplete evidence is not exhaustion; use the declared pause branch without inventing success."},
                "exhaustion": {"kind": "explicit_no_results | last_page", "text": "non-empty visible evidence"},
                "empty_candidates": "Requires explicit_no_results and has_next_page=false; an empty parser result is not exhaustion."},
            "result_example": example,
            "result_examples": {"results": copy.deepcopy(example), "last_page": last_page, "no_results": no_results}}


def _response(work: dict) -> dict:
    return {"ok": True, "event": "browser_work_required", "platform": work["binding"]["platform"],
            "request_preserved": True, "no_charge": True, "billing_status": "not_charged",
            "work": work, "next_suggested": f"jobagent work begin --work-id {work['work_id']}"}


def _completed_result(platform: str, active: dict) -> dict | None:
    state = (active.get("platforms") or {}).get(platform) or {}
    discover_id = (state.get("evidence") or {}).get("discover_id")
    if state.get("status") not in {"discovered", "reviewed", "awaiting_delivery_confirmation", "delivery_authorized"} or not discover_id:
        return None
    path = storage.discovery_path(platform, str(discover_id))
    envelope = existing.load_json(path)
    if not envelope or envelope.get("discover_id") != discover_id:
        _fail("native_decision_missing", "The completed discovery decision is missing; no new request was started", platform=platform,
              no_charge=False, billing_status="previous_decision")
    manifest = verify_stored_decision(envelope["manifest"], platform=platform, allow_expired=True)
    intent = active.get("intent")
    if intent and intent.get("status") == "confirmed" and manifest.get("intent_digest") != digest_payload(intent):
        raise ProtocolError("decision intent digest mismatch")
    return {"ok": True, "platform": platform, "discover_id": discover_id, "resumed": True,
            "decision_file": str(path), "deduplicated_count": manifest["deduplicated_count"],
            "selected": len(manifest.get("selected", [])), "review": len(manifest.get("review", [])),
            "rejected": len(manifest.get("rejected", [])),
            "next_suggested": f"jobagent boss greet preview --input {path}" if platform == "boss" else f"jobagent {platform} apply review --input {path}"}


def _advance(platform: str, session_id: str, plan: dict, progress: dict, binding: dict,
             profile: dict, active: dict) -> dict:
    from jobagent.infra import browser_work

    page = _next_page(plan, progress)
    if page is not None:
        task = _task(plan, progress, *page)
        return _response(browser_work.ensure_work(action="collect_search_page", task=task, binding=binding, side_effect=False,
                          key=f"collect:{page[0]}:{page[1]}"))
    if not progress["candidates"]:
        return {"ok": False, "error": "no_candidates", "platform": platform,
                "request_id": binding["request_id"], "discover_id": binding["discover_id"],
                "candidate_count": 0, "search_exhausted": True, "request_preserved": True,
                "collection_progress_preserved": True, "no_charge": True, "billing_status": "not_charged",
                "retryable": False, "requires_user_action": True,
                "user_prompt": f"已完成本平台签名搜索计划，没有找到可审阅岗位。请确认是否跳过 {platform}，继续下一平台。",
                "next_suggested": f"jobagent round skip --platform {platform} --confirm-skip"}
    storage.save_pending_decision(platform, plan=plan, jobs=progress["candidates"], request_id=binding["request_id"])
    return _resume_decision(platform, session_id, profile, active)


def _resume_decision(platform: str, session_id: str, profile: dict, active: dict) -> dict | None:
    """Reuse the cloud decision while preserving even terminal/unclear failures.

    The legacy retry wrapper may discard ``discover_failed_start_new``. Native
    receipts must never silently replace an already collected signed request.
    """
    pending = storage.load_pending_decision(platform)
    if pending is None:
        return None
    request_id = str(pending.get("request_id") or "")
    if not request_id:
        _fail("native_pending_request_binding_missing", "The pending decision has no request binding", platform=platform,
              no_charge=False, billing_status="response_pending_reconciliation")
    plan = pending["plan"]
    jobs = [_candidate(platform, item) for item in pending["jobs"]]
    checkpoint = storage.load_collection_checkpoint(platform)
    if checkpoint is not None:
        context = existing._start_context(platform, profile=profile, active_round=active, round_intent=active.get("intent"))
        saved_plan, progress, binding = _verify_checkpoint(platform, profile=profile, active=active,
            context=context, session_id=session_id, renew=True)
        if (binding["request_id"] != request_id or binding["discover_id"] != pending["discover_id"]
                or storage.collection_plan_digest(saved_plan) != storage.collection_plan_digest(plan)
                or jobs != progress["candidates"]):
            _fail("native_pending_decision_mismatch", "The pending decision differs from the preserved collection", platform=platform,
                  no_charge=False, billing_status="response_pending_reconciliation")
        plan = saved_plan
    kwargs = dict(platform=platform, profile=profile, round_intent=active.get("intent"),
                  request_id=request_id, require_request_id=True)
    try:
        verified = verify_search_plan(plan, **kwargs)
    except SearchPlanExpiredError as exc:
        plan = existing._renew_expired_plan(platform, expired_plan=exc.signed_plan, profile=profile,
                    round_intent=active.get("intent"), request_id=request_id)
        verified = verify_search_plan(plan, **kwargs)
        if storage.collection_plan_digest(plan) != storage.collection_plan_digest(pending["plan"]):
            _fail("collection_checkpoint_plan_mismatch", "Renewal changed the collected decision scope", platform=platform,
                  no_charge=False, billing_status="response_pending_reconciliation")
    if plan != pending["plan"] or jobs != pending["jobs"]:
        storage.save_pending_decision(platform, plan=plan, jobs=jobs, request_id=request_id)
    result = existing._decision_result(platform, plan=verified, candidates=jobs, resumed=True,
        profile=profile, round_intent=active.get("intent"), request_id=request_id)
    storage.clear_pending_start(platform, request_id=request_id)
    return result


def start_discovery(platform: str, session_id: str) -> dict[str, Any]:
    """Resume the current request or issue one bounded host-UI collection task."""
    from jobagent.infra import browser_work

    profile, active, context = _context(platform, session_id)
    completed = _completed_result(platform, active)
    if completed is not None:
        return completed
    resumed = _resume_decision(platform, session_id, profile, active)
    if resumed is not None:
        return resumed
    request_id = existing._preserved_request_id(context)
    if storage.load_collection_checkpoint(platform) is None:
        try:
            plan = existing.cloud_client.discovery_start(platform=platform, profile=profile,
                request_id=request_id, round_intent=active.get("intent"))
        except existing.cloud_client.CloudError as exc:
            exc.details.update({"request_preserved": True, "request_id": request_id,
                "no_charge": True, "billing_status": "not_charged", "next_suggested": f"jobagent {platform} discover"})
            raise
        try:
            verify_search_plan(plan, platform=platform, profile=profile, round_intent=active.get("intent"), request_id=request_id, require_request_id=True)
        except SearchPlanExpiredError as exc:
            original = plan
            plan = existing._renew_expired_plan(platform, expired_plan=exc.signed_plan, profile=profile,
                round_intent=active.get("intent"), request_id=request_id)
            verify_search_plan(plan, platform=platform, profile=profile, round_intent=active.get("intent"), request_id=request_id, require_request_id=True)
            if storage.collection_plan_digest(plan) != storage.collection_plan_digest(original):
                _fail("collection_checkpoint_plan_mismatch", "Renewal changed the initial signed scope", platform=platform)
        storage.save_collection_checkpoint(platform, request_id=request_id, plan=plan,
            progress={"candidates": [], "completed_pages": [], "exhausted_queries": [],
                      "native": {"schema_version": 1, "session_id": session_id, "receipts": {}}})
    plan, progress, binding = _verify_checkpoint(platform, profile=profile, active=active, context=context, session_id=session_id, renew=True)
    # A process may die after ledger submission but before checkpointing. Replay
    # only an already-closed exact-binding work, never repeat its browser action.
    for work in browser_work.list_work(binding):
        if work.get("action") == "collect_search_page" and work.get("state") == "closed" and work.get("result") and work["work_id"] not in progress["native"]["receipts"]:
            return accept_page(work, work["result"])
    return _advance(platform, session_id, plan, progress, binding, profile, active)


def _source_evidence(items: Any, value: str, sources: set[str], *, city: bool, platform: str) -> None:
    if not isinstance(items, list) or len(items) < 2:
        _fail("native_search_evidence_missing", "Two independent readable evidence sources are required", platform=platform)
    seen = set()
    for item in items:
        if (not isinstance(item, dict) or item.get("source") not in sources
                or not isinstance(item.get("value"), str) or not isinstance(item.get("text"), str) or not item["text"].strip()):
            _fail("native_search_evidence_invalid", "Search evidence source or text is invalid", platform=platform)
        matches = _same_city(item["value"], value) if city else item["value"].strip() == value.strip()
        if not matches:
            _fail("native_city_evidence_mismatch" if city else "native_query_evidence_mismatch", "Readable search evidence conflicts with the signed query", platform=platform)
        seen.add(item["source"])
    if len(seen) < 2 or (not city and "search_input" not in seen):
        _fail("native_search_evidence_missing", "Evidence sources are not independent", platform=platform)


def validate_page(work: dict, result: dict) -> dict[str, Any]:
    """Pure validation before ledger submission; never renew or change state."""
    binding = work.get("binding") or {}
    platform, session_id = binding.get("platform"), binding.get("session_id")
    profile, active, context = _context(platform, session_id)
    plan, progress, expected = _verify_checkpoint(platform, profile=profile, active=active, context=context, session_id=session_id, renew=False)
    if (binding != expected or work.get("action") != "collect_search_page" or not isinstance(result, dict)
            or result.get("binding") != expected or not isinstance(work.get("nonce"), str)
            or not work["nonce"].strip() or result.get("nonce") != work["nonce"]
            or not isinstance(result.get("receipt_id"), str) or not result["receipt_id"].strip()):
        _fail("native_page_binding_mismatch", "Page receipt does not match the issued work", platform=platform)
    receipt = progress["native"]["receipts"].get(work.get("work_id"))
    if receipt is not None:
        if receipt["digest"] != digest_payload(result):
            _fail("native_page_receipt_conflict", "A completed page has a different receipt", platform=platform)
        return {"duplicate": True}
    page = _next_page(plan, progress)
    task = work.get("task") or {}
    if page is None or (task.get("query_index"), task.get("page")) != page:
        _fail("native_page_out_of_order", "Receipt is not for the next signed query page", platform=platform)
    index, number = page
    query = plan["queries"][index]
    if any(task.get(k) != v for k, v in {"query": query["keyword"], "city": query["city"], "page_limit": int(query["page_limit"])}.items()):
        _fail("native_page_task_mismatch", "Work task differs from the saved signed query", platform=platform)
    if result.get("outcome") != "page_collected":
        _fail("native_page_state_unknown", "The page was not conclusively collected", platform=platform)
    evidence = result.get("evidence")
    if not isinstance(evidence, dict) or evidence.get("source") != "host_ui_observation":
        _fail("native_page_evidence_missing", "Host UI observation evidence is required", platform=platform)
    page_url = _official_url(evidence.get("page_url"), _PAGE_HOSTS[platform], platform=platform)
    if not page_url.path.strip("/"):
        _fail("native_search_route_unverified", "A platform homepage is not a verified search-results route", platform=platform)
    for field, value in {"query": query["keyword"], "city": query["city"], "query_index": index, "page": number}.items():
        if evidence.get(field) != value or (field in {"query_index", "page"} and type(evidence.get(field)) is not int):
            _fail("native_page_context_mismatch", "Observed page differs from the signed city, query or page", platform=platform)
    if evidence.get("page_state") not in {"results", "no_results"} or evidence.get("search_transition_observed") is not True:
        _fail("native_search_state_unverified", "A real search transition and result state are required", platform=platform)
    if evidence.get("login_required") or evidence.get("verification_required") or evidence.get("account_conflict"):
        _fail("native_page_intervention_required", "The page requires user intervention", platform=platform)
    _source_evidence(evidence.get("city_evidence"), query["city"], _CITY_SOURCES, city=True, platform=platform)
    _source_evidence(evidence.get("query_evidence"), query["keyword"], _QUERY_SOURCES, city=False, platform=platform)
    raw = result.get("candidates")
    remaining = min(100, int(plan["candidate_limit"])) - len(progress["candidates"])
    if not isinstance(raw, list) or len(raw) > remaining:
        _fail("native_candidate_limit_exceeded", "Candidate list exceeds the remaining signed limit", platform=platform)
    candidates = [_candidate(platform, item, query["city"]) for item in raw]
    if len({item["id"] for item in candidates}) != len(candidates):
        _fail("native_page_candidate_duplicate", "A page contains duplicate candidate IDs", platform=platform)
    has_next = evidence.get("has_next_page")
    exhaustion = evidence.get("exhaustion")
    if type(has_next) is not bool:
        _fail("native_pagination_evidence_missing", "Pagination state must be explicitly observed", platform=platform)
    if not has_next:
        if (not isinstance(exhaustion, dict) or exhaustion.get("kind") not in {"explicit_no_results", "last_page"}
                or not isinstance(exhaustion.get("text"), str) or not exhaustion["text"].strip()):
            _fail("native_exhaustion_unverified", "Explicit no-result or last-page evidence is required", platform=platform)
    elif exhaustion is not None:
        _fail("native_exhaustion_conflict", "A next page conflicts with exhaustion evidence", platform=platform)
    if not candidates:
        if has_next or evidence["page_state"] != "no_results" or exhaustion.get("kind") != "explicit_no_results":
            _fail("native_empty_page_unverified", "An empty candidate list is not proof of exhausted search", platform=platform)
    elif evidence["page_state"] != "results" or (exhaustion and exhaustion["kind"] == "explicit_no_results"):
        _fail("native_result_state_conflict", "Candidates conflict with the no-result evidence", platform=platform)
    return {"duplicate": False, "candidates": candidates, "page": [index, number], "exhausted": not has_next}


def accept_page(work: dict, result: dict) -> dict[str, Any]:
    """Checkpoint a ledger-closed page once, then return the next task/decision."""
    from jobagent.infra import browser_work

    binding = work.get("binding") or {}
    platform, session_id = binding.get("platform"), binding.get("session_id")
    profile, active, context = _context(platform, session_id)
    # Replaying the last closed work after decision completion must not buy a
    # replacement SearchPlan or call decide again.
    completed = _completed_result(platform, active)
    stored = browser_work.get_work(work.get("work_id"), binding)
    if (stored.get("state") != "closed" or stored.get("result") != result
            or stored.get("task") != work.get("task") or stored.get("binding") != binding):
        _fail("native_work_not_submitted", "Submit the exact validated page to the work ledger first", platform=platform)
    if completed is not None:
        if completed["discover_id"] != binding.get("discover_id"):
            _fail("native_page_binding_mismatch", "Closed page belongs to another completed discovery", platform=platform)
        return completed
    pending = storage.load_pending_decision(platform)
    if pending is not None:
        if pending.get("discover_id") != binding.get("discover_id") or pending.get("request_id") != binding.get("request_id"):
            _fail("native_page_binding_mismatch", "Closed page differs from the pending decision", platform=platform)
        return _resume_decision(platform, session_id, profile, active)
    normalized = validate_page(work, result)
    plan, progress, expected = _verify_checkpoint(platform, profile=profile, active=active, context=context, session_id=session_id, renew=False)
    if not normalized["duplicate"]:
        seen = {item["id"] for item in progress["candidates"]}
        progress["candidates"].extend(item for item in normalized["candidates"] if item["id"] not in seen)
        progress["completed_pages"].append(normalized["page"])
        progress["completed_pages"].sort()
        if normalized["exhausted"]:
            progress["exhausted_queries"] = sorted(set(progress["exhausted_queries"]) | {normalized["page"][0]})
        progress["native"]["receipts"][work["work_id"]] = {"digest": digest_payload(result), "page": normalized["page"]}
        storage.save_collection_checkpoint(platform, request_id=expected["request_id"], plan=plan, progress=progress)
    return _advance(platform, session_id, plan, progress, expected, profile, active)
