"""Factual conflicts, atomic answers and explicit manual continuation."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from jobagent.application import linkedin_application as application
from jobagent.infra import browser_work, rounds, state
from jobagent.infra.interaction_state import load_pending_interaction
from jobagent.infra.protocol import digest_payload


def observed(
    question="Full name", value="Different website name", kind="text", required=True
):
    field = {
        "field_id": digest_payload(question),
        "question": question,
        "value": value,
        "type": kind,
        "required": required,
        "disabled": False,
        "options": [],
    }
    return {"unsupported": [], "errors": [], "links": [], "fields": [field]}


def test_resume_conflict_is_shown_before_overwriting_and_job_specific_prefills_stay_explicit(
    monkeypatch,
):
    snapshot = observed()
    rule = {
        "behavior": "profile_fact",
        "value": "Resume Name",
        "reuse_key": "contact-name",
        "source": {"kind": "resume_fact", "reference": "basic.name"},
    }
    monkeypatch.setattr(
        application, "_plan", lambda *args: {snapshot["fields"][0]["field_id"]: rule}
    )
    checkpoint = {"facts": {}, "confirmed_prefills": {}}
    entry = {"snapshot": snapshot}
    application._resolve(checkpoint, {"id": "123"}, entry)
    assert entry["status"] == "waiting" and entry["answers"] == {}
    assert entry["missing"][0]["suggested_value"] == "Resume Name"
    confirmed = {
        "value": "Chosen Name",
        "source": {"kind": "user_confirmed", "reference": "current-card"},
    }
    checkpoint["facts"]["contact-name"] = confirmed
    checkpoint["confirmed_prefills"]["contact-name"] = {"123": "Different website name"}
    application._resolve(checkpoint, {"id": "123"}, entry)
    assert entry["status"] == "fill" and list(entry["answers"].values()) == [confirmed]
    application._resolve(checkpoint, {"id": "456"}, entry)
    assert (
        entry["status"] == "waiting"
    )  # A conflicting second profile is not silently overwritten.


@pytest.fixture
def checkpoint(tmp_path, monkeypatch):
    for name, value in {
        "STATE_DIR": tmp_path / "state",
        "ROUNDS_DIR": tmp_path / "state" / "rounds",
    }.items():
        monkeypatch.setattr(state, name, value)
    active = {
        "round_id": "round-unit",
        "status": "active",
        "schema_version": rounds.ROUND_SCHEMA_VERSION,
        "platform_order": ["linkedin"],
        "platforms": {"linkedin": {"status": "reviewed"}},
    }
    value = {
        "context": {"account_ref": "synthetic"},
        "facts": {},
        "answered": {},
        "jobs": {
            "123": {
                "status": "waiting",
                "revision": 2,
                "pages": [{"schema_digest": "original-page"}],
            }
        },
    }
    monkeypatch.setattr(
        application,
        "_load",
        lambda: (
            rounds.ensure_current_round(),
            deepcopy(rounds.ensure_current_round()["linkedin_preparation"]),
        ),
    )
    active["linkedin_preparation"] = value
    rounds.save_round(active)
    return active, value


def test_answers_validate_as_a_whole_and_restore_the_same_pending_card(checkpoint):
    active, value = checkpoint
    value["review"] = {
        "send_candidates": [{"id": "123", "title": "Role", "company": "Company"}]
    }
    entry = value["jobs"]["123"]
    entry["missing"] = [
        {
            "field": observed("Email", "", "email")["fields"][0],
            "reuse_key": "email",
            "job_id": "123",
            "policy_links": [],
            "suggested_value": None,
        },
        {
            "field": observed("Policy", False, "checkbox")["fields"][0],
            "reuse_key": "policy",
            "job_id": "123",
            "policy_links": [],
            "suggested_value": None,
        },
    ]
    response = application._card(active, value, [entry])
    identifier = response["interaction"]["interaction_id"]
    before = rounds.ensure_current_round()
    with pytest.raises(browser_work.BrowserWorkError):
        application.respond(
            SimpleNamespace(
                interaction_id=identifier,
                application_answers={
                    "email": "fixture@example.test",
                    "policy": "invented",
                },
            )
        )
    assert rounds.ensure_current_round() == before
    assert application._present(value)["interaction"] == response["interaction"]
    args = SimpleNamespace(
        interaction_id=identifier,
        application_answers={"email": "fixture@example.test", "policy": True},
    )
    application.respond(args)
    assert not load_pending_interaction()
    assert application.respond(args)["idempotent_replay"]
    with pytest.raises(browser_work.BrowserWorkError):
        application.respond(
            SimpleNamespace(
                interaction_id=identifier,
                application_answers={"email": "changed@example.test", "policy": True},
            )
        )


def test_manual_step_requires_explicit_answer_and_preserves_prior_pages(checkpoint):
    active, value = checkpoint
    response = application._manual(
        active, value, {"id": "123", "title": "Role", "company": "Company"}
    )
    identifier = response["interaction"]["interaction_id"]
    assert application.respond(
        SimpleNamespace(interaction_id=identifier, choice="pause_platform")
    )["requires_user_action"]
    assert (
        rounds.ensure_current_round()["linkedin_preparation"]["jobs"]["123"]["status"]
        == "waiting"
    )
    result = application.respond(
        SimpleNamespace(interaction_id=identifier, choice="synced")
    )
    after = rounds.ensure_current_round()["linkedin_preparation"]["jobs"]["123"]
    assert result["event"] == "linkedin_application_manual_resumed"
    assert after["status"] == "observe" and after["pages"] == [
        {"schema_digest": "original-page"}
    ]
    assert not load_pending_interaction()


def test_restart_clears_only_attachment_approval_and_retains_processed_receipts():
    from jobagent.application.linkedin_recovery import restart_entry
    value = {"jobs": {"123": {"revision": 6, "processed": ["completed-work"], "pages": [
        {"fields": [{"type": "attachment", "source": {"reference": "attachment-card"}}]}]}},
        "facts": {"attachment": {"source": {"reference": "attachment-card"}},
                  "phone": {"source": {"reference": "contact-card"}}},
        "confirmed_prefills": {"attachment": {"123": "Old.pdf"}}}
    restart_entry(value, "123")
    assert set(value["facts"]) == {"phone"}
    assert value["jobs"]["123"]["processed"] == ["completed-work"]
    assert value["jobs"]["123"]["restarting"]
    assert value["generation"] == 1


@pytest.mark.parametrize("issued", [False, True])
def test_new_context_cancels_only_unissued_work_and_is_crash_recoverable(checkpoint, monkeypatch, issued):
    from jobagent.application import linkedin_recovery as recovery
    from jobagent.application import native_work
    active, value = checkpoint
    binding = {"account_ref": "synthetic", "round_id": active["round_id"],
               "platform": "linkedin", "host_session_id": "synthetic-session", "job_id": "123"}
    monkeypatch.setattr(native_work, "_binding", lambda *args: binding)
    value["context"].update(round_id=active["round_id"], session={"id": "synthetic-session"}, profile_digest="old")
    fresh_context = {**value["context"], "profile_digest": "new"}
    active["linkedin_preparation"] = value
    rounds.save_round(active)
    work = browser_work.ensure_work(action="deliver", task={}, binding=binding, side_effect=True, key="old-submit")
    if issued:
        browser_work.begin_work(work["work_id"], binding)
        with pytest.raises(browser_work.BrowserWorkError, match="Reconcile"):
            recovery.replace_context(active, value, fresh_context)
        assert browser_work.get_work(work["work_id"], binding)["state"] != "closed"
        return
    cancel = browser_work.cancel_unexecuted

    def crash(*args, **kwargs):
        cancel(*args, **kwargs)
        raise RuntimeError("after cancellation")

    monkeypatch.setattr(browser_work, "cancel_unexecuted", crash)
    with pytest.raises(RuntimeError):
        recovery.replace_context(active, value, fresh_context)
    monkeypatch.setattr(browser_work, "cancel_unexecuted", cancel)
    recovery.replace_context(rounds.ensure_current_round(), value, fresh_context)
    saved = rounds.ensure_current_round()
    assert saved["linkedin_preparation"]["context"] == fresh_context
    assert work["work_id"] in saved["native_processed_work"]
    assert saved["linkedin_preparation"]["facts"] == {}
