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


@pytest.fixture(autouse=True)
def existing_account_content_language(monkeypatch):
    """Existing business fixtures start after language onboarding.

    Dedicated content-language tests override this with first-use / account-change
    responses. Never make an extra real network request from unrelated fixtures.
    """
    from jobagent.application import targeting
    from jobagent.infra import cloud_client

    def confirmed(*_):
        language = targeting.request().get('interaction_language', 'en')
        return {'content_locale': 'zh' if language == 'zh-CN' else language, 'confirmed': True}
    monkeypatch.setattr(cloud_client, 'content_language', confirmed)
