import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jobagent.drivers.boss import chrome_manager


@pytest.fixture
def launcher(monkeypatch, tmp_path):
    clock = SimpleNamespace(now=0.0)

    def sleep(seconds):
        clock.now += seconds

    profile = tmp_path / "chrome-profile"
    profile.mkdir()
    process = Mock(spec=chrome_manager.subprocess.Popen)
    process.poll.return_value = None
    spawn = Mock(return_value=process)
    monkeypatch.setattr(chrome_manager, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep))
    monkeypatch.setattr(chrome_manager, "find_chrome", lambda: "/fixture/chrome")
    monkeypatch.setattr(chrome_manager, "_is_port_open", lambda _: False)
    monkeypatch.setattr(chrome_manager.subprocess, "Popen", spawn)
    return SimpleNamespace(clock=clock, profile=profile, process=process, spawn=spawn)


@pytest.mark.parametrize("exit_code", [None, 0, 33, 38])
def test_cdp_startup_accepts_page_ready_after_old_deadline(launcher, monkeypatch, exit_code):
    launcher.process.poll.return_value = exit_code
    ws_url = "ws://127.0.0.1:19222/devtools/page/fixture"

    def page_target(_port, *, timeout):
        return ws_url if launcher.clock.now >= 17 else None

    monkeypatch.setattr(chrome_manager, "_get_first_page_ws_url", page_target)
    manager = chrome_manager.ChromeInstanceManager(user_data_dir=launcher.profile)

    assert manager.ensure_running() == ws_url
    assert launcher.clock.now == 17
    launcher.spawn.assert_called_once()
    assert f"--user-data-dir={launcher.profile}" in launcher.spawn.call_args.args[0]
    launcher.process.terminate.assert_not_called()


@pytest.mark.parametrize(("exit_code", "error", "waited"), [
    (21, "chrome_profile_in_use", 3),
    (24, "chrome_profile_in_use", 3),
    (23, "chrome_exited_before_cdp", 0),
])
def test_cdp_startup_reports_early_exit_and_preserves_profile(launcher, monkeypatch, exit_code, error, waited):
    launcher.process.poll.return_value = exit_code
    monkeypatch.setattr(chrome_manager, "_get_first_page_ws_url", lambda *_args, **_kw: None)
    marker = launcher.profile / "Preferences"
    marker.write_text("fixture profile", encoding="utf-8")
    manager = chrome_manager.ChromeInstanceManager(user_data_dir=launcher.profile)

    with pytest.raises(chrome_manager.ChromeStartupError) as raised:
        manager.ensure_running()

    assert raised.value.payload["error"] == error
    assert raised.value.payload["chrome_exit_code"] == exit_code
    assert launcher.clock.now == waited
    launcher.process.terminate.assert_not_called()
    assert marker.read_text(encoding="utf-8") == "fixture profile"


@pytest.mark.parametrize("exit_code", [21, 24])
def test_cdp_startup_can_connect_during_parallel_profile_start(launcher, monkeypatch, exit_code):
    launcher.process.poll.return_value = exit_code
    ws_url = "ws://127.0.0.1:19222/devtools/page/fixture"

    def page_target(_port, *, timeout):
        return ws_url if launcher.clock.now >= 1.5 else None

    monkeypatch.setattr(chrome_manager, "_get_first_page_ws_url", page_target)
    monkeypatch.setattr(chrome_manager, "_is_port_open", lambda _: launcher.clock.now >= 1.5)
    manager = chrome_manager.ChromeInstanceManager(user_data_dir=launcher.profile)

    assert manager.ensure_running() == ws_url
    assert manager._process is None
    assert manager.is_running()
    launcher.process.terminate.assert_not_called()


def test_cdp_startup_cleans_up_its_owned_child_at_deadline(launcher, monkeypatch):
    monkeypatch.setattr(chrome_manager, "_get_first_page_ws_url", lambda *_args, **_kw: None)
    manager = chrome_manager.ChromeInstanceManager(user_data_dir=launcher.profile)

    with pytest.raises(chrome_manager.ChromeStartupError) as raised:
        manager.ensure_running()

    assert raised.value.payload["error"] == "chrome_cdp_startup_timeout"
    assert raised.value.payload["chrome_exit_code"] is None
    assert launcher.clock.now == 30
    launcher.process.terminate.assert_called_once()


def test_cdp_startup_requires_a_debuggable_page(monkeypatch):
    ws_url = "ws://127.0.0.1:19222/devtools/page/fixture"
    target = {"type": "page", "webSocketDebuggerUrl": ws_url}
    connection = Mock()
    response = connection.getresponse.return_value
    response.status = 200
    response.read.side_effect = [
        b'[]',
        json.dumps([{"type": "page"}, target]).encode("utf-8"),
    ]
    monkeypatch.setattr(chrome_manager.http.client, "HTTPConnection", Mock(return_value=connection))

    assert chrome_manager._get_first_page_ws_url(19222) is None
    assert chrome_manager._get_first_page_ws_url(19222) == ws_url
    assert connection.close.call_count == 2


def test_boss_login_prints_typed_chrome_failure(launcher, monkeypatch, capsys, tmp_path):
    from jobagent import cli
    from jobagent.infra import rounds, state

    state_dir = tmp_path / "state"
    monkeypatch.setattr(state, "STATE_DIR", state_dir)
    monkeypatch.setattr(state, "LOG_DIR", state_dir / "logs")
    monkeypatch.setattr(state, "ROUNDS_DIR", state_dir / "rounds")
    rounds.start_new_round()
    launcher.process.poll.return_value = 21
    monkeypatch.setattr(chrome_manager, "_get_first_page_ws_url", lambda *_args, **_kw: None)
    monkeypatch.setattr(chrome_manager.ChromeInstanceManager, "_default_user_data_dir", lambda _: launcher.profile)
    monkeypatch.setattr(cli, "_maybe_update", lambda _: None)
    monkeypatch.setattr(cli, "_prepare_client_upgrade", lambda _: None)
    monkeypatch.setattr(cli, "_verify_state_owner_for_command", lambda _: None)
    monkeypatch.setattr(cli, "_schedule_analytics_flush_safely", lambda: None)
    monkeypatch.setattr("jobagent.infra.browser_work.has_inflight", lambda: True)
    monkeypatch.setattr("sys.argv", ["jobagent", "boss", "login", "--check"])

    with pytest.raises(SystemExit) as raised:
        cli.main()

    payload = json.loads(capsys.readouterr().err)
    assert raised.value.code == 2
    assert payload["error"] == "chrome_profile_in_use"
    assert payload["chrome_exit_code"] == 21
    assert payload["cdp_port"] == 19222
    assert str(launcher.profile) not in payload["message"]
