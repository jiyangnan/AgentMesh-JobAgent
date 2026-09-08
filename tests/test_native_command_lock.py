from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

import pytest

from jobagent.infra import state
from jobagent.infra.browser_work import BrowserWorkError
from jobagent.infra.native_command_lock import command_lock


_CHILD = """
import json
from pathlib import Path
import sys
from jobagent.infra import state
from jobagent.infra.browser_work import BrowserWorkError
from jobagent.infra.native_command_lock import command_lock

state.STATE_DIR = Path(sys.argv[1])
try:
    with command_lock():
        print(json.dumps({"status": "acquired"}), flush=True)
        if sys.argv[2] == "hold":
            sys.stdin.readline()
except BrowserWorkError as exc:
    print(json.dumps(exc.payload), flush=True)
"""


@pytest.fixture
def isolated_lock(monkeypatch, tmp_path):
    home = tmp_path / "home"
    app = home / ".jobagent"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    for name, path in {
        "APP_DIR": app,
        "STATE_DIR": app / "state",
        "LOG_DIR": app / "logs",
        "ROUNDS_DIR": app / "state/rounds",
        "LOCKS_DIR": app / "state/locks",
    }.items():
        monkeypatch.setattr(state, name, path)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return app / "state", env


def _attempt(directory, env):
    result = subprocess.run(
        [sys.executable, "-c", _CHILD, str(directory), "attempt"],
        env=env, capture_output=True, text=True, timeout=10, check=True,
    )
    return json.loads(result.stdout)


def test_lock_excludes_another_process_and_releases_without_unlinking(isolated_lock):
    directory, env = isolated_lock
    path = directory / "locks/native-command.lock"
    with command_lock():
        inode = path.stat().st_ino
        result = _attempt(directory, env)
        assert result["error"] == "native_command_busy"
        assert result["request_preserved"] is True
        assert path.exists()
    assert _attempt(directory, env) == {"status": "acquired"}
    assert path.exists()
    if os.name != "nt":
        assert path.stat().st_ino == inode
        assert path.stat().st_mode & 0o777 == 0o600


def test_exception_releases_lock_for_another_process(isolated_lock):
    directory, env = isolated_lock
    with pytest.raises(RuntimeError, match="simulated command failure"):
        with command_lock():
            raise RuntimeError("simulated command failure")
    assert _attempt(directory, env) == {"status": "acquired"}


def test_os_releases_lock_when_holder_process_terminates(isolated_lock):
    directory, env = isolated_lock
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(directory), "hold"],
        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    ready = queue.Queue()
    threading.Thread(target=lambda: ready.put(child.stdout.readline()), daemon=True).start()
    try:
        assert json.loads(ready.get(timeout=10)) == {"status": "acquired"}
        with pytest.raises(BrowserWorkError) as error:
            with command_lock():
                pytest.fail("a live child already holds this lock")
        assert error.value.payload["error"] == "native_command_busy"
        child.terminate()
        child.communicate(timeout=10)
        with command_lock():
            assert (directory / "locks/native-command.lock").exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


def test_symlink_lock_is_rejected_and_preserved(isolated_lock, tmp_path):
    directory, _env = isolated_lock
    path = directory / "locks/native-command.lock"
    path.parent.mkdir(parents=True)
    target = tmp_path / "unrelated-file"
    target.write_bytes(b"preserve me")
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("this host does not permit creating symlinks")
    with pytest.raises(BrowserWorkError) as error:
        with command_lock():
            pytest.fail("symlink locks must not be followed")
    assert error.value.payload["error"] == "native_command_lock_invalid"
    assert path.is_symlink()
    assert target.read_bytes() == b"preserve me"
