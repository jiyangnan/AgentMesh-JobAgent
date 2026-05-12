"""Tests for profile data validation / coercion."""

import pytest

from jobagent.domain.profile_builder import ProfileBuilder
from jobagent.domain.models import CandidateProfile


class TestProfileBuilder:
    def test_build_full(self) -> None:
        raw = {
            "years_experience": 10,
            "target_roles": ["AI产品经理", "高级产品经理"],
            "skills": ["Python", "AI"],
            "preferred_cities": ["深圳", "杭州"],
            "salary_expectation": {"min_k": 50, "max_k": 80},
            "industry_preferences": ["人工智能"],
            "exclusions": ["销售"],
        }
        profile = ProfileBuilder.build(raw)
        assert profile.years_experience == 10
        assert profile.target_roles == ["AI产品经理", "高级产品经理"]
        assert profile.salary_expectation == {"min_k": 50, "max_k": 80}

    def test_build_missing_fields_use_defaults(self) -> None:
        raw = {}
        profile = ProfileBuilder.build(raw)
        assert profile.years_experience == 0
        assert profile.target_roles == []
        assert profile.skills == []
        assert profile.salary_expectation == {"min_k": 0, "max_k": 0}

    def test_salary_min_max_swapped(self) -> None:
        raw = {"salary_expectation": {"min_k": 80, "max_k": 50}}
        profile = ProfileBuilder.build(raw)
        assert profile.salary_expectation["min_k"] == 50
        assert profile.salary_expectation["max_k"] == 80

    def test_salary_max_zero_infers_double(self) -> None:
        raw = {"salary_expectation": {"min_k": 30, "max_k": 0}}
        profile = ProfileBuilder.build(raw)
        assert profile.salary_expectation["max_k"] == 60

    def test_string_years_coerced(self) -> None:
        raw = {"years_experience": "5年"}
        profile = ProfileBuilder.build(raw)
        assert profile.years_experience == 5

    def test_comma_separated_skills(self) -> None:
        raw = {"skills": "Python, AI, LLM"}
        profile = ProfileBuilder.build(raw)
        assert profile.skills == ["Python", "AI", "LLM"]

    def test_none_values_become_defaults(self) -> None:
        raw = {
            "years_experience": None,
            "target_roles": None,
            "skills": None,
            "salary_expectation": None,
        }
        profile = ProfileBuilder.build(raw)
        assert profile.years_experience == 0
        assert profile.target_roles == []
        assert profile.skills == []
        assert profile.salary_expectation == {"min_k": 0, "max_k": 0}
