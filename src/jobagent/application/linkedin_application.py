"""Prepare a matched batch using the existing round, interaction and work ledger.

All applicant values remain in account-bound local state. The service supplies
signed factual policies; the runtime observes/fills one native page at a time.
This module never issues a submit intent.
"""

import shlex
from copy import deepcopy

from jobagent.infra import browser_work, cloud_client, protocol, rounds
from jobagent.infra.account_state import current_account_ref
from jobagent.infra.interaction_protocol import (
    build_host_presentations,
    build_interaction_required,
)
from jobagent.infra.interaction_state import (
    clear_pending_interaction,
    load_pending_interaction,
    save_pending_interaction,
)
from jobagent.infra.protocol import digest_payload

PREFIX = "linkedin-facts:"


def fail(code, message):
    raise browser_work.BrowserWorkError(code, message)


def _context(review, active):
    from jobagent.application.playwright_work import session

    current = session(active)
    if not current or not current.get("accounts", {}).get("linkedin"):
        fail(
            "linkedin_application_account_required",
            "Verify the dedicated browser account before preparing applications",
        )
    manifest = review["manifest"]
    if (
        manifest.get("account_ref") != current_account_ref()
        or manifest.get("round_id") != active["round_id"]
    ):
        fail(
            "linkedin_application_context_changed", "Keep the matched account and round"
        )
    if manifest.get("intent_digest") != digest_payload(
        active.get("intent")
    ) or manifest.get("resume_binding") != active.get("resume_binding"):
        fail(
            "linkedin_application_context_changed",
            "The matched intention or resume binding changed",
        )
    return {
        "account_ref": current_account_ref(),
        "round_id": active["round_id"],
        "discover_id": review["discover_id"],
        "manifest_id": manifest["manifest_id"],
        "profile_digest": manifest["profile_digest"],
        "intent_digest": manifest.get("intent_digest"),
        "resume_binding": active.get("resume_binding"),
        "criteria_revision": (active.get("round_criteria") or {}).get(
            "criteria_revision", 0
        ),
        "session": current,
    }


def _load():
    active = rounds.ensure_current_round()
    checkpoint = active.get("linkedin_preparation")
    if (
        not checkpoint
        or _context(checkpoint["review"], active) != checkpoint["context"]
    ):
        fail(
            "linkedin_application_context_changed",
            "The preserved application belongs to another account, resume or session",
        )
    protocol.verify_stored_decision(
        checkpoint["review"]["manifest"], platform="linkedin"
    )
    return active, checkpoint


def _save(active, checkpoint):
    active["linkedin_preparation"] = checkpoint
    rounds.save_round(active)


def assert_current_work(work):
    _, checkpoint = _load()
    if work["binding"].get("preparation_digest") != digest_payload(
        checkpoint["context"]
    ):
        fail(
            "linkedin_application_context_changed",
            "The queued preparation no longer belongs to this context",
        )


def _present(checkpoint):
    card = checkpoint["card"]["interaction"]
    old = load_pending_interaction()
    if old and old["interaction_id"] != card["interaction_id"]:
        fail(
            "interaction_already_pending",
            "Resolve the existing interaction before application facts",
        )
    save_pending_interaction(
        card,
        stage="linkedin_application_facts",
        context={"context_digest": digest_payload(checkpoint["context"])},
    )
    return {
        "ok": False,
        "error": "interaction_required",
        "event": "linkedin_application_facts",
        "requires_user_action": True,
        "request_preserved": True,
        "no_charge": True,
        "interaction": card,
        "host_presentations": build_host_presentations(card),
        "next_suggested": f"jobagent interaction respond --interaction-id {shlex.quote(card['interaction_id'])} --answer-file <answers.json>",
    }


def _plan(checkpoint, job, snapshot):
    fields, local_ids = [], {}
    for field in snapshot["fields"]:
        if field["disabled"]:
            continue
        # Email/account dropdowns and resume filenames are applicant data too.
        # The policy needs option identity/equality, not their literal contents.
        options = []
        for option in field["options"]:
            key = digest_payload({k: v for k, v in option.items() if k != "index"})
            options.append({"label": key, "value": key, "disabled": option["disabled"]})
        semantic = {
            "type": field["type"],
            "question": field["question"],
            "options": options,
        }
        identifier = digest_payload(semantic)
        fields.append(
            {**semantic, "required": field["required"], "field_id": identifier}
        )
        local_ids[identifier] = field["field_id"]
    context = checkpoint["context"]
    body = {
        k: context[k]
        for k in ("round_id", "discover_id", "manifest_id", "criteria_revision")
    }
    promoted = {
        str(item["job_id"]) for item in checkpoint["review"].get("user_overrides", [])
    }
    body.update(
        job_id=job["id"],
        confirm_promote=job["id"] in promoted,
        context_digest=digest_payload(context),
        page_digest=snapshot["snapshot_digest"],
        policy_digest=snapshot["policy_digest"],
        fields=fields,
    )
    key = digest_payload(body)
    signed = checkpoint["plans"].get(key)
    if signed is None:
        signed = cloud_client.linkedin_application_plan(body)["plan"]
    proof = protocol.verify_signed_payload(
        signed,
        public_key=protocol.DECISION_SIGNING_PUBLIC_KEY,
        expected_type="linkedin_application_plan",
    )
    expected = {
        k: body[k]
        for k in (
            "round_id",
            "discover_id",
            "manifest_id",
            "criteria_revision",
            "job_id",
            "context_digest",
            "page_digest",
        )
    }
    expected.update(
        account_ref=context["account_ref"],
        protocol_version=1,
        fields_digest=digest_payload(fields),
        delivery_authorized=False,
    )
    if any(proof.get(k) != v for k, v in expected.items()):
        fail(
            "linkedin_application_plan_mismatch",
            "The factual plan must bind this exact observed page",
        )
    decisions = proof.get("decisions", [])
    if {d.get("field_id") for d in decisions} != {f["field_id"] for f in fields} or len(
        decisions
    ) != len(fields):
        fail(
            "linkedin_application_plan_mismatch",
            "The factual plan must cover every observed editable field",
        )
    checkpoint["plans"][key] = signed
    return {local_ids[d["field_id"]]: d for d in decisions}


def _resolve(checkpoint, job, entry):
    snapshot = entry["snapshot"]
    if snapshot["unsupported"] or snapshot["errors"]:
        entry["status"] = "manual"
        return
    plan = _plan(checkpoint, job, snapshot)
    answers, missing = {}, []
    for field in snapshot["fields"]:
        if field["disabled"]:
            continue
        rule = plan[field["field_id"]]
        saved = checkpoint["facts"].get(rule["reuse_key"])
        normalized = (
            field["value"][0]
            if field["type"] in {"attachment", "radio"} and len(field["value"]) == 1
            else field["value"]
        )
        confirmed_prefill = (
            checkpoint.get("confirmed_prefills", {})
            .get(rule["reuse_key"], {})
            .get(job["id"])
        )
        if saved and (
            normalized in ("", [])
            or normalized == saved["value"]
            or normalized == confirmed_prefill
        ):
            answers[field["field_id"]] = deepcopy(saved)
        elif rule["behavior"] == "profile_fact" and normalized in ("", rule["value"]):
            answers[field["field_id"]] = {
                "value": rule["value"],
                "source": rule["source"],
            }
        elif (
            not field["required"]
            and field["value"] in ("", [])
            and field["type"] not in {"attachment", "checkbox"}
        ):
            continue
        else:
            missing.append(
                {
                    "field": deepcopy(field),
                    "reuse_key": rule["reuse_key"],
                    "job_id": job["id"],
                    "policy_links": snapshot["links"],
                    "suggested_value": saved["value"] if saved else rule.get("value"),
                }
            )
    entry.update(
        answers=answers, missing=missing, status="waiting" if missing else "fill"
    )


def _card(active, checkpoint, waiting):
    questions = {}
    jobs = {job["id"]: job for job in checkpoint["review"]["send_candidates"]}
    for entry in waiting:
        for item in entry["missing"]:
            key = item["reuse_key"]
            q = questions.setdefault(
                key,
                {
                    "field": item["field"],
                    "jobs": [],
                    "policy_links": item["policy_links"],
                    "observed_values": [],
                    "prefills": {},
                    "suggested_value": item["suggested_value"],
                },
            )
            q["jobs"].append(item["job_id"])
            value = item["field"]["value"]
            q["prefills"][item["job_id"]] = (
                value[0]
                if item["field"]["type"] in {"attachment", "radio"} and len(value) == 1
                else value
            )
            if item["field"]["value"] not in q["observed_values"]:
                q["observed_values"].append(item["field"]["value"])
    fields, text = (
        [],
        [
            "Please confirm the missing application facts below. No application has been submitted."
        ],
    )
    for key, item in questions.items():
        field = item["field"]
        label = field["question"] or "Existing resume attachment"
        targets = "; ".join(
            jobs[i]["title"] + " · " + jobs[i]["company"] for i in item["jobs"]
        )
        options = [
            {"option_id": o["value"], "label": o["label"]}
            for o in field["options"]
            if o["value"] and not o["disabled"]
        ]
        if field["type"] == "checkbox":
            options = [
                {"option_id": "true", "label": "Yes"},
                {"option_id": "false", "label": "No"},
            ]
        fields.append(
            {
                "field_id": key,
                "type": "single" if options else "text",
                "label": label,
                "required": True,
                "default_option_ids": [],
                **({"options": options} if options else {}),
            }
        )
        text.extend(
            [
                f"{key}: {label}",
                f"Applies to: {targets}",
                f"Observed values (unconfirmed): {item['observed_values']}",
            ]
        )
        if item["suggested_value"] is not None:
            text.append(
                f"Previous confirmed / resume value: {item['suggested_value']}. Confirm which value to use."
            )
        text.extend(f"{o['option_id']}: {o['label']}" for o in options)
        text.extend(link["text"] + ": " + link["url"] for link in item["policy_links"])
    ident = (
        PREFIX
        + digest_payload({"context": checkpoint["context"], "questions": questions,
                          "generation": checkpoint.get("generation", 0)})[
            7:31
        ]
    )
    card = build_interaction_required(
        interaction_id=ident,
        product_id="job_agent",
        kind="linkedin_application_facts",
        title="Complete application facts",
        prompt="Answer only facts you can confirm; unknown statements remain paused.",
        fields=fields,
        fallback_text="\n".join(text),
        continuation_action="jobagent.interaction.respond",
        idempotency_key=ident,
    )
    checkpoint["card"] = {"interaction": card, "questions": questions}
    _save(active, checkpoint)
    return _present(checkpoint)


def _manual(active, checkpoint, job):
    entry = checkpoint["jobs"][job["id"]]
    ident = (
        PREFIX
        + "manual-"
        + digest_payload([checkpoint["context"], job["id"], entry["revision"]])[7:31]
    )
    text = (
        f"{job['title']} · {job['company']} needs manual form handling. Complete the unsupported fields "
        "in the dedicated browser, then stop on a supported page or the final review. Do not submit. "
        "Choose synced when ready, or pause_platform to keep this application paused."
    )
    if entry.get("restarting"):
        text = (
            f"{job['title']} · {job['company']}: return to the first application page "
            "using the native Edit/Back controls. Do not submit. Choose synced to "
            "recheck every page and prepare a new full preview, or pause_platform."
        )
    card = build_interaction_required(
        interaction_id=ident,
        product_id="job_agent",
        kind="linkedin_application_manual",
        title="Review this application",
        prompt=text,
        fallback_text=text,
        fields=[
            {
                "field_id": "choice",
                "type": "single",
                "label": "Continue preparation",
                "required": True,
                "options": [
                    {"option_id": "synced", "label": "I completed the manual fields"},
                    {"option_id": "pause_platform", "label": "Keep paused"},
                ],
                "default_option_ids": [],
            }
        ],
        continuation_action="jobagent.interaction.respond",
        idempotency_key=ident,
    )
    checkpoint["card"] = {"interaction": card, "manual_job": job["id"]}
    _save(active, checkpoint)
    return _present(checkpoint)


def prepare(review, options):
    from jobagent.application import native_work, playwright_work

    active = rounds.ensure_current_round()
    if not playwright_work.enabled(active):
        fail(
            "linkedin_packaged_executor_required",
            "LinkedIn preparation uses the packaged browser runtime",
        )
    active["native_review"] = options
    rounds.save_round(active)
    playwright_work.ensure_session("linkedin")
    active = rounds.ensure_current_round()
    if not playwright_work.session(active).get("accounts", {}).get("linkedin"):
        return native_work.request_login("linkedin")
    context = _context(review, active)
    checkpoint = active.get("linkedin_preparation")
    if checkpoint and checkpoint["context"] != context:
        from jobagent.application.linkedin_recovery import replace_context
        checkpoint = replace_context(active, checkpoint, context)
    if not checkpoint:
        checkpoint = {
            "context": context,
            "review": deepcopy(review),
            "jobs": {},
            "facts": {},
            "plans": {},
            "answered": {},
        }
    checkpoint["review"] = deepcopy(review)
    if checkpoint.get("card"):
        _save(active, checkpoint)
        return _present(checkpoint)
    pending = browser_work.pending_work(native_work._binding("linkedin"))
    if pending:
        return native_work.present(pending)
    waiting = []
    for job in review["send_candidates"]:
        entry = checkpoint["jobs"].setdefault(
            job["id"], {"status": "observe", "revision": 0, "pages": []}
        )
        if entry["status"] == "resolve":
            _resolve(checkpoint, job, entry)
            _save(active, checkpoint)
        if entry["status"] == "waiting":
            waiting.append(entry)
            continue
        if entry["status"] == "manual":
            return _manual(active, checkpoint, job)
        if entry["status"] == "unavailable":
            continue
        if entry["status"] == "ready":
            application = entry["application"]
            job.update(
                application_contract_version=1,
                application=deepcopy(application),
                application_digest=digest_payload(application),
            )
            continue
        if entry["revision"] >= 40:
            fail(
                "linkedin_application_page_limit",
                "The native application exceeded its bounded preparation budget",
            )
        binding = {
            **native_work._binding("linkedin"),
            "session_id": context["session"]["id"],
            "job_id": job["id"],
            "preparation_digest": digest_payload(context),
            "preparation_revision": entry["revision"],
        }
        task = {
            "executor": "playwright",
            "session": context["session"],
            "job": deepcopy(job),
            "application_preparation": True,
            "operation": entry["status"],
            "snapshot_digest": (entry.get("snapshot") or {}).get("snapshot_digest"),
            "answers": entry.get("answers", {}),
            "restart_from_first_page": entry.get("restarting", False),
        }
        _save(active, checkpoint)
        return native_work.present(
            browser_work.ensure_work(
                action="prepare_application", task=task, binding=binding
            )
        )
    if waiting:
        return _card(active, checkpoint, waiting)
    review["skipped_unavailable"] = [
        j
        for j in review["send_candidates"]
        if checkpoint["jobs"][j["id"]]["status"] == "unavailable"
    ]
    review["send_candidates"] = [
        j
        for j in review["send_candidates"]
        if checkpoint["jobs"][j["id"]]["status"] != "unavailable"
    ]
    _save(active, checkpoint)
    return None


def validate_result(work, result):
    from jobagent.platforms.linkedin.collect import detail_id

    _active, checkpoint = _load()
    if work["binding"].get("preparation_digest") != digest_payload(
        checkpoint["context"]
    ):
        fail(
            "linkedin_application_context_changed",
            "The observation belongs to another preparation",
        )
    evidence, job = result["evidence"], work["task"]["job"]
    if (
        result["outcome"] != "success"
        or evidence.get("submission_attempted") is not False
        or evidence.get("job_id") != job["id"]
        or detail_id(evidence.get("job_url")) != job["id"]
        or evidence.get("title") != job["title"]
        or evidence.get("company") != job["company"]
        or evidence.get("preparation_state") not in {"form", "ready", "unavailable"}
    ):
        fail(
            "linkedin_application_observation_invalid",
            "Preparation must verify the exact job without submission",
        )
    snapshot = evidence.get("snapshot")
    if evidence["preparation_state"] != "unavailable" and (
        not isinstance(snapshot, dict)
        or snapshot.get("job_id") != job["id"]
        or snapshot.get("snapshot_digest")
        != digest_payload({k: v for k, v in snapshot.items() if k != "snapshot_digest"})
    ):
        fail(
            "linkedin_application_observation_invalid",
            "Preserve the exact native form observation",
        )


def accept_result(work, result):
    active, checkpoint = _load()
    evidence = result["evidence"]
    entry = checkpoint["jobs"][work["binding"]["job_id"]]
    if work["work_id"] in entry.get("processed", []):
        return
    entry.setdefault("processed", []).append(work["work_id"])
    entry["revision"] += 1
    completed = evidence.get("completed_page")
    if completed:
        entry["pages"] = [
            p
            for p in entry["pages"]
            if p["schema_digest"] != completed["schema_digest"]
        ] + [completed]
    if evidence.get("attachment"):
        if entry.get("attachment") and entry["attachment"] != evidence["attachment"]:
            from jobagent.application.linkedin_recovery import restart_entry
            restart_entry(checkpoint, work["binding"]["job_id"])
            _save(active, checkpoint)
            return
        entry["attachment"] = evidence["attachment"]
    if evidence.get("restarted_from_first_page"):
        entry.pop("restarting", None)
    entry.update(
        snapshot=evidence.get("snapshot"),
        status="unavailable"
        if evidence["preparation_state"] == "unavailable"
        else "manual"
        if evidence.get("requires_manual")
        else "resolve",
    )
    if evidence["preparation_state"] == "ready":
        snapshot = evidence["snapshot"]
        entry.update(
            status="ready",
            application={
                "job_id": work["binding"]["job_id"],
                "preparation_generation": checkpoint.get("generation", 0),
                "context": checkpoint["context"],
                "account_label": evidence["account_label"],
                "pages": entry["pages"],
                "review_text": snapshot["text"],
                "review_digest": snapshot["snapshot_digest"],
                "policy_links": snapshot["links"],
                "submission_attempted": False,
                "attachment": entry["attachment"],
                "manual_interventions": entry.get("manual_interventions", []),
                "attachment_verification": "user_confirmed_existing_attachment_bytes",
            },
        )
    _save(active, checkpoint)


def respond(args):
    ident = str(getattr(args, "interaction_id", "") or "")
    if not ident.startswith(PREFIX):
        return None
    active, checkpoint = _load()
    values = getattr(args, "application_answers", None)
    if any(
        getattr(args, key, None)
        for key in (
            "target_role",
            "target_city",
            "target_location",
            "target_locations",
            "resume_id",
            "attachment_id",
            "location_id",
            "exclude_index",
        )
    ):
        fail(
            "linkedin_application_answer_invalid",
            "Use only the fields from this application interaction",
        )
    replay_value = (
        {"choice": getattr(args, "choice", None)}
        if ident.startswith(PREFIX + "manual-")
        else values
    )
    previous = checkpoint["answered"].get(ident)
    if previous:
        if previous != replay_value:
            fail(
                "interaction_response_conflict",
                "The saved application answer cannot be replaced by a replay",
            )
        old = load_pending_interaction()
        if old and old["interaction_id"] == ident:
            clear_pending_interaction()
        return {
            "ok": True,
            "event": "linkedin_application_facts_saved",
            "idempotent_replay": True,
            "next_suggested": "jobagent work next",
        }
    card = checkpoint.get("card")
    if not card or card["interaction"]["interaction_id"] != ident:
        fail("interaction_not_pending", "Answer the current application facts card")
    if getattr(args, "choice", None) == "pause_platform" and values is None:
        return _present(checkpoint)
    if card.get("manual_job"):
        if getattr(args, "choice", None) != "synced" or values is not None:
            fail(
                "linkedin_application_manual_choice_invalid",
                "Confirm completion of the displayed manual step or keep it paused",
            )
        entry = checkpoint["jobs"][card["manual_job"]]
        entry["status"] = "observe"
        entry.setdefault("manual_interventions", []).append(ident)
        checkpoint["answered"][ident] = replay_value
        checkpoint.pop("card")
        _save(active, checkpoint)
        clear_pending_interaction()
        return {
            "ok": True,
            "event": "linkedin_application_manual_resumed",
            "next_suggested": "jobagent work next",
        }
    if getattr(args, "choice", None):
        fail(
            "linkedin_application_answers_incomplete",
            "Use the displayed application facts fields",
        )
    if not isinstance(values, dict) or set(values) != set(card["questions"]):
        fail(
            "linkedin_application_answers_incomplete",
            "Answer every displayed field, or keep the batch paused",
        )
    facts = {}
    for key, value in values.items():
        field = card["questions"][key]["field"]
        if field["type"] == "checkbox":
            valid = type(value) is bool
        else:
            valid = isinstance(value, str) and len(value) <= 10000
            if field["required"]:
                valid = valid and bool(value)
            if field["options"]:
                valid = valid and value in {
                    o["value"] for o in field["options"] if not o["disabled"]
                }
        if not valid:
            fail(
                "linkedin_application_answer_invalid",
                "Use a displayed native option or a literal factual value",
            )
        facts[key] = {
            "value": value,
            "source": {"kind": "user_confirmed", "reference": ident + ":" + key},
        }
    checkpoint["facts"].update(facts)
    for key, item in card["questions"].items():
        checkpoint.setdefault("confirmed_prefills", {}).setdefault(key, {}).update(
            item["prefills"]
        )
    checkpoint["answered"][ident] = deepcopy(values)
    checkpoint.pop("card")
    for entry in checkpoint["jobs"].values():
        if entry["status"] == "waiting":
            entry["status"] = "resolve"
    _save(active, checkpoint)
    old = load_pending_interaction()
    if old and old["interaction_id"] == ident:
        clear_pending_interaction()
    return {
        "ok": True,
        "event": "linkedin_application_facts_saved",
        "request_preserved": True,
        "no_charge": True,
        "next_suggested": "jobagent work next",
    }
