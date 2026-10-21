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
    monkeypatch.setattr(guards.random, 'choice', lambda _: 2.0)
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


def test_random_delay_is_reserved_before_request_and_survives_new_reader(tmp_path, monkeypatch, clock):
    selected = iter([2.0, 5.0, 3.0])
    monkeypatch.setattr(guards.random, 'choice', lambda options: next(selected))
    profile = tmp_path / 'chrome'
    observed = []
    for _ in range(3):
        guard = guards.CollectionGuard(profile)
        def read():
            saved = guard._load()
            assert saved['inflight'] is True
            observed.append((clock.now, saved['next_read_at']))
        guard.call('detail', read)
    assert observed == [(1000.0, 1002.0), (1002.0, 1007.0), (1007.0, 1010.0)]
    assert clock.waits == [2.0, 5.0]


def test_interrupted_wait_cannot_redraw_shorter_delay(tmp_path, monkeypatch, clock):
    guard = guards.CollectionGuard(tmp_path / 'chrome')
    monkeypatch.setattr(guards.random, 'choice', lambda _: 5.0)
    guard.call('detail', lambda: None)
    original_sleep = guards.time.sleep
    def interrupt(_):
        raise KeyboardInterrupt()
    monkeypatch.setattr(guards.time, 'sleep', interrupt)
    with pytest.raises(KeyboardInterrupt):
        guard.call('detail', lambda: pytest.fail('read before wait finished'))
    assert guard._load()['next_read_at'] == 1005.0
    monkeypatch.setattr(guards.time, 'sleep', original_sleep)
    monkeypatch.setattr(guards.random, 'choice', lambda _: 2.0)
    guards.CollectionGuard(tmp_path / 'chrome').call('list', lambda: None)
    assert clock.now == 1005.0
    assert guard._load()['total_requests'] == 2


def test_legacy_budget_keeps_last_request_when_adopting_random_schedule(tmp_path, monkeypatch, clock):
    guard = guards.CollectionGuard(tmp_path / 'chrome')
    state.save_json(guard.path, {'schema_version': 1, 'requests': [1000.0], 'total_requests': 31})
    monkeypatch.setattr(guards.random, 'choice', lambda _: 5.0)
    guard.call('detail', lambda: None)
    assert clock.now == 1005.0 and guard._load()['total_requests'] == 32
    assert guard._load()['requests'] == [1000.0, 1005.0]


def test_engineering_suspension_survives_confirmation_cooldown_and_new_reader(tmp_path, clock):
    guard = guards.CollectionGuard(tmp_path / 'chrome')
    guard.block('boss_detail_request_rejected', upstream_code=36)
    original_block = guard._load()['blocked']
    guard.suspend('user_stopped_live_testing')
    clock.now += 86400
    other = guards.CollectionGuard(tmp_path / 'chrome')
    with pytest.raises(CollectionError) as error:
        other.resume(confirmed=True, check_page=lambda: pytest.fail('suspended page probe'))
    assert error.value.code == 'collection_read_suspended'
    assert error.value.details['automatic_retry_allowed'] is False
    assert error.value.details['requires_user_action'] is False
    assert 'next_suggested' not in error.value.details
    with pytest.raises(CollectionError):
        other.call('detail', lambda: pytest.fail('suspended request'))
    assert other._load()['blocked'] == original_block


@pytest.mark.parametrize('value', [True, float('nan'), -1, 1001.0])
def test_invalid_persisted_next_read_time_never_resets_pacing(tmp_path, clock, value):
    guard = guards.CollectionGuard(tmp_path / 'chrome')
    guard.path.write_text(json.dumps({'schema_version': 1, 'requests': [1000.0],
                                     'total_requests': 1, 'next_read_at': value}), encoding='utf-8')
    with pytest.raises(CollectionError) as error:
        guard.call('list', lambda: pytest.fail('invalid delay was ignored'))
    assert error.value.code == 'collection_read_state_invalid'


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
