from __future__ import annotations

from jobagent.platforms import (
    check_all_platforms,
    check_platform_health,
    is_platform_enabled,
    list_platforms,
    normalize_platform_key,
)


def test_platform_registry_starts_with_boss_available():
    platforms = list_platforms()

    assert platforms[0].key == "boss"
    assert platforms[0].status == "available"
    assert "confirmed_send" in platforms[0].capabilities


def test_linkedin_is_dropped_in_registry():
    linkedin = next(platform for platform in list_platforms() if platform.key == "linkedin")

    assert linkedin.status == "dropped"
    assert linkedin.capabilities == []
    assert "complete vertical chains" in linkedin.notes


def test_liepin_registry_exposes_vertical_chain():
    liepin = next(platform for platform in list_platforms() if platform.key == "liepin")

    assert liepin.status == "beta"
    assert liepin.capabilities == [
        "fixture_parse",
        "login_check",
        "live_read_only_collect",
        "rank",
        "greet_preview",
        "apply_open",
        "apply_send",
        "audit",
    ]
    assert "controlled apply-send" in liepin.notes


def test_zhilian_registry_exposes_vertical_chain():
    zhilian = next(platform for platform in list_platforms() if platform.key == "zhilian")

    assert zhilian.status == "beta"
    assert zhilian.capabilities == [
        "fixture_parse",
        "login_check",
        "live_read_only_collect",
        "rank",
        "greet_preview",
        "apply_open",
        "apply_send",
        "audit",
    ]
    assert "resume-submit apply-send" in zhilian.notes
    assert "does not support in-page greeting send" in zhilian.notes


def test_legacy_zhipin_platform_normalizes_to_boss():
    assert normalize_platform_key("zhipin") == "boss"
    assert normalize_platform_key("") == "boss"


def test_platform_can_be_disabled_by_config_override():
    overrides = {"platforms": {"boss": {"enabled": False}}}
    boss = next(platform for platform in list_platforms(overrides) if platform.key == "boss")

    assert boss.status == "disabled"
    assert "Disabled by local platform config" in boss.notes
    assert is_platform_enabled("boss", overrides) is False
    assert is_platform_enabled("zhipin", overrides) is False


def test_boss_health_reports_available_when_chrome_exists(monkeypatch):
    monkeypatch.setattr(
        "jobagent.drivers.boss.chrome_manager.find_chrome",
        lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )

    health = check_platform_health("boss")

    assert health.platform == "boss"
    assert health.status == "available"
    assert health.ok is True
    assert [check.name for check in health.checks] == ["enabled", "chrome_available"]
    assert health.checks[1].evidence["path"].endswith("Google Chrome")


def test_boss_health_reports_degraded_when_chrome_missing(monkeypatch):
    monkeypatch.setattr(
        "jobagent.drivers.boss.chrome_manager.find_chrome",
        lambda: None,
    )

    health = check_platform_health("boss")

    assert health.platform == "boss"
    assert health.status == "degraded"
    assert health.ok is False
    assert health.checks[1].name == "chrome_available"
    assert health.checks[1].ok is False


def test_disabled_platform_health_stops_before_runtime_checks(monkeypatch):
    def fail_if_called():
        raise AssertionError("disabled healthcheck must not inspect Chrome")

    monkeypatch.setattr(
        "jobagent.drivers.boss.chrome_manager.find_chrome",
        fail_if_called,
    )

    health = check_platform_health("boss", {"platforms": {"boss": {"enabled": False}}})

    assert health.status == "disabled"
    assert health.ok is False
    assert [check.name for check in health.checks] == ["enabled"]


def test_unimplemented_platform_health_is_explicit():
    health = check_platform_health("linkedin")

    assert health.platform == "linkedin"
    assert health.status == "dropped"
    assert health.ok is False
    assert health.checks[-1].name == "implemented"
    assert health.checks[-1].ok is False


def test_liepin_health_reports_read_only_collect_available(monkeypatch):
    monkeypatch.setattr(
        "jobagent.drivers.boss.chrome_manager.find_chrome",
        lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )

    health = check_platform_health("liepin")

    assert health.platform == "liepin"
    assert health.status == "beta"
    assert health.ok is True
    assert [check.name for check in health.checks] == [
        "enabled",
        "fixture_parser_available",
        "login_guide_available",
        "live_read_only_collector_available",
        "chrome_available",
    ]


def test_zhilian_health_reports_read_only_collect_available(monkeypatch):
    monkeypatch.setattr(
        "jobagent.drivers.boss.chrome_manager.find_chrome",
        lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )

    health = check_platform_health("zhilian")

    assert health.platform == "zhilian"
    assert health.status == "beta"
    assert health.ok is True
    assert [check.name for check in health.checks] == [
        "enabled",
        "fixture_parser_available",
        "live_read_only_collector_available",
        "login_guide_available",
        "chrome_available",
    ]


def test_all_platform_health_uses_registry_order(monkeypatch):
    monkeypatch.setattr(
        "jobagent.drivers.boss.chrome_manager.find_chrome",
        lambda: "/usr/bin/chrome",
    )

    health = check_all_platforms()

    assert [item.platform for item in health] == ["boss", "liepin", "zhilian", "linkedin"]
