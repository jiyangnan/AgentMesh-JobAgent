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


class SearchPlanExpiredError(ProtocolError):
    """A correctly signed and context-bound SearchPlan crossed its TTL."""

    def __init__(self, signed_plan: dict[str, Any]):
        super().__init__("search plan expired")
        self.signed_plan = signed_plan


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
        {
            key: value
            for key, value in job.items()
            if key in _CANDIDATE_FIELDS and value is not None
        }
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


def _validate_search_queries(value: Any, *, page_ceiling: int = 5) -> None:
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
            raise ProtocolError(
                "search plan keyword looks like an opaque platform identifier"
            )
        if len(city) > 30 or any(ord(character) < 32 for character in city):
            raise ProtocolError("search plan city is invalid")
        if not 1 <= page_limit <= page_ceiling:
            raise ProtocolError("search plan page limit is invalid")


def verify_search_plan(
    plan: dict[str, Any],
    *,
    platform: str,
    profile: dict[str, Any],
    round_intent: dict[str, Any] | None = None,
    request_id: str | None = None,
    require_request_id: bool = False,
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
    if round_intent and round_intent.get("status") == "confirmed":
        expected_intent_digest = digest_payload(round_intent)
        if signed.get("intent_digest") != expected_intent_digest:
            raise ProtocolError("search plan intent digest mismatch")
        if signed.get("round_intent") != round_intent:
            raise ProtocolError("search plan round intent mismatch")
    version = signed.get("discovery_contract_version", 1)
    if type(version) is not int or version not in {1, 2}:
        raise ProtocolError("unsupported discovery contract version")
    bulk = version == 2
    limit = signed.get("candidate_limit", 0)
    if type(limit) is not int:
        raise ProtocolError("search plan candidate limit invalid")
    if (not bulk and limit != 100) or (bulk and not 1 <= limit <= 1000):
        raise ProtocolError("search plan candidate limit mismatch")
    if bulk:
        excluded = signed.get("excluded_delivered_ids", [])
        if (not isinstance(excluded, list)
                or any(not isinstance(identifier, str) or not 1 <= len(identifier.strip()) <= 200
                       for identifier in excluded)):
            raise ProtocolError("search plan delivered exclusions invalid")
        recommendation_limit = signed.get("recommendation_limit")
        if recommendation_limit is not None and (type(recommendation_limit) is not int or not 1 <= recommendation_limit <= 1000):
            raise ProtocolError("search plan recommendation limit invalid")
        if (signed.get("collection_executor") != "cdp_readonly"
                or signed.get("delivery_executor") not in ("codex_native", "playwright")
                or not isinstance(signed.get("account_ref"), str)
                or type(signed.get("page_budget")) is not int or not 1 <= signed["page_budget"] <= 200
                or type(signed.get("collection_time_budget_seconds")) is not int
                or not 1 <= signed["collection_time_budget_seconds"] <= 3600):
            raise ProtocolError("search plan bulk controls invalid")
    signed_request_id = signed.get("request_id")
    if require_request_id and not signed_request_id:
        raise ProtocolError("search plan request binding is missing")
    if request_id is not None and signed_request_id not in {None, request_id}:
        raise ProtocolError("search plan request binding mismatch")
    _validate_search_queries(signed.get("queries"), page_ceiling=200 if bulk else 5)
    if _is_expired(str(signed.get("expires_at", ""))):
        raise SearchPlanExpiredError(signed)
    return signed


def verify_decision_manifest(
    manifest: dict[str, Any],
    *,
    platform: str,
    discover_id: str,
    jobs: list[dict[str, Any]],
    intent_digest: str | None = None,
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
    if intent_digest is not None and signed.get("intent_digest") != intent_digest:
        raise ProtocolError("decision intent digest mismatch")
    if signed.get("candidate_digest") != candidate_digest(jobs):
        raise ProtocolError("decision candidate digest mismatch")
    if _is_expired(str(signed.get("expires_at", ""))):
        raise ProtocolError("decision manifest expired")
    classified = [
        item
        for bucket in ("selected", "review", "rejected", "reserve")
        for item in signed.get(bucket, [])
    ]
    ids = [str(item.get("id")) for item in classified]
    if len(ids) != len(set(ids)) or len(ids) != int(
        signed.get("deduplicated_count", -1)
    ):
        raise ProtocolError("decision categories are incomplete or duplicated")
    if not set(ids).issubset({str(j["id"]) for j in jobs}):
        raise ProtocolError("decision contains unknown candidates")
    _verify_bulk_coverage(signed)
    return signed


def verify_stored_decision(
    manifest: dict[str, Any], *, platform: str, allow_expired: bool = False
) -> dict[str, Any]:
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
    if not allow_expired and _is_expired(str(signed.get("expires_at", ""))):
        raise ProtocolError("decision manifest expired")
    classified = [
        item
        for bucket in ("selected", "review", "rejected", "reserve")
        for item in signed.get(bucket, [])
    ]
    ids = [str(item.get("id")) for item in classified]
    if len(ids) != len(set(ids)) or len(ids) != int(
        signed.get("deduplicated_count", -1)
    ):
        raise ProtocolError("decision categories are incomplete or duplicated")
    _verify_bulk_coverage(signed)
    return signed


def _verify_bulk_coverage(signed):
    if signed.get("discovery_contract_version") != 2:
        if signed.get("reserve"):
            raise ProtocolError("legacy decision cannot contain reserve")
        return
    count = signed["deduplicated_count"]
    coverage = signed.get("coverage") or {}
    if coverage.get("unique_candidates") != count or coverage.get("scored_candidates") != count:
        raise ProtocolError("bulk decision coverage incomplete")
    limit = signed.get("recommendation_limit")
    if limit is not None and (type(limit) is not int or len(signed.get("selected", [])) > limit):
        raise ProtocolError("bulk recommendation limit exceeded")
    for bucket in ("selected", "review", "rejected", "reserve"):
        if any(j.get("classification") != bucket for j in signed.get(bucket, [])):
            raise ProtocolError("bulk decision classification mismatch")
