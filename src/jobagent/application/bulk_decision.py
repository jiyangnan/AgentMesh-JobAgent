"""Transport and resume v2 jobs; matching policy remains on the server."""
from __future__ import annotations

from contextlib import ExitStack
import random
import time
from urllib.parse import quote

from jobagent import __version__
from jobagent.infra import cloud_client, state
from jobagent.infra.diagnostics import emit_stage
from jobagent.infra.protocol import (PROTOCOL_VERSION, DECISION_SIGNING_PUBLIC_KEY, ProtocolError,
    candidate_digest, digest_payload, verify_signed_payload)

BATCH_SIZE = 15
DETAIL_BATCH_SIZES = (7, 11, 13)


def request(method, discover_id, suffix, body=None):
    return cloud_client._request(method, f"/v1/discovery/{quote(discover_id, safe='')}/{suffix}",
        body, timeout=60, max_attempts=3, operation="bulk_discovery", request_id=discover_id)


def _checkpoint_path(discover_id):
    if not discover_id.replace("_", "").isalnum():
        raise ProtocolError("invalid discover id")
    return state.STATE_DIR / "bulk-discovery" / f"{discover_id}.json"


def upload_candidates(plan, jobs, *, final=False):
    """Commit stable prefixes while collecting; a partial final block freezes once."""
    discover_id = plan["discover_id"]
    path = _checkpoint_path(discover_id)
    saved = state.load_json(path) or {"discover_id": discover_id, "uploads": {}, "details": {}}
    repaired = saved.get("collection_repair_receipt")
    if repaired is not None:
        if (repaired.get("candidate_digest") != candidate_digest(jobs)
                or repaired.get("candidate_count") != len(jobs)):
            raise ProtocolError("repaired candidate pool changed")
        return repaired["batch_ids"]
    end = len(jobs) if final else len(jobs) // BATCH_SIZE * BATCH_SIZE
    ids = []
    for start in range(0, end, BATCH_SIZE):
        batch = jobs[start:min(start + BATCH_SIZE, end)]
        batch_id = f"batch-{start:06d}"
        digest = candidate_digest(batch)
        previous = saved["uploads"].get(batch_id)
        if previous is not None and previous != digest:
            raise ProtocolError("preserved upload prefix changed")
        if previous is None:
            receipt = request("PUT", discover_id, f"batches/{batch_id}", {"jobs": batch, "digest": digest})
            if receipt.get("digest") != digest or receipt.get("batch_id") != batch_id:
                raise ProtocolError("upload acknowledgement mismatch")
            saved["uploads"][batch_id] = digest
            state.save_json(path, saved)
        ids.append(batch_id)
    return ids


def _detail_batches(path, saved, items):
    """Freeze the detail work groups before reading; resumes keep the same groups."""
    identifiers = [item["id"] for item in items]
    batches = saved.get("detail_batches")
    if batches is None:
        batches = []
        offset = 0
        while offset < len(identifiers):
            target = random.choice(DETAIL_BATCH_SIZES)
            ids = identifiers[offset:offset + target]
            batches.append({"ids": ids, "target_size": target})
            offset += len(ids)
        saved["detail_batches"] = batches
        state.save_json(path, saved)
    if (not isinstance(batches, list)
            or any(not isinstance(batch, dict) or batch.get("target_size") not in DETAIL_BATCH_SIZES
                   or not isinstance(batch.get("ids"), list)
                   or not 1 <= len(batch["ids"]) <= batch["target_size"] for batch in batches)
            or any(len(batch["ids"]) != batch["target_size"] for batch in batches[:-1])
            or [identifier for batch in batches for identifier in batch["ids"]] != identifiers):
        raise ProtocolError("preserved detail batches do not match the signed task")
    return batches


def _enrich(platform, plan, jobs):
    from jobagent.application.hybrid_discovery import collection_reader
    from jobagent.platforms.detail_reader import read_detail, detail_url_allowed
    discover_id = plan["discover_id"]
    task = verify_signed_payload(request("GET", discover_id, "enrichment"),
        public_key=DECISION_SIGNING_PUBLIC_KEY, expected_type="discovery_enrichment")
    if (task.get("discover_id") != discover_id or task.get("platform") != platform
            or task.get("candidate_digest") != candidate_digest(jobs)
            or task.get("account_ref") != plan.get("account_ref")
            or task.get("collection_executor") != "cdp_readonly"):
        raise ProtocolError("enrichment task binding mismatch")
    originals = {j["id"]: j for j in jobs}
    items = task.get("items")
    if not isinstance(items, list) or len(items) > len(jobs) or len({j["id"] for j in items}) != len(items):
        raise ProtocolError("enrichment task coverage invalid")
    for item in items:
        if (originals.get(item["id"], {}).get("url") != item.get("url")
                or not detail_url_allowed(platform, item.get("url", ""))):
            raise ProtocolError("enrichment detail URL mismatch")
    path = _checkpoint_path(discover_id)
    saved = state.load_json(path)
    batches = _detail_batches(path, saved, items)
    by_id = {item["id"]: item for item in items}
    # Finish only missing reads. Each result is durable before its upload.
    with ExitStack() as stack:
        reader = None
        for index, batch in enumerate(batches):
            for identifier in batch["ids"]:
                item = by_id[identifier]
                existing = saved["details"].get(identifier, {})
                if existing.get("status") != "available" or not str(existing.get("jd") or "").strip():
                    if reader is None:
                        reader = stack.enter_context(collection_reader(platform))
                    detail = read_detail(reader, platform, {**originals[identifier], **item})
                    if detail.get("status") != "available" or not str(detail.get("jd") or "").strip():
                        from jobagent.platforms.discovery import CollectionError
                        saved["detail_pause"] = {"job_id": identifier, "code": "detail_evidence_required"}
                        state.save_json(path, saved)
                        raise CollectionError("detail_evidence_required",
                            "The reader did not obtain the required full job description; technical recovery is needed",
                            details={"retryable": False, "request_preserved": True,
                                     "requires_user_action": False, "automatic_retry_allowed": False,
                                     "requires_technical_recovery": True,
                                     "completed_details": len(saved["details"]), "required_details": len(items)})
                    saved["details"][identifier] = detail
                    saved.pop("detail_pause", None)
                    state.save_json(path, saved)
                    emit_stage("bulk_detail_collected", platform=platform,
                               detail_count=len(saved["details"]), detail_total=len(items))
            results = [saved["details"][identifier] for identifier in batch["ids"]]
            batch_id = f"details-{index:06d}"
            digest = digest_payload(results)
            uploaded = saved.setdefault("detail_uploads", {})
            if batch_id in uploaded and uploaded[batch_id] != digest:
                raise ProtocolError("preserved detail upload changed")
            if batch_id not in uploaded:
                request("PUT", discover_id, "enrichment/" + batch_id, {"results": results})
                uploaded[batch_id] = digest
                state.save_json(path, saved)
                emit_stage("bulk_detail_batch_uploaded", platform=platform,
                           batch_index=index + 1, batch_count=len(batches), detail_count=len(results))
    request("POST", discover_id, "enrichment/finalize")


def complete_decision(platform, plan, jobs, *, timeout_seconds=900, poll_seconds=2):
    discover_id = plan["discover_id"]
    ids = upload_candidates(plan, jobs, final=True)
    status = request("POST", discover_id, "finalize", {
        "batch_ids": ids, "candidate_digest": candidate_digest(jobs), "candidate_count": len(jobs),
        "client_version": __version__, "protocol_version": PROTOCOL_VERSION})
    deadline = time.monotonic() + timeout_seconds
    previous = None
    while True:
        progress = tuple(status.get(k) for k in ("status", "scored_count", "details_received"))
        if progress != previous:
            emit_stage("bulk_matching_progress", platform=platform, **{k: status.get(k) for k in (
                "status", "candidate_count", "scored_count", "details_received", "detail_count")})
            previous = progress
        if status.get("status") == "completed":
            return request("GET", discover_id, "decision")
        if status.get("status") == "enrichment_required":
            from jobagent.platforms.discovery import CollectionError
            try:
                _enrich(platform, plan, jobs)
            except CollectionError as exc:
                exc.details = {**(exc.details or {}), "request_preserved": True, "discover_id": discover_id,
                    "platform": platform, "billing_status": "charged_pending_matching", "no_charge": False,
                    "additional_charge_on_retry": False}
                exc.details.setdefault("retryable", False)
                exc.details.setdefault("automatic_retry_allowed", bool(exc.details["retryable"]))
                if exc.details.get("automatic_retry_allowed") is not False:
                    exc.details.setdefault("next_suggested", f"jobagent {platform} discover")
                raise
        elif status.get("status") == "failed":
            error = status.get("error") or {}
            raise cloud_client.CloudError("Cloud matching paused; retry the preserved Discover.",
                code=error.get("code", "bulk_matching_failed"), retryable=True,
                details={**status, "request_preserved": True})
        if time.monotonic() >= deadline:
            raise cloud_client.CloudError("Cloud matching continues; resume the same Discover to retrieve it.",
                code="bulk_matching_pending", retryable=True, details={"request_preserved": True})
        time.sleep(poll_seconds)
        status = request("GET", discover_id, "status")
