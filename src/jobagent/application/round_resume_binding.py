"""Round-scoped resume binding: the user-confirmed resume for deliveries.

The binding flows preparation -> selection interaction -> respond -> binding,
then rides the round state into discovery so signed decisions — and the
workbench application records they import — name the exact resume revision.
"""
from __future__ import annotations

import uuid
from typing import Any

from jobagent.infra import cloud_client


def pending_binding_path():
    from jobagent.infra.state import STATE_DIR, ensure_dirs

    ensure_dirs()
    return STATE_DIR / "pending_round_binding.json"


def load_pending_binding() -> dict[str, Any] | None:
    from jobagent.infra.state import load_json

    return load_json(pending_binding_path())


def save_pending_binding(value: dict[str, Any]) -> None:
    from jobagent.infra.state import save_json

    save_json(pending_binding_path(), value)


def clear_pending_binding() -> None:
    path = pending_binding_path()
    if path.exists():
        path.unlink()


def binding_summary(binding: dict[str, Any] | None) -> dict[str, Any] | None:
    if not binding or not binding.get("id"):
        return None
    return {
        "id": binding.get("id"),
        "context_id": binding.get("context_id"),
        "resume_id": binding.get("resume_id"),
        "resume_name": binding.get("resume_name"),
        "target_role": binding.get("target_role"),
        "resume_revision_id": binding.get("resume_revision_id"),
        "resume_revision_number": binding.get("resume_revision_number"),
    }


def begin_resume_selection() -> dict[str, Any]:
    """Start the user-confirmed resume choice for the coming round.

    Returns {"interaction": payload} when the user must choose (always, even
    with a single confirmed resume — decision D-B1), {"notice": text} when
    there is nothing prepared (old local-profile path continues), or
    {"error": payload} when the resume center is reachable but rejects the
    selection request.
    """
    preparation = cloud_client.resume_center_preparation()
    if not preparation.get("ready"):
        return {"notice": "no_prepared_resumes"}
    context_id = f"cli-round-{uuid.uuid4()}"
    selection = cloud_client.resume_selection(
        context_id, int(preparation["state_revision"])
    )
    interaction = selection.get("interaction") or {}
    options = (
        (interaction.get("fields") or [{}])[0].get("options")
        if isinstance(interaction.get("fields"), list)
        else None
    ) or []
    from jobagent.infra.interaction_state import save_pending_interaction

    save_pending_interaction(
        interaction,
        stage="resume_binding",
        context={
            "selection_id": str(
                selection.get("selection", {}).get("id")
                or interaction.get("interaction_id")
                or ""
            ),
            "round_context_id": context_id,
            "response_id": f"cli-response-{uuid.uuid4()}",
            "options": [
                {"resume_id": o.get("option_id"), "label": o.get("label")}
                for o in options
            ],
        },
    )
    fallback = str(interaction.get("fallback_text") or "")
    return {
        "interaction": {
            "ok": False,
            "error": "interaction_required",
            "interaction": interaction,
            "message": (
                "Choose the confirmed resume for this round's deliveries; "
                "every delivered job will record it."
            ),
            "next_suggested": "jobagent interaction respond --interaction-id "
            + str(interaction.get("interaction_id") or "")
            + " --resume-id <resume-id>",
            "fallback_text": fallback,
        }
    }


def respond_resume_selection(pending: dict[str, Any], resume_id: str) -> dict[str, Any]:
    """Submit the user's choice and stage the binding for the round."""
    context = pending.get("context") or {}
    selection_id = str(context.get("selection_id") or "")
    if not selection_id:
        return {
            "ok": False,
            "error": "invalid_interaction_response",
            "message": "The resume selection is no longer pending.",
            "next_suggested": "jobagent round start",
        }
    options = context.get("options") or []
    valid = {str(o.get("resume_id")) for o in options}
    if resume_id not in valid:
        return {
            "ok": False,
            "error": "invalid_interaction_response",
            "message": "Choose one of the offered resumes.",
            "next_suggested": str((pending.get("interaction") or {}).get("fallback_text") or ""),
        }
    result = cloud_client.resume_selection_respond(
        selection_id, str(context.get("response_id") or ""), resume_id
    )
    binding = result.get("binding") or {}
    if not binding.get("id"):
        return {
            "ok": False,
            "error": "resume_binding_invalid",
            "message": "The server did not return a usable resume binding.",
            "next_suggested": "jobagent round start",
        }
    save_pending_binding({"binding": binding})
    return {
        "ok": True,
        "resume_binding": binding_summary(binding),
        "next_suggested": "jobagent round start",
    }


def binding_direction_conflict(
    round_state: dict[str, Any] | None, binding: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Refuse to attach a binding whose direction contradicts the active round.

    The server only signs bound-round plans for exactly the bound resume's
    direction, so a mismatched attachment can never discover. Guide the user
    to finish (or skip) the round, or to re-select a matching resume.
    """
    if not binding or not binding.get("id"):
        return None
    intent = (round_state or {}).get("intent") or {}
    direction = str(binding.get("target_role") or "").strip()
    roles = [str(role).strip().casefold() for role in intent.get("target_roles") or []]
    if intent.get("status") == "confirmed" and roles == [direction.casefold()]:
        return None
    current_roles = [str(role) for role in intent.get("target_roles") or []]
    return {
        "ok": False,
        "error": "resume_binding_direction_mismatch",
        "message": (
            f"当前轮次的目标岗位是 {'、'.join(current_roles) or '（未确认）'}，"
            f"与绑定简历《{binding.get('resume_name') or binding.get('id')}》的方向"
            f"（{direction}）不一致。绑定简历的轮次只能投递该简历的方向。"
            "请先完成或跳过当前轮次，或重新选择与目标岗位同方向的简历。"
        ),
        "next_suggested": "jobagent round status",
    }


def binding_material_profile(binding: dict[str, Any]) -> dict[str, Any]:
    """Fetch the bound resume's own material: profile, digest, binding snapshot.

    The server returns the binding under ``binding`` (with ``context_id``
    inside it) and never leaks a top-level ``context_id``; tolerate the older
    mock shape ``resume_binding`` so tests can pin either contract.
    """
    material = cloud_client.resume_binding_material(str(binding.get("id")))
    snapshot = material.get("binding") or material.get("resume_binding") or {}
    if not snapshot.get("id"):
        raise cloud_client.CloudError(
            "The server did not return a usable resume binding.",
            code="resume_binding_invalid",
        )
    return {
        "binding": snapshot,
        "profile": material.get("profile") or {},
        "profile_digest": str(material.get("profile_digest") or ""),
    }


def attach_bound_round(
    binding: dict[str, Any] | None,
    *,
    intent: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Attach a user-confirmed binding to the active round, all guards applied.

    Every attach site must go through here: the direction guard refuses a
    binding that contradicts the round's confirmed intent (the server would
    reject every later discovery), and ``intent`` upgrades a pre-existing round
    to the binding's own material form so its digest and cities match what the
    server validates for bound rounds. Returns a conflict error dict, or None
    on success.
    """
    from jobagent.infra.rounds import attach_round_resume_binding

    if not binding or not binding.get("id"):
        return None
    current = _load_current_round()
    conflict = binding_direction_conflict(current, binding)
    if conflict:
        return conflict
    attach_round_resume_binding(binding, intent=intent)
    return None


def _load_current_round() -> dict[str, Any]:
    from jobagent.infra.state import current_round_path, load_json

    try:
        return load_json(current_round_path()) or {}
    except Exception:
        return {}


def resolve_round_binding(
    *, explicit_binding: str | None, no_binding: bool, current_round: dict[str, Any] | None
) -> dict[str, Any]:
    """Decide the binding for this round start call.

    Order: --no-resume-binding clears everything; an already-bound active
    round or a staged pending binding is reused; --resume-binding validates
    and adopts; otherwise the user chooses via the selection interaction.
    """
    if no_binding:
        clear_pending_binding()
        return {"binding": None}
    existing = (current_round or {}).get("resume_binding") if (current_round or {}).get("status") == "active" else None
    if existing and existing.get("id"):
        return {"binding": existing}
    staged = load_pending_binding()
    if staged and staged.get("binding", {}).get("id"):
        return {"binding": staged["binding"]}
    if explicit_binding:
        material = cloud_client.resume_binding_material(explicit_binding)
        binding = material.get("binding") or material.get("resume_binding") or {}
        if binding.get("id") != explicit_binding:
            return {
                "error": {
                    "ok": False,
                    "error": "resume_binding_invalid",
                    "message": "The server did not confirm this resume binding.",
                    "next_suggested": "jobagent round start",
                }
            }
        save_pending_binding({"binding": binding})
        return {"binding": binding}
    try:
        return begin_resume_selection()
    except cloud_client.CloudError as exc:
        if exc.status in (401, 403, 404):
            raise
        # Unprepared or unreachable resume center keeps the classic path.
        return {"notice": f"resume_center_skipped:{exc.code or exc.status}"}
