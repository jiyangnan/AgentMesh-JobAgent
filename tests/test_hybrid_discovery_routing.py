"""Collection capabilities never grant delivery or rewrite active old rounds."""
from copy import deepcopy

import pytest

from jobagent import cli
from jobagent.application import hybrid_discovery
from jobagent.infra import rounds, state


@pytest.fixture
def active(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "ROUNDS_DIR", tmp_path / "rounds")
    monkeypatch.setattr(rounds, "current_round_path", lambda: tmp_path / "current_round.json")
    value = {"schema_version": 4, "round_id": "hybrid-test", "status": "active",
             "browser_executor": "codex_native", "browser_session_id": "native-unbound",
             "collection_executor": "cdp_readonly", "delivery_executor": "codex_native",
             "native_session": None, "platform_order": list(rounds.DEFAULT_PLATFORM_ORDER),
             "platforms": {p: {"status": "pending"} for p in rounds.DEFAULT_PLATFORM_ORDER}}
    rounds.save_round(value)
    return value


def test_new_round_defaults_to_30_and_adaptive_recommendations(active):
    state.current_round_path().unlink()
    created = rounds.start_new_round({"status": "confirmed", "target_roles": ["产品经理"],
                                     "target_cities": ["上海"]})
    assert created["collection_executor"] == "cdp_readonly"
    assert created["delivery_executor"] == "playwright"
    assert created["discovery_options"] == {"candidate_limit": 30, "recommendation_limit": None}


@pytest.mark.parametrize("saved_limit", [None, 30, 100, 500])
def test_discover_keeps_existing_round_scope(active, monkeypatch, saved_limit):
    from jobagent.application import discover
    if saved_limit is not None:
        active["discovery_options"] = {"candidate_limit": saved_limit, "recommendation_limit": None}
    rounds.save_round(active)
    before = state.current_round_path().read_bytes()
    rounds.ensure_current_round()
    assert state.current_round_path().read_bytes() == before
    monkeypatch.setattr(hybrid_discovery, "assert_collection_available", lambda _: None)
    observed = []
    def collect(*args, **kwargs):
        observed.append(rounds.ensure_current_round()["discovery_options"])
        return {"ok": True}
    monkeypatch.setattr(discover, "run_discover", collect)
    assert hybrid_discovery.discover("boss")["ok"]
    assert observed == [{"candidate_limit": saved_limit or 100, "recommendation_limit": None}]


def test_explicit_idle_opt_in_uses_30_without_replacing_round(active):
    active.pop("collection_executor")
    rounds.save_round(active)
    assert hybrid_discovery.enable_existing_round(confirmed=True)["ok"]
    updated = rounds.ensure_current_round()
    assert updated["round_id"] == active["round_id"]
    assert updated["discovery_options"] == {"candidate_limit": 30, "recommendation_limit": None}


def test_old_round_and_existing_receipts_are_not_migrated_to_bulk(active):
    old = deepcopy(active)
    old.pop("collection_executor")
    old.pop("delivery_executor")
    old["platforms"]["boss"] = {"status": "reviewed", "native_delivery": {"authorization_id": "old"}}
    rounds.save_round(old)
    before = state.current_round_path().read_bytes()
    assert not hybrid_discovery.enabled()
    assert rounds.ensure_current_round() == old
    assert state.current_round_path().read_bytes() == before


def test_discovery_bypasses_native_capability_but_send_remains_native(active, monkeypatch):
    from jobagent.application import native_work
    monkeypatch.setattr(native_work, "request_discovery", lambda *_: pytest.fail("native collection used"))
    monkeypatch.setattr(hybrid_discovery, "discover", lambda *a, **k: {"executor": "cdp_readonly"})
    assert cli._native_dispatch(cli.build_parser().parse_args(["boss", "discover"])) == {"executor": "cdp_readonly"}
    assert hybrid_discovery.enabled()


def test_explicit_liepin_repair_flag_reaches_cli_handler(active, monkeypatch):
    active["platforms"]["boss"]["status"] = "completed"
    rounds.save_round(active)
    calls = []
    monkeypatch.setattr(hybrid_discovery, "discover", lambda *a, **k: calls.append((a, k)) or {"ok": True})
    result = cli._native_dispatch(cli.build_parser().parse_args(["liepin", "discover", "--repair-collection"]))
    assert result["ok"] and calls[0][0] == ("liepin",)
    assert calls[0][1]["repair_collection"] is True


@pytest.mark.parametrize("status", ["discovered", "awaiting_delivery_confirmation", "reviewed", "sent", "completed"])
def test_repair_cannot_reopen_a_finalized_liepin_discover(active, monkeypatch, status):
    from jobagent.platforms.discovery import CollectionError
    active["platforms"]["liepin"]["status"] = status
    rounds.save_round(active)
    monkeypatch.setattr(hybrid_discovery, "assert_collection_available", lambda _: pytest.fail("repair entered browser path"))
    with pytest.raises(CollectionError) as error:
        hybrid_discovery.discover("liepin", repair_collection=True)
    assert error.value.code == "collection_repair_not_allowed"


def test_repair_without_preserved_request_does_not_start_or_charge(active, monkeypatch):
    from jobagent.application import discover, resume_freshness
    from jobagent.platforms.discovery import CollectionError
    monkeypatch.setattr(resume_freshness, "gate_search", lambda _: pytest.fail("repair started a fresh workflow"))
    with pytest.raises(CollectionError) as error:
        discover.run_discover("liepin", repair_collection=True)
    assert error.value.code == "collection_repair_not_allowed"


def test_open_native_work_blocks_collection_before_browser_or_cloud(active, monkeypatch):
    from jobagent.infra import browser_work
    monkeypatch.setattr(browser_work, "has_open", lambda: True)
    with pytest.raises(rounds.RoundOrderError) as exc:
        hybrid_discovery.assert_collection_available("boss")
    assert exc.value.payload["error"] == "native_work_context_locked"


def test_confirmed_delivery_blocks_collection_even_between_native_commands(active):
    active["platforms"]["boss"].update(status="reviewed", native_delivery={"authorization_id": "approved"})
    rounds.save_round(active)
    with pytest.raises(rounds.RoundOrderError) as exc:
        hybrid_discovery.assert_collection_available("boss")
    assert exc.value.payload["error"] == "delivery_phase_locked"


def test_native_next_keeps_hybrid_discovery_on_cli(active, monkeypatch):
    from jobagent.application import native_work
    monkeypatch.setattr(native_work, 'current_account_ref', lambda: 'acct_hybrid_test')
    monkeypatch.setattr(native_work, 'request_discovery', lambda *_: pytest.fail('native collection issued'))
    active['platforms']['boss']['status'] = 'login_verified'
    rounds.save_round(active)
    assert native_work.next_work()['next_suggested'] == 'jobagent boss discover'


def test_old_native_binding_requires_new_collection_profile_check(active, monkeypatch):
    from jobagent.application import native_work
    monkeypatch.setattr(native_work, 'current_account_ref', lambda: 'acct_hybrid_test')
    active['collection_session'] = {'id': 'new-collection'}
    active['native_session'] = {'account_ref': 'acct_hybrid_test', 'round_id': active['round_id']}
    rounds.save_round(active)
    assert native_work._session() is None
    assert rounds.ensure_current_round()['native_session'] == active['native_session']


def test_work_next_does_not_reissue_login_or_discover_after_rejection(active, monkeypatch, tmp_path):
    from jobagent.application import native_work
    from jobagent.infra import collection_guard
    from jobagent.platforms.discovery import CollectionError
    guard = collection_guard.CollectionGuard(tmp_path / 'chrome')
    guard.block('boss_detail_request_rejected', upstream_code=36)
    monkeypatch.setattr(collection_guard, 'boss_guard', lambda: guard)
    monkeypatch.setattr(native_work, 'current_account_ref', lambda: 'acct_hybrid_test')
    monkeypatch.setattr(native_work, 'request_login', lambda *a, **k: pytest.fail('reissued login'))
    with pytest.raises(CollectionError) as error:
        native_work.next_work()
    assert error.value.code == 'collection_read_blocked'


def test_resume_collection_requires_confirmation_and_preserves_business_state(active, monkeypatch, tmp_path):
    from jobagent.infra import browser_diagnostics, collection_guard
    guard = collection_guard.CollectionGuard(tmp_path / 'chrome')
    guard.block('boss_detail_request_rejected', upstream_code=36)
    saved = guard._load()
    saved['blocked']['not_before'] = 0
    state.save_json(guard.path, saved)
    monkeypatch.setattr(collection_guard, 'boss_guard', lambda: guard)
    monkeypatch.setattr(browser_diagnostics, 'diagnose_browser', lambda _: pytest.fail('unconfirmed page probe'))
    before = state.current_round_path().read_bytes()
    assert hybrid_discovery.resume_collection('boss')['requires_user_action'] is True
    monkeypatch.setattr(browser_diagnostics, 'diagnose_browser', lambda _: {
        'ready_for_platform_work': True, 'verification_required': False})
    resumed = hybrid_discovery.resume_collection('boss', confirmed=True)
    assert resumed['next_suggested'] == 'jobagent boss discover'
    assert resumed['browser_requests_executed'] is False
    assert state.current_round_path().read_bytes() == before
