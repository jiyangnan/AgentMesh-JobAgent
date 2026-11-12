"""Validate user-supplied geography. Platform routing remains server-owned."""
from __future__ import annotations

import re
from typing import Any

MARKETS = {"mainland_china", "outside_mainland_china"}
SCOPES = {*MARKETS, "mixed"}
KINDS = {"country", "subdivision", "city", "metro", "global", "unspecified"}


def _name(value: Any, maximum: int) -> str:
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        raise ValueError("Target locations must be readable text without control characters")
    value = " ".join(value.split())
    if not 1 <= len(value) <= maximum or not any(char.isalpha() for char in value):
        raise ValueError("Target locations must be readable names")
    return value


def normalize_targets(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"version", "scope", "locations", "workplace_types"}:
        raise ValueError("Unsupported target fields; supply job-search geography only")
    if type(value.get("version", 1)) is not int or value.get("version", 1) != 1:
        raise ValueError("Unsupported targeting version")
    scope = value.get("scope")
    if scope is not None and (not isinstance(scope, str) or scope not in SCOPES):
        raise ValueError("Choose mainland_china, outside_mainland_china or mixed")
    locations = value.get("locations", [])
    modes = value.get("workplace_types", [])
    if not isinstance(locations, list) or len(locations) > 6:
        raise ValueError("Supply up to six target locations")
    if (not isinstance(modes, list) or len(modes) > 3
            or any(not isinstance(mode, str) or mode not in {"onsite", "hybrid", "remote"} for mode in modes)
            or len(set(modes)) != len(modes)):
        raise ValueError("Workplace types must be distinct onsite, hybrid or remote values")
    result: dict[str, Any] = {"version": 1, "locations": [], "workplace_types": list(modes)}
    if scope is not None:
        result["scope"] = scope
    seen = set()
    for item in locations:
        if not isinstance(item, dict) or set(item) - {
            "label", "market", "kind", "country_code", "subdivision", "locality", "radius_km"
        }:
            raise ValueError("Invalid target location fields")
        market, kind = item.get("market"), item.get("kind", "unspecified")
        if not isinstance(market, str) or market not in MARKETS or not isinstance(kind, str) or kind not in KINDS:
            raise ValueError("Each location needs its explicit market and a valid location kind")
        if scope in MARKETS and market != scope:
            raise ValueError("Target locations conflict with the selected region")
        location = {"label": _name(item.get("label"), 160), "market": market, "kind": kind}
        code = item.get("country_code")
        if code is not None:
            if not isinstance(code, str) or not re.fullmatch(r"[A-Z]{2}", code):
                raise ValueError("Country/territory code must have two uppercase letters")
            if (code == "CN") != (market == "mainland_china"):
                raise ValueError("Country/territory code conflicts with the selected region")
            location["country_code"] = code
        for key in ("subdivision", "locality"):
            if item.get(key) is not None:
                location[key] = _name(item[key], 120)
        radius = item.get("radius_km")
        if radius is not None:
            if type(radius) is not int or not 0 <= radius <= 500 or kind not in {"city", "metro"}:
                raise ValueError("A radius must be 0–500 km for a confirmed city or metro")
            location["radius_km"] = radius
        if kind == "global" and set(location) - {"label", "market", "kind"}:
            raise ValueError("Global targets cannot carry a single locality or radius")
        key = tuple((name, val.casefold() if name == "label" else val) for name, val in sorted(location.items()))
        if key in seen:
            raise ValueError("Target locations must be distinct")
        seen.add(key)
        result["locations"].append(location)
    return result
