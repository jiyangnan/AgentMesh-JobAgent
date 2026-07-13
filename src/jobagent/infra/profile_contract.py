"""Client-side compatibility checks for the cloud candidate profile contract."""

from __future__ import annotations

from typing import Any


PROFILE_SCHEMA_VERSION = 1
_PROFILE_SECTIONS = {
    "basic",
    "hardSkills",
    "career",
    "softSkills",
    "preferences",
    "qualitySignals",
}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def profile_compatibility_issues(profile: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not any(section in profile for section in _PROFILE_SECTIONS):
        return ["profile uses the retired pre-0.3 field layout"]

    version = profile.get("schema_version")
    if version is not None and version != PROFILE_SCHEMA_VERSION:
        issues.append(f"schema_version must be {PROFILE_SCHEMA_VERSION}")

    hard_skills = profile.get("hardSkills")
    if hard_skills is not None and not isinstance(hard_skills, dict):
        issues.append("hardSkills must be an object")
    elif isinstance(hard_skills, dict):
        for field in ("skills", "tools", "certifications", "languages", "projects", "industries", "achievements"):
            items = hard_skills.get(field)
            if items is not None and (
                not isinstance(items, list) or any(not isinstance(item, dict) for item in items)
            ):
                issues.append(f"hardSkills.{field} must be a list of objects")

    career = profile.get("career")
    if career is not None and not isinstance(career, dict):
        issues.append("career must be an object")
    elif isinstance(career, dict):
        trend = career.get("careerTrend")
        if trend is not None and trend not in {"ascending", "stable", "declining", "flat", "unclear"}:
            issues.append("career.careerTrend is not supported")
        stability = career.get("stability")
        if stability is not None and not isinstance(stability, dict):
            issues.append("career.stability must be an object")
        elif isinstance(stability, dict):
            tenure = stability.get("avgTenure")
            if tenure is not None and not _is_number(tenure):
                issues.append("career.stability.avgTenure must be a number")

    preferences = profile.get("preferences")
    if preferences is not None and not isinstance(preferences, dict):
        issues.append("preferences must be an object")
    elif isinstance(preferences, dict):
        roles = preferences.get("targetRoles")
        if roles is not None and (
            not isinstance(roles, list) or any(not isinstance(role, dict) for role in roles)
        ):
            issues.append("preferences.targetRoles must be a list of objects")
        elif isinstance(roles, list):
            for role in roles:
                confidence = role.get("confidence")
                if confidence is not None and confidence not in {"explicit", "inferred"}:
                    issues.append("preferences.targetRoles[].confidence is not supported")
                    break

    quality = profile.get("qualitySignals")
    if quality is not None and not isinstance(quality, dict):
        issues.append("qualitySignals must be an object")
    elif isinstance(quality, dict):
        language = quality.get("language")
        if language is not None and language not in {"zh", "en", "mixed"}:
            issues.append("qualitySignals.language is not supported")
        structure = quality.get("structureScore")
        if structure is not None and structure not in {"well_structured", "moderate", "poor"}:
            issues.append("qualitySignals.structureScore is not supported")
    return issues


def require_compatible_profile(profile: dict[str, Any]) -> None:
    issues = profile_compatibility_issues(profile)
    if issues:
        details = "; ".join(issues)
        raise ValueError(
            "Saved profile is incompatible with this Job Agent version "
            f"({details}). Run `jobagent resume analyze --file <resume>` to regenerate it."
        )


def stamp_profile(profile: dict[str, Any]) -> dict[str, Any]:
    stamped = dict(profile)
    stamped["schema_version"] = PROFILE_SCHEMA_VERSION
    return stamped
