"""Account-bound CLI continuation journal.

Cloud commands, signed decisions and BrowserWork remain the business authorities.
This journal only dispatches their exact continuations and records local execution
intent before calling a handler. It never upgrades missing output into success.
"""
from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path

from jobagent.infra import state, rounds, cloud_client
from jobagent.infra.account_state import current_account_ref
from jobagent.infra.workflow_protocol import executable_command, with_contract


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def _load():
    account = current_account_ref()
    if not account:
        raise ValueError("An account-verified state is required")
    value = state.load_json(state.STATE_DIR / "workflow.json")
    if value is None:
        return {"schema_version": 2, "account_ref": account, "revision": 0,
                "operations": {}, "last_result": None, "intent": None}
    if value.get("schema_version") != 2 or value.get("account_ref") != account:
        raise ValueError("Workflow state is incompatible or belongs to another account")
    return value


def _save(value):
    state.save_json(state.STATE_DIR / "workflow.json", value)


def _snapshot():
    # Business revisions and pending cards supersede stale continuations, even
    # when a user invoked an older direct CLI entry instead of workflow advance.
    return _digest({name: state.load_json(state.STATE_DIR / name) for name in (
        "current_round.json", "pending_interaction.json", "pending_round_binding.json",
        "profile.json", "resume_freshness_baselines.json")})


def _read_only(argv):
    return argv[1:3] in (["doctor", "env"], ["doctor", "tls"], ["resume", "list"], ["resume", "status"],
                         ["round", "status"], ["work", "status"], ["credits", "status"], ["credits", "quote"])


def _record_result(value, args, result):
    value["revision"] += 1
    value["last_result"] = result
    value["snapshot"] = _snapshot()
    if args.command == "doctor":
        value["doctor"] = {"checked": True, "ready": (result.get("workflow") or {}).get("ready", False)}
    for operation in value["operations"].values():
        if operation["status"] == "ready":
            operation["status"] = "superseded"
        elif operation["status"] == "claimed" and (
            (_read_only(operation["argv"]) and operation["argv"][1] == args.command)
            or (operation["argv"][1] in {"work", "browser", "boss", "liepin", "zhilian", "51job"}
                and args.command == "work" and args.work_command in {"status", "next", "submit", "continue"}
                and (result.get("work", {}).get("work_id") or any(result.get("work_counts", {}).values())
                     or (args.work_command in {"submit", "continue"} and result.get("ok") is not False)))
        ):
            # A recovered BrowserWork presentation can close this local dispatch
            # uncertainty. It does not declare the external action successful;
            # the browser ledger still owns nonce/reconcile-only restrictions.
            operation.update(status="recovered", recovery_result=_safe_result(result))


def remember_result(args, result):
    if not current_account_ref():
        return
    if not (state.STATE_DIR / "workflow.json").exists() and not (args.command == "doctor" and result.get("local_state", {}).get("ready")):
        return
    value = _load()
    _record_result(value, args, result)
    _save(value)


def _safe_result(result):
    # An old output containing a nonce must never be presented as a new grant.
    if result.get("native_step_issued"):
        return {"ok": True, "event": "native_permission_already_issued",
                "request_preserved": True, "next_suggested": "jobagent work next"}
    return result


def _source(value):
    from jobagent.infra.interaction_state import load_pending_interaction
    from jobagent.infra.interaction_protocol import build_host_presentations
    from jobagent.application.delivery_followup import pending as after_cancel
    from jobagent.infra import browser_work
    from jobagent.application import native_work
    from jobagent.application.round_direction import pending as direction_pending
    active = state.load_json(state.current_round_path()) or {}
    if active.get("round_id"):
        works = browser_work.list_work({"account_ref": current_account_ref(), "round_id": active["round_id"]})
        pending_work = next((w for w in works if w["state"] != "closed"), None)
        if pending_work:
            return native_work.present(pending_work, execution=False)
    direction = direction_pending()
    if direction:
        return direction
    from jobagent.application.native_resume_choice import pending as resume_choice_pending
    pending = resume_choice_pending(recover_answered=True)
    if pending:
        return pending
    pending = after_cancel()
    if pending:
        return pending
    pending = load_pending_interaction()
    if pending and pending.get("stage") != "delivery_authorized":
        interaction = {"product_id": "job_agent", **pending["interaction"]}
        result = {"ok": False, "error": "interaction_required", "requires_user_action": True,
                  "interaction": interaction, "host_presentations": build_host_presentations(interaction),
                  "user_prompt": interaction["fallback_text"]}
        # A final delivery card must retain every row of the preview, not just
        # the interaction's short question.
        previous = value.get("last_result") or {}
        if pending.get("stage", "").startswith("delivery"):
            from jobagent.application.delivery_confirmation import resume_pending_confirmation
            restored = resume_pending_confirmation()
            if restored:
                return restored
        elif previous.get("interaction", {}).get("interaction_id") == interaction["interaction_id"]:
            result = {**previous, **result}
        return result
    intent = value.get("intent") or {}
    if active.get("status") == "active" and not active.get("round_criteria"):
        filters = {key: intent[key] for key in ("salary", "company") if isinstance(intent.get(key), dict)
                   and any(v is not None for k, v in intent[key].items() if k != "unknown_evidence")}
        if filters:
            import shlex
            path = state.STATE_DIR / "round_criteria_input.json"
            body = {"request_id": "initial_" + _digest([active["round_id"], filters])[:32], "patch": filters}
            if state.load_json(path) != body:
                state.save_json(path, body)
            return {"ok": True, "next_suggested": f"jobagent round update --input {shlex.quote(str(path))} --expected-revision 0"}
    if active.get("status") == "completed" and value.get("new_round_requested_after") == active.get("round_id"):
        return {"ok": True, "next_suggested": "jobagent round start"}
    previous = value.get("last_result")
    if previous and value.get("snapshot") == _snapshot() and previous.get("next_suggested") != "jobagent workflow next":
        return _safe_result(previous)
    workflow = rounds.round_status()
    if workflow.get("status") == "active" or workflow.get("workflow_complete"):
        return {"ok": True, "workflow": workflow, "next_suggested": workflow.get("next_suggested")}
    if not value.get("doctor", {}).get("checked") or not value["doctor"].get("ready"):
        return {"ok": True, "next_suggested": "jobagent doctor env"}
    if value.get("intent"):
        return {"ok": True, "next_suggested": "jobagent round start"}
    return {"ok": True, "event": "setup_ready", "scope": "setup",
            "message": "设置状态已读取。请告诉 Agent 要找的岗位和城市，再提交求职需求。"}


def next_action():
    value = _load()
    for operation in value["operations"].values():
        if operation["status"] == "claimed":
            recovery = operation["argv"] if _read_only(operation["argv"]) else ["jobagent", "work", "status"]
            return {"ok": False, "error": "workflow_result_unresolved", "request_preserved": True,
                    "operation_id": operation["action_id"], "operation": operation,
                    "action": {"type": "blocked", "reason": "original_command_result_missing",
                               "recovery_argv": recovery},
                    "next_suggested": shlex.join(recovery)}
    result = with_contract(_source(value))
    action = result["action"]
    if action["type"] == "run":
        argv = action["argv"]
        # Neither doctor success nor installation grants a job-search request.
        active = state.load_json(state.current_round_path()) or {}
        if argv[1:3] == ["round", "start"] and not value.get("intent") and active.get("status") != "active":
            return {"ok": True, "event": "setup_ready", "scope": "setup",
                    "action": {"type": "done", "scope": "setup"},
                    "message": "设置已就绪，请提供本轮岗位和城市以开始求职。"}
        if argv[1] == "workflow":
            # Followup cards are restored above. A bare self-continuation is
            # never allowed to create a recursive loop.
            return {"ok": False, "error": "workflow_continuation_invalid",
                    "action": {"type": "blocked", "reason": "recursive_continuation"}}
        snapshot = _snapshot()
        action_id = "local_" + _digest([value["account_ref"], value["revision"], snapshot, argv])[:32]
        operation = value["operations"].get(action_id)
        if operation is None:
            operation = {"action_id": action_id, "expected_revision": value["revision"],
                         "status": "ready", "argv": argv, "snapshot": snapshot}
            value["operations"][action_id] = operation
            _save(value)
        action = {"type": "run", "action_id": action_id, "business_argv": argv,
                  "argv": ["jobagent", "workflow", "advance", "--action-id", action_id,
                           "--expected-revision", str(operation["expected_revision"])]}
    return {**result, "protocol": "jobagent.workflow", "protocol_version": 2,
            "state_revision": value["revision"], "action": action}


def advance(action_id, expected_revision):
    from jobagent.cli import build_parser, _dispatch_unlocked
    value = _load()
    operation = value["operations"].get(action_id)
    if not operation:
        return {"ok": False, "error": "workflow_action_not_found", "next_suggested": "jobagent workflow next"}
    if expected_revision != operation["expected_revision"]:
        return {"ok": False, "error": "workflow_revision_conflict", "next_suggested": "jobagent workflow next"}
    if operation["status"] == "completed":
        return {**_safe_result(operation["result"]), "replayed": True, "operation_id": action_id}
    if operation["status"] == "claimed":
        return next_action()
    if (operation["status"] != "ready" or value["revision"] != expected_revision
            or operation["snapshot"] != _snapshot()):
        return {"ok": False, "error": "workflow_action_stale", "next_suggested": "jobagent workflow next"}
    operation["status"] = "claimed"
    _save(value)
    args = build_parser().parse_args(operation["argv"][1:])
    # Same process, same serial CLI lock, same business gates as direct calls.
    try:
        result = _dispatch_unlocked(args)
    except Exception as exc:
        if isinstance(getattr(exc, "payload", None), dict):
            result = exc.payload
        elif isinstance(exc, cloud_client.CloudError):
            result = {"ok": False, "error": exc.code or "cloud_error", "retryable": bool(exc.retryable),
                      "request_preserved": bool(exc.details.get("request_preserved")), "message": str(exc), **(exc.details or {})}
            safe_read = args.command in {"doctor", "credits", "profile"} or (args.command == "resume" and args.resume_command in {"list", "status"})
            if exc.retryable and (safe_read or result["request_preserved"]):
                import shlex
                result["next_suggested"] = shlex.join(operation["argv"])
        else:
            raise
    value = _load()
    value["operations"][action_id].update(status="completed", result=result)
    _record_result(value, args, result)
    _save(value)
    return {**result, "operation_id": action_id}


def submit(path):
    from jobagent.application.round_request import remember
    from jobagent.infra import browser_work
    file = Path(path)
    if file.stat().st_size > 65536:
        raise ValueError("Intent file exceeds 64 KiB")
    body = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(body, dict) or set(body) != {"request_id", "criteria"}:
        raise ValueError("Intent JSON accepts only request_id and criteria")
    value = _load()
    active = state.load_json(state.current_round_path()) or {}
    if active.get("status") == "active":
        roles = (body.get("criteria") or {}).get("target_roles")
        if roles and roles != (active.get("intent") or {}).get("target_roles"):
            from jobagent.application.round_direction import request_change
            return request_change({"request_id": body["request_id"], "patch": body["criteria"]})
        return {"ok": False, "error": "workflow_round_update_required",
                "next_suggested": "jobagent round status", "request_preserved": True,
                "message": "本轮已存在，请用 round update 提交条件 patch，expected-revision 使用 round status 返回的 criteria_revision。"}
    if browser_work.has_open():
        return {"ok": False, "error": "native_work_context_locked", "next_suggested": "jobagent work status"}
    original = value.get("intent_request")
    if original and original["request_id"] == body.get("request_id"):
        if original["body"] != body:
            return {"ok": False, "error": "workflow_request_conflict"}
        return {**original["result"], "replayed": True}
    cloud = value.get("cloud_workflow") or {}
    payload = {**body, "expected_revision": cloud.get("revision", 0)}
    if cloud:
        payload["session_id"] = cloud["session_id"]
    result = cloud_client.workflow_submit(payload)
    if not result.get("ok"):
        return result
    workflow = result["workflow"]
    value.update(intent=workflow["criteria"], cloud_workflow=workflow, last_result=None,
                 revision=value["revision"] + 1)
    if active.get("status") == "completed":
        value["new_round_requested_after"] = active["round_id"]
    value["intent_request"] = {"request_id": body["request_id"], "body": body, "result": result}
    _save(value)
    remember(roles=value["intent"].get("target_roles"), cities=value["intent"].get("target_cities"))
    return {**result, "next_suggested": "jobagent workflow next"}


def dispatch(args):
    if args.workflow_command == "submit":
        return submit(args.input)
    if args.workflow_command == "next":
        return next_action()
    if args.workflow_command == "advance":
        return advance(args.action_id, args.expected_revision)
    value = _load()
    if args.operation_id:
        operation = value["operations"].get(args.operation_id)
        if operation is None:
            return {"ok": False, "error": "workflow_operation_not_found"}
        public = {k: v for k, v in operation.items() if k != "result"}
        if operation.get("result"):
            public["result"] = _safe_result(operation["result"])
        return {"ok": True, "operation": public, "state_revision": value["revision"]}
    return {"ok": True, "state_revision": value["revision"], "criteria": value.get("intent"),
            "workflow": rounds.round_status(), "next_suggested": "jobagent workflow next"}
