"""Map portable answer files to the existing, shared interaction handlers."""
import json
from pathlib import Path


def apply_answer_file(args):
    path = Path(args.answer_file)
    if path.stat().st_size > 65536:
        raise ValueError("Interaction answer file exceeds 64 KiB")
    try:
        answer = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Interaction answer must be valid UTF-8 JSON") from exc
    fields = {"choice": "choice", "resume_id": "resume_id", "attachment_id": "attachment_id", "target_roles": "target_role",
              "target_cities": "target_city", "target_locations": "target_locations", "exclude_indices": "exclude_index"}
    if str(args.interaction_id).startswith("linkedin-facts:") and not str(args.interaction_id).startswith("linkedin-facts:manual-"):
        if not isinstance(answer, dict) or set(answer) != {"application_answers"} or not isinstance(answer["application_answers"], dict):
            raise ValueError("Application facts require an application_answers object")
        if any(getattr(args, field, None) for field in (*fields.values(), "target_location", "location_id", "radius_id")):
            raise ValueError("Use either an answer file or explicit answer flags")
        args.application_answers = answer["application_answers"]
        args.answer_file = None
        return
    if not isinstance(answer, dict) or not answer or set(answer) - set(fields):
        raise ValueError("Interaction answer contains unsupported fields")
    if getattr(args, "radius_id", None) or getattr(args, "target_location", None) or getattr(args, "location_id", None) or any(getattr(args, dest, None) for dest in fields.values()):
        raise ValueError("Use either an answer file or explicit answer flags")
    from jobagent.infra.interaction_state import load_pending_interaction
    pending = load_pending_interaction() or {}
    if pending.get("interaction_id") == args.interaction_id:
        card = pending.get("interaction") or {}
        kind = card.get("kind") or pending.get("kind")
        field_ids = {field.get("field_id") for field in card.get("fields", [])}
        allowed = set()
        if kind == "resume_selection":
            allowed.add("resume_id")
        elif kind == "platform_resume_choice":
            allowed.add("attachment_id")
        else:
            allowed.update(field_ids & {"target_roles", "target_cities", "target_locations", "exclude_indices"})
            if any(field.get("options") for field in card.get("fields", [])):
                allowed.add("choice")
        if kind in {"delivery_confirmation", "delivery_exclusions"}:
            allowed.update({"choice", "exclude_indices"})
            if "exclude_indices" in answer and answer.get("choice", pending.get("choice")) != "exclude_jobs":
                raise ValueError("Job exclusions require the exclude_jobs choice")
        if kind == "target_region":
            allowed.add("target_locations")
        if set(answer) - allowed:
            raise ValueError("Answer fields are not allowed by the current interaction")
    from jobagent.cli import build_parser
    argv = ["interaction", "respond", "--interaction-id", args.interaction_id]
    for key, value in answer.items():
        if key == "target_locations":
            if not isinstance(value, list) or not value or len(value) > 6 or not all(isinstance(item, dict) for item in value):
                raise ValueError("Target-location answers require a nonempty structured list")
            continue
        if key in {"choice", "resume_id", "attachment_id"}:
            if not isinstance(value, str) or not value or len(value) > 200:
                raise ValueError("Invalid scalar interaction answer")
            flag = "--" + key.replace("_", "-")
            if key == "choice" and args.interaction_id.startswith("linkedin-location:") and value != "pause_platform":
                flag = "--location-id"
            if key == "choice" and args.interaction_id.startswith("linkedin-radius:") and value != "pause_platform":
                flag = "--radius-id"
            argv.extend([flag, value])
        else:
            if not isinstance(value, list) or not value or len(value) > 100:
                raise ValueError("Invalid list interaction answer")
            for item in value:
                if key == "exclude_indices":
                    if type(item) is not int or item < 1:
                        raise ValueError("Job numbers must be positive integers")
                elif not isinstance(item, str) or not item.strip() or len(item) > 200:
                    raise ValueError("Invalid text interaction answer")
                argv.extend(["--" + fields[key].replace("_", "-"), str(item)])
    parsed = build_parser().parse_args(argv)
    for dest in fields.values():
        setattr(args, dest, answer.get("target_locations") if dest == "target_locations" else getattr(parsed, dest))
    args.location_id = parsed.location_id
    args.radius_id = parsed.radius_id
    args.answer_file = None
