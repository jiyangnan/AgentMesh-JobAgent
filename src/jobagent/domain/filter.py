"""Filter Engine — multi-dimensional job filtering."""

from __future__ import annotations

import re
from typing import Optional

from jobagent.domain.models import Job
from jobagent.infra.config import FilterConfig


class FilterEngine:
    """Filter jobs based on salary, experience, keywords, etc."""

    DEGREE_RANK = {
        "不限": 0,
        "初中及以下": 1,
        "中专": 2,
        "中技": 3,
        "高中": 4,
        "大专": 5,
        "本科": 6,
        "硕士": 7,
        "博士": 8,
    }

    def _parse_salary(self, salary_str: str) -> Optional[int]:
        """Parse salary string like '30-50K·16薪' → max_k value (50).

        Handles '8000-15000元/月' → divide by 1000.
        """
        if not salary_str:
            return None
        is_monthly_rmb = '元' in salary_str and 'K' not in salary_str.upper()
        numbers = re.findall(r'(\d+)', salary_str.replace(',', ''))
        if not numbers:
            return None
        val = max(int(n) for n in numbers)
        if is_monthly_rmb:
            val = val / 1000
        return val

    def _parse_experience(self, exp_str: str) -> Optional[int]:
        """Parse experience string like '3-5年' → max years (5)"""
        if not exp_str or exp_str in ("不限", "无要求", "应届毕业生"):
            return 0
        numbers = re.findall(r'(\d+)', exp_str)
        if not numbers:
            return None
        return max(int(n) for n in numbers)

    def _experience_ok(self, job_exp: str, max_exp: Optional[str]) -> bool:
        if not max_exp:
            return True
        job_years = self._parse_experience(job_exp)
        max_years = self._parse_experience(max_exp)
        if job_years is None or max_years is None:
            return True
        return job_years <= max_years

    def _degree_ok(self, job_degree: str, min_degree: str) -> bool:
        job_rank = self.DEGREE_RANK.get(job_degree, 0)
        min_rank = self.DEGREE_RANK.get(min_degree, 0)
        return job_rank >= min_rank

    def apply(self, jobs: list[Job], config: FilterConfig) -> list[Job]:
        """Apply all filters to job list and return filtered jobs."""
        results = []
        for job in jobs:
            # Keyword blacklist
            if config.exclude_keywords:
                if any(kw in job.name for kw in config.exclude_keywords):
                    continue
                if any(kw in job.company for kw in config.exclude_keywords):
                    continue

            # Salary cap
            if config.max_salary_k > 0:
                salary = self._parse_salary(job.salary)
                if salary is not None and salary > config.max_salary_k:
                    continue

            # Experience
            if not self._experience_ok(job.experience, config.max_experience):
                continue

            # Degree
            if not self._degree_ok(job.degree, config.require_degree_above):
                continue

            results.append(job)
        return results
