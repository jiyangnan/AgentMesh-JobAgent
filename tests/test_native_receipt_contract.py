"""Presented receipt contracts, using only synthetic work and temporary state."""

import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from jobagent.application import native_discovery, native_repair, native_work as native
from jobagent.infra import browser_work as store, rounds, state


COMMON_EVIDENCE = {"source", "observed_at", "observation"}
SESSION_EVIDENCE = {"window_reference", "profile_label"}
JOB_EVIDENCE = {"job_id", "job_url", "title", "company"}
DELIVERY_ACTIONS = ("inspect_delivery", "open_communication", "send_greeting", "submit_resume")
SUCCESS_FIELDS = {
    "inspect_delivery": {
        "history_checked", "login_state", "resume_state", "communication_state",
        "resume_reference", "receipt_kind", "existing_outgoing_text", "message_state",
        "conversation_job_verified",
    },
    "open_communication": {"communication_state", "conversation_job_verified", "default_greeting_observed"},
    "send_greeting": {"outgoing_text", "message_state", "conversation_job_verified"},
    "submit_resume": {"resume_state", "resume_reference", "receipt_kind", "receipt_checked"},
}
EXAMPLE_FIELDS = {
    **SUCCESS_FIELDS,
    "inspect_delivery": SUCCESS_FIELDS["inspect_delivery"] - {"receipt_kind", "conversation_job_verified"},
    "open_communication": SUCCESS_FIELDS["open_communication"] - {"default_greeting_observed"},
}


@pytest.fixture
def receipt_env(tmp_path, monkeypatch):
    for name, value in {"APP_DIR": tmp_path, "STATE_DIR": tmp_path / "state",
                        "ROUNDS_DIR": tmp_path / "state" / "rounds"}.items():
        monkeypatch.setattr(state, name, value)
    monkeypatch.setattr(native, "current_account_ref", lambda: "synthetic-account")
    session = {
        "id": "synthetic-session", "account_ref": "synthetic-account", "round_id": "synthetic-round",
        "window_reference": "synthetic-window", "profile_label": "Synthetic profile",
        "group_reference": "Synthetic Job Agent", "accounts": {"liepin": "Synthetic user"},
    }
    active = {
        "schema_version": rounds.ROUND_SCHEMA_VERSION, "round_id": "synthetic-round", "status": "active",
        "platform_order": list(native.PLATFORMS),
        "intent": {"target_cities": ["郑州"], "target_roles": ["数据分析师"]},
        "browser_executor": native.EXECUTOR, "browser_session_id": session["id"],
        "native_session": session,
        "platforms": {p: {"status": "skipped_this_round" if p == "boss" else "active" if p == "liepin" else "pending"}
                      for p in native.PLATFORMS},
    }
    rounds.save_round(active)
    binding = {"account_ref": "synthetic-account", "round_id": "synthetic-round",
               "platform": "liepin", "session_id": session["id"]}
    job = {"id": "synthetic-job", "url": "https://www.liepin.com/job/synthetic-job.shtml",
           "title": "数据分析师", "company": "Synthetic Company", "cloud_greeting": "您好，希望了解这个岗位。"}

    def issue(action):
        if action == "collect_search_page":
            task = native_discovery._task(
                {"platform": "liepin", "candidate_limit": 3,
                 "queries": [{"keyword": "数据分析师", "city": "郑州", "page_limit": 1}]},
                {"candidates": []}, 0, 1,
            )
        elif action == "repair_detail":
            task = native_repair._task(
                {"session": session, "binding": {
                    "repair_id": "synthetic-repair", "discover_id": "synthetic-discover",
                    "manifest_id": "synthetic-manifest", "candidate_digest": "synthetic-digest",
                }}, job,
            )
        elif action == "bind_session":
            active["native_session"] = None
            rounds.save_round(active)
            task = {"result_schema": {"evidence": {"native_computer_use_available": "boolean"}}}
        else:
            task = {
                "job": job, "session": session,
                "delivery_source": {"input_path": str(tmp_path / "synthetic-review.json"),
                                    "preview_id": "synthetic-preview", "authorization_id": "synthetic-authorization"},
                "resume_reference": "Synthetic account resume",
                "result_schema": {"outcome": "success|uncertain|unresolved|unavailable",
                                  "evidence": {"source": "host_ui_observation", "receipt_checked": "boolean"}},
            }
        work = store.ensure_work(action=action, task=task, binding=binding,
                                 side_effect=action in {"open_communication", "send_greeting", "submit_resume"})
        issued = store.begin_work(work["work_id"], binding)
        return native.present(issued, execution=True)["work"]

    return SimpleNamespace(issue=issue, session=session, job=job, path=tmp_path, binding=binding)


@pytest.mark.parametrize("action", DELIVERY_ACTIONS)
def test_delivery_presentation_describes_applicable_evidence_and_example(receipt_env, action):
    work = receipt_env.issue(action)
    task = work["task"]
    schema = task["result_schema"]
    assert {"receipt_id", "nonce", "binding", "outcome", "evidence"} <= set(schema["envelope"])
    assert COMMON_EVIDENCE <= set(schema["evidence_common"])
    fields = COMMON_EVIDENCE | SESSION_EVIDENCE | JOB_EVIDENCE | {"page_url", "account_label"}
    assert fields | SUCCESS_FIELDS[action] <= set(schema["evidence"])
    example = task["result_example"]
    assert example["nonce"] == work["nonce"] and example["binding"] == work["binding"]
    assert fields | EXAMPLE_FIELDS[action] <= set(example["evidence"])
    for field, expected in {"job_id": receipt_env.job["id"], "job_url": receipt_env.job["url"],
                            "title": receipt_env.job["title"], "company": receipt_env.job["company"]}.items():
        assert example["evidence"][field] == expected
    if action == "send_greeting":
        assert example["evidence"]["outgoing_text"] == receipt_env.job["cloud_greeting"]
    if action == "submit_resume":
        assert example["evidence"]["resume_reference"] == work["task"]["resume_reference"]
    native._validate_delivery(work, example, example["evidence"])


@pytest.mark.parametrize("action,field,values", [
    ("inspect_delivery", "resume_state", {"sent", "not_sent", "not_applicable", "unknown"}),
    ("inspect_delivery", "communication_state", {"open", "not_open", "not_applicable", "unknown"}),
    ("inspect_delivery", "message_state", {"sent", "delivered", "not_sent", "unknown"}),
    ("open_communication", "communication_state", {"open"}),
    ("send_greeting", "message_state", {"sent", "delivered"}),
    ("submit_resume", "receipt_kind", {"application_history", "resume_card", "application_success_and_history"}),
])
def test_delivery_schema_lists_accepted_state_values(receipt_env, action, field, values):
    description = json.dumps(receipt_env.issue(action)["task"]["result_schema"]["evidence"][field])
    assert all(value in description for value in values)


@pytest.mark.parametrize("action", DELIVERY_ACTIONS)
def test_unresolved_example_has_identity_and_receipt_check_without_success_claims(receipt_env, action):
    work = receipt_env.issue(action)
    result = copy.deepcopy(work["task"]["unresolved_result_example"])
    assert result["outcome"] == "unresolved"
    assert not result.get("requires_user_action")
    assert result["nonce"] == work["nonce"] and result["binding"] == work["binding"]
    evidence = result["evidence"]
    assert COMMON_EVIDENCE | SESSION_EVIDENCE | JOB_EVIDENCE <= set(evidence)
    assert evidence["receipt_checked"] is True
    assert evidence.get("resume_state") != "sent"
    assert evidence.get("communication_state") != "open"
    assert evidence.get("message_state") not in {"sent", "delivered"}
    assert not evidence.get("outgoing_text") and not evidence.get("existing_outgoing_text")
    assert evidence.get("conversation_job_verified") is not True
    assert evidence.get("history_checked") is not True
    result["receipt_id"] = f"synthetic-unresolved-{action}"
    evidence["observed_at"] = datetime.now(timezone.utc).isoformat()
    evidence["observation"] = "Synthetic official receipt check remains inconclusive."
    native._validate_delivery(work, result, evidence)


@pytest.mark.parametrize("action", ["collect_search_page", "repair_detail", "send_greeting"])
def test_pause_example_has_only_common_and_existing_session_observations(receipt_env, action):
    work = receipt_env.issue(action)
    sample = work["task"]["pause_result_example"]
    assert set(sample) == {"receipt_id", "nonce", "binding", "outcome", "evidence", "requires_user_action", "reason"}
    assert sample["outcome"] == "uncertain" and sample["requires_user_action"] is True
    assert sample["nonce"] == work["nonce"] and sample["binding"] == work["binding"]
    assert set(sample["evidence"]) == COMMON_EVIDENCE | SESSION_EVIDENCE
    for field in SESSION_EVIDENCE:
        assert sample["evidence"][field] == receipt_env.session[field]


@pytest.mark.parametrize("action", ["collect_search_page", "repair_detail", "send_greeting", "bind_session"])
def test_pause_schema_declares_minimal_evidence_and_exempts_success_fields(receipt_env, action):
    task = receipt_env.issue(action)["task"]
    schema = task["pause_result_schema"]
    assert set(schema["required"]) == {
        "receipt_id", "nonce", "binding", "outcome", "requires_user_action", "reason", "evidence",
    }
    expected = COMMON_EVIDENCE if action == "bind_session" else COMMON_EVIDENCE | SESSION_EVIDENCE
    assert set(schema["evidence_required"]) == expected
    assert set(schema["evidence_optional"]) == {"page_url", "account_label"}
    assert schema["action_specific_success_fields_required"] is False
    assert schema["outcome"] == "uncertain" and schema["requires_user_action"] is True
    assert schema["reason"] == task["pause_reason_values"]
    assert {"verification_required", "permission_required", "session_unknown"} <= set(schema["reason"])


@pytest.mark.parametrize("action", ["collect_search_page", "repair_detail", "send_greeting", "bind_session"])
def test_filled_pause_example_is_preserved_without_success_validation(receipt_env, monkeypatch, action):
    work = receipt_env.issue(action)
    monkeypatch.setattr(native_discovery, "validate_page", lambda *a, **k: pytest.fail("pause reached page validation"))
    monkeypatch.setattr(native_repair, "validate_detail", lambda *a, **k: pytest.fail("pause reached detail validation"))
    monkeypatch.setattr(native, "_validate_delivery", lambda *a, **k: pytest.fail("pause reached delivery validation"))
    result = copy.deepcopy(work["task"]["pause_result_example"])
    result["receipt_id"] = f"synthetic-pause-{action}"
    result["evidence"]["observed_at"] = datetime.now(timezone.utc).isoformat()
    result["evidence"]["observation"] = "Synthetic host permission unavailable." if action == "bind_session" else "Synthetic verification challenge visible."
    if action == "bind_session":
        result["reason"] = "permission_required"
        assert set(result["evidence"]) == COMMON_EVIDENCE
    path = receipt_env.path / "pause-result.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    response = native.submit(work["work_id"], str(path))
    assert response["requires_user_action"] is True
    assert response["request_preserved"] is True
    assert response["work"]["work_id"] == work["work_id"]
    assert response["work"]["state"] == "reconcile_only"
    assert store.get_work(work["work_id"], receipt_env.binding)["result"] == result
    assert response["next_suggested"] == f"jobagent work begin --work-id {work['work_id']}"
