from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DoctorReport:
    status: str
    checks: list[CheckResult]
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "created_at": self.created_at,
            "checks": [c.to_dict() for c in self.checks],
        }


@dataclass
class SendAttempt:
    job_url: str
    message: str
    delivered: bool
    created_at: str = field(default_factory=now_iso)
    steps: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Job:
    """Standardized job data model"""
    name: str
    salary: str
    company: str
    area: str
    experience: str
    degree: str
    skills: str
    boss: str
    city: str
    url: str
    platform: str = "zhipin"
    raw_data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "salary": self.salary,
            "company": self.company,
            "area": self.area,
            "experience": self.experience,
            "degree": self.degree,
            "skills": self.skills,
            "boss": self.boss,
            "city": self.city,
            "url": self.url,
            "platform": self.platform,
        }


@dataclass
class RankedJob:
    """精选岗位结果 — AI 打分排序后"""
    job: Job
    score: float           # 0-100 综合匹配分
    match_level: str       # "high" / "medium" / "low"
    reasons: list[str]     # 推荐理由（2-3条）
    risk_flags: list[str]  # 风险提示（0-2条）

    def to_dict(self) -> dict:
        d = self.job.to_dict()
        d.update({
            "score": round(self.score, 1),
            "match_level": self.match_level,
            "reasons": self.reasons,
            "risk_flags": self.risk_flags,
        })
        return d


@dataclass
class CandidateProfile:
    """候选人画像 — 用于岗位匹配打分"""
    years_experience: int = 0
    target_roles: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    preferred_cities: list[str] = field(default_factory=list)
    salary_expectation: dict = field(default_factory=dict)  # {"min_k": 50, "max_k": 80}
    industry_preferences: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)

    @classmethod
    def from_config(cls, config) -> "CandidateProfile":
        """从 Config 对象构造简化画像"""
        profile_data = getattr(config, "candidate", None)
        if profile_data is None:
            return cls()
        return cls(
            years_experience=profile_data.years_experience,
            target_roles=profile_data.target_roles,
            skills=profile_data.skills,
            preferred_cities=profile_data.preferred_cities,
            salary_expectation=profile_data.salary_expectation,
            industry_preferences=profile_data.industry_preferences,
            exclusions=profile_data.exclusions,
        )


@dataclass
class GreetResult:
    """Result of a greet operation"""
    job: Job
    status: str  # ok | failed | retry
    greeting: str
    verified: bool = False
    error_msg: str = ""
    greeted_at: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.job.name,
            "company": self.job.company,
            "url": self.job.url,
            "status": self.status,
            "greeting": self.greeting,
            "verified": self.verified,
            "error_msg": self.error_msg,
            "greeted_at": self.greeted_at,
        }
