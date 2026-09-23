"""Host-independent execution contract. This is a protocol, not a UI interceptor.

The CLI owns transitions; the host renders decisions and executes issued work.
Unknown continuations never grant permission to improvise a browser operation.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import shlex
from typing import Any

PROTOCOL = "jobagent.workflow"
VERSION = 1
PLATFORMS = ["boss", "liepin", "zhilian", "51job"]


def command_catalog(parser: argparse.ArgumentParser, prefix=None) -> list[dict]:
    prefix = prefix or ["jobagent"]
    children = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
    if children:
        return [item for name, child in children.choices.items()
                for item in command_catalog(child, [*prefix, name])]
    return [{"argv_prefix": prefix, "arguments": [
        {"flags": action.option_strings, "required": action.required,
         "choices": list(action.choices) if action.choices is not None else None,
         "repeatable": isinstance(action, argparse._AppendAction), "nargs": action.nargs}
        for action in parser._actions if action.dest != "help"
    ]}]


def contract(parser: argparse.ArgumentParser) -> dict:
    return {
        "ok": True, "protocol": PROTOCOL, "protocol_version": VERSION,
        "entry_command": "jobagent onboarding", "resume_command": "jobagent work next",
        "commands": command_catalog(parser),
        "setup": ["api_key", "environment_and_account", "resume_preparation",
                  "resume_selection", "target_cities", "target_roles", "round_created"],
        "platform_order": PLATFORMS,
        "per_platform": ["login", "discover", "signed_review", "delivery_preview",
                         "delivery_confirmation", "send", "audit"],
        "action_types": ["run_cli", "wait_user", "native_work", "wait", "report", "blocked"],
        "rules": {
            "authority": "CLI and server state, signed decisions and explicit user answers",
            "business_data": "Read through CLI; never inspect the workbench UI as a data fallback.",
            "browser": "Only an issued work begin response permits its exact native task; obey allowed_mode, nonce, binding and result_schema.",
            "user_web_handoff": "The user opens the supplied URL and returns to this conversation; this does not authorize host browser automation.",
            "host_adapters": "Use only currently callable native tools. Render interaction.fallback_text when cards are unavailable. Missing browser capability uses the work pause schema; never switch driver or API.",
            "confirmation": "Defaults are recommendations. Never fabricate a user answer or reuse another platform's delivery authorization.",
            "delivery": "Display every preview item; confirm_all, exclude_jobs or cancel_delivery. Exclusions require a new complete preview and confirmation.",
            "recovery": "Preserve identifiers and state. Unknown outcomes require reconciliation; never repeat a send click.",
            "new_round": "Only an explicit user job-search request authorizes round start; installation alone does not.",
            "unsupported": "Stop with the returned blocker; do not invent commands, UI work or hidden API calls.",
        },
    }


def executable_command(command: Any) -> list[str] | None:
    if not isinstance(command, str) or not command.strip() or "<" in command or ">" in command:
        return None
    try:
        argv = shlex.split(command)
        if not argv or argv[0] != "jobagent" or any(a in {";", "&&", "||", "|"} for a in argv):
            return None
        from jobagent.cli import build_parser
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            build_parser().parse_args(argv[1:])
        return argv
    except (ValueError, SystemExit):
        return None


def with_contract(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    workflow = payload.get("workflow") or {}
    interaction = payload.get("interaction") or {}
    work = payload.get("work") or {}
    continuation = payload.get("next_suggested") or workflow.get("next_suggested")
    argv = executable_command(continuation)
    action: dict[str, Any] = {"type": "blocked", "reason": "no_executable_continuation"}
    if payload.get("requires_user_action") or interaction or payload.get("error") == "interaction_required":
        action = {"type": "wait_user", "prompt_field": (
            "delivery_preview.fallback_text" if payload.get("delivery_preview") else
            "interaction.fallback_text" if interaction else "user_prompt"),
            "interaction_id": interaction.get("interaction_id"),
            "continuation_template": continuation, "auto_answer": False}
        if interaction:
            kind = interaction.get("kind")
            action["response_arguments"] = {
                "command": ["jobagent", "interaction", "respond", "--interaction-id", interaction.get("interaction_id")],
                "answer_flag": "--resume-id" if kind == "resume_selection" else (
                    "--target-city" if kind == "target_city_input" else
                    "--exclude-index" if kind == "delivery_exclusions" else
                    "--target-role" if kind == "target_role_input" else "--choice"),
                "source": "explicit_user_answer",
            }
    elif payload.get("requires_technical_recovery"):
        action = {"type": "blocked", "reason": payload.get("error"), "recovery_command": continuation}
    elif payload.get("event") == "browser_work_required" and work.get("nonce") and payload.get("native_step_issued"):
        action = {"type": "native_work", "work_id": work.get("work_id"),
                  "allowed_mode": work.get("allowed_mode"), "task_field": "work.task",
                  "result_schema_field": "work.task.result_schema", "continuation_template": continuation}
    elif payload.get("event") == "browser_work_wait":
        action = {"type": "wait", "seconds": payload.get("wait_seconds"), "argv": argv}
    elif workflow.get("workflow_complete"):
        action = {"type": "report", "reason": "round_completed"}
    elif argv:
        action = {"type": "run_cli", "argv": argv}
    elif not continuation and payload.get("ok") is not False:
        action = {"type": "report"}
    return {**payload, "agent_action": {
        "protocol": PROTOCOL, "protocol_version": VERSION,
        "state": (payload.get("onboarding") or {}).get("stage") or interaction.get("kind")
                 or work.get("action") or payload.get("event") or payload.get("error")
                 or workflow.get("status") or "command_result",
        "platform": payload.get("platform") or workflow.get("current_platform"),
        "browser_fallback_allowed": False, **action,
    }}
