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
    fields = {"choice": "choice", "resume_id": "resume_id", "target_roles": "target_role",
              "target_cities": "target_city", "exclude_indices": "exclude_index"}
    if not isinstance(answer, dict) or not answer or set(answer) - set(fields):
        raise ValueError("Interaction answer contains unsupported fields")
    if any(getattr(args, dest, None) for dest in fields.values()):
        raise ValueError("Use either an answer file or explicit answer flags")
    from jobagent.cli import build_parser
    argv = ["interaction", "respond", "--interaction-id", args.interaction_id]
    for key, value in answer.items():
        if key in {"choice", "resume_id"}:
            if not isinstance(value, str) or not value or len(value) > 200:
                raise ValueError("Invalid scalar interaction answer")
            argv.extend(["--" + key.replace("_", "-"), value])
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
        setattr(args, dest, getattr(parsed, dest))
    args.answer_file = None
