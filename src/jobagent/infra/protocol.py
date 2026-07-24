"""Signature and digest verification for the official Job Agent protocol."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from jobagent.infra.cloud_client import PROTOCOL_VERSION

RELEASE_SIGNING_PUBLIC_KEY = "08rY8C6SMBqyCD4rZGiSyLJsmrzLd_l-BolAyVe20Ww"
DECISION_SIGNING_PUBLIC_KEY = "qc1RriVcFumPm0mBxJ4HaVUx4fq9VGn5tyHB1jaCYE0"

_CANDIDATE_FIELDS = {
    "id",
    "title",
    "company",
    "area",
    "salary",
    "experience",
    "degree",
    "skills",
    "company_size",
    "industry",
    "finance_stage",
    "boss_name",
    "boss_title",
    "url",
    "security_id",
    "jd",
}


class ProtocolError(ValueError):
    pass


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest_payload(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def canonical_candidates(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in job.items() if key in _CANDIDATE_FIELDS and value is not None}
        for job in jobs
    ]


def candidate_digest(jobs: list[dict[str, Any]]) -> str:
    return digest_payload(canonical_candidates(jobs))


def _decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:
        raise ProtocolError("invalid base64 signature material") from exc


def verify_signed_payload(
    payload: dict[str, Any],
    *,
    public_key: str,
    expected_type: str | None = None,
) -> dict[str, Any]:
    signed = dict(payload)
    signature = str(signed.pop("signature", ""))
    if not signature:
        raise ProtocolError("signed payload is missing signature")
    if signed.get("signature_algorithm") != "Ed25519":
        raise ProtocolError("unsupported signature algorithm")
    if expected_type and signed.get("manifest_type") != expected_type:
        raise ProtocolError(f"unexpected manifest type: {signed.get('manifest_type')}")
    try:
        Ed25519PublicKey.from_public_bytes(_decode(public_key)).verify(
            _decode(signature),
            canonical_json_bytes(signed),
        )
    except (InvalidSignature, ValueError) as exc:
        raise ProtocolError("manifest signature verification failed") from exc
    return signed


def _is_expired(value: str) -> bool:
    try:
        expires = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProtocolError("manifest has an invalid expiry") from exc
    return datetime.now(timezone.utc) >= expires.astimezone(timezone.utc)


def _validate_search_queries(value: Any) -> None:
    if not isinstance(value, list) or not 1 <= len(value) <= 12:
        raise ProtocolError("search plan queries are invalid")
    for item in value:
        if not isinstance(item, dict):
            raise ProtocolError("search plan query is invalid")
        keyword = str(item.get("keyword") or "").strip()
        city = str(item.get("city") or "").strip()
        try:
            page_limit = int(item.get("page_limit", 0))
        except (TypeError, ValueError) as exc:
            raise ProtocolError("search plan page limit is invalid") from exc
        if (
            not 1 <= len(keyword) <= 80
            or not any(character.isalpha() for character in keyword)
            or any(ord(character) < 32 for character in keyword)
        ):
            raise ProtocolError("search plan keyword is invalid")
        compact = "".join(keyword.split())
        digit_count = sum(character.isdigit() for character in compact)
        if (
            len(compact) >= 16
            and compact.isalnum()
            and digit_count >= len(compact) // 4
        ):
            raise ProtocolError("search plan keyword looks like an opaque platform identifier")
        if len(city) > 30 or any(ord(character) < 32 for character in city):
            raise ProtocolError("search plan city is invalid")
        if not 1 <= page_limit <= 5:
            raise ProtocolError("search plan page limit is invalid")


def verify_search_plan(
    plan: dict[str, Any],
    *,
    platform: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    signed = verify_signed_payload(
        plan,
        public_key=DECISION_SIGNING_PUBLIC_KEY,
        expected_type="search_plan",
    )
    if signed.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError("search plan protocol version mismatch")
    if signed.get("platform") != platform:
        raise ProtocolError("search plan platform mismatch")
    if signed.get("profile_digest") != digest_payload(profile):
        raise ProtocolError("search plan profile digest mismatch")
    if int(signed.get("candidate_limit", 0)) != 100:
        raise ProtocolError("search plan candidate limit mismatch")
    _validate_search_queries(signed.get("queries"))
    if _is_expired(str(signed.get("expires_at", ""))):
        raise ProtocolError("search plan expired")
    return signed


def verify_decision_manifest(
    manifest: dict[str, Any],
    *,
    platform: str,
    discover_id: str,
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    signed = verify_signed_payload(
        manifest,
        public_key=DECISION_SIGNING_PUBLIC_KEY,
        expected_type="decision_manifest",
    )
    if signed.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError("decision protocol version mismatch")
    if signed.get("platform") != platform or signed.get("discover_id") != discover_id:
        raise ProtocolError("decision binding mismatch")
    if signed.get("candidate_digest") != candidate_digest(jobs):
        raise ProtocolError("decision candidate digest mismatch")
    if _is_expired(str(signed.get("expires_at", ""))):
        raise ProtocolError("decision manifest expired")
    classified = [
        item
        for bucket in ("selected", "review", "rejected")
        for item in signed.get(bucket, [])
    ]
    ids = [str(item.get("id")) for item in classified]
    if len(ids) != len(set(ids)) or len(ids) != int(signed.get("deduplicated_count", -1)):
        raise ProtocolError("decision categories are incomplete or duplicated")
    return signed


def verify_stored_decision(manifest: dict[str, Any], *, platform: str) -> dict[str, Any]:
    """Verify a persisted manifest when raw candidates have already been discarded."""
    signed = verify_signed_payload(
        manifest,
        public_key=DECISION_SIGNING_PUBLIC_KEY,
        expected_type="decision_manifest",
    )
    if signed.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError("decision protocol version mismatch")
    if signed.get("platform") != platform:
        raise ProtocolError("decision platform mismatch")
    if _is_expired(str(signed.get("expires_at", ""))):
        raise ProtocolError("decision manifest expired")
    classified = [
        item
        for bucket in ("selected", "review", "rejected")
        for item in signed.get(bucket, [])
    ]
    ids = [str(item.get("id")) for item in classified]
    if len(ids) != len(set(ids)) or len(ids) != int(signed.get("deduplicated_count", -1)):
        raise ProtocolError("decision categories are incomplete or duplicated")
    return signed
