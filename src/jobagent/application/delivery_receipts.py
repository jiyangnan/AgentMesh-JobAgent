"""Durable business-receipt outbox; synchronization never repeats delivery."""
from pathlib import Path

from jobagent.infra import state, cloud_client
from jobagent.infra.account_state import current_account_ref
from jobagent.infra.protocol import digest_payload

_FIELDS = {"source", "observed_at", "observation", "job_id", "job_url", "account_label",
           "message_state", "outgoing_text", "existing_outgoing_text", "conversation_job_verified",
           "resume_state", "receipt_checked", "prior_application", "history_record"}


def sync_verified(platform, by_job):
    account = current_account_ref()
    path = state.STATE_DIR / "delivery-receipts" / (digest_payload(account)[7:] + ".json")
    saved = state.load_json(path) or {"account_ref": account, "receipts": {}}
    if saved["account_ref"] != account:
        raise ValueError("Delivery receipt outbox account mismatch")
    for job_id, works in by_job.items():
        closed = [w for w in works if w["state"] == "closed" and (w.get("result") or {}).get("outcome") == "success"]
        if not closed:
            continue
        source = closed[0]["task"].get("delivery_source") or {}
        if not source.get("input_path"):
            continue
        reviewed = state.load_json(Path(source["input_path"])) or {}
        auth = (reviewed.get("delivery_authorization") or {}).get("server_authorization")
        if not auth or auth.get("account_ref") != account:
            continue  # Preserve older receipts; never invent a server consent.
        job = closed[0]["task"]["job"]
        observations = [w["result"].get("evidence", {}) for w in closed]
        greeting = any(e.get("message_state") in {"sent", "delivered"} and e.get("conversation_job_verified") is True
            and job.get("cloud_greeting") and (e.get("outgoing_text") == job["cloud_greeting"] or e.get("existing_outgoing_text") == job["cloud_greeting"])
            for e in observations)
        resume = any(e.get("resume_state") == "sent" and e.get("receipt_checked") is True for e in observations)
        if not (greeting if platform == "boss" else resume and (platform != "liepin" or greeting)):
            continue
        observations = [{k: v for k, v in e.items() if k in _FIELDS} for e in observations]
        payload = {"preview_id": auth["preview_id"], "authorization_id": auth["authorization_id"],
                   "job_id": job_id, "work_ids": sorted(w["work_id"] for w in closed), "observations": observations}
        receipt_id = "receipt_" + digest_payload(payload)[7:]
        saved["receipts"].setdefault(receipt_id, {"payload": payload, "synced": False})
    state.save_json(path, saved)
    return _flush(path, saved)


def flush_pending():
    """Replay only an existing account-bound outbox, including after completion."""
    account = current_account_ref()
    if not account:
        return None
    path = state.STATE_DIR / "delivery-receipts" / (digest_payload(account)[7:] + ".json")
    saved = state.load_json(path)
    if not saved:
        return None
    if saved.get("account_ref") != account:
        raise ValueError("Delivery receipt outbox account mismatch")
    return _flush(path, saved)


def _flush(path, saved):
    for receipt_id, item in saved["receipts"].items():
        if item["synced"]:
            continue
        try:
            result = cloud_client._request("PUT", f"/v1/delivery/receipts/{receipt_id}", item["payload"],
                timeout=15, max_attempts=1, operation="delivery_receipt_sync")
        except cloud_client.CloudError as exc:
            item["error"] = exc.code
            state.save_json(path, saved)
            break
        item.update(synced=True, server_receipt_id=result["receipt_id"])
        item.pop("error", None)
        state.save_json(path, saved)
    return {"synced": sum(i["synced"] for i in saved["receipts"].values()),
            "pending": sum(not i["synced"] for i in saved["receipts"].values())}
