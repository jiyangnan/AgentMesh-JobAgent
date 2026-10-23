"""Browser spacing survives process boundaries without touching a browser."""
import pytest
from jobagent.application import playwright_work as pw
from jobagent.infra import state
from jobagent.infra.browser_work import BrowserWorkError


def test_next_invocation_honors_profile_budget(tmp_path, monkeypatch):
    clock = [100.0]
    sleeps = []
    monkeypatch.setattr(pw.time, "time", lambda: clock[0])
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr(pw.time, "sleep", sleep)
    monkeypatch.setattr(pw.secrets, "choice", lambda values: 3)
    session = {"profile_directory": str(tmp_path)}
    pw.pace(session)
    pw.pace(dict(session))
    assert sleeps == [3]
    assert state.load_json(tmp_path / "jobagent-playwright-pacing.json")["next_start_not_before"] == 106


def test_clock_jump_never_sleeps_or_issues_an_action(tmp_path, monkeypatch):
    state.save_json(tmp_path / "jobagent-playwright-pacing.json", {"next_start_not_before": 1000})
    monkeypatch.setattr(pw.time, "time", lambda: 100)
    monkeypatch.setattr(pw.time, "sleep", lambda seconds: pytest.fail("Unbounded sleep"))
    with pytest.raises(BrowserWorkError, match="clock moved backwards"):
        pw.pace({"profile_directory": str(tmp_path)})
