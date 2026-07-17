"""Public delivery validation for platforms that support personalized messages."""

from __future__ import annotations

import hashlib
from typing import Any

from jobagent.platforms.registry import list_platforms


def validate_personalized_message(platform: str, message: str) -> dict[str, Any]:
    info = next(item for item in list_platforms() if item.key == platform)
    contract = info.delivery_contract
    normalized = str(message or "").strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else None
    if contract is None or contract.personalized_message == "unsupported":
        return {
            "ok": False,
            "error": "personalized_message_unsupported",
            "length": len(normalized),
            "sha256": digest,
        }
    if not normalized:
        return {
            "ok": False,
            "error": "missing_signed_greeting",
            "length": 0,
            "sha256": None,
            "max_chars": contract.message_max_chars,
        }
    if contract.message_max_chars is not None and len(normalized) > contract.message_max_chars:
        return {
            "ok": False,
            "error": "signed_greeting_too_long",
            "length": len(normalized),
            "sha256": digest,
            "max_chars": contract.message_max_chars,
        }
    return {
        "ok": True,
        "length": len(normalized),
        "sha256": digest,
        "max_chars": contract.message_max_chars,
    }
