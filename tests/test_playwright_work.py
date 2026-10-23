"""Transport migration and at-most-once delivery, without real platforms."""
import copy
import json
import pytest
from tests.test_native_work import env, observation, submit
from jobagent.application import native_work as native, playwright_work as pw
from jobagent.infra import browser_work as store, rounds


@pytest.fixture(autouse=True)
def account(monkeypatch):
    monkeypatch.setattr(pw, "current_account_ref", lambda: "account-test")
    monkeypatch.setattr(pw, "pace", lambda session: None)


def switch(env, platform="liepin"):
    env.choose(platform)
    old = env.start(platform)["work"]
    before = copy.deepcopy(rounds.ensure_current_round())
    response = pw.migrate(confirmed=True)
    assert response["request_preserved"]
    active = rounds.ensure_current_round()
    for field in ("round_id", "intent", "native_session", "platforms"):
        assert active[field] == before[field]
    assert active["playwright_session"]["id"] == before["native_session"]["id"]
    return old, native.next_work()["work"]


def test_explicit_idempotent_migration_preserves_authorization(env):
    old, new = switch(env)
    assert new["work_id"] != old["work_id"]
    assert new["binding"] == old["binding"]
    assert store.get_work(old["work_id"], native._binding())["state"] == "closed"
    after = copy.deepcopy(rounds.ensure_current_round())
    assert pw.migrate(confirmed=True)["idempotent_replay"]
    assert rounds.ensure_current_round() == after
    assert native.audit("liepin", complete=False)["summary"]["jobs"] == 1
    assert native.audit("liepin", complete=False)["summary"]["pending"] == 1


def test_migration_rejects_issued_external_action(env):
    env.choose("liepin")
    work = native.begin(env.start("liepin")["work"]["work_id"])["work"]
    following = submit(env, work)
    sent = native.begin(following["work"]["work_id"])["work"]
    snapshot = copy.deepcopy(rounds.ensure_current_round())
    with pytest.raises(store.BrowserWorkError, match="reconciled"):
        pw.migrate(confirmed=True)
    assert rounds.ensure_current_round() == snapshot
    assert store.get_work(sent["work_id"], native._binding())["state"] != "closed"


def test_old_completed_job_skipped_after_migration(env):
    env.choose("boss", count=2)
    response = env.start("boss")
    old_receipts = []
    while response.get("work", {}).get("binding", {}).get("job_id") == "job0":
        work = native.begin(response["work"]["work_id"])["work"]
        response = submit(env, work)
        old_receipts.append(store.get_work(work["work_id"], native._binding()))
    pw.migrate(confirmed=True)
    following = native.next_work()["work"]
    assert following["binding"]["job_id"] == "job1"
    assert native.audit("boss", complete=False)["summary"]["greeting_sent"] == 1
    for old in old_receipts:
        assert store.get_work(old["work_id"], native._binding()) == old


@pytest.mark.parametrize("paused", [False, True])
def test_migration_rejects_closed_prepare_with_pending_resume_choice(env, paused):
    from tests.test_native_resume_choice import show
    from jobagent.application import native_resume_choice as choices
    prepared, response = show(env)
    if paused:
        choices.respond(response["interaction"]["interaction_id"], "pause_delivery")
    assert not store.has_open()
    before = copy.deepcopy(rounds.ensure_current_round())
    with pytest.raises(store.BrowserWorkError, match="prepared resume dialog"):
        pw.migrate(confirmed=True)
    assert rounds.ensure_current_round() == before
    assert store.get_work(prepared["work_id"], native._binding())["state"] == "closed"


class Runtime:
    sends = 0
    connections = 0
    fail_after_send = False
    def __init__(self, session): self.session = session
    def __enter__(self):
        Runtime.connections += 1
        return self
    def __exit__(self, *args): pass
    def adapter(self, work): return self
    def prepare(self, work): pass
    def evidence(self, work):
        return {"profile_directory": self.session["profile_directory"], "profile_verified": True,
                "page_target_id": "fixture-target", "page_url": work["task"]["job"]["url"],
                "observation": "Isolated synthetic page receipt."}
    def execute(self, work, *, may_act):
        result = observation(work)
        page_url = result["evidence"]["page_url"]
        result["evidence"].update(self.evidence(work))
        result["evidence"]["page_url"] = page_url
        if work["side_effect"] and may_act:
            Runtime.sends += 1
            if Runtime.fail_after_send:
                Runtime.fail_after_send = False
                raise TimeoutError("response lost after click")
        return result


@pytest.fixture
def runtime(monkeypatch):
    from jobagent.drivers import playwright_runtime
    Runtime.sends = 0
    Runtime.connections = 0
    Runtime.fail_after_send = False
    monkeypatch.setattr(playwright_runtime, "PlaywrightRuntime", Runtime)


def test_interrupted_send_only_reconciles_and_replay_never_clicks(env, runtime):
    _, work = switch(env, "zhilian")
    response = pw.run(work["work_id"])
    send = response["work"]
    Runtime.fail_after_send = True
    response = pw.run(send["work_id"])
    assert Runtime.sends == 1
    assert response["requires_technical_recovery"]
    response = pw.run(send["work_id"])
    assert Runtime.sends == 1
    assert response["summary"]["resume_submitted"] == 1
    pw.run(send["work_id"])
    assert Runtime.sends == 1


def test_host_cannot_issue_or_forge_playwright_receipt(env):
    _, work = switch(env)
    result = native.begin(work["work_id"])
    assert result["executor"] == "playwright"
    assert store.get_work(work["work_id"], native._binding())["state"] == "ready"
    path = env.path / "forged.json"
    path.write_text(json.dumps({"outcome": "success", "evidence": {"source": pw.SOURCE}}))
    with pytest.raises(store.BrowserWorkError, match="not host-authored"):
        native.submit(work["work_id"], str(path))


@pytest.mark.parametrize("platform", ["boss", "liepin", "zhilian", "51job"])
def test_shared_platform_state_machine_uses_truthful_source(env, runtime, platform):
    _, work = switch(env, platform)
    response = pw.run(work["work_id"], max_steps=15)
    assert response["completion_state"] == "completed"
    assert response["evidence_source"] == pw.SOURCE
    assert response["summary"]["resume_submitted"] == int(platform != "boss")
    assert response["summary"]["greeting_sent"] == int(platform in {"boss", "liepin"})
    assert all(w["result"]["evidence"]["source"] == pw.SOURCE for w in store.list_work(native._binding()) if pw.owns(w))
    assert Runtime.connections == 1


def test_rejected_receipt_is_retained_and_cannot_reissue_send(env, runtime, monkeypatch):
    _, work = switch(env, "zhilian")
    send = pw.run(work["work_id"])["work"]
    real_execute = Runtime.execute
    def invalid(self, work, *, may_act):
        result = real_execute(self, work, may_act=may_act)
        result["evidence"]["job_id"] = "wrong-job"
        return result
    monkeypatch.setattr(Runtime, "execute", invalid)
    result = pw.run(send["work_id"])
    assert result["receipt_rejected"] and result["automatic_retry_allowed"] is False
    assert Runtime.sends == 1
    assert not pw.receipt_path(send["work_id"]).exists()
    assert list(pw.receipt_path(send["work_id"]).parent.glob("*.rejected-*.json"))
    monkeypatch.setattr(Runtime, "execute", real_execute)
    assert pw.run(send["work_id"])["summary"]["resume_submitted"] == 1
    assert Runtime.sends == 1


def test_batch_attachment_choice_matches_all_options_and_scope(env):
    from tests.test_native_resume_choice import show, OPTIONS
    from jobagent.application import native_resume_choice as choices
    prepared, _ = show(env)
    response = pw.select_resume('liepin', OPTIONS[1]['reference'], confirmed=True)
    assert response['attachment_reference'] == OPTIONS[1]['reference']
    assert choices.pending() is None
    assert native.next_work()['work']['action'] == 'submit_resume'
    prepared = store.get_work(prepared['work_id'], native._binding())
    selected, question = choices.selection(prepared)
    assert question is None
    assert selected['attachment_reference'] == OPTIONS[1]['reference']
    changed = copy.deepcopy(prepared)
    changed['result']['evidence']['attachment_options'][1]['reference'] = 'Replacement upload'
    with pytest.raises(store.BrowserWorkError, match="options changed"):
        choices.selection(changed)
    changed = copy.deepcopy(prepared)
    changed['binding']['authorization_id'] = 'another-authorization'
    with pytest.raises(store.BrowserWorkError, match="Preserve the original"):
        choices.selection(changed)
