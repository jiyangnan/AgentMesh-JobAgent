"""Durable permissions and receipts for host-executed browser work.

This ledger coordinates product commands, not the host's browser tools. An
intent records that permission was issued; it cannot prove whether a host
clicked, nor fence a host that operates outside the protocol. Unclear effects
therefore retain the global lease and permit only read-only reconciliation.
Platform evidence is validated by the application before ``submit_work``.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import secrets
import sqlite3
from typing import Iterator

from jobagent.infra import state as state_store


SCHEMA_VERSION = 1
MAX_OBSERVATION_ATTEMPTS = 3
_STATES = {"ready", "intent_recorded", "reconcile_only", "closed"}


class BrowserWorkError(Exception):
    """A public-safe protocol failure that preserves existing work."""

    def __init__(self, error: str, message: str):
        self.payload = {
            "ok": False,
            "error": error,
            "message": message,
            "request_preserved": True,
        }
        super().__init__(message)


def _error(error: str, message: str) -> BrowserWorkError:
    return BrowserWorkError(error, message)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _json_value(value: object) -> bool:
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeError:
            return False
        return True
    if value is None or isinstance(value, (bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_value(key) and _json_value(item)
                   for key, item in value.items())
    return False


def _mapping(value: object, label: str) -> dict:
    try:
        valid = isinstance(value, dict) and _json_value(value)
        if valid:
            return json.loads(_canonical(value))
    except (RecursionError, TypeError, ValueError):
        pass
    raise _error("browser_work_invalid_input", f"{label} must be a JSON object.")


def _binding(value: object) -> dict:
    binding = _mapping(value, "binding")
    for key in ("account_ref", "round_id"):
        if not isinstance(binding.get(key), str) or not binding[key].strip():
            raise _error("browser_work_binding_required",
                         "Browser work requires an account and round binding.")
    return binding


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or not _json_value(value):
        raise _error("browser_work_invalid_input", f"{label} must be non-empty text.")
    return value


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _matches(actual: dict, expected: dict) -> bool:
    # Canonical comparison does not treat True and 1 as the same context.
    return all(key in actual and _canonical(actual[key]) == _canonical(value)
               for key, value in expected.items())


def _initialize(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    if version != 0 or conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone():
        raise _error("browser_work_schema_unsupported",
                     "This browser work ledger requires a compatible client.")
    conn.execute("""
        CREATE TABLE works (
            work_id TEXT PRIMARY KEY,
            specification_digest TEXT NOT NULL,
            action TEXT NOT NULL,
            task_json TEXT NOT NULL,
            binding_json TEXT NOT NULL,
            side_effect INTEGER NOT NULL CHECK (side_effect IN (0, 1)),
            state TEXT NOT NULL CHECK
                (state IN ('ready', 'intent_recorded', 'reconcile_only', 'closed')),
            nonce TEXT,
            result_json TEXT,
            observation_attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    # A ready work also holds the lease. It cannot be bypassed by changing
    # account, round, request or process. There is intentionally no expiry.
    conn.execute("""
        CREATE UNIQUE INDEX one_open_browser_work
        ON works ((1)) WHERE state != 'closed'
    """)
    conn.execute("""
        CREATE TABLE receipts (
            receipt_id TEXT PRIMARY KEY,
            work_id TEXT NOT NULL REFERENCES works(work_id),
            digest TEXT NOT NULL,
            result_json TEXT NOT NULL,
            received_at TEXT NOT NULL
        )
    """)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


@contextmanager
def _transaction(*, create: bool = False) -> Iterator[sqlite3.Connection | None]:
    """Use a new connection and transaction for every command/process."""
    conn = None
    try:
        path = state_store.STATE_DIR / "browser-work.sqlite3"
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
        elif not path.exists():
            yield None
            return
        conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("BEGIN IMMEDIATE")
        _initialize(conn)
        yield conn
        conn.commit()
    except BrowserWorkError:
        if conn is not None:
            conn.rollback()
        raise
    except (sqlite3.Error, OSError) as exc:
        if conn is not None:
            conn.rollback()
        raise _error("browser_work_storage_unavailable",
                     "Browser work storage is unavailable; existing work is preserved.") from exc
    finally:
        if conn is not None:
            conn.close()


def _decode(row: sqlite3.Row) -> dict:
    try:
        binding = json.loads(row["binding_json"])
        task = json.loads(row["task_json"])
        result = json.loads(row["result_json"]) if row["result_json"] else None
        if row["state"] not in _STATES or not isinstance(binding, dict) or not isinstance(task, dict):
            raise ValueError("invalid ledger record")
        return {
            "protocol": "jobagent.browser_work",
            "version": 1,
            "work_id": row["work_id"],
            "action": row["action"],
            "task": task,
            "binding": binding,
            "side_effect": bool(row["side_effect"]),
            "state": row["state"],
            "nonce": row["nonce"],
            "result": result,
            "observation_attempts": row["observation_attempts"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "execution_permitted": False,
            "reconciliation_required": row["state"] in {"intent_recorded", "reconcile_only"},
        }
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise _error("browser_work_storage_unavailable",
                     "Browser work storage is invalid; existing work is preserved.") from exc


def _get(conn: sqlite3.Connection | None, work_id: str, binding: dict) -> dict:
    row = conn.execute("SELECT * FROM works WHERE work_id = ?", (work_id,)).fetchone() if conn else None
    if row is None:
        raise _error("browser_work_not_found", "Browser work was not found.")
    work = _decode(row)
    if not _matches(work["binding"], binding):
        raise _error("browser_work_binding_mismatch",
                     "Browser work does not match the verified context.")
    return work


def ensure_work(*, action: str, task: dict, binding: dict,
                side_effect: bool = False, key: str | None = None) -> dict:
    """Create or recover exactly the same task without granting permission.

    An explicit key is scoped to the full binding and action. Reusing that key
    with a changed task or effect classification is a conflict, not a new work.
    """
    action = _text(action, "action")
    task = _mapping(task, "task")
    binding = _binding(binding)
    if not isinstance(side_effect, bool):
        raise _error("browser_work_invalid_input", "side_effect must be a boolean.")
    if key is not None:
        key = _text(key, "key")
    specification = {"action": action, "task": task, "binding": binding,
                     "side_effect": side_effect}
    identity = specification if key is None else {"action": action, "binding": binding, "key": key}
    work_id = "bw_" + _digest(identity)
    specification_digest = _digest(specification)
    with _transaction(create=True) as conn:
        existing = conn.execute("SELECT * FROM works WHERE work_id = ?", (work_id,)).fetchone()
        if existing is not None:
            if existing["specification_digest"] != specification_digest:
                raise _error("browser_work_conflict",
                             "An existing browser work key has a different task.")
            return _decode(existing)
        if conn.execute("SELECT 1 FROM works WHERE state != 'closed' LIMIT 1").fetchone():
            # Do not disclose the other account, task, platform, or work ID.
            raise _error("browser_work_pending",
                         "Another browser work remains open; finish or reconcile it first.")
        now = _now()
        conn.execute("""
            INSERT INTO works (work_id, specification_digest, action, task_json,
                binding_json, side_effect, state, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'ready', ?, ?)
        """, (work_id, specification_digest, action, _canonical(task),
              _canonical(binding), int(side_effect), now, now))
        return _get(conn, work_id, binding)


def get_work(work_id, binding) -> dict:
    """Read work matching an account/round plus optional expected context."""
    work_id, binding = _text(work_id, "work_id"), _binding(binding)
    with _transaction() as conn:
        return _get(conn, work_id, binding)


def list_work(binding) -> list[dict]:
    """List only work matching the supplied account-bound context."""
    binding = _binding(binding)
    with _transaction() as conn:
        if conn is None:
            return []
        works = [_decode(row) for row in conn.execute("SELECT * FROM works ORDER BY created_at, work_id")]
        return [work for work in works if _matches(work["binding"], binding)]


def list_account_work(account_ref: str) -> list[dict]:
    """Read one verified account's history across rounds, never other accounts."""
    account_ref = _text(account_ref, "account_ref")
    with _transaction() as conn:
        if conn is None:
            return []
        works = [_decode(row) for row in conn.execute("SELECT * FROM works ORDER BY created_at, work_id")]
        return [work for work in works if work["binding"].get("account_ref") == account_ref]


def pending_work(binding) -> dict | None:
    """Recover current work; an issued intent never implies a new permission."""
    binding = _binding(binding)
    with _transaction() as conn:
        row = conn.execute("SELECT * FROM works WHERE state != 'closed'").fetchone() if conn else None
        if row is None:
            return None
        work = _decode(row)
        if not _matches(work["binding"], binding):
            return None
        if work["state"] == "intent_recorded":
            conn.execute("UPDATE works SET state = 'reconcile_only', updated_at = ? WHERE work_id = ?",
                         (_now(), work["work_id"]))
            work = _get(conn, work["work_id"], binding)
        return work


def begin_work(work_id, binding) -> dict:
    """Persist intent before returning a one-use side-effect permission.

    Read-only work can be observed again up to MAX_OBSERVATION_ATTEMPTS. Its
    nonce remains stable so a delayed receipt can still be reconciled.
    """
    work_id, binding = _text(work_id, "work_id"), _binding(binding)
    with _transaction() as conn:
        work = _get(conn, work_id, binding)
        if work["state"] == "closed":
            return work
        if work["side_effect"] and work["state"] != "ready":
            conn.execute("UPDATE works SET state = 'reconcile_only', updated_at = ? WHERE work_id = ?",
                         (_now(), work_id))
            return _get(conn, work_id, binding)
        if not work["side_effect"] and work["observation_attempts"] >= MAX_OBSERVATION_ATTEMPTS:
            raise _error("browser_work_observation_limit",
                         "The read-only observation limit was reached; preserve the current work.")
        nonce = work["nonce"] or secrets.token_urlsafe(32)
        attempts = work["observation_attempts"] + (0 if work["side_effect"] else 1)
        conn.execute("""
            UPDATE works SET state = 'intent_recorded', nonce = ?,
                observation_attempts = ?, updated_at = ? WHERE work_id = ?
        """, (nonce, attempts, _now(), work_id))
        work = _get(conn, work_id, binding)
        work["execution_permitted"] = True
        work["reconciliation_required"] = False
        return work


def submit_work(work_id, binding, result: dict) -> dict:
    """Atomically record an application-validated receipt and its transition."""
    work_id, binding = _text(work_id, "work_id"), _binding(binding)
    result = _mapping(result, "result")
    receipt_id = _text(result.get("receipt_id"), "receipt_id")
    outcome = _text(result.get("outcome"), "outcome")
    result_binding = _binding(result.get("binding"))
    nonce = _text(result.get("nonce"), "nonce")
    digest = _digest(result)
    with _transaction() as conn:
        work = _get(conn, work_id, binding)
        if _canonical(result_binding) != _canonical(work["binding"]) or any(
            name in result and result[name] != value
            for name, value in (("work_id", work_id), ("action", work["action"]))
        ):
            raise _error("browser_work_binding_mismatch",
                         "The receipt does not match the browser work context.")
        if work["nonce"] is None or not secrets.compare_digest(nonce.encode("utf-8"), work["nonce"].encode("utf-8")):
            raise _error("browser_work_nonce_mismatch",
                         "The receipt does not match the recorded execution intent.")
        previous = conn.execute("SELECT work_id, digest FROM receipts WHERE receipt_id = ?",
                                (receipt_id,)).fetchone()
        if previous is not None:
            if previous["work_id"] != work_id or previous["digest"] != digest:
                raise _error("browser_work_receipt_conflict",
                             "This receipt ID was already used with different content.")
            work["receipt_replayed"] = True
            return work
        if work["state"] == "closed":
            raise _error("browser_work_closed",
                         "This browser work is already closed; its result is preserved.")
        if work["state"] == "ready":
            raise _error("browser_work_intent_required",
                         "Record execution intent before submitting a browser work receipt.")
        now = _now()
        result_json = _canonical(result)
        conn.execute("INSERT INTO receipts VALUES (?, ?, ?, ?, ?)",
                     (receipt_id, work_id, digest, result_json, now))
        next_state = "reconcile_only" if outcome == "uncertain" else "closed"
        conn.execute("UPDATE works SET state = ?, result_json = ?, updated_at = ? WHERE work_id = ?",
                     (next_state, result_json, now, work_id))
        work = _get(conn, work_id, binding)
        work["receipt_replayed"] = False
        return work


def has_inflight() -> bool:
    """Whether any account has an issued or unresolved execution intent."""
    with _transaction() as conn:
        if conn is None:
            return False
        return conn.execute("SELECT 1 FROM works WHERE state IN ('intent_recorded', 'reconcile_only') LIMIT 1").fetchone() is not None


def has_open() -> bool:
    """Account switches and round replacement must not orphan ready work either."""
    with _transaction() as conn:
        return conn is not None and conn.execute("SELECT 1 FROM works WHERE state != 'closed' LIMIT 1").fetchone() is not None


def cancel_unexecuted(work_id, binding) -> dict:
    """Cancel only work that can no longer cause a recruiting side effect."""
    work_id, binding = _text(work_id, "work_id"), _binding(binding)
    with _transaction() as conn:
        work = _get(conn, work_id, binding)
        if work["state"] == "closed":
            return work
        if work["side_effect"] and work["state"] != "ready":
            raise _error("browser_work_reconciliation_required", "An issued external action cannot be cancelled; inspect its receipt without clicking again.")
        result = {"outcome": "cancelled", "reason": "explicit_user_cancellation"}
        conn.execute("UPDATE works SET state = 'closed', result_json = ?, updated_at = ? WHERE work_id = ?",
                     (_canonical(result), _now(), work_id))
        return _get(conn, work_id, binding)
