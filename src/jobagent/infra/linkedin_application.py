"""Local full-application binding; transport only its digest to the service."""

import re
from copy import deepcopy

from jobagent.infra.protocol import digest_payload


def validate_application(job):
    value = job.get("application")
    if (
        not isinstance(value, dict)
        or job.get("application_contract_version") != 1
        or value.get("job_id") != str(job.get("id"))
        or job.get("application_digest") != digest_payload(value)
        or not value.get("context")
        or not value.get("account_label")
        or not value.get("pages")
        or not str(value.get("review_text") or "").strip()
        or not re.fullmatch(
            r"[0-9a-f]{64}", str((value.get("attachment") or {}).get("sha256", ""))
        )
        or value.get("submission_attempted") is not False
    ):
        raise ValueError(
            "LinkedIn requires a complete, unsubmitted and content-bound application preview"
        )
    attachments = [
        f for p in value["pages"] for f in p["fields"] if f["type"] == "attachment"
    ]
    if not attachments or any(
        len(f["value"]) != 1 or f["source"]["kind"] != "user_confirmed"
        for f in attachments
    ):
        raise ValueError(
            "Confirm the existing attachment before application authorization"
        )
    for page in value["pages"]:
        for field in page["fields"]:
            if field.get("source", {}).get("kind") not in {
                "user_confirmed",
                "resume_fact",
                "optional_blank",
            }:
                raise ValueError(
                    "Every displayed application value must retain its source"
                )
            if field["source"]["kind"] == "optional_blank" and field["value"] not in (
                "",
                [],
            ):
                raise ValueError(
                    "An unconfirmed prefilled value cannot be presented as optional blank"
                )
            if (
                field["type"] in {"attachment", "checkbox"}
                and field["source"]["kind"] != "user_confirmed"
            ):
                raise ValueError(
                    "Application attachments and consent require explicit choices"
                )
    return value


def application_text(application):
    lines = []
    for page in application["pages"]:
        for field in page["fields"]:
            value = field["value"]
            if isinstance(value, list):
                value = "; ".join(value)
            elif type(value) is bool:
                value = "Yes" if value else "No"
            lines.append(f"{field['question'] or 'Resume'}: {value or '(blank)'}")
    lines.extend(["Native final preview:", application["review_text"]])
    lines.append("Confirmed existing resume: " + application["attachment"]["file_path"])
    for link in application["policy_links"]:
        lines.append(f"{link['text']}: {link['url']}")
    return "\n".join(lines)


def transport_candidates(review):
    if review.get("platform") != "linkedin":
        return review["send_candidates"]
    result = []
    for item in review["send_candidates"]:
        validate_application(item)
        result.append({k: deepcopy(v) for k, v in item.items() if k != "application"})
    return result
