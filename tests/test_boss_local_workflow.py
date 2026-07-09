from __future__ import annotations

import json
from pathlib import Path

import pytest

import jobagent.cli as cli
from jobagent.cli import _cmd_greet_preview, _cmd_greet_send, _cmd_jobs_rank, build_parser


def parse_args(*args: str):
    return build_parser().parse_args(list(args))


def test_boss_rank_local_bypasses_api_key_and_cloud(monkeypatch, tmp_path):
    input_path = tmp_path / "boss_jobs.json"
    output_path = tmp_path / "boss_ranked.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "AI产品经理",
                    "salary": "40-60k",
                    "company": "Boss Example",
                    "city": "深圳",
                    "url": "https://www.zhipin.com/job_detail/boss-1.html",
                    "platform": "zhipin",
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli,
        "_require_api_key_or_exit",
        lambda command: pytest.fail("local Boss rank must not require API key"),
    )
    monkeypatch.setattr(
        cli,
        "_cmd_jobs_rank_cloud",
        lambda *args, **kwargs: pytest.fail("local Boss rank must not call cloud rank"),
    )
    args = parse_args(
        "boss", "rank",
        "--local",
        "--config", str(tmp_path / "missing.yaml"),
        "--input", str(input_path),
        "--output", str(output_path),
    )

    _cmd_jobs_rank(args)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["via"] == "local"
    assert payload["platform"] == "zhipin"
    assert payload["jobs"][0]["platform"] == "zhipin"
    assert "jobagent boss greet preview --local" in payload["next_suggested"]


def test_boss_greet_preview_local_injects_greeting(monkeypatch, tmp_path):
    input_path = tmp_path / "boss_ranked.json"
    output_path = tmp_path / "boss_ready.json"
    input_path.write_text(
        json.dumps({
            "via": "local",
            "platform": "zhipin",
            "jobs": [
                {
                    "name": "AI产品经理",
                    "salary": "40-60k",
                    "company": "Boss Example",
                    "city": "深圳",
                    "boss": "李女士",
                    "url": "https://www.zhipin.com/job_detail/boss-1.html",
                    "platform": "zhipin",
                    "score": 92,
                    "reasons": ["岗位方向与您的 AI 产品经验高度相关"],
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli,
        "_require_api_key_or_exit",
        lambda command: pytest.fail("local Boss greet preview must not require API key"),
    )
    monkeypatch.setattr(
        cli,
        "_cmd_greet_preview_cloud",
        lambda *args, **kwargs: pytest.fail("local Boss greet preview must not call cloud greet"),
    )
    args = parse_args(
        "boss", "greet", "preview",
        "--local",
        "--config", str(tmp_path / "missing.yaml"),
        "--input", str(input_path),
        "--output", str(output_path),
    )

    _cmd_greet_preview(args)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    job = payload["jobs"][0]
    assert payload["greeting_via"] == "local"
    assert payload["platform"] == "zhipin"
    assert job["greeting_source"] == "local"
    assert "李女士您好" in job["greeting"]
    assert "岗位方向与您的 AI 产品经验高度相关" in job["greeting"]


def test_greet_send_uses_local_greeting_without_api_key(monkeypatch, tmp_path):
    input_path = tmp_path / "boss_ready.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "AI产品经理",
                    "salary": "40-60k",
                    "company": "Boss Example",
                    "city": "深圳",
                    "boss": "李女士",
                    "url": "https://www.zhipin.com/job_detail/boss-1.html",
                    "platform": "zhipin",
                    "score": 92,
                    "match_level": "high",
                    "reasons": [],
                    "risk_flags": [],
                    "greeting": "李女士您好，想进一步沟通这个岗位。",
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class FakeGreeterEngine:
        def __init__(self, config):
            self.config = config

        def send_batch(self, ranked, limit=10, message_overrides=None):
            captured["limit"] = limit
            captured["overrides"] = message_overrides
            captured["ranked_count"] = len(ranked)
            return []

    monkeypatch.setattr(
        cli,
        "_require_api_key_or_exit",
        lambda command: pytest.fail("Boss greet send must not require API key when input has local greeting"),
    )
    monkeypatch.setattr("jobagent.domain.greeter.GreeterEngine", FakeGreeterEngine)
    monkeypatch.chdir(tmp_path)
    args = parse_args(
        "boss", "greet", "send",
        "--config", str(tmp_path / "missing.yaml"),
        "--input", str(input_path),
        "--limit", "1",
    )

    _cmd_greet_send(args)

    assert captured["limit"] == 1
    assert captured["ranked_count"] == 1
    assert captured["overrides"] == {
        "https://www.zhipin.com/job_detail/boss-1.html": "李女士您好，想进一步沟通这个岗位。"
    }
