"""Profile Builder — validates and coerces raw profile data into CandidateProfile.

NOTE: This module does NOT call any LLM. Resume text analysis is the agent's
responsibility (the agent already has its own LLM). This module only provides
data validation, type coercion, and CandidateProfile construction.
"""

from __future__ import annotations

from typing import Any

from jobagent.domain.models import CandidateProfile


class ProfileBuilder:
    """Builds a CandidateProfile from raw dict data (produced by the agent's LLM).

    The agent is responsible for:
    1. Calling `jobagent resume extract --file resume.pdf` to get text
    2. Using its own LLM to analyze the text and produce a JSON dict
    3. Calling `jobagent profile save --data '{...}'` to persist

    This class only handles step 3: validation + coercion.
    """

    @staticmethod
    def build(raw: dict[str, Any]) -> CandidateProfile:
        """Validate and coerce raw profile data into a CandidateProfile.

        Args:
            raw: Dict produced by the agent's LLM analysis.

        Returns:
            A validated CandidateProfile with sensible defaults.
        """
        # Salary expectation with validation
        salary_raw = raw.get("salary_expectation") or {}
        min_k = _to_int(salary_raw.get("min_k"), 0)
        max_k = _to_int(salary_raw.get("max_k"), 0)
        if min_k > max_k and max_k > 0:
            min_k, max_k = max_k, min_k
        if max_k == 0:
            max_k = min_k * 2 if min_k > 0 else 0

        return CandidateProfile(
            years_experience=_to_int(raw.get("years_experience"), 0),
            target_roles=_to_str_list(raw.get("target_roles")),
            skills=_to_str_list(raw.get("skills")),
            preferred_cities=_to_str_list(raw.get("preferred_cities")),
            salary_expectation={"min_k": min_k, "max_k": max_k},
            industry_preferences=_to_str_list(raw.get("industry_preferences")),
            exclusions=_to_str_list(raw.get("exclusions")),
        )


def _to_int(value: Any, default: int = 0) -> int:
    """Coerce value to int with fallback."""
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        digits = "".join(c for c in value if c.isdigit())
        return int(digits) if digits else default
    if isinstance(value, float):
        return int(value)
    return default


def _to_str_list(value: Any) -> list[str]:
    """Coerce value to list of strings with fallback."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if v is not None and str(v).strip()]
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return []
