from __future__ import annotations

import json

from jobagent.infra import support


def test_first_delivery_star_prompt_records_and_prints_once(monkeypatch, tmp_path, capsys):
    state_path = tmp_path / "support_state.json"
    monkeypatch.setattr(support, "support_state_path", lambda: state_path)

    first = support.print_first_delivery_star_prompt_once(
        platform="liepin",
        command="jobagent liepin apply send",
        delivered=1,
    )
    second = support.print_first_delivery_star_prompt_once(
        platform="zhilian",
        command="jobagent zhilian apply send",
        delivered=1,
    )

    captured = capsys.readouterr()
    assert first is True
    assert second is False
    assert "首次真实投递" in captured.err
    assert captured.err.count(support.PUBLIC_REPO_URL) == 1
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["first_delivery_platform"] == "liepin"
    assert state["first_delivery_command"] == "jobagent liepin apply send"
    assert state["first_delivery_delivered"] == 1


def test_first_delivery_star_prompt_ignores_dry_run_and_zero_delivery(monkeypatch, tmp_path, capsys):
    state_path = tmp_path / "support_state.json"
    monkeypatch.setattr(support, "support_state_path", lambda: state_path)

    dry_run = support.print_first_delivery_star_prompt_once(
        platform="liepin",
        command="jobagent liepin apply send",
        delivered=1,
        dry_run=True,
    )
    zero_delivery = support.print_first_delivery_star_prompt_once(
        platform="boss",
        command="jobagent boss greet send",
        delivered=0,
    )

    captured = capsys.readouterr()
    assert dry_run is False
    assert zero_delivery is False
    assert captured.err == ""
    assert not state_path.exists()


def test_support_star_payload_is_voluntary():
    payload = support.support_star_payload()

    assert payload["ok"] is True
    assert payload["url"] == support.PUBLIC_REPO_URL
    assert "optional" in payload["note"].lower()
