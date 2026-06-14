from __future__ import annotations

import pytest

from jobagent.cli import build_parser, _require_platform_enabled_or_exit


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
    assert args.config == "config/config.yaml"


def test_doctor_liepin_uses_readiness_shape():
    args = parse_args(
        "doctor",
        "liepin",
        "--query",
        "AI产品",
        "--city",
        "深圳",
        "--wait-seconds",
        "2",
        "--limit",
        "3",
        "--with-cloud",
    )

    assert args.command == "doctor"
    assert args.doctor_target == "liepin"
    assert args.query == "AI产品"
    assert args.city == "深圳"
    assert args.wait_seconds == 2
    assert args.limit == 3
    assert args.with_cloud is True


def test_doctor_liepin_defaults_use_broad_readiness_query():
    args = parse_args("doctor", "liepin")

    assert args.doctor_target == "liepin"
    assert args.query == "产品"
    assert args.city == ""
    assert args.with_cloud is False


def test_legacy_jobs_collect_is_removed():
    with pytest.raises(SystemExit):
        parse_args("jobs", "collect", "--city", "深圳", "--query", "AI产品经理")


def test_legacy_greet_preview_is_removed():
    with pytest.raises(SystemExit):
        parse_args("greet", "preview", "--input", "ranked.json")


def test_boss_rank_command_uses_rank_shape():
    args = parse_args("boss", "rank", "--input", "raw.json", "--top", "5")

    assert args.command == "boss"
    assert args.boss_command == "rank"
    assert args.input == "raw.json"
    assert args.top == 5
    assert args.local is False
    assert args.config == "config/config.yaml"


def test_boss_rank_command_accepts_local_mode():
    args = parse_args("boss", "rank", "--local", "--input", "raw.json")

    assert args.boss_command == "rank"
    assert args.local is True


def test_boss_greet_preview_command_uses_greet_shape():
    args = parse_args("boss", "greet", "preview", "--input", "ranked.json", "--limit", "3")

    assert args.command == "boss"
    assert args.boss_command == "greet"
    assert args.boss_greet_command == "preview"
    assert args.input == "ranked.json"
    assert args.limit == 3
    assert args.local is False
    assert args.config == "config/config.yaml"


def test_boss_greet_preview_command_accepts_local_mode():
    args = parse_args("boss", "greet", "preview", "--local", "--input", "ranked.json")

    assert args.boss_greet_command == "preview"
    assert args.local is True


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
    assert args.config == "config/config.yaml"


def test_liepin_collect_uses_fixture_probe_shape():
    args = parse_args(
        "liepin",
        "collect",
        "--fixture",
        "liepin.json",
        "--query",
        "AI产品",
        "--limit",
        "7",
        "--pages",
        "3",
        "--page-delay",
        "0.5",
        "--skip-login-check",
    )

    assert args.command == "liepin"
    assert args.liepin_command == "collect"
    assert args.fixture == "liepin.json"
    assert args.query == "AI产品"
    assert args.limit == 7
    assert args.page == 1
    assert args.pages == 3
    assert args.page_delay == 0.5
    assert args.skip_login_check is True
    assert args.config == "config/config.yaml"


def test_liepin_login_uses_read_only_session_shape():
    args = parse_args(
        "liepin",
        "login",
        "--check",
        "--timeout",
        "10",
        "--poll-interval",
        "2",
    )

    assert args.command == "liepin"
    assert args.liepin_command == "login"
    assert args.check is True
    assert args.timeout == 10
    assert args.poll_interval == 2
    assert args.query == "AI产品经理"
    assert args.city == "深圳"
    assert args.config == "config/config.yaml"


def test_liepin_rank_uses_platform_rank_shape():
    args = parse_args("liepin", "rank", "--input", "liepin.raw.json", "--top", "5")

    assert args.command == "liepin"
    assert args.liepin_command == "rank"
    assert args.input == "liepin.raw.json"
    assert args.top == 5
    assert args.local is False
    assert args.config == "config/config.yaml"


def test_liepin_rank_accepts_local_mode():
    args = parse_args("liepin", "rank", "--local", "--input", "liepin.raw.json")

    assert args.liepin_command == "rank"
    assert args.local is True


def test_liepin_greet_preview_uses_read_only_preview_shape():
    args = parse_args("liepin", "greet", "preview", "--input", "liepin.ranked.json", "--limit", "3")

    assert args.command == "liepin"
    assert args.liepin_command == "greet"
    assert args.liepin_greet_command == "preview"
    assert args.input == "liepin.ranked.json"
    assert args.limit == 3
    assert args.local is False
    assert args.config == "config/config.yaml"


def test_liepin_greet_send_uses_safe_handoff_shape():
    args = parse_args(
        "liepin",
        "greet",
        "send",
        "--input",
        "liepin.ready.json",
        "--limit",
        "4",
        "--dry-run",
    )

    assert args.command == "liepin"
    assert args.liepin_command == "greet"
    assert args.liepin_greet_command == "send"
    assert args.input == "liepin.ready.json"
    assert args.limit == 4
    assert args.dry_run is True
    assert args.config == "config/config.yaml"


def test_liepin_apply_open_uses_manual_handoff_shape():
    args = parse_args("liepin", "apply", "open", "--input", "liepin.ready.json", "--limit", "2", "--dry-run")

    assert args.command == "liepin"
    assert args.liepin_command == "apply"
    assert args.liepin_apply_command == "open"
    assert args.input == "liepin.ready.json"
    assert args.limit == 2
    assert args.dry_run is True
    assert args.require_greeting is False
    assert args.skip_login_check is False
    assert args.config == "config/config.yaml"


def test_liepin_apply_open_accepts_require_greeting():
    args = parse_args("liepin", "apply", "open", "--input", "liepin.ready.json", "--require-greeting")

    assert args.require_greeting is True


def test_liepin_apply_open_accepts_skip_login_check():
    args = parse_args("liepin", "apply", "open", "--input", "liepin.ready.json", "--skip-login-check")

    assert args.skip_login_check is True


def test_liepin_apply_send_requires_explicit_confirmation_flag_shape():
    args = parse_args(
        "liepin",
        "apply",
        "send",
        "--input",
        "liepin.ready.json",
        "--limit",
        "1",
        "--confirm-submit",
    )

    assert args.liepin_apply_command == "send"
    assert args.input == "liepin.ready.json"
    assert args.limit == 1
    assert args.confirm_submit is True
    assert args.dry_run is False


def test_liepin_audit_uses_platform_audit_shape():
    args = parse_args("liepin", "audit", "--recent", "3")

    assert args.command == "liepin"
    assert args.liepin_command == "audit"
    assert args.recent == 3


def test_zhilian_collect_uses_read_only_spike_shape():
    args = parse_args(
        "zhilian",
        "collect",
        "--fixture",
        "zhilian.json",
        "--query",
        "AI产品",
        "--limit",
        "7",
        "--pages",
        "3",
        "--page-delay",
        "0.5",
    )

    assert args.command == "zhilian"
    assert args.zhilian_command == "collect"
    assert args.fixture == "zhilian.json"
    assert args.query == "AI产品"
    assert args.limit == 7
    assert args.page == 1
    assert args.pages == 3
    assert args.page_delay == 0.5
    assert args.config == "config/config.yaml"


def test_zhilian_login_uses_read_only_session_shape():
    args = parse_args(
        "zhilian",
        "login",
        "--check",
        "--timeout",
        "10",
        "--poll-interval",
        "2",
    )

    assert args.command == "zhilian"
    assert args.zhilian_command == "login"
    assert args.check is True
    assert args.timeout == 10
    assert args.poll_interval == 2
    assert args.query == "AI产品经理"
    assert args.city == "深圳"
    assert args.config == "config/config.yaml"


def test_zhilian_rank_uses_platform_rank_shape():
    args = parse_args("zhilian", "rank", "--input", "zhilian.raw.json", "--local")

    assert args.command == "zhilian"
    assert args.zhilian_command == "rank"
    assert args.input == "zhilian.raw.json"
    assert args.local is True
    assert args.config == "config/config.yaml"


def test_zhilian_greet_preview_uses_platform_shape():
    args = parse_args("zhilian", "greet", "preview", "--input", "zhilian.ranked.json", "--local")

    assert args.command == "zhilian"
    assert args.zhilian_command == "greet"
    assert args.zhilian_greet_command == "preview"
    assert args.input == "zhilian.ranked.json"
    assert args.local is True


def test_zhilian_apply_send_requires_explicit_confirmation_flag_shape():
    args = parse_args(
        "zhilian",
        "apply",
        "send",
        "--input",
        "zhilian.ready.json",
        "--confirm-submit",
    )

    assert args.command == "zhilian"
    assert args.zhilian_command == "apply"
    assert args.zhilian_apply_command == "send"
    assert args.confirm_submit is True


def test_zhilian_audit_uses_platform_audit_shape():
    args = parse_args("zhilian", "audit", "--recent", "3")

    assert args.command == "zhilian"
    assert args.zhilian_command == "audit"
    assert args.recent == 3


def test_platforms_status_accepts_config_override_path():
    args = parse_args("platforms", "status", "--config", "/tmp/jobagent-platforms.yaml")

    assert args.command == "platforms"
    assert args.platforms_command == "status"
    assert args.config == "/tmp/jobagent-platforms.yaml"


def test_platforms_health_accepts_platform_and_config():
    args = parse_args(
        "platforms",
        "health",
        "--platform",
        "boss",
        "--config",
        "/tmp/jobagent-platforms.yaml",
    )

    assert args.command == "platforms"
    assert args.platforms_command == "health"
    assert args.platform == "boss"
    assert args.config == "/tmp/jobagent-platforms.yaml"


def test_disabled_platform_exits_before_command_runs(tmp_path, capsys):
    config = tmp_path / "platforms.yaml"
    config.write_text("platforms:\n  boss:\n    enabled: false\n", encoding="utf-8")
    args = parse_args("boss", "greet", "audit")
    args.config = str(config)

    with pytest.raises(SystemExit) as exc:
        _require_platform_enabled_or_exit("boss", args)

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert '"error": "platform_disabled"' in err
    assert '"platform": "boss"' in err
