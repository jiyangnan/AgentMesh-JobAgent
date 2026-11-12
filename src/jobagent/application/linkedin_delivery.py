"""LinkedIn steps on the shared durable delivery ledger, without reissuing intent."""

from jobagent.infra import browser_work, rounds
from jobagent.infra.linkedin_application import validate_application
from jobagent.infra.protocol import digest_payload


def validate_result(work, result, evidence):
    from jobagent.application.native_work import _error

    job = work["task"]["job"]
    validate_application(job)
    if (
        evidence.get("company") != job["company"]
        or evidence.get("application_digest") != job["application_digest"]
    ):
        _error(
            "linkedin_application_receipt_mismatch",
            "Receipt must bind the approved company and complete application",
        )
    if result["outcome"] in {"uncertain", "unresolved"}:
        if evidence.get("receipt_checked") is not True:
            _error(
                "linkedin_application_receipt_required",
                "Inspect native receipts before reporting an unknown outcome",
            )
        return
    if result["outcome"] != "success":
        _error("linkedin_application_receipt_invalid", "Unsupported application result")
    if (
        evidence.get("resume_state") == "not_sent"
        and work["action"] == "inspect_delivery"
    ):
        if (
            evidence.get("native_preview_verified") is not True
            or evidence.get("submission_attempted") is not False
        ):
            _error(
                "linkedin_application_preview_required",
                "Verify the unchanged full native preview before issuing submission",
            )
        return
    history = evidence.get("history_record") or {}
    if (
        evidence.get("resume_state") != "sent"
        or evidence.get("receipt_checked") is not True
        or evidence.get("native_status_persisted") is not True
        or evidence.get("receipt_kind") != "native_application_persistent"
        or history.get("title") != job["title"]
    ):
        _error(
            "linkedin_application_receipt_required",
            "A native submitted status must persist and agree with the account tracker",
        )
    from jobagent.platforms.linkedin.collect import detail_id

    if detail_id(history.get("url")) != job["id"]:
        _error(
            "linkedin_application_receipt_mismatch",
            "The persistent history must identify this exact job",
        )
    if (
        work["action"] == "inspect_delivery"
        and evidence.get("prior_application") is not True
    ):
        _error(
            "linkedin_prior_application_required",
            "A read-only inspection cannot claim a new submission",
        )


def next_delivery():
    from jobagent.application import native_work as native
    from jobagent.application.delivery import _load_reviewed

    binding = native._binding("linkedin")
    pending = browser_work.pending_work(binding)
    if pending:
        return native.present(pending)
    active = rounds.ensure_current_round()
    source = active["platforms"]["linkedin"]["native_delivery"]
    if source.get("cancelled"):
        return {
            "ok": True,
            "event": "delivery_cancelled",
            "request_preserved": True,
            "next_suggested": "jobagent linkedin apply review --input "
            + source["input_path"],
        }
    reviewed = _load_reviewed(
        "linkedin",
        source["input_path"],
        preview_id=source["preview_id"],
        authorization_id=source["authorization_id"],
    )
    session = native._session()
    if not session or "linkedin" not in session.get("accounts", {}):
        return native.request_login("linkedin")
    works = browser_work.list_account_work(binding["account_ref"])
    from jobagent.application.native_delivery_recovery import superseded_inspections

    superseded = superseded_inspections(works)
    for job in reviewed["send_candidates"][: source["limit"]]:
        application = validate_application(job)
        key = {
            **binding,
            "session_id": session["id"],
            "discover_id": reviewed["discover_id"],
            "preview_id": source["preview_id"],
            "authorization_id": source["authorization_id"],
            "candidate_digest": digest_payload(reviewed["send_candidates"]),
            "job_id": job["id"],
        }
        own = [
            w
            for w in works
            if w["work_id"] not in superseded
            and all(w["binding"].get(k) == v for k, v in key.items())
        ]
        prior = [
            w
            for w in works
            if w.get("side_effect")
            and w["state"] != "ready"
            and (w.get("result") or {}).get("outcome") != "cancelled"
            and w["binding"].get("platform") == "linkedin"
            and w["binding"].get("job_id") == job["id"]
            and w not in own
        ]
        inspect = next(
            (
                w
                for w in own
                if w["action"] == "inspect_delivery" and w["state"] == "closed"
            ),
            None,
        )
        references = {
            f["value"][0]
            for p in application["pages"]
            for f in p["fields"]
            if f["type"] == "attachment"
        }
        if len(references) != 1:
            native._error(
                "linkedin_attachment_ambiguous",
                "One confirmed existing resume must be bound to this application",
            )
        task = {
            "executor": "playwright",
            "job": job,
            "session": session,
            "delivery_source": source,
            "resume_reference": references.pop(),
            "historical_action_requires_reconciliation": bool(prior),
        }
        if not inspect:
            return native.present(
                browser_work.ensure_work(
                    action="inspect_delivery", task=task, binding=key
                )
            )
        if inspect["result"]["outcome"] != "success":
            return native._delivery_paused("linkedin")
        if inspect["result"]["evidence"].get("prior_application"):
            continue
        submit = next((w for w in own if w["action"] == "submit_resume"), None)
        if submit:
            if submit["state"] != "closed":
                return native.present(submit)
            if submit["result"]["outcome"] != "success":
                return native._delivery_paused("linkedin")
            continue
        if prior:
            native._error(
                "native_previous_action_unresolved",
                "A previous submit intent needs read-only reconciliation; no new application permission is available",
            )
        return native.present(
            browser_work.ensure_work(
                action="submit_resume", task=task, binding=key, side_effect=True
            )
        )
    summary = native.audit("linkedin", complete=False)
    if source["limit"] < len(reviewed["send_candidates"]):
        from jobagent.application.native_delivery_followup import register

        return register("linkedin", summary, reviewed=reviewed, source=source)
    rounds.set_platform_status(
        "linkedin",
        "sent",
        command="jobagent linkedin apply send",
        evidence=summary["summary"],
        next_suggested="jobagent linkedin audit",
    )
    return {
        **summary,
        "next_suggested": "jobagent linkedin audit",
        "workflow": rounds.round_status(),
    }
