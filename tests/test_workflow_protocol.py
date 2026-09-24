"""Public host contract: no credentials, platform sessions or external side effects."""
import json
import sys

import pytest

from jobagent import cli
from jobagent.infra.workflow_protocol import contract, executable_command, with_contract


def test_catalog_is_offline_and_contains_every_platform_command(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['jobagent', 'workflow-contract'])
    monkeypatch.setattr(cli, '_maybe_update', lambda *_: pytest.fail('offline contract checked update'))
    monkeypatch.setattr(cli, '_verify_state_owner_for_command', lambda *_: pytest.fail('offline contract read account'))
    cli.main()
    result = json.loads(capsys.readouterr().out)
    paths = [entry['argv_prefix'][1:] for entry in result['commands']]
    for platform in result['platform_order']:
        assert [platform, 'login'] in paths
        assert [platform, 'discover'] in paths
        assert [platform, 'audit'] in paths
    assert ['resume', 'status'] in paths
    assert result['agent_action']['type'] == 'report'


@pytest.mark.parametrize('command', [
    'jobagent preparation select --context unknown',
    'jobagent round start --made-up yes',
    'jobagent init --key <your_api_key>',
    'https://agentmesh360.com/workbench/',
    'jobagent work next && jobagent round start',
])
def test_unimplemented_or_template_continuation_cannot_grant_execution(command):
    result = with_contract({'ok': False, 'error': 'unsupported', 'next_suggested': command})
    assert result['agent_action']['type'] == 'blocked'
    assert result['agent_action']['browser_fallback_allowed'] is False
    assert executable_command(command) is None


def test_argument_errors_are_structured_and_do_not_echo_credentials(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['jobagent', 'init', '--key', 'private-key', '--typo', 'private-extra'])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    output = capsys.readouterr().err
    assert exc.value.code == 2
    assert 'private-' not in output
    assert json.loads(output)['agent_action']['argv'] == ['jobagent', 'workflow-contract']


def test_user_handoffs_cannot_execute_the_continuation_before_answer():
    result = with_contract({'ok': False, 'requires_user_action': True,
        'user_prompt': 'Complete verification and return.', 'next_suggested': 'jobagent work next'})
    assert result['agent_action']['type'] == 'wait_user'
    assert 'argv' not in result['agent_action']


def test_receipt_reconciliation_cannot_be_mistaken_for_send_permission():
    response = {'event': 'browser_work_required', 'native_step_issued': True,
        'work': {'work_id': 'work-test', 'nonce': 'nonce-test', 'allowed_mode': 'reconcile_only'},
        'next_suggested': 'jobagent work submit --work-id work-test --result <result.json>'}
    action = with_contract(response)['agent_action']
    assert action['type'] == 'native_work'
    assert action['allowed_mode'] == 'reconcile_only'
    assert action['result_schema_field'] == 'work.task.result_schema'
    # A status read with an old nonce never reissues permission.
    response['native_step_issued'] = False
    assert with_contract(response)['agent_action']['type'] == 'blocked'


def test_completed_round_is_reported_without_starting_another_round():
    result = with_contract({'ok': True, 'workflow': {'workflow_complete': True,
                           'next_suggested': 'jobagent round start'}})
    assert result['agent_action']['type'] == 'report'


def test_distributed_skills_share_complete_contract():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    sections = []
    for name in ['docs/agent-onboarding.md', 'skills/codex-job-agent/SKILL.md',
                 'skills/claude-code/SKILL.md', 'skills/openclaw-job-agent/SKILL.md']:
        text = (root / name).read_text()
        sections.append(text.split('## Host-independent workflow contract\n', 1)[1].split('## Installation-to-account handoff', 1)[0])
    assert all(section == sections[0] for section in sections)
