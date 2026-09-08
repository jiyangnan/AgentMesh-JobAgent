from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobagent.infra import state


@pytest.fixture
def isolated_state(monkeypatch, tmp_path):
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
    return app / "state/nested/checkpoint.json"


def test_save_json_replaces_only_after_complete_same_directory_write(isolated_state, monkeypatch):
    path = isolated_state
    path.parent.mkdir(parents=True)
    old = b'{"old": true}\n'
    path.write_bytes(old)
    payload = {"city": "郑州", "items": [1, 2], "complete": True}
    replace = state.os.replace
    calls = []

    def inspect_replace(source, destination):
        temporary = Path(source)
        assert temporary.parent == path.parent
        assert temporary.name.startswith(f".{path.name}.")
        assert temporary.name.endswith(".tmp")
        assert Path(destination) == path
        assert path.read_bytes() == old
        assert json.loads(temporary.read_text(encoding="utf-8")) == payload
        calls.append(temporary)
        replace(source, destination)

    monkeypatch.setattr(state.os, "replace", inspect_replace)
    state.save_json(path, payload)
    assert len(calls) == 1
    assert state.load_json(path) == payload
    assert "郑州" in path.read_text(encoding="utf-8")
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("existing", [True, False])
@pytest.mark.parametrize("failure_point", ["replace", "fsync"])
def test_failed_atomic_write_preserves_old_data_and_removes_temporary(
    isolated_state, monkeypatch, existing, failure_point,
):
    path = isolated_state
    path.parent.mkdir(parents=True)
    old = b'{"request_id": "preserved", "pending": true}\n'
    if existing:
        path.write_bytes(old)
    observed_temporary = []

    def fail(*_args):
        temporary = list(path.parent.glob(f".{path.name}.*.tmp"))
        assert len(temporary) == 1
        observed_temporary.extend(temporary)
        raise OSError(f"simulated {failure_point} failure")

    monkeypatch.setattr(state.os, failure_point, fail)
    with pytest.raises(OSError, match=f"simulated {failure_point} failure"):
        state.save_json(path, {"request_id": "new", "pending": False})
    assert len(observed_temporary) == 1
    assert all(not temporary.exists() for temporary in observed_temporary)
    if existing:
        assert path.read_bytes() == old
        assert list(path.parent.iterdir()) == [path]
    else:
        assert not path.exists()
        assert list(path.parent.iterdir()) == []


def test_invalid_json_does_not_replace_old_data_or_leave_temporary(isolated_state):
    path = isolated_state
    path.parent.mkdir(parents=True)
    old = b'{"pending": true}\n'
    path.write_bytes(old)
    with pytest.raises(ValueError):
        state.save_json(path, {"not_json": float("nan")})
    assert path.read_bytes() == old
    assert list(path.parent.iterdir()) == [path]
