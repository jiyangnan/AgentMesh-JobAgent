"""Offline regression coverage for rejected-read bursts and preserved recovery."""
import json
from types import SimpleNamespace

import pytest

from jobagent.infra import collection_guard as guards, state
from jobagent.platforms.discovery import CollectionError


@pytest.fixture
def clock(monkeypatch):
    value = SimpleNamespace(now=1000.0, waits=[])
    def sleep(seconds):
        value.waits.append(seconds)
        value.now += seconds
    monkeypatch.setattr(guards, 'time', SimpleNamespace(time=lambda: value.now, sleep=sleep))
    return value


def test_list_and_details_share_rate_budget_across_readers_and_state_directories(tmp_path, monkeypatch, clock):
    profile = tmp_path / 'chrome'
    starts = []
    for i in range(45):
        monkeypatch.setattr(state, 'STATE_DIR', tmp_path / str(i))
        guard = guards.CollectionGuard(profile)
        guard.call('list' if i < 7 else 'detail', lambda: starts.append(clock.now))
    assert all(b - a >= guards.MIN_INTERVAL_SECONDS for a, b in zip(starts, starts[1:]))
    for start in starts:
        assert sum(start <= t < start + guards.WINDOW_SECONDS for t in starts) <= guards.MAX_REQUESTS_PER_WINDOW
    assert guard._load()['total_requests'] == 45 and clock.waits
    assert len(guard._load()['requests']) <= guards.MAX_REQUESTS_PER_WINDOW


@pytest.mark.parametrize('upstream', [7, 36, 37, 5012, 122])
def test_first_rejection_stops_all_later_reads_and_new_reader_construction(tmp_path, monkeypatch, clock, upstream):
    from jobagent.drivers.collection_reader import CollectionReader
    from jobagent.drivers.boss import cdp_driver, chrome_manager
    profile = tmp_path / 'chrome'
    manager = SimpleNamespace(user_data_dir=profile)
    attempts = []
    def evaluate(*a, **kw):
        attempts.append(1)
        return {'result': {'value': {'code': upstream}}}
    reader = CollectionReader(driver=SimpleNamespace(manager=manager, cdp=SimpleNamespace(evaluate=evaluate)))
    url = 'https://www.zhipin.com/wapi/zpgeek/search/joblist.json'
    with pytest.raises(CollectionError) as first:
        reader.api_fetch(url)
    assert first.value.details['requires_user_action'] is True
    assert first.value.details['automatic_retry_allowed'] is False
    with pytest.raises(CollectionError, match='stopped'):
        reader.api_fetch(url)
    monkeypatch.setattr(chrome_manager, 'ChromeInstanceManager', lambda: manager)
    monkeypatch.setattr(cdp_driver, 'CDPBossDriver', lambda **kw: pytest.fail('connected before checking stop'))
    clock.now += 3600  # A later process or expired cooldown never silently clears the stop.
    with pytest.raises(CollectionError):
        CollectionReader()
    assert len(attempts) == 1


def test_same_profile_cannot_issue_concurrent_requests(tmp_path, clock):
    first = guards.CollectionGuard(tmp_path / 'chrome')
    second = guards.CollectionGuard(tmp_path / 'chrome')
    def during_read():
        with pytest.raises(CollectionError) as busy:
            second.call('detail', lambda: pytest.fail('concurrent read'))
        assert busy.value.code == 'collection_read_busy'
    first.call('list', during_read)
    assert first._load()['total_requests'] == 1


def test_confirmation_cooldown_and_visible_recovery_are_all_required(tmp_path, clock):
    guard = guards.CollectionGuard(tmp_path / 'chrome')
    guard.call('list', lambda: {})
    budget = guard._load()['requests'][:]
    guard.block('boss_detail_request_rejected', upstream_code=36)
    no_probe = lambda: pytest.fail('readiness probe before confirmation/cooldown')
    with pytest.raises(CollectionError):
        guard.resume(confirmed=False, check_page=no_probe)
    with pytest.raises(CollectionError):
        guard.resume(confirmed=True, check_page=no_probe)
    clock.now += guards.RECOVERY_DELAY_SECONDS
    for observed in ({'ready_for_platform_work': True, 'verification_required': True},
                     {'ready_for_platform_work': True},
                     {'ready_for_platform_work': False, 'verification_required': False}):
        with pytest.raises(CollectionError):
            guard.resume(confirmed=True, check_page=lambda: observed)
    result = guard.resume(confirmed=True, check_page=lambda: {
        'ready_for_platform_work': True, 'verification_required': False})
    assert not result['already_available']
    assert guard._load()['requests'] == budget and guard._load()['total_requests'] == 1
    guard.assert_available()


def test_interrupted_or_corrupt_read_state_cannot_reset_the_budget(tmp_path, clock):
    guard = guards.CollectionGuard(tmp_path / 'chrome')
    def interrupted():
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        guard.call('detail', interrupted)
    with pytest.raises(CollectionError):
        guards.CollectionGuard(tmp_path / 'chrome').call('detail', lambda: pytest.fail('replayed'))
    assert guard._load()['total_requests'] == 1
    guard.path.write_text('{unfinished', encoding='utf-8')
    with pytest.raises(CollectionError) as error:
        guard.call('detail', lambda: pytest.fail('ignored broken state'))
    assert error.value.code == 'collection_read_state_invalid'
    assert guard.path.read_text() == '{unfinished'


def test_full_jd_error_preserves_existing_charge_without_issuing_automatic_retry(monkeypatch):
    from jobagent.application import bulk_decision
    from jobagent.infra.workflow_protocol import with_contract
    monkeypatch.setattr(bulk_decision, 'upload_candidates', lambda *a, **kw: [])
    monkeypatch.setattr(bulk_decision, 'request', lambda *a, **kw: {'status': 'enrichment_required'})
    def fail(*a):
        raise CollectionError('detail_identity_mismatch', 'The response belongs to another job')
    monkeypatch.setattr(bulk_decision, '_enrich', fail)
    with pytest.raises(CollectionError) as error:
        bulk_decision.complete_decision('boss', {'discover_id': 'dis_original'}, [])
    details = error.value.details
    assert details['discover_id'] == 'dis_original' and details['no_charge'] is False
    assert details['billing_status'] == 'charged_pending_matching'
    assert details['additional_charge_on_retry'] is False
    result = with_contract({'ok': False, 'error': error.value.code, **details})
    assert result['action']['type'] == 'blocked' and 'next_suggested' not in result


def test_persisted_stop_is_visible_in_another_process(tmp_path, clock):
    import os
    import subprocess
    import sys
    guard = guards.CollectionGuard(tmp_path / 'chrome')
    guard.block('boss_detail_request_rejected', upstream_code=36)
    script = '''
import sys
from jobagent.infra.collection_guard import CollectionGuard
from jobagent.platforms.discovery import CollectionError
try:
    CollectionGuard(sys.argv[1]).call('list', lambda: sys.exit(9))
except CollectionError as exc:
    print(exc.code)
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path / 'chrome')],
                            env=os.environ.copy(), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and result.stdout.strip() == 'collection_read_blocked'
