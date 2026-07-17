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
    assert "greet_send" in platforms[0].capabilities
    assert platforms[0].delivery_contract.personalized_message == "required_exact"
    assert platforms[0].delivery_contract.message_max_chars == 100


def test_linkedin_is_dropped_in_registry():
    linkedin = next(platform for platform in list_platforms() if platform.key == "linkedin")

    assert linkedin.status == "dropped"
    assert linkedin.capabilities == []
    assert "complete vertical chains" in linkedin.notes


def test_liepin_registry_exposes_vertical_chain():
    liepin = next(platform for platform in list_platforms() if platform.key == "liepin")

    assert liepin.status == "available"
    assert liepin.capabilities == [
        "login",
        "discover",
        "apply_review",
        "apply_send",
        "audit",
    ]
    assert "signed personalized greeting" in liepin.notes
    assert liepin.delivery_contract.action == "resume_and_personalized_greeting"
    assert liepin.delivery_contract.success_evidence == [
        "resume_delivery_visible_in_chat",
        "exact_message_visible_in_outgoing_chat",
    ]


def test_zhilian_registry_exposes_vertical_chain():
    zhilian = next(platform for platform in list_platforms() if platform.key == "zhilian")

    assert zhilian.status == "available"
    assert zhilian.capabilities == [
        "login",
        "discover",
        "apply_review",
        "apply_send",
        "audit",
    ]
    assert "resume submission" in zhilian.notes
    assert zhilian.delivery_contract.personalized_message == "unsupported"


def test_job51_registry_exposes_vertical_chain():
    job51 = next(platform for platform in list_platforms() if platform.key == "51job")

    assert job51.status == "available"
    assert job51.capabilities == [
        "login",
        "discover",
        "apply_review",
        "apply_send",
        "audit",
    ]
    assert "QR handoff" in job51.notes
    assert "web_chat" in job51.delivery_contract.unsupported_behaviors


def test_legacy_zhipin_platform_normalizes_to_boss():
    assert normalize_platform_key("zhipin") == "boss"
    assert normalize_platform_key("") == "boss"
    assert normalize_platform_key("job51") == "51job"


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
    assert [check.name for check in health.checks] == [
        "enabled",
        "discover_adapter_available",
        "chrome_available",
    ]
    assert health.checks[2].evidence["path"].endswith("Google Chrome")


def test_boss_health_reports_degraded_when_chrome_missing(monkeypatch):
    monkeypatch.setattr(
        "jobagent.drivers.boss.chrome_manager.find_chrome",
        lambda: None,
    )

    health = check_platform_health("boss")

    assert health.platform == "boss"
    assert health.status == "degraded"
    assert health.ok is False
    assert health.checks[2].name == "chrome_available"
    assert health.checks[2].ok is False


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
    assert health.status == "available"
    assert health.ok is True
    assert [check.name for check in health.checks] == [
        "enabled",
        "discover_adapter_available",
        "chrome_available",
    ]


def test_zhilian_health_reports_read_only_collect_available(monkeypatch):
    monkeypatch.setattr(
        "jobagent.drivers.boss.chrome_manager.find_chrome",
        lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )

    health = check_platform_health("zhilian")

    assert health.platform == "zhilian"
    assert health.status == "available"
    assert health.ok is True
    assert [check.name for check in health.checks] == [
        "enabled",
        "discover_adapter_available",
        "chrome_available",
    ]


def test_job51_health_reports_read_only_collect_available(monkeypatch):
    monkeypatch.setattr(
        "jobagent.drivers.boss.chrome_manager.find_chrome",
        lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )

    health = check_platform_health("51job")

    assert health.platform == "51job"
    assert health.status == "available"
    assert health.ok is True
    assert [check.name for check in health.checks] == [
        "enabled",
        "discover_adapter_available",
        "chrome_available",
    ]


def test_all_platform_health_uses_registry_order(monkeypatch):
    monkeypatch.setattr(
        "jobagent.drivers.boss.chrome_manager.find_chrome",
        lambda: "/usr/bin/chrome",
    )

    health = check_all_platforms()

    assert [item.platform for item in health] == ["boss", "liepin", "zhilian", "51job", "linkedin"]
