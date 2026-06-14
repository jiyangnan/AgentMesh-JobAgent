"""Recruiting platform capability registry.

The registry is intentionally small for now: it records platform boundaries
without coupling the fragile page automation flows together.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class PlatformInfo:
    key: str
    display_name: str
    status: str
    capabilities: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_PLATFORMS: tuple[PlatformInfo, ...] = (
    PlatformInfo(
        key="boss",
        display_name="Boss直聘",
        status="available",
        capabilities=[
            "login",
            "collect",
            "rank",
            "greet_preview",
            "confirmed_send",
            "audit",
        ],
        notes="Stable first platform; use jobagent boss ... commands as the canonical entry.",
    ),
    PlatformInfo(
        key="liepin",
        display_name="猎聘",
        status="beta",
        capabilities=[
            "fixture_parse",
            "login_check",
            "live_read_only_collect",
            "rank",
            "greet_preview",
            "apply_open",
            "apply_send",
            "audit",
        ],
        notes="M2 beta vertical chain: collect, rank, greeting preview, manual apply-open, controlled apply-send, and Liepin audit.",
    ),
    PlatformInfo(
        key="zhilian",
        display_name="智联招聘",
        status="beta",
        capabilities=[
            "fixture_parse",
            "login_check",
            "live_read_only_collect",
            "rank",
            "greet_preview",
            "apply_open",
            "apply_send",
            "audit",
        ],
        notes="M3 vertical chain: collect, rank, review-note preview, manual apply-open, controlled resume-submit apply-send, and audit. Zhilian does not support in-page greeting send.",
    ),
    PlatformInfo(
        key="linkedin",
        display_name="LinkedIn",
        status="dropped",
        capabilities=[],
        notes="Dropped by product decision: Job Agent only invests in platforms that can be built as complete vertical chains.",
    ),
)

_ALIASES = {
    "": "boss",
    "zhipin": "boss",
}


def _platform_enabled(key: str, overrides: dict[str, Any] | None) -> bool:
    platforms = (overrides or {}).get("platforms", {})
    if not isinstance(platforms, dict):
        return True
    entry = platforms.get(key)
    if not isinstance(entry, dict):
        return True
    return entry.get("enabled", True) is not False


def list_platforms(overrides: dict[str, Any] | None = None) -> list[PlatformInfo]:
    """Return platform capability metadata in roadmap order."""
    result: list[PlatformInfo] = []
    for platform in _PLATFORMS:
        if _platform_enabled(platform.key, overrides):
            result.append(platform)
            continue
        result.append(replace(
            platform,
            status="disabled",
            notes=f"{platform.notes} Disabled by local platform config.",
        ))
    return result


def is_platform_enabled(key: str, overrides: dict[str, Any] | None = None) -> bool:
    """Return whether a platform is enabled by local config overrides."""
    return _platform_enabled(normalize_platform_key(key), overrides)


def normalize_platform_key(platform: str | None) -> str:
    """Return the canonical platform key used by CLI status and audit logs."""
    value = (platform or "").strip().lower()
    return _ALIASES.get(value, value or "boss")
