"""Privacy-bounded, account-bound relay for committed CLI lifecycle facts."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from jobagent import __version__
from jobagent.infra import state


EVENT_SCHEMA_VERSION = "1.0"
SPOOL_SCHEMA_VERSION = 1
MAX_RELAY_BATCH = 25
MAX_SPOOL_EVENTS = 25

_INITIALIZED_EVENT = "jobagent_initialized"
_DELIVERY_EVENT = "delivery_verified"
_PLATFORMS = frozenset({"boss", "liepin", "zhilian", "51job"})
_DISABLE_ENV_VARS = (
    "JOBAGENT_ANALYTICS_DISABLED",
    "JOBAGENT_ANALYTICS_KILL_SWITCH",
    "DO_NOT_TRACK",
)
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_CLIENT_RELEASE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$")

_STATE_THREAD_LOCK = threading.Lock()
_FLUSH_THREAD_LOCK = threading.Lock()
_WORKER_GUARD = threading.Lock()
_worker: threading.Thread | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _disabled() -> bool:
    return any(
        str(os.environ.get(name) or "").strip().lower() in _TRUE_VALUES
        for name in _DISABLE_ENV_VARS
    )


def _spool_path() -> Path:
    from jobagent.infra.account_state import current_account_ref

    account_ref = current_account_ref(app_dir=_active_app_dir())
    if not account_ref:
        return state.STATE_DIR / "analytics_spool.json"
    return (
        _active_app_dir()
        / "accounts"
        / account_ref
        / "state"
        / "analytics_spool.json"
    )


def _state_lock_path() -> Path:
    return state.STATE_DIR / "locks" / "analytics-state.lock"


def _flush_lock_path() -> Path:
    return state.STATE_DIR / "locks" / "analytics-flush.lock"


def _active_app_dir() -> Path:
    return state.STATE_DIR.parent


def _pid_alive(pid: int) -> bool:
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


def _lock_pid(path: Path) -> int:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return 0


@contextmanager
def _exclusive_lock(path: Path, thread_lock: threading.Lock) -> Iterator[bool]:
    """Acquire a short-lived cross-process lock without waiting."""

    if not thread_lock.acquire(blocking=False):
        yield False
        return
    fd: int | None = None
    acquired = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        for _attempt in range(2):
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                acquired = True
                os.write(fd, str(os.getpid()).encode("ascii"))
                os.fsync(fd)
                break
            except FileExistsError:
                if _pid_alive(_lock_pid(path)):
                    break
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    break
        yield acquired
    finally:
        if fd is not None:
            os.close(fd)
        if acquired:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
        thread_lock.release()


def _empty_spool() -> dict[str, Any]:
    return {"schema_version": SPOOL_SCHEMA_VERSION, "facts": [], "events": []}


def _fact_key(event_name: str, payload: dict[str, Any]) -> str | None:
    if event_name == _INITIALIZED_EVENT:
        return _INITIALIZED_EVENT
    if event_name == _DELIVERY_EVENT:
        platform = str(payload.get("platform") or "")
        if platform in _PLATFORMS:
            return f"{_DELIVERY_EVENT}:{platform}"
    return None


def _allowed_fact_keys() -> frozenset[str]:
    return frozenset(
        {_INITIALIZED_EVENT}
        | {f"{_DELIVERY_EVENT}:{platform}" for platform in _PLATFORMS}
    )


def _valid_event(event: Any) -> bool:
    if not isinstance(event, dict) or set(event) != {
        "event_id",
        "event_name",
        "schema_version",
        "occurred_at",
        "payload",
    }:
        return False
    try:
        parsed_id = uuid.UUID(str(event["event_id"]))
    except (ValueError, TypeError, AttributeError):
        return False
    if parsed_id.version != 4 or str(parsed_id) != str(event["event_id"]):
        return False
    if event.get("schema_version") != EVENT_SCHEMA_VERSION:
        return False
    occurred_at = str(event.get("occurred_at") or "")
    try:
        parsed_time = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if not occurred_at.endswith("Z") or parsed_time.tzinfo is None:
        return False
    event_name = str(event.get("event_name") or "")
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return False
    client_release = payload.get("client_release")
    if not isinstance(client_release, str) or not _CLIENT_RELEASE.fullmatch(
        client_release
    ):
        return False
    if event_name == _INITIALIZED_EVENT:
        return set(payload) == {"client_release"}
    if event_name == _DELIVERY_EVENT:
        return (
            set(payload) == {"platform", "client_release"}
            and payload.get("platform") in _PLATFORMS
        )
    return False


def _load_spool() -> dict[str, Any] | None:
    path = _spool_path()
    if not path.exists():
        return _empty_spool()
    try:
        os.chmod(path, 0o600)
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "facts",
        "events",
    }:
        return None
    if payload.get("schema_version") != SPOOL_SCHEMA_VERSION:
        return None
    facts = payload.get("facts")
    events = payload.get("events")
    if not isinstance(facts, list) or not isinstance(events, list):
        return None
    if len(events) > MAX_SPOOL_EVENTS:
        return None
    if len(facts) != len(set(facts)) or not set(facts).issubset(
        _allowed_fact_keys()
    ):
        return None
    if any(not _valid_event(event) for event in events):
        return None
    event_ids = [str(event["event_id"]) for event in events]
    if len(event_ids) != len(set(event_ids)):
        return None
    if any(
        _fact_key(str(event["event_name"]), event["payload"]) not in facts
        for event in events
    ):
        return None
    return payload


def _write_spool(payload: dict[str, Any]) -> None:
    path = _spool_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    fd: int | None = None
    try:
        fd = os.open(str(temporary), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _owner_matches(api_key: str) -> bool:
    if not api_key:
        return False
    from jobagent.infra.account_state import api_key_matches_current_owner

    return api_key_matches_current_owner(api_key, app_dir=_active_app_dir())


def _new_event(event_name: str, payload: dict[str, str]) -> dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "event_name": event_name,
        "schema_version": EVENT_SCHEMA_VERSION,
        "occurred_at": _utc_now(),
        "payload": payload,
    }


def _record_fact(event_name: str, payload: dict[str, str], *, api_key: str) -> bool:
    if _disabled() or not _owner_matches(api_key):
        return False
    fact = _fact_key(event_name, payload)
    if fact is None:
        return False
    with _exclusive_lock(_state_lock_path(), _STATE_THREAD_LOCK) as acquired:
        if not acquired or not _owner_matches(api_key):
            return False
        spool = _load_spool()
        if spool is None or fact in spool["facts"]:
            return False
        if len(spool["events"]) >= MAX_SPOOL_EVENTS:
            return False
        event = _new_event(event_name, payload)
        if not _valid_event(event):
            return False
        spool["facts"].append(fact)
        spool["events"].append(event)
        _write_spool(spool)
    schedule_flush(api_key=api_key)
    return True


def record_jobagent_initialized(*, api_key: str) -> bool:
    """Persist the first verified init fact for the active account."""

    try:
        return _record_fact(
            _INITIALIZED_EVENT,
            {"client_release": __version__},
            api_key=api_key,
        )
    except Exception:
        return False


def record_delivery_verified(platform: str, *, api_key: str | None = None) -> bool:
    """Persist the first audited real delivery fact for one platform."""

    try:
        normalized = str(platform or "").strip().lower()
        if normalized not in _PLATFORMS:
            return False
        if api_key is None:
            from jobagent.infra.credentials import load_api_key

            api_key = load_api_key()
        return _record_fact(
            _DELIVERY_EVENT,
            {"platform": normalized, "client_release": __version__},
            api_key=str(api_key or ""),
        )
    except Exception:
        return False


def _acknowledged_ids(
    response: Any,
    *,
    pending_ids: set[str],
) -> set[str] | None:
    if not isinstance(response, dict):
        return None
    response_keys = {"accepted_event_ids", "duplicate_event_ids", "rejected"}
    if not response_keys.intersection(response):
        return None
    accepted = response.get("accepted_event_ids", [])
    duplicates = response.get("duplicate_event_ids", [])
    rejected = response.get("rejected", [])
    if (
        not isinstance(accepted, list)
        or not isinstance(duplicates, list)
        or not isinstance(rejected, list)
    ):
        return None
    acknowledged = {str(event_id) for event_id in accepted + duplicates}
    if len(acknowledged) != len(accepted) + len(duplicates):
        return None
    rejected_ids: set[str] = set()
    for item in rejected:
        if not isinstance(item, dict) or not isinstance(item.get("event_id"), str):
            return None
        rejected_ids.add(item["event_id"])
    if not acknowledged.issubset(pending_ids) or not rejected_ids.issubset(pending_ids):
        return None
    if acknowledged.intersection(rejected_ids):
        return None
    return acknowledged


def _flush_once(*, api_key: str | None = None) -> bool:
    """Relay one bounded batch. All failures retain the original spool."""

    if _disabled():
        return False
    if api_key is None:
        from jobagent.infra.credentials import load_api_key

        api_key = load_api_key()
    key = str(api_key or "")
    if not _owner_matches(key):
        return False
    with _exclusive_lock(_flush_lock_path(), _FLUSH_THREAD_LOCK) as flush_acquired:
        if not flush_acquired or not _owner_matches(key):
            return False
        with _exclusive_lock(_state_lock_path(), _STATE_THREAD_LOCK) as state_acquired:
            if not state_acquired:
                return False
            spool = _load_spool()
            if spool is None:
                return False
            events = [dict(event) for event in spool["events"][:MAX_RELAY_BATCH]]
        if not events or not _owner_matches(key):
            return False
        try:
            from jobagent.infra import cloud_client

            response = cloud_client.analytics_events(events, api_key=key)
        except Exception:
            return False
        pending_ids = {str(event["event_id"]) for event in events}
        acknowledged = _acknowledged_ids(response, pending_ids=pending_ids)
        if acknowledged is None or not acknowledged or not _owner_matches(key):
            return False
        with _exclusive_lock(_state_lock_path(), _STATE_THREAD_LOCK) as state_acquired:
            if not state_acquired or not _owner_matches(key):
                return False
            current = _load_spool()
            if current is None:
                return False
            current["events"] = [
                event
                for event in current["events"]
                if str(event.get("event_id") or "") not in acknowledged
            ]
            _write_spool(current)
        return True


def _flush_worker(api_key: str | None) -> None:
    try:
        _flush_once(api_key=api_key)
    except Exception:
        pass


def schedule_flush(*, api_key: str | None = None) -> bool:
    """Start one best-effort daemon relay without delaying the CLI command."""

    global _worker
    try:
        if _disabled():
            return False
        key = api_key
        if key is None:
            from jobagent.infra.credentials import load_api_key

            key = load_api_key()
        if not _owner_matches(str(key or "")):
            return False
        with _WORKER_GUARD:
            if _worker is not None and _worker.is_alive():
                return False
            _worker = threading.Thread(
                target=_flush_worker,
                args=(str(key or ""),),
                name="jobagent-analytics-relay",
                daemon=True,
            )
            _worker.start()
        return True
    except Exception:
        return False
