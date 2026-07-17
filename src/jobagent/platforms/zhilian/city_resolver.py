"""Discover and verify Zhilian city codes before candidate data leaves the browser."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from jobagent.infra.state import APP_DIR

CITY_CACHE_SCHEMA_VERSION = 1
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

    def remember(self, city: str, code: str, *, evidence_url: str) -> None:
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
        observed_code = city_code_from_url(observed_url)
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
        code_matches = bool(observed_code and (not expected_code or observed_code == expected_code))
        cards_match = matching > 0 or not (snapshot.get("cards") or [])
        verified = bool(code_matches and cards_match)
        return {
            "ok": verified,
            "mode": "zhilian_city_resolution",
            "city": normalized,
            "source": source,
            "expectedCode": expected_code,
            "observedCode": observed_code,
            "observedUrl": observed_url,
            "matchingCards": matching,
            "mismatchedCardCities": sorted(set(mismatches)),
            "verified": verified,
        }
