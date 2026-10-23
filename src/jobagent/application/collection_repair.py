"""Auditable recovery of an unfinished, malformed split-view collection."""
from __future__ import annotations

from jobagent import __version__
from jobagent.application import bulk_decision
from jobagent.infra import cloud_client, rounds, state
from jobagent.infra.account_state import current_account_ref
from jobagent.infra.discovery_state import (
    collection_scope_digest, save_pending_decision,
)
from jobagent.infra.protocol import ProtocolError, candidate_digest
from jobagent.platforms.discovery import CollectionError, collect_from_search_plan


def journal_path(discover_id):
    return bulk_decision._checkpoint_path(discover_id).with_suffix(".repair.json")


def pending_repair(pending):
    return bool(pending and pending.get("plan", {}).get("platform") == "zhilian"
                and journal_path(pending["discover_id"]).exists())


def repair_pending_collection(platform, pending, plan, *, wait_seconds=6, page_delay=2):
    from jobagent.application.hybrid_discovery import collection_reader
    from jobagent.application.discover import _decision_candidates
    from jobagent.infra.diagnostics import emit_stage

    active = rounds.ensure_current_round()
    if (platform != "zhilian" or plan.get("discovery_contract_version") != 2
            or plan.get("account_ref") != current_account_ref()
            or plan.get("round_id") != active["round_id"]
            or active.get("platforms", {}).get(platform, {}).get("native_delivery")):
        raise ProtocolError("collection repair context mismatch")
    discover_id = plan["discover_id"]
    path = journal_path(discover_id)
    saved = state.load_json(path)
    if saved is None:
        status = bulk_decision.request("GET", discover_id, "status")
        if (status.get("status") != "enrichment_required"
                or not 0 < len(pending["jobs"]) < plan["candidate_limit"]):
            raise CollectionError("collection_repair_not_allowed",
                "Only an unfinished, incomplete split-list collection can be repaired",
                details={"request_preserved": True, "additional_charge_on_retry": False})
        saved = {"schema_version": 1, "discover_id": discover_id,
                 "plan_scope": collection_scope_digest(plan),
                 "original_pending": pending, "original_bulk": state.load_json(bulk_decision._checkpoint_path(discover_id)),
                 "progress": None}
        state.save_json(path, saved)  # Before any new page read.
    if (saved.get("discover_id") != discover_id
            or saved.get("plan_scope") != collection_scope_digest(plan)
            or saved.get("original_pending", {}).get("request_id") != pending.get("request_id")):
        raise ProtocolError("preserved collection repair changed context")
    if not saved.get("body"):
        def checkpoint(progress):
            saved["progress"] = progress
            state.save_json(path, saved)
        with collection_reader(platform) as reader:
            jobs = collect_from_search_plan(plan, driver=reader, wait_seconds=wait_seconds,
                page_delay=page_delay, login_verification=rounds.recent_platform_login_verification(platform),
                resume_progress=saved["progress"], checkpoint_callback=checkpoint,
                progress_callback=emit_stage)
        jobs = _decision_candidates(jobs)
        if len(jobs) != plan["candidate_limit"] or any(not j.get("jd") for j in jobs):
            raise CollectionError("collection_repair_evidence_incomplete",
                "Recovery requires the full original candidate target and full descriptions",
                details={"request_preserved": True, "additional_charge_on_retry": False,
                         "automatic_retry_allowed": False, "candidate_count": len(jobs)})
        saved["body"] = {"reason": "zhilian_split_list_v1",
            "expected_candidate_digest": candidate_digest(saved["original_pending"]["jobs"]),
            "jobs": jobs, "client_version": __version__, "protocol_version": 1}
        state.save_json(path, saved)
    body = saved["body"]
    jobs = body["jobs"]
    if "receipt" not in saved:
        try:
            receipt = bulk_decision.request("POST", discover_id, "collection-repair", body)
        except cloud_client.CloudError as exc:
            exc.details.update(request_preserved=True, additional_charge_on_retry=False,
                candidate_count=len(jobs), full_jd_count=sum(bool(j.get("jd")) for j in jobs),
                automatic_retry_allowed=False, requires_technical_recovery=True,
                next_suggested=f"jobagent {platform} discover --repair-collection")
            raise
        if (receipt.get("discover_id") != discover_id or receipt.get("additional_credits") != 0
                or receipt.get("candidate_digest") != candidate_digest(jobs)
                or receipt.get("candidate_count") != len(jobs)
                or receipt.get("expected_candidate_digest") != body["expected_candidate_digest"]
                or not receipt.get("batch_ids")):
            raise ProtocolError("collection repair acknowledgement mismatch")
        saved["receipt"] = receipt
        state.save_json(path, saved)
    receipt = saved["receipt"]
    bulk_path = bulk_decision._checkpoint_path(discover_id)
    bulk = state.load_json(bulk_path)
    if not isinstance(bulk, dict):
        raise ProtocolError("original bulk checkpoint is unavailable")
    old_receipt = bulk.get("collection_repair_receipt")
    if old_receipt is not None and old_receipt != receipt:
        raise ProtocolError("preserved collection repair receipt changed")
    bulk["collection_repair_receipt"] = receipt
    state.save_json(bulk_path, bulk)
    # The original pending task and uploads remain verbatim in the journal.
    # This active pointer changes only after the server's durable acceptance.
    save_pending_decision(platform, plan=plan, jobs=jobs, request_id=pending["request_id"])
    return jobs


def needs_job51_staged_repair(collection):
    if not collection:
        return False
    plan = collection.get("plan") or {}
    return (plan.get("platform") == "51job" and plan.get("discovery_contract_version") == 2
            and (journal_path(plan["discover_id"]).exists() or any(
                "/pc/search?" in str(j.get("url", "")) and "#jobId=" in str(j.get("url", ""))
                for j in collection.get("progress", {}).get("candidates", []))))


def repair_staged_job51(collection, plan, *, wait_seconds=6, page_delay=2):
    """Replace only an unfinalized upload with verified full-JD rows, retaining its source."""
    from jobagent.application.hybrid_discovery import collection_reader
    from jobagent.application.discover import _decision_candidates
    from jobagent.infra.diagnostics import emit_stage
    from jobagent.platforms.detail_reader import detail_url_allowed

    active = rounds.ensure_current_round()
    discover_id = plan["discover_id"]
    if (plan.get("platform") != "51job" or plan.get("account_ref") != current_account_ref()
            or plan.get("round_id") != active["round_id"]
            or active.get("platforms", {}).get("51job", {}).get("native_delivery")):
        raise ProtocolError("staged collection repair context mismatch")
    path = journal_path(discover_id)
    saved = state.load_json(path)
    if saved is None:
        status = bulk_decision.request("GET", discover_id, "status")
        if status.get("status") != "collecting":
            raise ProtocolError("staged collection is already frozen")
        saved = {"schema_version": 1, "kind": "job51_staged_cards_v1",
                 "discover_id": discover_id, "plan_scope": collection_scope_digest(plan),
                 "original_collection": collection,
                 "original_bulk": state.load_json(bulk_decision._checkpoint_path(discover_id)),
                 "progress": None}
        state.save_json(path, saved)
    if (saved.get("kind") != "job51_staged_cards_v1"
            or saved.get("plan_scope") != collection_scope_digest(plan)):
        raise ProtocolError("staged repair context changed")
    originals = saved["original_collection"]["progress"]["candidates"]
    if not saved.get("body"):
        def checkpoint(progress):
            saved["progress"] = progress
            state.save_json(path, saved)
        with collection_reader("51job") as reader:
            jobs = collect_from_search_plan(plan, driver=reader, wait_seconds=wait_seconds,
                page_delay=page_delay, resume_progress=saved["progress"],
                checkpoint_callback=checkpoint, progress_callback=emit_stage)
        jobs = _decision_candidates(jobs)
        mapped = {j["id"]: j for j in jobs}
        if (len(jobs) != plan["candidate_limit"] or len(mapped) != len(jobs)
                or any(not j.get("jd") or not detail_url_allowed("51job", j["url"]) for j in jobs)
                or any(j["id"] not in mapped or any(mapped[j["id"]].get(k) != j.get(k)
                       for k in ("title", "company", "area", "salary")) for j in originals)):
            raise CollectionError("collection_repair_evidence_incomplete",
                "The original rows and full descriptions must all be verified before repair",
                details={"request_preserved": True, "no_charge": True, "automatic_retry_allowed": False})
        prefix = []
        for batch_id, digest in sorted(saved["original_bulk"]["uploads"].items()):
            if not batch_id.startswith("batch-"):
                raise ProtocolError("unknown original upload")
            offset = int(batch_id.removeprefix("batch-"))
            batch = _decision_candidates(originals[offset:offset + bulk_decision.BATCH_SIZE])
            if candidate_digest(batch) != digest:
                raise ProtocolError("original staged upload changed")
            prefix.extend(batch)
        if not prefix:
            raise ProtocolError("original staged upload missing")
        saved["body"] = {"reason": "job51_staged_cards_v1", "expected_candidate_digest": candidate_digest(prefix),
                         "jobs": jobs, "client_version": __version__, "protocol_version": 1}
        state.save_json(path, saved)
    body = saved["body"]
    if "receipt" not in saved:
        try:
            receipt = bulk_decision.request("POST", discover_id, "collection-repair", body)
        except cloud_client.CloudError as exc:
            exc.details.update(request_preserved=True, no_charge=True, billing_status="not_charged",
                additional_charge_on_retry=False, automatic_retry_allowed=False,
                requires_technical_recovery=True, candidate_count=len(body["jobs"]),
                full_jd_count=sum(bool(j.get("jd")) for j in body["jobs"]),
                next_suggested="jobagent 51job discover")
            raise
        if (receipt.get("discover_id") != discover_id or receipt.get("additional_credits") != 0
                or receipt.get("billing_status") != "not_charged"
                or receipt.get("candidate_digest") != candidate_digest(body["jobs"])
                or receipt.get("candidate_count") != len(body["jobs"])
                or receipt.get("expected_candidate_digest") != body["expected_candidate_digest"]
                or not receipt.get("batch_ids")):
            raise ProtocolError("staged collection repair acknowledgement mismatch")
        saved["receipt"] = receipt
        state.save_json(path, saved)
    bulk_path = bulk_decision._checkpoint_path(discover_id)
    bulk = state.load_json(bulk_path)
    if bulk.get("collection_repair_receipt") not in (None, saved["receipt"]):
        raise ProtocolError("staged collection repair receipt changed")
    bulk["collection_repair_receipt"] = saved["receipt"]
    state.save_json(bulk_path, bulk)
    return body["jobs"]
