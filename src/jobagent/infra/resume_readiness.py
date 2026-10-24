"""Read-only material readiness shared by diagnostics and upgrade checks.

An active or staged binding is authoritative. Preparation facts may guide a
new selection, but never replace a bound revision or authorize paid analysis.
"""
from __future__ import annotations

import json
from typing import Any

from jobagent.infra import cloud_client, state
from jobagent.infra.profile_contract import profile_compatibility_issues


def _local_profile() -> dict[str, Any]:
    base = {"name": "profile", "source": "local_snapshot"}
    try:
        profile = state.load_json(state.profile_path())
    except (OSError, json.JSONDecodeError):
        return {**base, "ok": False, "error": "profile_unreadable",
                "action": "jobagent upgrade-check"}
    if not profile:
        return {**base, "ok": False, "error": "profile_missing",
                "action": "jobagent resume status"}
    issues = profile_compatibility_issues(profile) if isinstance(profile, dict) else ["profile must be an object"]
    if issues:
        return {**base, "ok": False, "error": "profile_incompatible", "issues": issues,
                "action": "jobagent resume status"}
    return {**base, "ok": True, "schema_version": profile.get("schema_version")}


def _preparation(account_response: dict[str, Any]) -> dict[str, Any]:
    from jobagent.infra.account_state import AccountStateError, account_ref_from_response, current_account_ref

    base = {"name": "profile", "source": "resume_center"}
    try:
        account_ref = account_ref_from_response(account_response)
    except AccountStateError:
        account_ref = None
    if not account_ref or account_ref != current_account_ref():
        return {**base, "ok": False, "error": "resume_account_unverified",
                "action": "jobagent account status"}
    try:
        value = cloud_client.resume_center_preparation()
    except cloud_client.CloudError as exc:
        return {**base, "ok": False, "error": exc.code or "resume_preparation_unavailable",
                "retryable": bool(exc.retryable), "action": "jobagent resume status"}
    if (not isinstance(value, dict) or value.get("ok") is not True
            or value.get("account_ref") != account_ref or value.get("offline") is True
            or value.get("stale") is True or type(value.get("ready")) is not bool
            or not isinstance(value.get("resumes"), list)
            or any(not isinstance(doc, dict) for doc in value["resumes"])):
        return {**base, "ok": False, "error": "resume_preparation_invalid",
                "action": "jobagent resume status"}
    if value["ready"]:
        receipt = value.get("receipt")
        receipt = receipt if isinstance(receipt, dict) else {}
        revisions = receipt.get("revisions")
        docs = {doc["id"]: doc for doc in value["resumes"] if isinstance(doc.get("id"), str)}
        if (value.get("state") != "ready" or not receipt.get("id")
                or not isinstance(revisions, list) or not revisions
                or any(not isinstance(ref, dict) or not ref.get("resume_revision_id")
                       or not isinstance(ref.get("resume_id"), str)
                       or docs.get(ref.get("resume_id"), {}).get("confirmed_revision_id") != ref["resume_revision_id"]
                       for ref in revisions)):
            return {**base, "ok": False, "error": "resume_preparation_invalid",
                    "action": "jobagent resume status"}
        return {**base, "ok": True, "resume_count": len(revisions),
                "action": "jobagent resume status"}
    if value.get("state") == "empty" and not value["resumes"]:
        # An explicitly maintained classic local profile remains supported.
        return _local_profile()
    return {**base, "ok": False, "error": "resume_preparation_required",
            "requires_user_action": True, "action": "jobagent resume status",
            "workbench_url": value.get("workbench_url"),
            "user_prompt": "请在工作台完成并确认已有简历，再回到当前对话继续；已有轮次和记录已保留。"}


def check_resume(account_response: dict[str, Any] | None) -> dict[str, Any]:
    """Inspect the current material without selecting, analyzing or saving it."""
    active = state.load_json(state.current_round_path()) or {}
    if active.get("status") == "active":
        if isinstance(active.get("resume_binding"), dict) and active["resume_binding"].get("id"):
            return _check_bound_profile(active, {"ok": bool(account_response), "account": account_response})
        return _local_profile()
    staged = state.load_json(state.STATE_DIR / "pending_round_binding.json") or {}
    if isinstance(staged.get("binding"), dict) and staged["binding"].get("id"):
        result = _check_bound_profile({"resume_binding": staged["binding"]},
                                      {"ok": bool(account_response), "account": account_response})
        return {**result, "source": "pending_resume_binding"}
    if (staged.get("round_request") or {}).get("no_resume_binding"):
        return _local_profile()
    capabilities = ((account_response or {}).get("server") or {}).get("capabilities") or {}
    if capabilities.get("resume_center") is True:
        return _preparation(account_response)
    return _local_profile()


def _check_bound_profile(active: dict[str, Any], account_check: dict[str, Any]) -> dict[str, Any]:
    from jobagent.infra.account_state import AccountStateError, account_ref_from_response, current_account_ref
    from jobagent.infra.protocol import digest_payload

    base = {"name": "profile", "source": "round_resume_binding", "request_preserved": True}
    try:
        account_ref = account_ref_from_response(account_check.get("account") or {}) if account_check.get("ok") else None
    except AccountStateError:
        account_ref = None
    if not account_ref or account_ref != current_account_ref() or active.get("account_ref", account_ref) != account_ref:
        return {**base, "ok": False, "error": "bound_resume_account_unverified", "action": "jobagent account status"}
    binding = active["resume_binding"]
    try:
        material = cloud_client.resume_binding_material(binding["id"])
    except cloud_client.CloudError as exc:
        return {**base, "ok": False, "error": "bound_resume_material_unavailable",
                "cause": exc.code, "retryable": bool(exc.retryable),
                "action": "jobagent upgrade-check" if exc.retryable else "jobagent round status"}
    snapshot = material.get("binding") or material.get("resume_binding") or {}
    profile = material.get("profile") or {}
    fields = ("id", "context_id", "resume_id", "resume_revision_id", "resume_revision_number", "content_digest", "target_role")
    if (material.get("ok") is not True or material.get("offline") is True or material.get("stale") is True
            or material.get("account_ref") != account_ref
            or not isinstance(snapshot, dict) or not isinstance(profile, dict)
            or binding.get("account_ref", account_ref) != account_ref
            or snapshot.get("account_ref", account_ref) != account_ref
            or any(not binding.get(k) for k in ("id", "resume_id", "resume_revision_id"))
            or any(snapshot.get(k) != binding[k] for k in fields if k in binding)
            or profile_compatibility_issues(profile)
            or material.get("profile_digest") != digest_payload(profile)
            or (active.get("intent", {}).get("profile_digest")
                and active["intent"]["profile_digest"] != material.get("profile_digest"))):
        return {**base, "ok": False, "error": "bound_resume_material_mismatch", "action": "jobagent round status"}
    return {**base, "ok": True, "binding_id": binding["id"], "schema_version": profile.get("schema_version")}
