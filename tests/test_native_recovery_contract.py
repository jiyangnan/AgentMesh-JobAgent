"""A technical collection stop must expose an actionable, confirmed recovery."""
import copy
from datetime import datetime, timezone

from tests.test_native_work import env, submit
from jobagent import cli
from jobagent.application import native_work as native
from jobagent.infra import browser_work as store


def test_exhausted_collection_offers_confirmed_recovery_without_claiming_success(env):
    env.choose('boss')
    work = store.ensure_work(action='collect_search_page', task={'query': '产品经理', 'city': '郑州', 'page': 1},
        binding={'account_ref': 'account-test', 'round_id': 'round-test', 'platform': 'boss',
                 'session_id': 'native-test', 'request_id': 'original-request', 'discover_id': 'original-discover'})
    for attempt in range(3):
        begun = native.begin(work['work_id'])['work']
        receipt = copy.deepcopy(begun['task']['blocked_result_example'])
        receipt['receipt_id'] = f'identity-{attempt}'
        receipt['evidence'].update(observed_at=datetime.now(timezone.utc).isoformat(), observation='Synthetic truncated job identity')
        response = submit(env, begun, receipt)
    assert response['recovery_command'] == f"jobagent work recover --work-id {work['work_id']} --confirm-recover"
    assert response['recovery_requires_confirmation'] is True
    assert response['requires_user_action'] is True
    assert response['completion_command'].startswith('jobagent work submit')
    assert response['cancel_command'].endswith('--confirm-cancel')
    assert response['work']['state'] == 'reconcile_only'
    assert response['work']['allowed_mode'] == 'reconcile_only'
    assert not response['work']['execution_permitted']


def test_recover_cli_requires_explicit_confirmation_flag():
    args = cli.build_parser().parse_args(['work', 'recover', '--work-id', 'original'])
    assert args.confirm_recover is False
    args = cli.build_parser().parse_args(['work', 'recover', '--work-id', 'original', '--confirm-recover'])
    assert args.confirm_recover is True
