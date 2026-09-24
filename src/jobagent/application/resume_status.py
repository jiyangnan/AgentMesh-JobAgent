"""Read confirmed resume metadata through the authenticated public API."""
from __future__ import annotations

from jobagent.infra import cloud_client


def resume_status(resume_id: str) -> dict:
    document = cloud_client.resume_center_resume(resume_id)
    # Deliberately omit drafts, bodies, personal info and revision contents.
    resume = {key: document.get(key) for key in (
        "id", "name", "target_role", "status", "version", "confirmed_revision_id",
        "confirmed_revision_number", "confirmed_at", "has_draft", "updated_at",
    )}
    ready = bool(resume["confirmed_revision_id"] and resume["status"] == "active")
    return {
        "ok": True, "source": "resume_center", "resume": resume,
        "ready": ready, "requires_user_action": not ready,
        "workbench_url": "https://agentmesh360.com/workbench/#/resumes",
        "next_suggested": "jobagent round start" if ready else "jobagent resume status",
        **({"user_prompt": "请在工作台完成并确认这份简历，回到当前对话后继续检查状态。"} if not ready else {}),
    }
