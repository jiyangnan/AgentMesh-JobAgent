"""Native identity evidence for Liepin's undisclosed-client headhunter jobs."""
from urllib.parse import urlsplit


def eligible(work: dict) -> bool:
    job = work["task"].get("job", {})
    url = urlsplit(str(job.get("url") or ""))
    company = str(job.get("company") or "")
    return (work["binding"].get("platform") == "liepin"
            and url.scheme == "https" and url.hostname == "www.liepin.com"
            and url.path == f"/a/{job.get('id')}.shtml"
            and company.startswith("某") and company.endswith("公司"))


def contract(work: dict, task: dict, fields: dict) -> None:
    if not eligible(work):
        return
    task["anonymous_company_identity"] = (
        "If this approved headhunter detail does not disclose the client company, "
        "do not copy the signed company label into observed company or use another "
        "job's recommendation card. Report company=null, company_visibility=not_disclosed, "
        "and the actual recruiter_name and recruiter_company. On the initial read-only "
        "inspection, verify the exact approved detail URL, title, salary and area. "
        "Later work must verify that same recruiter and the exact job in the conversation. "
        "The signed list/company and authorization remain unchanged. A disclosed but "
        "different client company is a mismatch, not this branch."
    )
    fields.update(company="exact observed approved client company; null only under anonymous_company_identity",
        company_visibility="not_disclosed only when the client company is absent from the headhunter detail",
        recruiter_name="actual visible recruiter name; must remain equal to the initial inspection",
        recruiter_company="actual visible recruiter employer; distinct from the undisclosed client",
        detail_salary="exact task.job.salary observed on the initial approved detail",
        detail_area="exact task.job.area observed on the initial approved detail")
    task["instruction"] = task.get("instruction", "").replace(
        "Confirm visible job ID, title, company and account.",
        "Confirm visible job ID, title, account and client company, or the declared anonymous_company_identity evidence.")


def validate(work: dict, evidence: dict) -> None:
    from jobagent.application import native_work as native
    job = work["task"]["job"]
    anchor = work["task"].get("anonymous_recruiter_identity")
    if evidence.get("company") == job["company"] and not anchor:
        return
    if (not eligible(work) or evidence.get("company") not in (None, "")
            or evidence.get("company_visibility") != "not_disclosed"
            or not all(isinstance(evidence.get(k), str) and evidence[k].strip()
                       for k in ("recruiter_name", "recruiter_company"))):
        native._error("native_job_identity_mismatch", "Verify the approved client company or the declared undisclosed-client identity evidence.")
    initial = (work["action"] == "inspect_delivery"
               and work["task"].get("inspection_phase") != "after_communication")
    if initial:
        page, expected = urlsplit(str(evidence.get("page_url") or "")), urlsplit(job["url"])
        if (page.scheme != "https" or page.hostname != expected.hostname or page.path != expected.path
                or not job.get("salary") or evidence.get("detail_salary") != job["salary"]
                or not job.get("area") or evidence.get("detail_area") != job["area"]):
            native._error("native_anonymous_detail_unverified", "Verify the exact approved detail route, title, salary and area before establishing its recruiter identity.")
    elif (not anchor or any(evidence.get(k) != anchor[k] for k in ("recruiter_name", "recruiter_company"))
          or evidence.get("conversation_job_verified") is not True):
        native._error("native_anonymous_recruiter_mismatch", "Verify the same recruiter and exact approved job in the conversation; do not send or submit again.")


def anchor_from(evidence: dict) -> dict | None:
    if evidence.get("company_visibility") == "not_disclosed" and evidence.get("company") in (None, ""):
        return {k: evidence[k] for k in ("recruiter_name", "recruiter_company")}
    return None
