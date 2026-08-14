"""Discover and verify Zhilian city codes before candidate data leaves the browser."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from jobagent.infra.state import APP_DIR

CITY_CACHE_SCHEMA_VERSION = 2
BUNDLED_CITY_CODES = {
    "北京": "530",
    "上海": "538",
    "深圳": "489",
}


def normalize_city_name(city: str) -> str:
    return city.strip().removesuffix("市")


def city_code_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    query_code = (parse_qs(parsed.query).get("jl") or [None])[0]
    if query_code and str(query_code).isdigit():
        return str(query_code)
    match = re.search(r"(?:^|/)jl(\d+)(?:/|$)", parsed.path)
    return match.group(1) if match else None


class ZhilianCityResolver:
    def __init__(self, cache_path: Path | None = None):
        self.cache_path = cache_path or APP_DIR / "metadata" / "zhilian_city_codes.json"

    def _load(self) -> dict[str, Any]:
        if not self.cache_path.exists():
            return {"schema_version": CITY_CACHE_SCHEMA_VERSION, "cities": {}}
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": CITY_CACHE_SCHEMA_VERSION, "cities": {}}
        if not isinstance(payload, dict) or not isinstance(payload.get("cities"), dict):
            return {"schema_version": CITY_CACHE_SCHEMA_VERSION, "cities": {}}
        return payload

    def lookup(self, city: str) -> tuple[str | None, str]:
        normalized = normalize_city_name(city)
        cached = (self._load().get("cities") or {}).get(normalized) or {}
        code = str(cached.get("code") or "")
        if code.isdigit():
            return code, "verified_cache"
        bundled = BUNDLED_CITY_CODES.get(normalized)
        return (bundled, "bundled_seed") if bundled else (None, "unresolved")

    def remember(
        self,
        city: str,
        code: str,
        *,
        evidence_url: str,
        evidence_sources: list[str] | None = None,
        verification_source: str = "legacy_verified",
    ) -> None:
        normalized = normalize_city_name(city)
        if not normalized or not code.isdigit():
            return
        payload = self._load()
        payload["schema_version"] = CITY_CACHE_SCHEMA_VERSION
        cities = payload.setdefault("cities", {})
        cities[normalized] = {
            "code": code,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "evidence_url": evidence_url,
            "evidence_sources": sorted(set(evidence_sources or [])),
            "verification_source": verification_source,
        }
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.cache_path)

    def verify_snapshot(
        self,
        city: str,
        snapshot: dict[str, Any],
        *,
        expected_code: str | None,
        source: str,
    ) -> dict[str, Any]:
        normalized = normalize_city_name(city)
        observed_url = str(snapshot.get("url") or "")
        url_code = city_code_from_url(observed_url)
        city_filter = snapshot.get("cityFilter") if isinstance(snapshot.get("cityFilter"), dict) else {}
        candidate_code = str(
            snapshot.get("candidateCityCode")
            or city_filter.get("candidateCode")
            or ""
        )
        if not candidate_code.isdigit():
            candidate_code = ""
        observed_code = url_code or candidate_code or None
        code_evidence_conflict = bool(
            url_code and candidate_code and url_code != candidate_code
        )
        observed_code_source = (
            "result_url" if url_code else "city_control_metadata" if candidate_code else "none"
        )
        observed_title = str(snapshot.get("title") or "")
        visible_values = _visible_city_values(snapshot)
        matching = 0
        mismatches: list[str] = []
        for card in snapshot.get("cards") or []:
            if not isinstance(card, dict):
                continue
            card_city = normalize_city_name(str(card.get("cityName") or card.get("city") or ""))
            if not card_city:
                continue
            if card_city == normalized:
                matching += 1
            else:
                mismatches.append(card_city)
        card_cities_present = bool(matching or mismatches)
        all_cards_other_city = bool(card_cities_present and matching == 0)
        title_matches_city = bool(
            normalized and normalized in normalize_city_name(observed_title)
        )
        visible_city_conflict = bool(visible_values and normalized not in visible_values)
        evidence_sources: list[str] = []
        if title_matches_city:
            evidence_sources.append("page_title")
        if normalized and normalized in visible_values:
            evidence_sources.append("visible_city")
        if matching:
            evidence_sources.append("job_cards")
        evidence_sources.sort()
        non_card_city_sources = {
            value for value in evidence_sources if value in {"page_title", "visible_city"}
        }

        code_changed = bool(observed_code and expected_code and observed_code != expected_code)
        trusted_code_match = bool(
            observed_code
            and expected_code
            and observed_code == expected_code
            and source in {"bundled_seed", "verified_cache"}
        )
        result_surface_city_consistent = bool(
            observed_code
            and (
                (trusted_code_match and non_card_city_sources)
                or (
                    (code_changed or not expected_code)
                    and len(non_card_city_sources) >= 2
                )
            )
        )
        cross_city_fallback_only = bool(
            all_cards_other_city
            and result_surface_city_consistent
            and not visible_city_conflict
            and not code_evidence_conflict
        )
        evidence_conflict = bool(
            visible_city_conflict
            or code_evidence_conflict
            or (all_cards_other_city and not cross_city_fallback_only)
        )
        if not observed_code or evidence_conflict:
            verified = False
        elif code_changed or not expected_code:
            # A newly observed code is only learned when two independent,
            # human-readable page signals agree on the requested city.
            verified = len(evidence_sources) >= 2
        else:
            # Existing seeds/cache still require at least one semantic signal;
            # the URL code alone is never sufficient.
            verified = bool(trusted_code_match and evidence_sources)

        verification_source = (
            "dynamic_multi_source" if verified and (code_changed or not expected_code)
            else "trusted_mapping_with_page_evidence" if verified
            else "unverified"
        )
        if verified:
            evidence_status = "verified"
            reason = "verified"
        elif evidence_conflict:
            evidence_status = "evidence_conflict"
            reason = "city_evidence_conflict"
        elif not observed_code:
            evidence_status = "evidence_pending"
            reason = "observed_city_code_missing"
        elif code_changed or not expected_code:
            evidence_status = "evidence_pending"
            reason = "dynamic_evidence_insufficient"
        else:
            evidence_status = "evidence_pending"
            reason = "trusted_mapping_evidence_missing"
        return {
            "ok": verified,
            "mode": "zhilian_city_resolution",
            "city": normalized,
            "source": source,
            "expectedCode": expected_code,
            "observedCode": observed_code,
            "observedCodeSource": observed_code_source,
            "candidateCode": candidate_code or None,
            "codeEvidenceConflict": code_evidence_conflict,
            "observedUrl": observed_url,
            "observedTitle": observed_title,
            "visibleCities": visible_values,
            "matchingCards": matching,
            "mismatchedCardCities": sorted(set(mismatches)),
            "allCardsOtherCity": all_cards_other_city,
            "crossCityFallbackOnly": cross_city_fallback_only,
            "titleMatchesCity": title_matches_city,
            "visibleCityConflict": visible_city_conflict,
            "evidenceStatus": evidence_status,
            "reason": reason,
            "codeChanged": code_changed,
            "evidenceSources": evidence_sources,
            "verificationSource": verification_source,
            "verified": verified,
        }


def _visible_city_values(snapshot: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("visibleCity", "currentCity", "selectedCity"):
        if snapshot.get(key):
            values.append(snapshot[key])
    if isinstance(snapshot.get("visibleCities"), list):
        values.extend(snapshot["visibleCities"])
    normalized = {
        normalize_city_name(str(value))
        for value in values
        if normalize_city_name(str(value))
    }
    return sorted(normalized)
