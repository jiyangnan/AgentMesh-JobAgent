"""Bind sensitive local Job Agent state to one opaque AgentMesh account."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jobagent.infra.state import APP_DIR

OWNER_SCHEMA_VERSION = 2
_KEY_FINGERPRINT_CONTEXT = b"jobagent-state-owner-v1\0"
_ACCOUNT_REF = re.compile(r"^acct_[A-Za-z0-9_-]{8,}$")
_ACCOUNT_OWNED_PATHS = (
    "profile.json",
    "support_state.json",
    "product_announcements.json",
    "current_round.json",
    "pending_interaction.json",
    "pending_round_binding.json",
    "resume_freshness_baselines.json",
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


def _key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(_KEY_FINGERPRINT_CONTEXT + api_key.encode("utf-8")).hexdigest()


def _credential_mtime(path: Path) -> float:
    return path.stat().st_mtime


def _timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


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
        "offline_capable": bool(owner.get("key_fingerprint")) and status == "ready",
        "verified_at": owner.get("verified_at") if status == "ready" else None,
    }


def current_account_ref(*, app_dir: Path | None = None) -> str | None:
    """Return the opaque account bound to the active local state."""

    owner = _read_json(_owner_path(_root(app_dir))) or {}
    account_ref = str(owner.get("account_ref") or "")
    return account_ref if _ACCOUNT_REF.fullmatch(account_ref) else None


def verified_account_ref_for_api_key(
    api_key: str,
    *,
    app_dir: Path | None = None,
) -> str | None:
    """Return the active account from one verified owner-proof snapshot."""

    owner = _read_json(_owner_path(_root(app_dir))) or {}
    account_ref = str(owner.get("account_ref") or "")
    fingerprint = str(owner.get("key_fingerprint") or "")
    verified = bool(
        _ACCOUNT_REF.fullmatch(account_ref)
        and fingerprint
        and api_key
        and hmac.compare_digest(fingerprint, _key_fingerprint(api_key))
    )
    return account_ref if verified else None


def api_key_matches_current_owner(
    api_key: str,
    *,
    app_dir: Path | None = None,
) -> bool:
    """Check the active account proof without migrating or exposing its identity."""

    return verified_account_ref_for_api_key(api_key, app_dir=app_dir) is not None


def _bind(
    root: Path,
    account_ref: str,
    *,
    reason: str,
    api_key: str | None = None,
) -> dict[str, Any]:
    now = _utc_now()
    payload: dict[str, Any] = {
        "schema_version": OWNER_SCHEMA_VERSION,
        "account_ref": account_ref,
        "bound_at": now,
        "reason": reason,
    }
    if api_key:
        payload.update(
            {
                "key_fingerprint": _key_fingerprint(api_key),
                "verified_at": now,
                "proof_source": "online_account_verification",
            }
        )
    _write_json(_owner_path(root), payload)
    return state_owner_status(account_ref, app_dir=root)


def _refresh_online_proof(root: Path, account_ref: str, api_key: str) -> dict[str, Any]:
    owner = _read_json(_owner_path(root)) or {}
    owner.update(
        {
            "schema_version": OWNER_SCHEMA_VERSION,
            "account_ref": account_ref,
            "key_fingerprint": _key_fingerprint(api_key),
            "verified_at": _utc_now(),
            "proof_source": "online_account_verification",
        }
    )
    owner.setdefault("bound_at", owner["verified_at"])
    owner.setdefault("reason", "verified_existing_state")
    _write_json(_owner_path(root), owner)
    return state_owner_status(account_ref, app_dir=root)


def ensure_account_state(
    account_response: dict[str, Any],
    *,
    api_key: str | None = None,
    app_dir: Path | None = None,
) -> dict[str, Any]:
    account_ref = account_ref_from_response(account_response)
    root = _root(app_dir)
    status = state_owner_status(account_ref, app_dir=root)
    if status["status"] == "empty_unbound":
        return _bind(root, account_ref, reason="new_empty_state", api_key=api_key)
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
    if api_key:
        return _refresh_online_proof(root, account_ref, api_key)
    return status


def verify_offline_account_state(
    api_key: str,
    *,
    app_dir: Path | None = None,
    credential_file: Path | None = None,
    env_key_in_use: bool | None = None,
) -> dict[str, Any]:
    """Authorize local control state without cloud access using a key-bound proof.

    Schema-1 owners can migrate only when the saved credentials file is provably
    older than the online binding. A subsequently replaced key is never allowed
    to claim the previous account's local state while offline.
    """

    root = _root(app_dir)
    owner_path = _owner_path(root)
    owner = _read_json(owner_path) or {}
    account_ref = str(owner.get("account_ref") or "")
    if not _ACCOUNT_REF.fullmatch(account_ref):
        raise AccountStateError(
            {
                "ok": False,
                "error": "offline_account_proof_required",
                "message": (
                    "Cloud account verification is unavailable and this local state has no "
                    "verified account binding for offline control."
                ),
                "offline": True,
                "stale": True,
                "next_suggested": "jobagent doctor env",
            }
        )

    fingerprint = str(owner.get("key_fingerprint") or "")
    if fingerprint:
        if not hmac.compare_digest(fingerprint, _key_fingerprint(api_key)):
            raise AccountStateError(
                {
                    "ok": False,
                    "error": "offline_account_proof_mismatch",
                    "message": (
                        "The configured API key does not match the key-bound proof for the "
                        "active local state. Online account verification is required."
                    ),
                    "offline": True,
                    "stale": True,
                    "next_suggested": "jobagent account status",
                }
            )
    else:
        env_active = (
            bool(os.environ.get("JOBAGENT_API_KEY"))
            if env_key_in_use is None
            else env_key_in_use
        )
        credentials = credential_file or (root / "credentials")
        bound_timestamp = _timestamp(owner.get("bound_at"))
        migration_safe = bool(
            int(owner.get("schema_version") or 1) == 1
            and not env_active
            and credentials.exists()
            and bound_timestamp is not None
        )
        if migration_safe:
            try:
                saved_key = credentials.read_text(encoding="utf-8").strip()
                migration_safe = bool(
                    saved_key
                    and hmac.compare_digest(saved_key, api_key)
                    and _credential_mtime(credentials) <= float(bound_timestamp)
                )
            except OSError:
                migration_safe = False
        if not migration_safe:
            raise AccountStateError(
                {
                    "ok": False,
                    "error": "offline_account_proof_required",
                    "message": (
                        "Cloud account verification is unavailable and the existing local "
                        "binding cannot be safely upgraded to a key-bound offline proof."
                    ),
                    "offline": True,
                    "stale": True,
                    "next_suggested": "jobagent doctor env",
                }
            )
        owner.update(
            {
                "schema_version": OWNER_SCHEMA_VERSION,
                "key_fingerprint": _key_fingerprint(api_key),
                "verified_at": owner.get("bound_at"),
                "proof_source": "legacy_bound_credentials",
            }
        )
        _write_json(owner_path, owner)

    return {
        "status": "offline_verified",
        "ready": True,
        "offline": True,
        "stale": True,
        "verified_at": owner.get("verified_at") or owner.get("bound_at"),
        "proof_source": owner.get("proof_source") or "key_fingerprint",
    }


def bind_legacy_state(
    account_response: dict[str, Any],
    *,
    confirm_legacy: bool,
    api_key: str | None = None,
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
    bound = _bind(
        root,
        account_ref,
        reason="confirmed_legacy_state",
        api_key=api_key,
    )
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
    api_key: str | None = None,
    app_dir: Path | None = None,
) -> dict[str, Any]:
    account_ref = account_ref_from_response(account_response)
    root = _root(app_dir)
    status = state_owner_status(account_ref, app_dir=root)
    if status["status"] == "ready":
        if api_key:
            status = _refresh_online_proof(root, account_ref, api_key)
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
        bound = _bind(root, account_ref, reason="new_empty_state", api_key=api_key)
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
        bound = _bind(root, account_ref, reason="account_switch", api_key=api_key)
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
