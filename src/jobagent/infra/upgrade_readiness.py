"""One-shot compatibility checks for users upgrading an existing install."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jobagent.infra import cloud_client
from jobagent.infra.credentials import load_api_key
from jobagent.infra.profile_contract import profile_compatibility_issues
from jobagent.infra.state import current_round_path, load_json, profile_path


def _check_api_key() -> dict[str, Any]:
    key = load_api_key()
    if not key:
        return {
            "name": "api_key",
            "ok": False,
            "error": "api_key_missing",
            "action": "jobagent init --key <your_api_key>",
        }
    if key.startswith("jba_live_"):
        return {
            "name": "api_key",
            "ok": False,
            "error": "retired_license_key",
            "action": "Create an AgentMesh360 API key, then run `jobagent init --key <your_api_key>`.",
        }
    try:
        account = cloud_client.me()
    except cloud_client.CloudError as exc:
        return {
            "name": "api_key",
            "ok": False,
            "error": exc.code or "api_key_verification_failed",
            "status": exc.status,
            "message": str(exc),
            "action": "Run `jobagent init --key <your_api_key>` with a current key.",
        }
    return {"name": "api_key", "ok": True, "account": account}


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


def _check_profile(account_check: dict[str, Any] | None = None) -> dict[str, Any]:
    active = load_json(current_round_path()) or {}
    if active.get("status") == "active" and isinstance(active.get("resume_binding"), dict) and active["resume_binding"].get("id"):
        # The current round uses its confirmed cloud revision. A missing/stale
        # local file cannot authorize a paid reanalysis or a different resume.
        return _check_bound_profile(active, account_check or {})
    profile = load_json(profile_path())
    if not profile:
        return {
            "name": "profile",
            "ok": False,
            "error": "profile_missing",
            "action": "jobagent resume analyze --file <resume>",
        }
    issues = profile_compatibility_issues(profile)
    if issues:
        return {
            "name": "profile",
            "ok": False,
            "error": "profile_incompatible",
            "issues": issues,
            "action": "jobagent resume analyze --file <resume>",
        }
    return {
        "name": "profile",
        "ok": True,
        "schema_version": profile.get("schema_version"),
    }


def _check_repo_config() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[3]
    path = root / "config" / "config.yaml"
    if not path.exists():
        return {"name": "config_template", "ok": True, "status": "not_applicable"}
    import yaml

    from jobagent.platforms import list_platforms

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    configured = set((data.get("platforms") or {}).keys())
    expected = {item.key for item in list_platforms() if item.status == "available"}
    return {
        "name": "config_template",
        "ok": configured == expected,
        "configured": sorted(configured),
        "expected": sorted(expected),
    }


def run_upgrade_check(*, client_state: dict[str, Any] | None = None) -> dict[str, Any]:
    account_check = _check_api_key()
    checks = [account_check, _check_profile(account_check), _check_repo_config()]
    if client_state is not None:
        checks.append(
            {
                "name": "client_state",
                "ok": bool(client_state.get("ok")),
                "upgrade_detected": bool(client_state.get("upgrade_detected")),
                "cleared": list(client_state.get("cleared") or []),
                "migrated": list(client_state.get("migrated") or []),
                "archived": list(client_state.get("archived") or []),
                "conflicts": list(client_state.get("conflicts") or []),
                "action": client_state.get("next_suggested"),
            }
        )
    return {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "next_suggested": next(
            (check.get("action") for check in checks if not check["ok"]),
            "jobagent round status",
        ),
    }
