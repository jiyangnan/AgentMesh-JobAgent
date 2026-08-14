"""Versioned, conservative migration of persisted Job Agent client state."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jobagent import __version__
from jobagent.domain.reviewability import delivery_reviewability_issues
from jobagent.infra.cloud_client import PROTOCOL_VERSION
from jobagent.infra.profile_contract import profile_compatibility_issues
from jobagent.infra.rounds import migrate_round_payload
from jobagent.infra.state import APP_DIR

STATE_MIGRATION_VERSION = 5

_EPHEMERAL_FILES = (
    "state/release_manifest_cache.json",
    "state/platform_tabs.json",
    "state/browser_session.json",
    "state/last_doctor_report.json",
    "state/last_probe_send.json",
)
_LOCK_FILES = (
    "state/locks/activity.lock",
    "state/locks/browser-session.lock",
    "state/locks/update.lock",
)
_RECOVERY_COMMANDS = {
    None,
    "account",
    "doctor",
    "init",
    "platforms",
    "profile",
    "resume",
    "round-status",
    "support",
    "update",
    "upgrade-check",
}


class UpgradeCompatibilityError(RuntimeError):
    def __init__(self, payload: dict[str, Any]):
        super().__init__(str(payload.get("message") or payload.get("error")))
        self.payload = payload


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _default_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _lock_pid(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if raw.startswith("{"):
            payload = json.loads(raw)
            raw = str(payload.get("pid") or "")
        return int(raw)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _relative(path: Path, app_dir: Path) -> str:
    return path.relative_to(app_dir).as_posix()


def _has_existing_state(app_dir: Path) -> bool:
    state = app_dir / "state"
    candidates = (
        app_dir / "credentials",
        state / "profile.json",
        state / "current_round.json",
        state / "audit_log.json",
        state / "liepin_audit_log.json",
        state / "zhilian_audit_log.json",
        state / "job51_audit_log.json",
        state / "release_manifest_cache.json",
        state / "platform_tabs.json",
    )
    return any(path.exists() for path in candidates)


def _conflicts(app_dir: Path) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    credentials = app_dir / "credentials"
    try:
        key = credentials.read_text(encoding="utf-8").strip()
    except OSError:
        key = ""
    if key.startswith("jba_live_"):
        conflicts.append(
            {
                "code": "retired_api_key",
                "message": "The saved API key uses a retired format.",
                "next_suggested": "jobagent init --key <your_api_key>",
            }
        )

    profile_path = app_dir / "state" / "profile.json"
    if profile_path.exists():
        profile = _read_json(profile_path)
        issues = (
            profile_compatibility_issues(profile)
            if profile is not None
            else ["profile.json is not valid JSON"]
        )
        if issues:
            conflicts.append(
                {
                    "code": "profile_incompatible",
                    "message": "The saved profile is incompatible: " + "; ".join(issues),
                    "next_suggested": "jobagent resume analyze --file <resume>",
                }
            )
    return conflicts


def _migrate_delivery_confirmation_state(
    root: Path,
    *,
    migrated: list[str],
    cleared: list[str],
) -> None:
    """Invalidate pre-confirmation previews without discarding signed decisions."""

    state_dir = root / "state"
    discoveries = state_dir / "discoveries"
    if discoveries.exists():
        for path in discoveries.rglob("*.review.json"):
            payload = _read_json(path)
            if payload is None:
                continue
            changed = False
            for key in ("delivery_preview", "delivery_authorization"):
                if key in payload:
                    payload.pop(key, None)
                    changed = True
            if changed:
                _write_json(path, payload)
                migrated.append(_relative(path, root))

    pending_path = state_dir / "pending_interaction.json"
    pending = _read_json(pending_path)
    if pending and str(pending.get("kind") or "").startswith("delivery_"):
        pending_path.unlink()
        cleared.append(_relative(pending_path, root))

    round_path = state_dir / "current_round.json"
    current = _read_json(round_path)
    if current is None:
        return
    changed = False
    platforms = current.get("platforms")
    if isinstance(platforms, dict):
        for platform, item in platforms.items():
            if not isinstance(item, dict) or item.get("status") not in {
                "reviewed",
                "awaiting_delivery_confirmation",
            }:
                continue
            item["status"] = "awaiting_delivery_confirmation"
            item["next_suggested"] = (
                "jobagent boss greet preview"
                if platform == "boss"
                else f"jobagent {platform} apply review"
            )
            evidence = item.get("evidence")
            if isinstance(evidence, dict):
                evidence.pop("preview_id", None)
                evidence.pop("authorization_id", None)
            changed = True
    if changed:
        _write_json(round_path, current)
        relative = _relative(round_path, root)
        if relative not in migrated:
            migrated.append(relative)


def _migrate_zhilian_reviewability_state(
    root: Path,
    *,
    migrated: list[str],
    cleared: list[str],
) -> None:
    """Invalidate only Zhilian previews whose core review fields are unsafe."""

    state_dir = root / "state"
    discoveries = state_dir / "discoveries" / "zhilian"
    affected_discoveries: set[str] = set()
    if discoveries.exists():
        for path in discoveries.glob("*.review.json"):
            payload = _read_json(path)
            if payload is None:
                continue
            send_candidates = [
                item
                for item in (payload.get("send_candidates") or [])
                if isinstance(item, dict)
            ]
            if not any(delivery_reviewability_issues(item) for item in send_candidates):
                continue
            changed = False
            for key in ("delivery_preview", "delivery_authorization"):
                if key in payload:
                    payload.pop(key, None)
                    changed = True
            if changed:
                _write_json(path, payload)
                migrated.append(_relative(path, root))
            discover_id = str(payload.get("discover_id") or "")
            if discover_id:
                affected_discoveries.add(discover_id)

    if not affected_discoveries:
        return

    pending_path = state_dir / "pending_interaction.json"
    pending = _read_json(pending_path)
    context = pending.get("context") if isinstance(pending, dict) else None
    if (
        isinstance(pending, dict)
        and str(pending.get("kind") or "").startswith("delivery_")
        and isinstance(context, dict)
        and str(context.get("discover_id") or "") in affected_discoveries
    ):
        pending_path.unlink()
        cleared.append(_relative(pending_path, root))

    round_path = state_dir / "current_round.json"
    current = _read_json(round_path)
    if current is None:
        return
    platforms = current.get("platforms")
    zhilian = platforms.get("zhilian") if isinstance(platforms, dict) else None
    evidence = zhilian.get("evidence") if isinstance(zhilian, dict) else None
    if (
        not isinstance(zhilian, dict)
        or zhilian.get("status") not in {"reviewed", "awaiting_delivery_confirmation"}
        or not isinstance(evidence, dict)
        or str(evidence.get("discover_id") or "") not in affected_discoveries
    ):
        return
    zhilian["status"] = "awaiting_delivery_confirmation"
    zhilian["next_suggested"] = "jobagent zhilian apply review"
    evidence.pop("preview_id", None)
    evidence.pop("authorization_id", None)
    _write_json(round_path, current)
    relative = _relative(round_path, root)
    if relative not in migrated:
        migrated.append(relative)


def run_client_upgrade(
    *,
    app_dir: Path | None = None,
    current_version: str = __version__,
    protocol_version: int = PROTOCOL_VERSION,
    pid_alive: Callable[[int], bool] | None = None,
) -> dict[str, Any]:
    """Migrate safe state and report conflicts that require user recovery."""
    root = Path(app_dir) if app_dir is not None else APP_DIR
    state_dir = root / "state"
    marker_path = state_dir / "client_upgrade_state.json"
    round_path = state_dir / "current_round.json"
    marker = _read_json(marker_path)
    round_payload = _read_json(round_path)
    round_invalid = round_path.exists() and round_payload is None
    prior_version = marker.get("client_version") if marker else None
    prior_protocol = marker.get("protocol_version") if marker else None
    prior_migration_version = int(marker.get("state_migration_version") or 0) if marker else 0
    migration_changed = prior_migration_version != STATE_MIGRATION_VERSION
    version_changed = prior_version is not None and prior_version != current_version
    protocol_changed = prior_protocol is not None and prior_protocol != protocol_version
    upgrade_detected = bool(
        version_changed
        or protocol_changed
        or bool(marker and marker.get("migration_pending"))
        or round_invalid
        or migration_changed
        and _has_existing_state(root)
    )
    alive = pid_alive or _default_pid_alive
    cleared: list[str] = []
    migrated: list[str] = []
    archived: list[str] = []
    conflicts = _conflicts(root)

    live_locks: list[Path] = []
    if upgrade_detected:
        for relative in _LOCK_FILES:
            path = root / relative
            if not path.exists():
                continue
            pid = _lock_pid(path)
            if pid is not None and alive(pid):
                live_locks.append(path)
                conflicts.append(
                    {
                        "code": "active_process_lock",
                        "message": f"Another Job Agent process is active (PID {pid}).",
                        "next_suggested": "Wait for the active Job Agent command to finish, then retry.",
                    }
                )

    if upgrade_detected and not live_locks:
        for relative in _EPHEMERAL_FILES:
            path = root / relative
            if path.exists():
                path.unlink()
                cleared.append(relative)

        for relative in _LOCK_FILES:
            path = root / relative
            if path.exists():
                path.unlink()
                cleared.append(relative)

        if round_invalid:
            archive = state_dir / "archive" / f"current_round-invalid-{_timestamp()}.json"
            archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(round_path), str(archive))
            archived.append(_relative(archive, root))
        elif round_payload is not None:
            migrated_round = migrate_round_payload(round_payload)
            if migrated_round != round_payload:
                _write_json(round_path, migrated_round)
                migrated.append("state/current_round.json")

        if prior_migration_version < 4:
            _migrate_delivery_confirmation_state(
                root,
                migrated=migrated,
                cleared=cleared,
            )
        if prior_migration_version < 5:
            _migrate_zhilian_reviewability_state(
                root,
                migrated=migrated,
                cleared=cleared,
            )

        discoveries = state_dir / "discoveries"
        if protocol_changed and discoveries.exists():
            archive = state_dir / "archive" / f"discoveries-protocol-{prior_protocol}-{_timestamp()}"
            archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(discoveries), str(archive))
            archived.append(_relative(archive, root))

    next_suggested = (
        conflicts[0]["next_suggested"] if conflicts else "jobagent round status"
    )
    marker_payload = {
        "state_migration_version": STATE_MIGRATION_VERSION,
        "client_version": current_version,
        "protocol_version": protocol_version,
        "status": "blocked" if conflicts else "ready",
        "migration_pending": bool(live_locks),
        "conflicts": conflicts,
        "checked_at": _utc_now(),
    }
    _write_json(marker_path, marker_payload)
    return {
        "ok": not conflicts,
        "upgrade_detected": upgrade_detected,
        "version_changed": version_changed,
        "from_version": prior_version or "unknown",
        "to_version": current_version,
        "state_migration_version": STATE_MIGRATION_VERSION,
        "cleared": cleared,
        "migrated": migrated,
        "archived": archived,
        "conflicts": conflicts,
        "next_suggested": next_suggested,
    }


def enforce_upgrade_for_command(
    command: str | None,
    report: dict[str, Any],
) -> dict[str, Any]:
    """Block state-changing platform commands until upgrade conflicts are repaired."""
    if report.get("ok") or command in _RECOVERY_COMMANDS:
        return report
    raise UpgradeCompatibilityError(
        {
            "ok": False,
            "error": "client_upgrade_required",
            "message": "Resolve the reported local-state conflicts before platform automation.",
            "conflicts": report.get("conflicts", []),
            "next_suggested": report.get("next_suggested"),
        }
    )
