"""Preserve explicit round inputs across setup interactions and process restarts.

Uses the existing account-bound staging file. Never edits a confirmed resume.
"""
from __future__ import annotations

from typing import Any

from jobagent.application.round_resume_binding import load_pending_binding, save_pending_binding


def request() -> dict[str, Any]:
    return dict((load_pending_binding() or {}).get("round_request") or {})


def remember(*, roles=None, cities=None, accept_suggested=False, no_binding=False) -> dict[str, Any]:
    staged = load_pending_binding() or {}
    value = dict(staged.get("round_request") or {})
    if roles:
        value["target_roles"] = list(roles)
        value["accept_suggested"] = False
    if accept_suggested:
        value["target_roles"] = []
        value["accept_suggested"] = True
    if cities:
        value["target_cities"] = list(cities)
    if no_binding:
        value["no_resume_binding"] = True
    if value:
        save_pending_binding({**staged, "round_request": value})
    return value


def pending_setup() -> dict[str, Any] | None:
    from jobagent.infra.interaction_state import load_pending_interaction
    from jobagent.infra.interaction_protocol import build_host_presentations

    pending = load_pending_interaction()
    if not pending or pending.get("stage") not in {"resume_binding", "cities", "choice", "roles"}:
        return None
    # Resume-center v1 predates the local product_id requirement. Adapt only
    # its product namespace; all server ids, choices and text remain unchanged.
    interaction = {"product_id": "job_agent", **pending["interaction"]}
    kind = interaction["kind"]
    flag = "--resume-id <resume-id>" if kind == "resume_selection" else (
        "--target-city <city>" if kind == "target_city_input" else (
            "--target-role <role>" if pending["stage"] == "roles" else "--choice <choice>"))
    import shlex
    return {
        "ok": False, "error": "interaction_required", "requires_user_action": True,
        "interaction": interaction, "host_presentations": build_host_presentations(interaction),
        "user_prompt": interaction["fallback_text"], "request_preserved": True,
        "round_request": request(),
        "next_suggested": f"jobagent interaction respond --interaction-id {shlex.quote(interaction['interaction_id'])} {flag}",
    }
