"""Recruiting platform capability registry.

The registry is intentionally small for now: it records platform boundaries
without coupling the fragile page automation flows together.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class DeliveryContract:
    action: str
    resume_source: str
    personalized_message: str
    message_max_chars: int | None
    success_evidence: list[str] = field(default_factory=list)
    unsupported_behaviors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PlatformInfo:
    key: str
    display_name: str
    status: str
    capabilities: list[str] = field(default_factory=list)
    delivery_contract: DeliveryContract | None = None
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
            "discover",
            "greet_preview",
            "greet_send",
            "audit",
        ],
        delivery_contract=DeliveryContract(
            action="personalized_greeting",
            resume_source="not_submitted",
            personalized_message="required_exact",
            message_max_chars=100,
            success_evidence=["exact_message_visible_in_outgoing_chat"],
            unsupported_behaviors=["platform_default_message_as_success"],
        ),
        notes="Discover plus signed personalized greeting delivery and exact-message audit.",
    ),
    PlatformInfo(
        key="liepin",
        display_name="猎聘",
        status="available",
        capabilities=[
            "login",
            "discover",
            "apply_review",
            "apply_send",
            "audit",
        ],
        delivery_contract=DeliveryContract(
            action="resume_and_personalized_greeting",
            resume_source="platform_account_resume",
            personalized_message="required_exact",
            message_max_chars=100,
            success_evidence=[
                "resume_delivery_visible_in_chat",
                "exact_message_visible_in_outgoing_chat",
            ],
            unsupported_behaviors=["platform_default_message_as_personalized_success"],
        ),
        notes="Discover plus verified account-resume and signed personalized greeting delivery.",
    ),
    PlatformInfo(
        key="zhilian",
        display_name="智联招聘",
        status="available",
        capabilities=[
            "login",
            "discover",
            "apply_review",
            "apply_send",
            "audit",
        ],
        delivery_contract=DeliveryContract(
            action="resume_submit",
            resume_source="platform_account_resume",
            personalized_message="unsupported",
            message_max_chars=None,
            success_evidence=["platform_delivery_confirmation"],
            unsupported_behaviors=["personalized_message_send"],
        ),
        notes="Discover plus verified resume submission; personalized messages are not supported.",
    ),
    PlatformInfo(
        key="51job",
        display_name="前程无忧 / 51Job",
        status="available",
        capabilities=[
            "login",
            "discover",
            "apply_review",
            "apply_send",
            "audit",
        ],
        delivery_contract=DeliveryContract(
            action="resume_submit",
            resume_source="platform_account_resume",
            personalized_message="unsupported",
            message_max_chars=None,
            success_evidence=["platform_delivery_confirmation"],
            unsupported_behaviors=["web_personalized_message_send", "web_chat"],
        ),
        notes="Discover plus verified resume submission; web chat remains a mobile QR handoff.",
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
    "job51": "51job",
    "51": "51job",
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
