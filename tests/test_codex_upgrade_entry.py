from __future__ import annotations

import pytest


@pytest.fixture
def isolated_entry(monkeypatch, tmp_path):
    home = tmp_path / "home"
    codex = tmp_path / "codex"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setenv("JOBAGENT_SKIP_UPDATE", "1")
    from jobagent import cli
    from jobagent.infra import browser_work, codex_skill, diagnostics, state

    app = home / ".jobagent"
    for name, path in {
        "APP_DIR": app, "STATE_DIR": app / "state", "LOG_DIR": app / "logs",
        "ROUNDS_DIR": app / "state/rounds", "LOCKS_DIR": app / "state/locks",
    }.items():
        monkeypatch.setattr(state, name, path)
    flags = {"changed": True, "resumed": True, "deferred": False}

    def maybe_update(args):
        args._client_update_resume_reported = flags["resumed"]
        args._native_update_deferred = flags["deferred"]

    def prepare(args):
        args._client_upgrade_report = {"version_changed": flags["changed"]}

    monkeypatch.setattr("sys.argv", ["jobagent", "work", "status"])
    monkeypatch.setattr(cli, "_maybe_update", maybe_update)
    monkeypatch.setattr(cli, "_prepare_client_upgrade", prepare)
    monkeypatch.setattr(cli, "_verify_state_owner_for_command", lambda _args: None)
    monkeypatch.setattr(browser_work, "has_inflight", lambda: flags["deferred"])
    monkeypatch.setattr(cli, "_attach_pending_product_announcements", lambda result, **_kw: result)
    monkeypatch.setattr(diagnostics, "write_exception_log", lambda *_a, **_kw: tmp_path / "unused-diagnostic.log")
    installation = codex_skill.install_skill
    statuses = []

    def install():
        result = installation()
        statuses.append(result.get("status") or result.get("error"))
        return result

    monkeypatch.setattr(codex_skill, "install_skill", install)
    return cli, flags, statuses, codex / "skills/codex-job-agent", app


def test_first_business_dispatch_failure_does_not_lose_skill_refresh(isolated_entry, monkeypatch, capsys):
    cli, flags, statuses, target, app = isolated_entry

    def failed_dispatch(_args):
        raise RuntimeError("simulated business dispatch failure")

    monkeypatch.setattr(cli, "_dispatch", failed_dispatch)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert statuses == ["installed"]
    assert (target / "SKILL.md").is_file()
    assert (target / "agents/openai.yaml").is_file()
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in target.rglob("*") if path.is_file()}
    capsys.readouterr()

    # The update event was consumed and the migration marker now says current.
    # A later successful business command must still run the idempotent refresh.
    flags.update(changed=False, resumed=False)
    monkeypatch.setattr(cli, "_dispatch", lambda _args: {"ok": True})
    cli.main()
    assert statuses == ["installed", "current"]
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before} == before
    assert [p.relative_to(app).as_posix() for p in app.rglob("*") if p.is_file()] == ["state/locks/native-command.lock"]


def test_native_inflight_never_hot_installs_skill(isolated_entry, monkeypatch):
    cli, flags, statuses, target, app = isolated_entry
    flags["deferred"] = True
    monkeypatch.setattr(cli, "_dispatch", lambda _args: {"ok": True})
    cli.main()
    assert statuses == []
    assert not target.exists()
    assert [p.relative_to(app).as_posix() for p in app.rglob("*") if p.is_file()] == ["state/locks/native-command.lock"]


def test_custom_skill_conflict_does_not_overwrite_or_rollback_business_result(isolated_entry, monkeypatch, capsys):
    cli, _flags, statuses, target, app = isolated_entry
    target.mkdir(parents=True)
    custom = target / "SKILL.md"
    custom.write_text("User-owned skill", encoding="utf-8")
    monkeypatch.setattr(cli, "_dispatch", lambda _args: {"ok": True})
    cli.main()
    assert statuses == ["codex_skill_unmanaged_target"]
    assert custom.read_text() == "User-owned skill"
    assert "codex_skill_unmanaged_target" in capsys.readouterr().out
    assert [p.relative_to(app).as_posix() for p in app.rglob("*") if p.is_file()] == ["state/locks/native-command.lock"]


def test_update_migration_account_and_dispatch_share_one_lock(isolated_entry, monkeypatch):
    from jobagent.infra.native_command_lock import command_lock
    from jobagent.infra.browser_work import BrowserWorkError
    cli, _flags, _statuses, _target, _app = isolated_entry
    stages = []
    def check(stage, result=None):
        def call(args):
            assert args._native_command_locked is True
            with pytest.raises(BrowserWorkError) as exc:
                with command_lock():
                    pytest.fail("upgrade and begin could interleave")
            assert exc.value.payload["error"] == "native_command_busy"
            stages.append(stage)
            return result
        return call
    monkeypatch.setattr(cli, "_maybe_update", check("update"))
    monkeypatch.setattr(cli, "_prepare_client_upgrade", check("migration"))
    monkeypatch.setattr(cli, "_verify_state_owner_for_command", check("account"))
    monkeypatch.setattr(cli, "_dispatch", check("dispatch", {"ok": True}))
    cli.main()
    assert stages == ["update", "migration", "account", "dispatch"]
    with command_lock():
        pass
