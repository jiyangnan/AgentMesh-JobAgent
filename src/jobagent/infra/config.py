"""Configuration management — YAML loading + typed config dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from jobagent.domain.models import Job


@dataclass
class FilterConfig:
    """Filter configuration"""
    exclude_keywords: list[str] = field(default_factory=list)
    max_salary_k: int = 0
    max_experience: Optional[str] = None
    require_degree_above: str = "中专"


@dataclass
class CandidateConfig:
    """Candidate profile configuration for ranking"""
    years_experience: int = 0
    target_roles: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    preferred_cities: list[str] = field(default_factory=list)
    salary_expectation: dict = field(default_factory=dict)  # {"min_k": 50, "max_k": 80}
    industry_preferences: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)


@dataclass
class GreeterConfig:
    """Greeter configuration"""
    enabled: bool = False
    dry_run: bool = False
    template: str = ""
    verify: bool = True

    def get_template(self, job: Job) -> str:
        """Render greeting template with job variables.

        Supports: {job_name}, {company}, {boss}, {experience},
                  {salary}, {area}, {skills}, {city}.
        Unknown variables are left as-is (e.g. {unknown}).
        """
        class _SafeDict(dict):
            def __missing__(self, key: str) -> str:
                return "{" + key + "}"

        try:
            return self.template.format_map(_SafeDict(
                job_name=job.name,
                company=job.company,
                boss=job.boss,
                experience=job.experience,
                salary=job.salary,
                area=job.area,
                skills=job.skills,
                city=job.city,
            ))
        except (KeyError, ValueError):
            return self.template


@dataclass
class CrawlerConfig:
    """Crawler configuration"""
    queries: list[str] = field(default_factory=list)
    cities: list[dict] = field(default_factory=list)
    pages_per_query: int = 5


@dataclass
class Config:
    """Main configuration"""
    platform: str = "zhipin"
    crawler: CrawlerConfig = field(default_factory=CrawlerConfig)
    filter: FilterConfig = field(default_factory=FilterConfig)
    greeter: GreeterConfig = field(default_factory=GreeterConfig)
    candidate: CandidateConfig = field(default_factory=CandidateConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> Config:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # If candidate section is missing in YAML, try loading from profile.json
        candidate_data = data.get("candidate", {})
        if not candidate_data:
            from jobagent.infra.state import load_json, profile_path
            profile = load_json(profile_path())
            if profile:
                candidate_data = profile
        if candidate_data and ("basic" in candidate_data or "hardSkills" in candidate_data or "preferences" in candidate_data):
            from jobagent.domain.profile_builder import ProfileBuilder

            profile = ProfileBuilder.build(candidate_data)
            candidate_data = {
                "years_experience": profile.years_experience,
                "target_roles": profile.target_roles,
                "skills": profile.skills,
                "preferred_cities": profile.preferred_cities,
                "salary_expectation": profile.salary_expectation,
                "industry_preferences": profile.industry_preferences,
                "exclusions": profile.exclusions,
            }

        return cls(
            platform=data.get("platform", "zhipin"),
            crawler=CrawlerConfig(**data.get("crawler", {})),
            filter=FilterConfig(**data.get("filter", {})),
            greeter=GreeterConfig(**data.get("greeter", {})),
            candidate=CandidateConfig(**candidate_data),
        )
