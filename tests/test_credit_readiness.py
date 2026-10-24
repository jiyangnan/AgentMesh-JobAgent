"""Cloud-only material, quote boundaries and retained-work recovery contracts."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from jobagent import cli
from jobagent.application import workflow
from jobagent.infra import account_state, browser_work, cloud_client, credentials, rounds, state, upgrade_readiness
from jobagent.infra.protocol import digest_payload
from jobagent.infra.resume_readiness import check_resume
from jobagent.infra.workflow_protocol import with_contract


ACCOUNT = "acct_readiness_synthetic"
PROFILE = {"schema_version": 1, "basic": {"name": "Synthetic candidate"}}
BINDING = {"id": "original-binding", "context_id": "original-context", "resume_id": "original-resume",
           "resume_revision_id": "original-revision", "content_digest": "sha256:" + "1" * 64,
           "resume_revision_number": 1}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "APP_DIR", tmp_path)
    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(state, "ROUNDS_DIR", tmp_path / "state/rounds")
    monkeypatch.setattr(state, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(account_state, "current_account_ref", lambda **_: ACCOUNT)
    monkeypatch.setattr(workflow, "current_account_ref", lambda: ACCOUNT)
    monkeypatch.setattr(account_state, "ensure_account_state", lambda *a, **kw: {"ready": True})
    monkeypatch.setattr(credentials, "load_api_key", lambda: "synthetic-key")
    monkeypatch.setattr(upgrade_readiness, "load_api_key", lambda: "synthetic-key")
    monkeypatch.setattr("shutil.which", lambda _: "/synthetic/bin")
    monkeypatch.setattr(cloud_client, "health", lambda: {"status": "ok"})
    account = {"account": {"account_ref": ACCOUNT, "credit": 7685, "source": "monthly_pass"},
               "server": {"capabilities": {"resume_center": True, "resume_center_adopted": True,
                                           "workflow_protocol_version": 2}}}
    preparation = {"ok": True, "account_ref": ACCOUNT, "ready": True, "state": "ready",
        "offline": False, "stale": False, "resumes": [{"id": "cloud-resume", "confirmed_revision_id": "cloud-revision"}],
        "receipt": {"id": "preparation-receipt", "revisions": [
            {"resume_id": "cloud-resume", "resume_revision_id": "cloud-revision"}]}}
    calls = []
    monkeypatch.setattr(cloud_client, "me", lambda: deepcopy(account))
    def prep():
        calls.append("preparation")
        return deepcopy(preparation)
    monkeypatch.setattr(cloud_client, "resume_center_preparation", prep)
    def quote(action, request_id, scope_digest, **kwargs):
        calls.append(("quote", action, kwargs))
        assert action == "discover", "diagnosis must never quote resume analysis"
        return {"quote": {"credits_required": 10, "charged": False}}
    monkeypatch.setattr(cloud_client, "credits_quote", quote)
    material = {"ok": True, "account_ref": ACCOUNT, "binding": deepcopy(BINDING),
                "profile": deepcopy(PROFILE), "profile_digest": digest_payload(PROFILE)}
    def bound(identifier):
        calls.append(("binding", identifier))
        return deepcopy(material)
    monkeypatch.setattr(cloud_client, "resume_binding_material", bound)
    for name in ("resume_analyze", "discovery_start", "resume_selection", "resume_selection_respond"):
        monkeypatch.setattr(cloud_client, name, lambda *a, **kw: pytest.fail("diagnosis attempted a business mutation"))
    return SimpleNamespace(path=tmp_path, account=account, prep=preparation, material=material, calls=calls)


def snapshot(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def active_round():
    active = rounds.start_new_round({"status": "confirmed", "target_roles": ["Engineer"],
        "target_cities": ["深圳"], "profile_digest": digest_payload(PROFILE), "source": "user_explicit"})
    active.update(account_ref=ACCOUNT, resume_binding=deepcopy(BINDING))
    rounds.save_round(active)
    return active


def test_cloud_resume_without_local_profile_recovers_doctor_credits_and_upgrade(setup):
    state.save_json(state.STATE_DIR / "audit_log.json", {"delivered": ["previous-job"]})
    before = snapshot(setup.path)
    for argv in (["doctor", "env"], ["credits", "status"]):
        result = cli._dispatch_unlocked(cli.build_parser().parse_args(argv))
        assert result["environment_healthy"] and result["workflow"]["ready"]
        assert not result["workflow"]["profile_exists"]
        assert result["workflow"]["resume_ready"] and result["workflow"]["resume_source"] == "resume_center"
        assert result["cloud_access"]["usable"] is True
        assert result["next_suggested"] == "jobagent resume status"
        assert "analysis" not in result["credit_quotes"]
    upgraded = upgrade_readiness.run_upgrade_check()
    assert upgraded["ok"] and upgraded["checks"][1]["source"] == "resume_center"
    assert snapshot(setup.path) == before


def test_empty_material_is_preparation_not_service_failure_or_purchase(setup):
    setup.prep.update(ready=False, state="empty", resumes=[], receipt=None)
    result = cli._doctor_env()
    assert result["environment_healthy"] and not result["workflow"]["ready"]
    assert result["cloud_access"]["usable"] is None
    assert result["cloud_access"]["reason"] == "quote_requires_material"
    assert result["cloud_access"]["paid_pass_required"] is False
    assert result["next_suggested"] == "jobagent resume status"
    assert setup.calls == ["preparation"]


def test_unconfirmed_cloud_material_is_not_replaced_by_local_profile(setup):
    setup.prep.update(ready=False, state="needs_confirmation", receipt=None)
    state.save_json(state.profile_path(), PROFILE)
    result = cli._doctor_env()
    assert not result["workflow"]["ready"]
    assert result["resume_readiness"]["error"] == "resume_preparation_required"
    assert with_contract(result)["action"]["type"] == "handoff"
    assert not any(isinstance(c, tuple) and c[0] == "quote" for c in setup.calls)


@pytest.mark.parametrize("change", [
    {"account_ref": "acct_foreign_synthetic"}, {"offline": True}, {"stale": True},
    {"receipt": None}, {"receipt": []}, {"receipt": {"id": "x", "revisions": "invalid"}},
    {"ready": "true"}, {"resumes": []},
])
def test_bad_cloud_preparation_cannot_enable_work_or_fall_back(setup, change):
    state.save_json(state.profile_path(), PROFILE)
    setup.prep.update(change)
    result = cli._doctor_env()
    assert not result["workflow"]["ready"]
    assert result["error"] == "resume_preparation_invalid"
    assert with_contract(result)["action"]["type"] == "blocked"
    assert not any(isinstance(c, tuple) and c[0] == "quote" for c in setup.calls)


@pytest.mark.parametrize("error,status", [("network_timeout", 503), ("invalid_api_key", 401)])
def test_cloud_preparation_failure_keeps_typed_cause_and_state(setup, monkeypatch, error, status):
    state.save_json(state.profile_path(), PROFILE)
    before = snapshot(setup.path)
    def failed():
        raise cloud_client.CloudError("unavailable", status=status, code=error, retryable=status == 503)
    monkeypatch.setattr(cloud_client, "resume_center_preparation", failed)
    result = cli._doctor_env()
    assert result["error"] == error and not result["workflow"]["ready"]
    assert with_contract(result)["action"]["type"] == "blocked"
    assert snapshot(setup.path) == before


@pytest.mark.parametrize("balance", [0, None, 7685])
def test_original_work_recovers_without_a_new_price_or_material(setup, monkeypatch, balance):
    active = active_round()
    setup.account["account"]["credit"] = balance
    monkeypatch.setattr(browser_work, "list_work", lambda *_: [{"state": "reconcile_only"}])
    state.save_json(state.STATE_DIR / "audit_log.json", {"delivered": ["already-delivered"]})
    before = snapshot(setup.path)
    result = cli._doctor_env()
    assert result["workflow"]["ready"] and result["next_suggested"] == "jobagent work next"
    assert result["cloud_access"]["usable"] is None
    assert result["cloud_access"]["reason"] == "existing_work_preserved"
    assert setup.calls == [("binding", BINDING["id"])]
    assert upgrade_readiness.run_upgrade_check()["ok"]
    assert snapshot(setup.path) == before
    assert rounds.round_status()["round_id"] == active["round_id"]


def test_material_mismatch_remains_blocked_even_with_pending_work(setup, monkeypatch):
    active_round()
    setup.material["profile_digest"] = digest_payload({"changed": True})
    monkeypatch.setattr(browser_work, "list_work", lambda *_: [{"state": "reconcile_only"}])
    result = cli._doctor_env()
    assert not result["workflow"]["ready"]
    assert result["error"] == "bound_resume_material_mismatch"
    assert with_contract(result)["action"]["type"] == "blocked"


def test_staged_choice_is_checked_without_reselecting_or_reanalysis(setup):
    state.save_json(state.STATE_DIR / "pending_round_binding.json", {"binding": BINDING})
    before = snapshot(setup.path)
    result = cli._doctor_env()
    assert result["workflow"]["ready"] and result["resume_readiness"]["source"] == "pending_resume_binding"
    assert "preparation" not in setup.calls
    assert snapshot(setup.path) == before


def test_completed_round_checks_current_preparation_without_rebinding_history(setup):
    active = active_round()
    active["status"] = "completed"
    rounds.save_round(active)
    # Historical material need not be reloaded to prepare a new, user-selected round.
    setup.material["profile_digest"] = "old-no-longer-readable"
    before = snapshot(setup.path)
    assert upgrade_readiness.run_upgrade_check()["ok"]
    assert setup.calls == ["preparation"]
    assert snapshot(setup.path) == before


@pytest.mark.parametrize("classic", ["explicit", "active", "legacy_server", "cloud_empty"])
def test_valid_classic_profile_remains_supported(setup, classic):
    state.save_json(state.profile_path(), PROFILE)
    if classic == "explicit":
        state.save_json(state.STATE_DIR / "pending_round_binding.json", {"round_request": {"no_resume_binding": True}})
    elif classic == "active":
        active = active_round(); active.pop("resume_binding"); rounds.save_round(active)
    elif classic == "legacy_server":
        setup.account.pop("server")
    else:
        setup.prep.update(ready=False, state="empty", resumes=[], receipt=None)
    assert check_resume(setup.account)["source"] == "local_snapshot"
    assert check_resume(setup.account)["ok"]


@pytest.mark.parametrize("cost,balance,usable", [(10, 9, False), (10, 10, True), (0, 0, True)])
def test_quote_cost_and_balance_remain_authoritative(setup, monkeypatch, cost, balance, usable):
    setup.account["account"]["credit"] = balance
    monkeypatch.setattr(cloud_client, "credits_quote", lambda *a, **kw: {"quote": {"credits_required": cost}})
    result = cli._doctor_env()
    assert result["cloud_access"]["usable"] is usable
    assert result["cloud_access"]["required_credits"] == cost
    assert result["workflow"]["ready"] is usable


def test_actual_quote_failure_is_preserved_and_never_auto_loops(setup, monkeypatch):
    def failed(*a, **kw):
        raise cloud_client.CloudError("temporarily unavailable", code="core_unavailable", status=503, retryable=True)
    monkeypatch.setattr(cloud_client, "credits_quote", failed)
    result = cli._doctor_env()
    assert not result["workflow"]["ready"]
    assert result["cloud_access"]["cause"] == "core_unavailable"
    assert result["credit_quotes"]["discover"]["http_status"] == 503
    assert with_contract(result)["action"]["type"] == "blocked"


@pytest.mark.parametrize("args,error", [([], "analysis_quote_material_required"),
    (["--material-digest", "sha256:" + "2" * 64], "analysis_quote_scope_mismatch")])
def test_manual_analysis_quote_without_exact_context_never_calls_server(setup, args, error):
    parsed = cli.build_parser().parse_args(["credits", "quote", "--action", "analysis",
        "--request-id", "original-request", "--scope-digest", "sha256:" + "3" * 64, *args])
    result = cli._dispatch_unlocked(parsed)
    assert result["error"] == error and result["charged"] is False
    assert not setup.calls


def test_manual_analysis_quote_passes_exact_bound_digest(setup, monkeypatch):
    digest = digest_payload({"resume_text": "Synthetic text", "file_name": "synthetic.txt"})
    calls = []
    monkeypatch.setattr(cloud_client, "credits_quote", lambda **kw: calls.append(kw) or {"ok": True})
    args = cli.build_parser().parse_args(["credits", "quote", "--action", "analysis", "--request-id", "original-request",
        "--scope-digest", digest_payload({"material_digest": digest}), "--material-digest", digest])
    assert cli._dispatch_unlocked(args)["ok"]
    assert calls == [{"action": "analysis", "request_id": "original-request",
                      "scope_digest": digest_payload({"material_digest": digest}), "material_digest": digest}]


def test_old_diagnostic_failure_in_journal_runs_fresh_read_and_keeps_history(setup, monkeypatch):
    monkeypatch.setattr(browser_work, "list_work", lambda *_: [])
    # Saved 0.6.22 result: account valid, no local profile, generic analysis quote rejected.
    old = {"ok": True, "local_state": {"ready": True}, "workflow": {"ready": False},
           "cloud_access": {"reason": "credit_price_unavailable"},
           "credit_quotes": {"analysis": {"error": "analysis_quote_required"}},
           "next_suggested": "jobagent doctor env"}
    journal = workflow._load()
    journal.update(last_result=old, snapshot=workflow._snapshot(), revision=5,
                   operations={"original-read": {"status": "completed", "argv": ["jobagent", "doctor", "env"], "result": old}})
    workflow._save(journal)
    action = workflow.next_action()["action"]
    assert action["business_argv"] == ["jobagent", "doctor", "env"]
    result = workflow.advance(action["action_id"], 5)
    assert result["workflow"]["ready"]
    assert workflow._load()["operations"]["original-read"]["result"] == old
    following = workflow.next_action()["action"]
    assert following["business_argv"] == ["jobagent", "resume", "status"]
    assert not state.current_round_path().exists()
