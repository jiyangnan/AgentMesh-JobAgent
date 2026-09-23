from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from jobagent import cli
from jobagent.infra import credentials, onboarding
from jobagent.infra import tls_support

ROOT = Path(__file__).resolve().parents[1]


def test_installer_adds_transport_check_without_claiming_account_verification(monkeypatch, capsys):
    monkeypatch.setattr(credentials, 'load_api_key', lambda: 'secret')
    monkeypatch.setattr(tls_support, 'transport_preflight', lambda: {
        'ok': False, 'agent_instructions': 'Do not repeat until repaired.', 'user_prompt': 'HTTPS blocked.'})
    monkeypatch.setattr(sys, 'argv', ['onboarding', '--installer'])
    onboarding.main()
    output = capsys.readouterr().out
    result = json.loads(output.split('Agent handoff (follow before ending setup):\n')[1])
    assert result['transport_preflight']['ok'] is False
    assert result['account_verified'] is False
    assert result['next_suggested'] == 'jobagent doctor env'
    assert 'secret' not in output
    assert 'HTTPS blocked.' in output


def test_doctor_keeps_typed_tls_diagnostics_instead_of_rebinding_key(monkeypatch):
    from jobagent.infra.cloud_client import CloudError
    detail = {'tls_diagnostic': {'reason': 'certificate_expired', 'verify_code': 10},
              'next_suggested': 'jobagent doctor tls', 'agent_instructions': 'Check time and service certificate.'}
    def failed():
        raise CloudError('TLS failure', code='tls_certificate_verification_failed', details=detail)
    monkeypatch.setattr(credentials, 'load_api_key', lambda: 'existing-secret')
    monkeypatch.setattr('jobagent.infra.cloud_client.health', failed)
    result = cli._doctor_env()
    assert result['tls_diagnostic']['verify_code'] == 10
    assert result['next_suggested'] == 'jobagent doctor tls'
    assert result['workflow']['next_suggested'] == 'jobagent doctor tls'
    assert result['api_key_configured'] is True
    assert 'existing-secret' not in json.dumps(result)


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('saved_key', [None, '', 'agentmesh_live_private_value'])
def test_handoff_is_read_only_and_never_confuses_presence_with_verification(monkeypatch, tmp_path, saved_key):
    key_path = tmp_path / 'credentials'
    if saved_key is not None:
        key_path.write_text(saved_key)
    (tmp_path / 'browser-work.sqlite3').write_bytes(b'preserved ledger, not inspected')
    monkeypatch.delenv('JOBAGENT_API_KEY', raising=False)
    monkeypatch.setattr(credentials, 'CREDENTIALS_PATH', key_path)
    before = snapshot(tmp_path)
    result = onboarding.installation_handoff()
    assert result['account_verified'] is False
    assert result['event'] == 'onboarding_handoff'
    assert result['api_key_configured'] is bool(saved_key)
    if saved_key:
        assert result['next_suggested'] == 'jobagent doctor env'
        assert result['requires_user_action'] is False
        assert saved_key not in json.dumps(result)
    else:
        assert result['requires_user_action'] is True
        assert result['onboarding']['stage'] == 'api_key_required'
        assert onboarding.ACCOUNT_URL in result['user_prompt']
        assert '回到当前这段 Agent 对话' in result['user_prompt']
        assert '我已配置 API Key，请继续 Job Agent 设置。' in result['user_prompt']
    assert snapshot(tmp_path) == before


def test_env_key_uses_same_doctor_handoff_without_printing_secret(monkeypatch, tmp_path):
    monkeypatch.setenv('JOBAGENT_API_KEY', 'secret-from-env')
    monkeypatch.setattr(credentials, 'CREDENTIALS_PATH', tmp_path / 'absent')
    result = onboarding.installation_handoff()
    assert result['next_suggested'] == 'jobagent doctor env'
    assert 'secret-from-env' not in json.dumps(result)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('error', [PermissionError('secret'), UnicodeError('secret')])
def test_unreadable_credentials_do_not_claim_new_account_or_leak_contents(monkeypatch, error):
    def unreadable():
        raise error
    monkeypatch.setattr(credentials, 'load_api_key', unreadable)
    result = onboarding.installation_handoff()
    assert result['onboarding']['stage'] == 'credentials_unreadable'
    assert result['requires_user_action'] is True
    assert 'secret' not in json.dumps(result)
    assert result['next_suggested'] == 'jobagent onboarding'


def test_cli_onboarding_bypasses_all_business_startup_hooks(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail('onboarding must not update, migrate, lock, connect or write')
    for name in ['_maybe_update', '_prepare_client_upgrade', '_verify_state_owner_for_command',
                 '_schedule_analytics_flush_safely', '_dispatch']:
        monkeypatch.setattr(cli, name, forbidden)
    monkeypatch.setattr('jobagent.infra.native_command_lock.command_lock', forbidden)
    monkeypatch.setattr('jobagent.infra.codex_skill.install_skill', forbidden)
    monkeypatch.setattr('jobagent.infra.cloud_client.me', forbidden)
    monkeypatch.setattr(credentials, 'load_api_key', lambda: None)
    monkeypatch.setattr(sys, 'argv', ['jobagent', 'onboarding'])
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result['onboarding']['stage'] == 'api_key_required'


def test_work_next_without_key_returns_the_return_to_agent_handoff(monkeypatch):
    from jobagent.infra.account_state import AccountStateError
    monkeypatch.setattr(credentials, 'load_api_key', lambda: None)
    with pytest.raises(AccountStateError) as exc:
        cli._verify_state_owner_for_command(cli.build_parser().parse_args(['work', 'next']))
    result = exc.value.payload
    assert result['error'] == 'api_key_required'
    assert result['requires_user_action'] is True
    assert '回到当前这段 Agent 对话' in result['user_prompt']


@pytest.mark.parametrize('verified,credit', [(True, 30), (True, 0), (False, 0)])
def test_init_always_continues_to_doctor_before_resume_or_payment(monkeypatch, tmp_path, verified, credit):
    monkeypatch.setattr('jobagent.infra.cloud_client.me', lambda **kw: {'account': {'credit': credit}})
    monkeypatch.setattr(credentials, 'save_api_key', lambda key: tmp_path / 'credentials')
    monkeypatch.setattr('jobagent.infra.account_state.ensure_account_state', lambda *a, **kw: {'ready': True})
    monkeypatch.setattr(cli, '_record_initialized_safely', lambda key: None)
    monkeypatch.setattr('jobagent.infra.product_announcements.mark_workbench_launch_announced', lambda: None)
    argv = ['init', '--key', 'test-secret'] + ([] if verified else ['--no-verify'])
    result = cli._init(cli.build_parser().parse_args(argv))
    assert result['next_suggested'] == 'jobagent doctor env'
    assert result['requires_user_action'] is False
    assert '回到原来的 Agent 对话' in result['message']
    assert 'test-secret' not in json.dumps(result)


def test_init_preserves_account_recovery_instead_of_advancing_setup(monkeypatch, tmp_path):
    from jobagent.infra.account_state import AccountStateError
    recovery = {'ok': False, 'error': 'local_state_account_mismatch', 'next_suggested': 'jobagent account switch --new-state'}
    def blocked(*a, **kw):
        raise AccountStateError(recovery)
    monkeypatch.setattr('jobagent.infra.cloud_client.me', lambda **kw: {'account': {}})
    monkeypatch.setattr(credentials, 'save_api_key', lambda key: tmp_path / 'credentials')
    monkeypatch.setattr('jobagent.infra.account_state.ensure_account_state', blocked)
    result = cli._init(cli.build_parser().parse_args(['init', '--key', 'test-secret']))
    assert result['next_suggested'] == recovery['next_suggested']
    assert result['ok'] is False
    assert 'onboarding' not in result


@pytest.fixture
def doctor_context(monkeypatch, tmp_path):
    monkeypatch.setattr(credentials, 'load_api_key', lambda: 'test-secret')
    monkeypatch.setattr('jobagent.infra.cloud_client.health', lambda: {'status': 'ok'})
    monkeypatch.setattr('jobagent.infra.cloud_client.me', lambda: {'account': {'credit': 30, 'source': 'signup_trial'}})
    monkeypatch.setattr('jobagent.infra.account_state.ensure_account_state', lambda *a, **kw: {'ready': True})
    monkeypatch.setattr('jobagent.infra.state.profile_path', lambda: tmp_path / 'profile.json')
    monkeypatch.setattr('jobagent.infra.rounds.round_status', lambda: {'next_suggested': 'jobagent round start'})
    monkeypatch.setattr(shutil, 'which', lambda cmd: '/fake/' + cmd)
    return tmp_path


@pytest.mark.parametrize('missing', [True, False])
def test_doctor_key_handoff_for_absent_or_rejected_key(monkeypatch, doctor_context, missing):
    from jobagent.infra.cloud_client import CloudError
    if missing:
        monkeypatch.setattr(credentials, 'load_api_key', lambda: None)
    else:
        def invalid():
            raise CloudError('invalid', status=401, code='invalid_api_key')
        monkeypatch.setattr('jobagent.infra.cloud_client.me', invalid)
    result = cli._doctor_env()
    assert result['requires_user_action'] is True
    assert result['onboarding']['stage'] == 'api_key_required'
    assert '回到当前这段 Agent 对话' in result['user_prompt']


@pytest.mark.parametrize('health_down', [True, False])
def test_doctor_outage_preserves_key_and_never_sends_user_to_register(monkeypatch, doctor_context, health_down):
    from jobagent.infra.cloud_client import CloudError
    if health_down:
        monkeypatch.setattr('jobagent.infra.cloud_client.health', lambda: {'ok': False})
    else:
        def unavailable():
            raise CloudError('unavailable', code='network_timeout', retryable=True)
        monkeypatch.setattr('jobagent.infra.cloud_client.me', unavailable)
    result = cli._doctor_env()
    assert result['next_suggested'] == 'jobagent doctor env'
    assert result['api_key_action'] == 'jobagent doctor env'
    assert result['onboarding']['stage'] == 'account_verification_pending'
    assert '申请' not in result.get('user_prompt', '')


def test_doctor_resume_handoff_includes_workbench_and_return_path(doctor_context):
    result = cli._doctor_env()
    assert result['environment_healthy'] is True
    assert result['cloud_access']['usable'] is True
    assert result['onboarding']['stage'] == 'resume_required'
    assert result['requires_user_action'] is True
    assert onboarding.WORKBENCH_URL in result['user_prompt']
    assert '简历已准备好，请继续' in result['user_prompt']
    assert 'jobagent resume list' in result['user_prompt']
    assert '#pricing' not in result['user_prompt']


def test_doctor_payment_handoff_only_for_insufficient_credits(monkeypatch, doctor_context):
    monkeypatch.setattr('jobagent.infra.cloud_client.me', lambda: {'account': {'credit': 0}})
    result = cli._doctor_env()
    assert result['environment_healthy'] is True
    assert result['workflow']['ready'] is False
    assert result['cloud_access']['paid_pass_required'] is True
    assert result['onboarding']['stage'] == 'credits_required'
    assert '额度已准备好，请继续' in result['user_prompt']


def test_doctor_unknown_credit_balance_never_recommends_purchase(monkeypatch, doctor_context):
    monkeypatch.setattr('jobagent.infra.cloud_client.me', lambda: {'account': {}})
    result = cli._doctor_env()
    assert result['cloud_access']['reason'] == 'credit_status_unavailable'
    assert result['cloud_access']['paid_pass_required'] is None
    assert result['next_suggested'] == 'jobagent doctor env'
    assert result['onboarding']['stage'] == 'credit_status_pending'
    assert '#pricing' not in json.dumps(result)


def test_doctor_rejected_key_uses_status_even_without_error_code(monkeypatch, doctor_context):
    from jobagent.infra.cloud_client import CloudError
    def rejected():
        raise CloudError('unauthorized', status=401)
    monkeypatch.setattr('jobagent.infra.cloud_client.me', rejected)
    result = cli._doctor_env()
    assert result['onboarding']['stage'] == 'api_key_required'
    assert result['next_suggested'] == onboarding.INIT_COMMAND


def test_existing_profile_does_not_restart_onboarding(doctor_context):
    (doctor_context / 'profile.json').write_text('{}')
    result = cli._doctor_env()
    assert 'onboarding' not in result
    assert not result.get('requires_user_action')
    assert result['next_suggested'] == 'jobagent round start'


@pytest.mark.parametrize('name', ['docs/agent-onboarding.md', 'skills/claude-code/SKILL.md',
                                  'skills/openclaw-job-agent/SKILL.md', 'skills/codex-job-agent/SKILL.md',
                                  'src/jobagent/data/codex_skill/SKILL.md'])
def test_distributed_instructions_require_install_handoff_and_return_path(name):
    text = (ROOT / name).read_text()
    assert 'onboarding_handoff' in text
    assert 'jobagent onboarding' in text
    assert 'this same Agent conversation' in text
    assert 'jobagent doctor env' in text
    assert 'installation' in text.lower()


def isolated_env(tmp_path):
    env = dict(os.environ)
    env.pop('JOBAGENT_API_KEY', None)
    env.update(HOME=str(tmp_path / 'home'), USERPROFILE=str(tmp_path / 'home'),
               CODEX_HOME=str(tmp_path / 'codex'), PYTHONPATH=str(ROOT / 'src'),
               PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    Path(env['HOME']).mkdir(exist_ok=True)
    # Installer runs real handoff code; replace only its external network boundary.
    hooks = tmp_path / 'python-hooks'
    hooks.mkdir(exist_ok=True)
    (hooks / 'sitecustomize.py').write_text(
        "from jobagent.infra import tls_support\n"
        "tls_support.transport_preflight = lambda: {'ok': True, 'account_verified': False, 'checks': []}\n")
    env['PYTHONPATH'] = str(hooks) + os.pathsep + env['PYTHONPATH']
    return env


@pytest.mark.skipif(os.name == 'nt', reason='POSIX installer integration; Windows tail tested separately')
@pytest.mark.parametrize('existing,key_present', [(False, False), (True, False), (True, True)])
def test_real_bash_installer_ends_with_actionable_handoff_without_business_mutation(tmp_path, existing, key_present):
    # Stub only git and dependency installation. Run the actual installer,
    # actual packaged Skill installer and actual onboarding module offline.
    env = isolated_env(tmp_path)
    install = tmp_path / 'client'
    binaries = tmp_path / 'tools'
    binaries.mkdir()
    if existing:
        (install / '.git').mkdir(parents=True)
    state = Path(env['HOME']) / '.jobagent'
    state.mkdir()
    (state / 'sentinel').write_text('preserve existing state')
    if key_present:
        (state / 'credentials').write_text('secret-never-print')
    before = snapshot(state)
    git = binaries / 'git'
    git.write_text('#!/bin/sh\nif [ "$1" = clone ]; then mkdir -p "$3/.git"; fi\n')
    git.chmod(0o755)
    fake_python = binaries / 'python3'
    fake_python.write_text('#!' + sys.executable + '\n' + '''import os, pathlib, sys
if sys.argv[1:3] == ['-m', 'venv']:
    bindir = pathlib.Path(sys.argv[3]) / 'bin'
    bindir.mkdir(parents=True)
    (bindir / 'python').symlink_to(sys.executable)
    pip = bindir / 'pip'
    pip.write_text('#!/bin/sh\\nexit 0\\n')
    pip.chmod(0o755)
else:
    print('3.11')
''')
    fake_python.chmod(0o755)
    env.update(PATH=str(binaries) + os.pathsep + env['PATH'],
               JOBAGENT_INSTALL_DIR=str(install), JOBAGENT_BIN_DIR=str(tmp_path / 'bin'))
    result = subprocess.run(['bash', str(ROOT / 'scripts/install.sh')], env=env, text=True,
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    marker = 'Agent handoff (follow before ending setup):\n'
    handoff = json.loads(result.stdout.split(marker)[1])
    assert handoff['api_key_configured'] is key_present
    assert handoff['event'] == 'onboarding_handoff'
    assert handoff['next_suggested'] == ('jobagent doctor env' if key_present else onboarding.INIT_COMMAND)
    assert 'secret-never-print' not in result.stdout + result.stderr
    assert snapshot(state) == before
    if not key_present:
        assert '回到当前这段 Agent 对话' in result.stdout.split(marker)[0]


@pytest.mark.skipif(not shutil.which('pwsh'), reason='PowerShell runtime is exercised by Windows CI')
def test_powershell_installer_tail_executes_real_handoff(tmp_path):
    env = isolated_env(tmp_path)
    script = (ROOT / 'scripts/install.ps1').read_text()
    tail = script[script.index('# Always surface the current setup handoff'):]
    quoted_python = sys.executable.replace("'", "''")
    harness = tmp_path / 'handoff.ps1'
    harness.write_text("function Info($msg) { Write-Host $msg }\nfunction Die($msg) { throw $msg }\n"
                       + "$venvPy = '" + quoted_python + "'\n" + tail, encoding='utf-8-sig')
    result = subprocess.run(['pwsh', '-NoProfile', '-File', str(harness)], env=env,
                            text=True, encoding='utf-8', capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    handoff = json.loads(result.stdout.split('Agent handoff (follow before ending setup):\n')[1])
    assert handoff['onboarding']['stage'] == 'api_key_required'
    assert '回到当前这段 Agent 对话' in result.stdout
