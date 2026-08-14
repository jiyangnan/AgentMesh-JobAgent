"""Public-safe reviewability checks for user-visible job delivery items."""

from __future__ import annotations

import re
from typing import Any


GENERIC_JOB_TITLES = frozenset(
    {
        "更多",
        "更多信息",
        "了解更多",
        "查看",
        "查看信息",
        "查看详情",
        "查看更多",
        "查看更多信息",
        "查看职位",
        "查看职位详情",
        "职位详情",
        "详情",
        "展开",
        "点击查看",
    }
)

_INVALID_COMPANY_LABELS = frozenset(
    {
        "立即投递",
        "立即沟通",
        "继续沟通",
        "投递",
        "沟通",
        "高回复率",
        "刚刚活跃",
        "今日回复",
    }
)

_SALARY_PATTERN = re.compile(
    r"(?:\d+(?:\.\d+)?\s*[-~—至]\s*\d+(?:\.\d+)?\s*"
    r"(?:万|千|[kK]|元)|面议)"
)


def clean_review_field(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def is_reviewable_job_title(value: Any) -> bool:
    title = clean_review_field(value)
    return bool(2 <= len(title) <= 80 and title not in GENERIC_JOB_TITLES)


def is_reviewable_company(value: Any) -> bool:
    company = clean_review_field(value)
    return bool(2 <= len(company) <= 120 and company not in _INVALID_COMPANY_LABELS)


def is_reviewable_salary(value: Any) -> bool:
    salary = clean_review_field(value)
    return bool(salary and _SALARY_PATTERN.search(salary))


def delivery_reviewability_issues(item: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not is_reviewable_job_title(item.get("title") or item.get("name")):
        issues.append("title")
    if not is_reviewable_company(item.get("company")):
        issues.append("company")
    if not is_reviewable_salary(item.get("salary")):
        issues.append("salary")
    return issues
