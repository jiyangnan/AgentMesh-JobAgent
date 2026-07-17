"""Bind sensitive local Job Agent state to one opaque AgentMesh account."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jobagent.infra.state import APP_DIR

OWNER_SCHEMA_VERSION = 1
_ACCOUNT_REF = re.compile(r"^acct_[A-Za-z0-9_-]{8,}$")
_ACCOUNT_OWNED_PATHS = (
    "profile.json",
    "support_state.json",
    "current_round.json",
    "rounds",
    "discoveries",
    "archive",
    "audit_log.json",
    "liepin_audit_log.json",
    "zhilian_audit_log.json",
    "job51_audit_log.json",
)
_SESSION_MARKERS = (
    "browser_session.json",
    "platform_tabs.json",
    "last_doctor_report.json",
    "last_probe_send.json",
)


class AccountStateError(RuntimeError):
    def __init__(self, payload: dict[str, Any]):
        super().__init__(str(payload.get("message") or payload.get("error")))
        self.payload = payload


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root(app_dir: Path | None) -> Path:
    return Path(app_dir) if app_dir is not None else APP_DIR


def _owner_path(root: Path) -> Path:
    return root / "state_owner.json"


def _state_dir(root: Path) -> Path:
    return root / "state"


def _snapshot_dir(root: Path, account_ref: str) -> Path:
    return root / "accounts" / account_ref / "state"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def account_ref_from_response(account_response: dict[str, Any]) -> str:
    account = account_response.get("account") or {}
    account_ref = str(account.get("account_ref") or "")
    if not _ACCOUNT_REF.fullmatch(account_ref):
        raise AccountStateError(
            {
                "ok": False,
                "error": "cloud_account_ref_unavailable",
                "message": "The cloud account response does not include a valid stable account reference.",
                "next_suggested": "jobagent doctor env",
            }
        )
    return account_ref


def has_account_owned_state(*, app_dir: Path | None = None) -> bool:
    state = _state_dir(_root(app_dir))
    return any((state / relative).exists() for relative in _ACCOUNT_OWNED_PATHS)


def state_owner_status(account_ref: str, *, app_dir: Path | None = None) -> dict[str, Any]:
    root = _root(app_dir)
    owner = _read_json(_owner_path(root)) or {}
    state_ref = str(owner.get("account_ref") or "")
    has_state = has_account_owned_state(app_dir=root)
    if not state_ref:
        status = "legacy_unbound" if has_state else "empty_unbound"
    elif state_ref == account_ref:
        status = "ready"
    else:
        status = "account_mismatch"
    return {
        "status": status,
        "ready": status == "ready",
        "api_account_ref": account_ref,
        "state_account_ref": state_ref or None,
        "has_account_owned_state": has_state,
    }


def _bind(root: Path, account_ref: str, *, reason: str) -> dict[str, Any]:
    _write_json(
        _owner_path(root),
        {
            "schema_version": OWNER_SCHEMA_VERSION,
            "account_ref": account_ref,
            "bound_at": _utc_now(),
            "reason": reason,
        },
    )
    return state_owner_status(account_ref, app_dir=root)


def ensure_account_state(account_response: dict[str, Any], *, app_dir: Path | None = None) -> dict[str, Any]:
    account_ref = account_ref_from_response(account_response)
    root = _root(app_dir)
    status = state_owner_status(account_ref, app_dir=root)
    if status["status"] == "empty_unbound":
        return _bind(root, account_ref, reason="new_empty_state")
    if status["status"] == "legacy_unbound":
        raise AccountStateError(
            {
                "ok": False,
                "error": "local_state_owner_required",
                "message": "Existing Job Agent state predates account binding and cannot be claimed automatically.",
                **status,
                "next_suggested": "jobagent account bind --confirm-legacy",
            }
        )
    if status["status"] == "account_mismatch":
        raise AccountStateError(
            {
                "ok": False,
                "error": "local_state_account_mismatch",
                "message": "The configured API key belongs to a different account than the active local state.",
                **status,
                "next_suggested": "jobagent account switch --new-state",
            }
        )
    return status


def bind_legacy_state(
    account_response: dict[str, Any],
    *,
    confirm_legacy: bool,
    app_dir: Path | None = None,
) -> dict[str, Any]:
    account_ref = account_ref_from_response(account_response)
    root = _root(app_dir)
    status = state_owner_status(account_ref, app_dir=root)
    if status["status"] == "account_mismatch":
        raise AccountStateError(
            {
                "ok": False,
                "error": "local_state_account_mismatch",
                "message": "An already-bound state cannot be reassigned. Switch accounts instead.",
                **status,
                "next_suggested": "jobagent account switch --new-state",
            }
        )
    if status["status"] == "ready":
        return {"ok": True, "local_state": status, "changed": False}
    if not confirm_legacy:
        raise AccountStateError(
            {
                "ok": False,
                "error": "user_confirmation_required",
                "message": "Confirm that the existing local Job Agent state belongs to this AgentMesh account.",
                **status,
                "next_suggested": "jobagent account bind --confirm-legacy",
            }
        )
    bound = _bind(root, account_ref, reason="confirmed_legacy_state")
    return {"ok": True, "local_state": bound, "changed": True}


def _move_paths(source: Path, destination: Path, journal: list[tuple[Path, Path]]) -> list[str]:
    moved: list[str] = []
    for relative in _ACCOUNT_OWNED_PATHS:
        item = source / relative
        if not item.exists():
            continue
        target = destination / relative
        if target.exists():
            raise AccountStateError(
                {
                    "ok": False,
                    "error": "account_state_snapshot_conflict",
                    "message": f"A saved account state already contains {relative}.",
                    "next_suggested": "jobagent account status",
                }
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(item), str(target))
        journal.append((target, item))
        moved.append(relative)
    return moved


def switch_account_state(
    account_response: dict[str, Any],
    *,
    new_state: bool,
    app_dir: Path | None = None,
) -> dict[str, Any]:
    account_ref = account_ref_from_response(account_response)
    root = _root(app_dir)
    status = state_owner_status(account_ref, app_dir=root)
    if status["status"] == "ready":
        return {"ok": True, "local_state": status, "changed": False}
    if status["status"] == "legacy_unbound":
        raise AccountStateError(
            {
                "ok": False,
                "error": "local_state_owner_required",
                "message": "Bind the legacy state before switching away from it.",
                **status,
                "next_suggested": "jobagent account bind --confirm-legacy",
            }
        )
    if status["status"] == "empty_unbound":
        bound = _bind(root, account_ref, reason="new_empty_state")
        return {"ok": True, "local_state": bound, "changed": True, "restored": []}
    if not new_state:
        raise AccountStateError(
            {
                "ok": False,
                "error": "user_confirmation_required",
                "message": "Confirm an account-state switch. Existing account data will be preserved.",
                **status,
                "next_suggested": "jobagent account switch --new-state",
            }
        )

    old_ref = str(status["state_account_ref"])
    active = _state_dir(root)
    old_snapshot = _snapshot_dir(root, old_ref)
    new_snapshot = _snapshot_dir(root, account_ref)
    journal: list[tuple[Path, Path]] = []
    try:
        saved = _move_paths(active, old_snapshot, journal)
        restored = _move_paths(new_snapshot, active, journal)
        for relative in _SESSION_MARKERS:
            marker = active / relative
            if marker.exists():
                marker.unlink()
        bound = _bind(root, account_ref, reason="account_switch")
    except Exception:
        for source, destination in reversed(journal):
            if source.exists() and not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
        raise
    return {
        "ok": True,
        "changed": True,
        "previous_account_ref": old_ref,
        "local_state": bound,
        "saved": saved,
        "restored": restored,
        "browser_profile_preserved": True,
    }
