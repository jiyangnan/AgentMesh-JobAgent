"""A technical collection stop must expose an actionable, confirmed recovery."""
import copy
from datetime import datetime, timezone

import pytest

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


@pytest.mark.parametrize("receipt_kind", ["blocked_result_example", "pause_result_example"])
def test_exhaustion_recovery_is_consistent_across_all_read_and_begin_entries(env, monkeypatch, receipt_kind):
    from jobagent.application import workflow
    from jobagent.infra import state
    from jobagent.infra.workflow_protocol import with_contract
    monkeypatch.setattr(workflow, "current_account_ref", lambda: "account-test")
    env.choose("boss")
    work = store.ensure_work(action="collect_search_page", task={"query": "产品经理", "city": "郑州", "page": 1},
        binding={"account_ref": "account-test", "round_id": "round-test", "platform": "boss",
                 "session_id": "native-test", "request_id": "preserved-request", "discover_id": "preserved-discover"})
    for attempt in range(store.MAX_OBSERVATION_ATTEMPTS):
        issued = native.begin(work["work_id"])
        # The third successfully issued permit still allows its one observation.
        assert issued["native_step_issued"]
        assert issued["work"]["allowed_mode"] == "observe"
        assert not issued.get("recovery_requires_confirmation")
        receipt = copy.deepcopy(issued["work"]["task"][receipt_kind])
        receipt["receipt_id"] = f"bounded-observation-{attempt}"
        receipt["evidence"].update(observed_at=datetime.now(timezone.utc).isoformat(), observation="Synthetic inconclusive page")
        response = submit(env, issued["work"], receipt)
    before = store.get_work(work["work_id"], work["binding"])
    round_bytes = state.current_round_path().read_bytes()
    with pytest.raises(store.BrowserWorkError) as rejected:
        native.begin(work["work_id"])
    assert rejected.value.payload["error"] == "browser_work_observation_limit"
    for result in (response, native.next_work(), native.status(), workflow.next_action(), rejected.value.payload):
        assert result["work"]["allowed_mode"] == "reconcile_only"
        assert result["work"]["observation_attempts"] == 3
        assert not result["native_step_issued"]
        assert result["requires_user_action"] is True
        assert result["recovery"]["status"] == "confirmation_required"
        assert result["recovery"]["new_observation_permitted"] is False
        assert result["recovery"]["after_confirmation_argv"] == ["jobagent", "work", "recover", "--work-id", work["work_id"], "--confirm-recover"]
        action = with_contract(result)["action"]
        assert action["type"] == "handoff" and action["confirmation_required"]
        assert "argv" not in action
    assert store.get_work(work["work_id"], work["binding"]) == before
    assert state.current_round_path().read_bytes() == round_bytes
