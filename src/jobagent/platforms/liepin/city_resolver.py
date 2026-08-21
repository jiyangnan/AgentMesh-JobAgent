"""Verified Liepin city metadata and local cache handling."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jobagent.infra.state import APP_DIR


CITY_CACHE_SCHEMA_VERSION = 1

BUNDLED_CITY_CODES = {
    "北京": "010",
    "上海": "020",
    "广州": "050020",
    "深圳": "050090",
}


def normalize_city_name(value: str) -> str:
    return str(value or "").strip().removesuffix("市")


class LiepinCityResolver:
    """Keep city codes as candidates until the live result page verifies them."""

    def __init__(self, cache_path: Path | None = None):
        self.cache_path = cache_path or APP_DIR / "metadata" / "liepin_city_codes.json"

    def lookup(self, city: str) -> tuple[str | None, str]:
        normalized = normalize_city_name(city)
        cached = self._load().get("cities", {}).get(normalized)
        if isinstance(cached, dict):
            code = str(cached.get("code") or "")
            if code.isdigit():
                return code, "verified_cache"
        bundled = BUNDLED_CITY_CODES.get(normalized)
        if bundled:
            return bundled, "bundled_seed"
        return None, "none"

    def forget(self, city: str, *, code: str | None = None) -> None:
        normalized = normalize_city_name(city)
        payload = self._load()
        cities = payload.setdefault("cities", {})
        current = cities.get(normalized)
        if not isinstance(current, dict):
            return
        if code is not None and str(current.get("code") or "") != str(code):
            return
        cities.pop(normalized, None)
        self._write(payload)

    def remember(
        self,
        city: str,
        code: str,
        verification: dict[str, Any],
    ) -> bool:
        normalized = normalize_city_name(city)
        normalized_code = str(code or "").strip()
        if (
            verification.get("verified") is not True
            or normalize_city_name(str(verification.get("city") or "")) != normalized
            or str(verification.get("code") or "") != normalized_code
            or not normalized_code.isdigit()
        ):
            return False
        payload = self._load()
        payload.setdefault("cities", {})[normalized] = {
            "code": normalized_code,
            "verified_at": datetime.now(UTC).isoformat(),
            "city_sources": list(verification.get("city_sources") or []),
            "query_sources": list(verification.get("query_sources") or []),
        }
        self._write(payload)
        return True

    def verify_evidence(
        self,
        evidence: dict[str, Any],
        *,
        city: str,
        query: str,
        expected_code: str | None = None,
    ) -> dict[str, Any]:
        expected_city = normalize_city_name(city)
        expected_query = str(query or "").strip()
        code = str(evidence.get("controlCode") or "").strip()
        city_values = {
            "control": normalize_city_name(str(evidence.get("controlCity") or "")),
            "meta": normalize_city_name(str(evidence.get("metaCity") or "")),
            "title": normalize_city_name(str(evidence.get("titleCity") or "")),
            "visible": normalize_city_name(str(evidence.get("visibleCity") or "")),
        }
        city_sources = [
            source
            for source, observed in city_values.items()
            if observed and observed == expected_city
        ]
        conflicts: list[str] = []
        if any(observed and observed != expected_city for observed in city_values.values()):
            conflicts.append("city")
        if expected_code and code and code != str(expected_code):
            conflicts.append("code")

        query_values = {
            "input": str(evidence.get("inputQuery") or "").strip(),
            "url": str(evidence.get("urlQuery") or "").strip(),
        }
        query_sources = [
            source
            for source, observed in query_values.items()
            if observed and observed == expected_query
        ]
        if any(observed and observed != expected_query for observed in query_values.values()):
            conflicts.append("query")

        try:
            card_count = max(0, int(evidence.get("jobCardCount") or 0))
        except (TypeError, ValueError):
            card_count = 0
        no_results = evidence.get("noResults") is True
        result_surface = evidence.get("resultSurface") is True and (
            card_count > 0 or no_results
        )
        verified = bool(
            code.isdigit()
            and city_values["control"] == expected_city
            and len(city_sources) >= 2
            and query_values["input"] == expected_query
            and query_sources
            and result_surface
            and not conflicts
        )
        return {
            "verified": verified,
            "city": expected_city,
            "code": code,
            "city_sources": city_sources,
            "query_sources": query_sources,
            "result_state": "jobs" if card_count > 0 else ("no_results" if no_results else "unknown"),
            "conflicts": sorted(set(conflicts)),
        }

    def _load(self) -> dict[str, Any]:
        empty = {"schema_version": CITY_CACHE_SCHEMA_VERSION, "cities": {}}
        if not self.cache_path.exists():
            return empty
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return empty
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != CITY_CACHE_SCHEMA_VERSION
            or not isinstance(payload.get("cities"), dict)
        ):
            return empty
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        payload["schema_version"] = CITY_CACHE_SCHEMA_VERSION
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.cache_path)
