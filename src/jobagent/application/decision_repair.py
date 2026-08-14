"""Zero-additional-charge repair for signed, unreviewable Zhilian decisions."""

from __future__ import annotations

from typing import Any

from jobagent.domain.models import Job
from jobagent.domain.reviewability import (
    delivery_reviewability_issues,
    is_reviewable_company,
    is_reviewable_job_title,
    is_reviewable_salary,
)
from jobagent.infra import cloud_client
from jobagent.infra.diagnostics import emit_stage, progress_heartbeat
from jobagent.infra.discovery_state import save_manifest
from jobagent.infra.interaction_state import (
    clear_pending_interaction,
    load_pending_interaction,
)
from jobagent.infra.protocol import verify_decision_manifest, verify_stored_decision
from jobagent.platforms.zhilian.collect import ZhilianReadOnlyCollector


class DecisionRepairError(RuntimeError):
    def __init__(self, payload: dict[str, Any]):
        super().__init__(str(payload.get("message") or payload.get("error")))
        self.payload = payload


def _selected_needing_repair(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in manifest.get("selected", [])
        if isinstance(item, dict) and delivery_reviewability_issues(item)
    ]


def _job_from_manifest_item(item: dict[str, Any]) -> Job:
    return Job(
        name=str(item.get("title") or ""),
        salary=str(item.get("salary") or ""),
        company=str(item.get("company") or ""),
        area=str(item.get("area") or ""),
        experience="",
        degree="",
        skills="",
        boss="",
        city=str(item.get("area") or "").split("·", 1)[0],
        url=str(item.get("url") or ""),
        platform="zhilian",
        raw_data={"positionId": str(item.get("id") or "")},
    )


def _patch(item: dict[str, Any], job: Job) -> dict[str, Any]:
    patch: dict[str, Any] = {
        "id": str(item.get("id") or ""),
        "url": str(item.get("url") or ""),
    }
    if is_reviewable_job_title(job.name):
        patch["title"] = job.name
    if is_reviewable_company(job.company):
        patch["company"] = job.company
    if is_reviewable_salary(job.salary):
        patch["salary"] = job.salary
    return patch


def _invalidate_matching_delivery_interaction(discover_id: str) -> None:
    pending = load_pending_interaction()
    if not isinstance(pending, dict):
        return
    context = pending.get("context")
    if (
        str(pending.get("kind") or "").startswith("delivery_")
        and isinstance(context, dict)
        and str(context.get("discover_id") or "") == discover_id
    ):
        clear_pending_interaction()


def repair_zhilian_decision_if_needed(
    envelope: dict[str, Any],
    *,
    driver: Any | None = None,
    wait_seconds: int = 8,
) -> dict[str, Any]:
    manifest = verify_stored_decision(
        envelope["manifest"],
        platform="zhilian",
        allow_expired=True,
    )
    targets = _selected_needing_repair(manifest)
    if not targets:
        return {"repaired": False, "envelope": envelope}

    discover_id = str(manifest["discover_id"])
    _invalidate_matching_delivery_interaction(discover_id)
    jobs = [_job_from_manifest_item(item) for item in targets]
    if any(not job.url for job in jobs):
        raise DecisionRepairError(
            {
                "ok": False,
                "error": "decision_repair_candidate_url_missing",
                "message": "A selected Zhilian job has no signed detail URL to repair safely.",
                "platform": "zhilian",
                "discover_id": discover_id,
                "request_preserved": True,
                "no_charge": True,
                "billing": {"additional_credits": 0},
                "requires_user_action": False,
                "next_suggested": "jobagent browser diagnose --platform zhilian",
            }
        )

    emit_stage(
        "decision_reviewability_repair_started",
        platform="zhilian",
        discover_id=discover_id,
        candidate_count=len(jobs),
        additional_credits=0,
    )
    collector = ZhilianReadOnlyCollector(driver=driver)
    with progress_heartbeat(
        "decision_reviewability_repair_in_progress",
        platform="zhilian",
        candidate_count=len(jobs),
        additional_credits=0,
    ):
        detail_result = collector._hydrate_from_details(
            jobs,
            detail_limit=len(jobs),
            wait_seconds=wait_seconds,
        )
    failure = str(detail_result.get("error") or "")
    if failure:
        requires_login = failure == "zhilian_login_required"
        raise DecisionRepairError(
            {
                "ok": False,
                "error": failure,
                "message": "Zhilian detail fields could not be repaired safely.",
                "platform": "zhilian",
                "discover_id": discover_id,
                "request_preserved": True,
                "no_charge": True,
                "billing": {"additional_credits": 0},
                "requires_user_action": requires_login,
                "next_suggested": (
                    "jobagent zhilian login"
                    if requires_login
                    else "jobagent browser diagnose --platform zhilian"
                ),
            }
        )

    repaired_jobs = list(detail_result.get("jobs") or [])
    patches = [_patch(item, job) for item, job in zip(targets, repaired_jobs, strict=True)]
    unresolved = [
        {
            "job_id": str(item.get("id") or ""),
            "missing_or_invalid_fields": delivery_reviewability_issues(
                {
                    "title": job.name,
                    "company": job.company,
                    "salary": job.salary,
                }
            ),
        }
        for item, job in zip(targets, repaired_jobs, strict=True)
        if delivery_reviewability_issues(
            {"title": job.name, "company": job.company, "salary": job.salary}
        )
    ]
    if unresolved:
        raise DecisionRepairError(
            {
                "ok": False,
                "error": "decision_repair_incomplete",
                "message": (
                    "Zhilian detail pages did not expose enough reliable fields to rebuild a "
                    "reviewable delivery list. No delivery was authorized."
                ),
                "platform": "zhilian",
                "discover_id": discover_id,
                "candidates": unresolved,
                "request_preserved": True,
                "no_charge": True,
                "billing": {"additional_credits": 0},
                "requires_user_action": False,
                "next_suggested": "jobagent browser diagnose --platform zhilian",
            }
        )

    response = cloud_client.discovery_repair(
        discover_id=discover_id,
        expected_manifest_id=str(manifest["manifest_id"]),
        expected_candidate_digest=str(manifest["candidate_digest"]),
        patches=patches,
    )
    repaired_manifest = response.get("manifest")
    repaired_candidates = response.get("candidates")
    repair = response.get("repair")
    if (
        not isinstance(repaired_manifest, dict)
        or not isinstance(repaired_candidates, list)
        or not isinstance(repair, dict)
        or repair.get("additional_credits") != 0
        or repair.get("same_discover_id") is not True
    ):
        raise DecisionRepairError(
            {
                "ok": False,
                "error": "decision_repair_protocol_invalid",
                "message": "The cloud repair response did not preserve its zero-charge binding.",
                "platform": "zhilian",
                "discover_id": discover_id,
                "request_preserved": True,
                "no_charge": True,
                "billing": {"additional_credits": 0},
                "requires_user_action": False,
                "next_suggested": "jobagent zhilian apply review",
            }
        )
    verified = verify_decision_manifest(
        repaired_manifest,
        platform="zhilian",
        discover_id=discover_id,
        jobs=repaired_candidates,
        intent_digest=manifest.get("intent_digest"),
    )
    path = save_manifest(repaired_manifest)
    repaired_envelope = {
        **envelope,
        "manifest": repaired_manifest,
        "source_path": str(path),
    }
    emit_stage(
        "decision_reviewability_repair_completed",
        platform="zhilian",
        discover_id=discover_id,
        selected=len(verified.get("selected", [])),
        review=len(verified.get("review", [])),
        rejected=len(verified.get("rejected", [])),
        additional_credits=0,
    )
    return {
        "repaired": True,
        "envelope": repaired_envelope,
        "repair": repair,
        "decision_file": str(path),
    }
