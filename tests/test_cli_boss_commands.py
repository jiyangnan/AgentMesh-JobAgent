from __future__ import annotations

import pytest

from jobagent.cli import build_parser


def parse_args(*args: str):
    return build_parser().parse_args(list(args))


def test_boss_collect_command_uses_collect_shape():
    args = parse_args("boss", "collect", "--city", "深圳", "--query", "AI产品经理")

    assert args.command == "boss"
    assert args.boss_command == "collect"
    assert args.city == "深圳"
    assert args.query == "AI产品经理"
    assert args.pages == 1
    assert args.page_delay == 5.0


def test_boss_rank_command_uses_rank_shape():
    args = parse_args("boss", "rank", "--input", "raw.json", "--top", "5")

    assert args.command == "boss"
    assert args.boss_command == "rank"
    assert args.input == "raw.json"
    assert args.top == 5


def test_boss_greet_preview_command_uses_greet_shape():
    args = parse_args("boss", "greet", "preview", "--input", "ranked.json", "--limit", "3")

    assert args.command == "boss"
    assert args.boss_command == "greet"
    assert args.boss_greet_command == "preview"
    assert args.input == "ranked.json"
    assert args.limit == 3


def test_boss_greet_send_command_uses_greet_shape():
    args = parse_args("boss", "greet", "send", "--input", "ready.json", "--limit", "2")

    assert args.command == "boss"
    assert args.boss_command == "greet"
    assert args.boss_greet_command == "send"
    assert args.input == "ready.json"
    assert args.limit == 2
    assert args.config == "config/config.yaml"


def test_boss_greet_audit_command_uses_greet_shape():
    args = parse_args("boss", "greet", "audit", "--recent", "7")

    assert args.command == "boss"
    assert args.boss_command == "greet"
    assert args.boss_greet_command == "audit"
    assert args.recent == 7


def test_legacy_jobs_collect_is_removed():
    with pytest.raises(SystemExit):
        parse_args("jobs", "collect", "--city", "深圳", "--query", "AI产品经理")


def test_legacy_greet_preview_is_removed():
    with pytest.raises(SystemExit):
        parse_args("greet", "preview", "--input", "ranked.json")
