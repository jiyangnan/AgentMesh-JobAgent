"""Generated LinkedIn continuations must be executable, serialized commands."""
from contextlib import contextmanager

import pytest

from jobagent import cli
from jobagent.application import native_work
from jobagent.infra import state


@pytest.mark.parametrize("argv", [
    ["linkedin", "login", "--check"], ["linkedin", "discover"],
    ["linkedin", "apply", "review"], ["linkedin", "apply", "send"],
    ["linkedin", "audit"], ["round", "audit", "--platform", "linkedin"],
    ["round", "skip", "--platform", "linkedin", "--confirm-skip"],
    ["browser", "diagnose", "--platform", "linkedin"],
    ["platforms", "health", "--platform", "linkedin"],
])
def test_linkedin_continuation_catalog_accepts_commands(argv):
    assert cli.build_parser().parse_args(argv).command == argv[0]


def test_linkedin_dispatch_uses_same_command_lock(monkeypatch):
    held = []
    @contextmanager
    def locked():
        held.append(True)
        try:
            yield
        finally:
            held.pop()
    monkeypatch.setattr("jobagent.infra.native_command_lock.command_lock", locked)
    def dispatch(args):
        assert held == [True] and args.command == "linkedin"
        return {"ok": True}
    monkeypatch.setattr(cli, "_dispatch_unlocked", dispatch)
    assert cli._dispatch(cli.build_parser().parse_args(["linkedin", "audit"])) == {"ok": True}
    assert held == []


def test_linkedin_round_audit_never_reads_domestic_legacy_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    state.save_json(state.current_round_path(), {"platforms": {"linkedin": {"native_delivery": True}}})
    monkeypatch.setattr("jobagent.application.delivery.audit_round", lambda **_: pytest.fail("no LinkedIn legacy log"))
    monkeypatch.setattr(native_work.rounds, "round_status", lambda: {"next_suggested": "jobagent work status"})
    def audited(platform, *, complete):
        assert platform == "linkedin" and complete is False
        return {"summary": {"resume_submitted": 2}, "evidence_source": "packaged_browser_observation"}
    monkeypatch.setattr(native_work, "audit", audited)
    result = cli._native_dispatch(cli.build_parser().parse_args(["round", "audit", "--platform", "linkedin"]))
    assert result["platforms"] == {}
    assert result["native_audit"] == {"linkedin": {"resume_submitted": 2}}
