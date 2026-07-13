"""One-shot compatibility checks for users upgrading an existing install."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jobagent.infra import cloud_client
from jobagent.infra.credentials import load_api_key
from jobagent.infra.profile_contract import profile_compatibility_issues
from jobagent.infra.state import load_json, profile_path


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


def _check_profile() -> dict[str, Any]:
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
    checks = [_check_api_key(), _check_profile(), _check_repo_config()]
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
