"""Lightweight platform health checks.

These checks are intentionally non-invasive: they do not open browsers, log in,
or touch recruiting sites. Platform flow checks remain in platform-specific
doctor commands.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .registry import list_platforms, normalize_platform_key


@dataclass(frozen=True)
class HealthCheck:
    name: str
    ok: bool
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlatformHealth:
    platform: str
    status: str
    ok: bool
    checks: list[HealthCheck] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "status": self.status,
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
        }


def check_platform_health(
    platform: str,
    overrides: dict[str, Any] | None = None,
) -> PlatformHealth:
    key = normalize_platform_key(platform)
    platform_info = next((p for p in list_platforms(overrides) if p.key == key), None)
    if platform_info is None:
        return PlatformHealth(
            platform=key,
            status="unknown",
            ok=False,
            checks=[HealthCheck("registered", False, "Unknown platform")],
        )

    checks = [
        HealthCheck("enabled", platform_info.status != "disabled", platform_info.status),
    ]
    if platform_info.status == "disabled":
        return PlatformHealth(platform=key, status="disabled", ok=False, checks=checks)

    if key in {"boss", "liepin", "zhilian", "51job"}:
        from jobagent.drivers.boss.chrome_manager import find_chrome
        from jobagent.platforms.discovery import collect_from_search_plan

        chrome_path = find_chrome()
        checks.append(
            HealthCheck(
                "discover_adapter_available",
                callable(collect_from_search_plan),
                "The platform Discover adapter is available.",
            ),
        )
        checks.append(
            HealthCheck(
                "chrome_available",
                chrome_path is not None,
                "Google Chrome or Chromium executable found"
                if chrome_path
                else "Chrome executable not found",
                {"path": chrome_path or ""},
            ),
        )
        return PlatformHealth(
            platform=key,
            status="available" if all(check.ok for check in checks) else "degraded",
            ok=all(check.ok for check in checks),
            checks=checks,
        )

    checks.append(
        HealthCheck(
            "implemented",
            False,
            "Platform has been dropped by product decision; no runtime healthcheck is planned."
            if platform_info.status == "dropped"
            else f"Platform flow is {platform_info.status}; no runtime healthcheck implemented yet.",
        ),
    )
    return PlatformHealth(
        platform=key,
        status=platform_info.status,
        ok=False,
        checks=checks,
    )


def check_all_platforms(
    overrides: dict[str, Any] | None = None,
) -> list[PlatformHealth]:
    return [
        check_platform_health(platform.key, overrides)
        for platform in list_platforms(overrides)
    ]
