from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


_ORIGINAL_HOME = os.environ.get("HOME")
_ORIGINAL_USERPROFILE = os.environ.get("USERPROFILE")
_TEST_HOME = Path(tempfile.mkdtemp(prefix="jobagent-pytest-home-"))
os.environ["HOME"] = str(_TEST_HOME)
os.environ["USERPROFILE"] = str(_TEST_HOME)


@pytest.fixture(autouse=True)
def isolate_platform_browser_lock(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    from jobagent.infra import platform_lock

    monkeypatch.setattr(
        platform_lock,
        "browser_session_lock_path",
        lambda: tmp_path / "browser-session.lock",
    )
    monkeypatch.setattr(
        platform_lock,
        "ensure_current_round",
        lambda: {"round_id": "pytest-round", "status": "active"},
    )
    monkeypatch.setattr(platform_lock, "set_platform_status", lambda *args, **kwargs: None)


def pytest_unconfigure() -> None:
    if _ORIGINAL_HOME is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = _ORIGINAL_HOME
    if _ORIGINAL_USERPROFILE is None:
        os.environ.pop("USERPROFILE", None)
    else:
        os.environ["USERPROFILE"] = _ORIGINAL_USERPROFILE
    shutil.rmtree(_TEST_HOME, ignore_errors=True)
