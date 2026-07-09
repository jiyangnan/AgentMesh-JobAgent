"""Local resume analysis into the 6-category / 36-field profile shape."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHOOL_TIERS = ("top2", "985", "211", "双一流", "普通一本", "二本", "专科", "海外", "unknown")


PRODUCT_SKILLS = [
    "AI", "Agent", "LLM", "大模型", "人工智能", "产品设计", "产品规划", "PRD", "原型",
    "数据分析", "用户研究", "增长", "广告投放", "风险控制", "BI", "数据中台", "用户画像",
    "敏捷", "项目管理", "PMP", "SQL", "Python", "Java", "Hadoop", "Kylin",
    "Superset", "ELK", "ES", "A/B", "埋点", "舆情", "SaaS",
]

TOOLS = [
    "JIRA", "Tapd", "TAPD", "Axure", "Figma", "SQL", "Python", "Java", "Hadoop",
    "Kylin", "Superset", "ELK", "ES", "echarts", "HDFS", "SAFe",
]

INDUSTRY_KEYWORDS = {
    "游戏": ["游戏", "发行", "研发", "运营"],
    "互联网": ["互联网", "电商", "用户增长", "C端"],
    "人工智能": ["人工智能", "AI", "大模型", "Agent", "LLM", "机器人"],
    "大数据": ["大数据", "数据中台", "BI", "数据平台", "用户画像"],
    "广告营销": ["广告", "投放", "营销", "增长"],
    "金融科技": ["平安", "金融", "续保", "保险"],
    "教育科技": ["在线教育", "学堂在线", "教学", "高校"],
    "企业服务": ["SaaS", "企业级", "中台", "CRM"],
}

DOMAIN_KEYWORDS = [
    "数据平台", "数据中台", "BI", "用户画像", "广告投放", "风险控制", "增长",
    "AI产品", "大模型", "Agent", "智能体", "舆情监控", "教学分析",
    "项目管理", "敏捷研发", "用户行为分析", "消息中心",
]


@dataclass(frozen=True)
class LocalProfileAnalysis:
    profile: dict[str, Any]
    simplified: dict[str, Any]


def analyze_resume_local(
    resume_text: str,
    *,
    file_name: str = "",
    target_role: str | None = None,
    target_cities: list[str] | None = None,
) -> LocalProfileAnalysis:
    """Build a local 36-field profile without cloud dependencies.

    This is deterministic and deliberately conservative: exact fields come from
    resume text; inferred fields use visible evidence and simple heuristics.
    """
    text = _normalize(resume_text)
    companies = _extract_work_history(text)
    skills = _extract_skills(text)
    tools = _extract_terms(text, TOOLS)
    domains = _extract_terms(text, DOMAIN_KEYWORDS)
    industries = _extract_industries(text)
    achievements = _extract_achievements(text)
    total_experience = _extract_total_experience(text, companies)
    education = _extract_education(text)
    current_job = companies[0] if companies else {}
    target_roles = _target_roles(text, target_role)
    preferred_cities = target_cities or _target_cities(text)
    salary = _salary_expectation(text, target_roles)
    projects = _extract_projects(text, achievements, skills)

    profile = {
        "basic": {
            "name": _extract_name(text),
            "gender": _infer_gender(text),
            "age": _extract_age(text),
            "currentCity": _current_city(text, preferred_cities),
            "education": education,
            "totalExperience": total_experience,
        },
        "hardSkills": {
            "skills": [
                {"name": item, "level": _skill_level(item, text, total_experience), "yearsUsed": _skill_years(item, text, total_experience)}
                for item in skills
            ],
            "tools": tools,
            "certifications": _extract_certifications(text),
            "languages": _extract_languages(text),
            "projects": projects,
            "industries": industries,
            "domains": domains,
            "achievements": achievements,
        },
        "career": {
            "currentJob": {
                "title": current_job.get("title", ""),
                "company": current_job.get("company", ""),
                "duration": current_job.get("duration", ""),
                "level": _career_level(total_experience, text),
            },
            "careerLevel": _career_level(total_experience, text),
            "careerTrend": _career_trend(companies, text),
            "stability": _stability(companies),
            "companyBackground": _company_background(companies),
            "workHistory": companies,
            "salaryCurrent": "",
        },
        "softSkills": {
            "leadership": _leadership(text),
            "collaboration": _evidence_level(text, ["跨部门", "协调", "沟通", "合作", "业务方"]),
            "communication": _evidence_level(text, ["客户", "沟通", "汇报", "对接", "培训"]),
            "selfDriven": _evidence_level(text, ["负责", "主导", "推动", "搭建", "探索", "挖掘"]),
            "international": "none",
        },
        "preferences": {
            "targetRoles": [{"title": role, "confidence": conf, "priority": idx + 1} for idx, (role, conf) in enumerate(target_roles)],
            "targetIndustries": industries[:5],
            "targetCities": [{"city": city, "priority": idx + 1} for idx, city in enumerate(preferred_cities)],
            "salaryExpectation": salary,
            "availability": "看看新机会" if "看看新机会" in text else "unknown",
            "companyPreference": _company_preference(text),
            "workMode": "onsite_or_hybrid",
        },
        "qualitySignals": {
            "completeness": _completeness(text, skills, companies, achievements),
            "quantificationRate": _quantification_rate(achievements),
            "structureScore": _structure_score(text),
            "language": "zh-CN",
        },
        "_meta": {
            "analysisMode": "local",
            "schema": "job-agent-profile-v1",
            "fieldGroups": 6,
            "fieldCount": 36,
            "sourceFile": Path(file_name).name if file_name else "",
        },
    }
    return LocalProfileAnalysis(profile=profile, simplified=simplify_profile(profile))


def simplify_profile(profile: dict[str, Any]) -> dict[str, Any]:
    basic = profile.get("basic", {}) if isinstance(profile, dict) else {}
    hard = profile.get("hardSkills", {}) if isinstance(profile, dict) else {}
    prefs = profile.get("preferences", {}) if isinstance(profile, dict) else {}
    skills = [
        str(item.get("name", "")).strip()
        for item in hard.get("skills", [])
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    ]
    target_roles = [
        str(item.get("title", "")).strip()
        for item in prefs.get("targetRoles", [])
        if isinstance(item, dict) and str(item.get("title", "")).strip()
    ]
    cities = [
        str(item.get("city", "")).strip()
        for item in prefs.get("targetCities", [])
        if isinstance(item, dict) and str(item.get("city", "")).strip()
    ]
    salary = prefs.get("salaryExpectation") or {}
    return {
        "years_experience": _to_int(basic.get("totalExperience"), 0),
        "target_roles": target_roles,
        "skills": skills,
        "preferred_cities": cities,
        "salary_expectation": {
            "min_k": _to_int(salary.get("minK") or salary.get("min_k"), 0),
            "max_k": _to_int(salary.get("maxK") or salary.get("max_k"), 0),
        },
        "industry_preferences": [str(item).strip() for item in hard.get("industries", []) if str(item).strip()],
        "exclusions": [],
    }


def profile_context(profile: dict[str, Any]) -> dict[str, Any]:
    hard = profile.get("hardSkills", {}) if isinstance(profile, dict) else {}
    career = profile.get("career", {}) if isinstance(profile, dict) else {}
    soft = profile.get("softSkills", {}) if isinstance(profile, dict) else {}
    return {
        "achievements": hard.get("achievements", [])[:5],
        "domains": hard.get("domains", [])[:6],
        "industries": hard.get("industries", [])[:5],
        "career_level": career.get("careerLevel", ""),
        "leadership": soft.get("leadership", {}),
    }


def _normalize(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def _extract_name(text: str) -> str:
    for line in _nonempty_lines(text)[:20]:
        if re.fullmatch(r"[\u4e00-\u9fa5]{1,4}(?:先生|女士)", line):
            return line
    for line in _nonempty_lines(text):
        if re.fullmatch(r"[\u4e00-\u9fa5]{1,4}(?:先生|女士)", line):
            return line
    m = re.search(r"(?:姓名[:：]\s*)?([\u4e00-\u9fa5]{1,4}(?:先生|女士))", text)
    return m.group(1).strip() if m else ""


def _infer_gender(text: str) -> str:
    if "先生" in text:
        return "male"
    if "女士" in text:
        return "female"
    return "unknown"


def _extract_age(text: str) -> int:
    m = re.search(r"(\d{2})岁", text)
    return int(m.group(1)) if m else 0


def _extract_total_experience(text: str, companies: list[dict[str, Any]]) -> int:
    m = re.search(r"工作\s*(\d+)\s*年", text)
    if m:
        return int(m.group(1))
    years = []
    for company in companies:
        years.extend(int(y) for y in re.findall(r"(20\d{2}|19\d{2})", company.get("duration", "")))
    if len(years) >= 2:
        return max(years) - min(years)
    return 0


def _extract_education(text: str) -> dict[str, Any]:
    degree = "本科" if "本科" in text else ("硕士" if "硕士" in text else ("专科" if "专科" in text else "unknown"))
    school = ""
    for line in _nonempty_lines(text):
        if (
            re.fullmatch(r"[\u4e00-\u9fa5]{2,12}大学", line)
            and not line.startswith(("与", "和", "及"))
        ):
            school = line
            break
    if not school:
        m = re.search(r"(?:毕业院校|学校)[:：]\s*([\u4e00-\u9fa5]{2,12}大学)", text)
        if m:
            school = m.group(1)
    major = "计算机科学与技术" if "计算机科学与技术" in text else ""
    tier = "双一流" if "双一流" in text else "unknown"
    if "985" in text:
        tier = "985"
    elif "211" in text:
        tier = "211"
    return {
        "degree": degree,
        "school": school,
        "major": major,
        "schoolTier": tier if tier in SCHOOL_TIERS else "unknown",
        "graduationYear": _first_int(re.findall(r"(20\d{2})/0?6|毕业", text), 0),
    }


def _extract_work_history(text: str) -> list[dict[str, Any]]:
    items = []
    lines = _nonempty_lines(text)
    for idx, line in enumerate(lines):
        if not re.fullmatch(r"(?:19|20)\d{2}/\d{2}[-~至到][^\n]{2,20}", line):
            continue
        company = _clean_company_line(lines[idx - 1] if idx > 0 else "")
        title = _next_title_after_duration(lines, idx)
        if not company or not _looks_like_company(company) or not _looks_like_title(title):
            continue
        items.append({
            "company": company,
            "duration": line.strip(),
            "title": title.strip(),
            "level": "",
        })
        if len(items) >= 8:
            break
    return items[:8]


def _nonempty_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _clean_company_line(line: str) -> str:
    line = re.sub(r"^[\d一二三四五六七八九十、.．\s]+", "", line).strip()
    return line.splitlines()[-1].strip()


def _looks_like_company(line: str) -> bool:
    if not (2 <= len(line) <= 40):
        return False
    if any(bad in line for bad in ("负责", "项目", "内容", "职责", "不支持", "其他各类事务")):
        return False
    return any(
        key in line
        for key in ("有限公司", "科技", "中国平安", "大学", "信息技术", "股份", "叠纸", "星云纵横", "思特奇")
    )


def _looks_like_title(line: str) -> bool:
    if not (2 <= len(line) <= 30):
        return False
    if re.fullmatch(r"[\u4e00-\u9fa5]{1,4}(?:先生|女士)", line):
        return False
    return any(key in line for key in ("产品", "负责人", "经理", "工程师", "主管", "总监", "专家", "顾问"))


def _next_title_after_duration(lines: list[str], duration_idx: int) -> str:
    for offset in range(1, 8):
        idx = duration_idx + offset
        if idx >= len(lines):
            break
        line = lines[idx].strip()
        if not line or line in ("工作经历", "教育经历"):
            continue
        if "·" in line or re.fullmatch(r"[A-Za-z0-9_.-]{3,}", line):
            continue
        if re.fullmatch(r"[\u4e00-\u9fa5]{1,4}(?:先生|女士)", line):
            continue
        if _looks_like_title(line):
            return line
    return lines[duration_idx + 1].strip() if duration_idx + 1 < len(lines) else ""


def _extract_skills(text: str) -> list[str]:
    found = _extract_terms(text, PRODUCT_SKILLS)
    if "AI" in found and "人工智能" in found:
        found.remove("人工智能")
    return found[:18]


def _extract_terms(text: str, terms: list[str]) -> list[str]:
    lowered = text.lower()
    found = []
    for term in terms:
        if term.lower() in lowered and term not in found:
            found.append(term)
    return found


def _extract_industries(text: str) -> list[str]:
    found = []
    for industry, keywords in INDUSTRY_KEYWORDS.items():
        if any(keyword.lower() in text.lower() for keyword in keywords):
            found.append(industry)
    return found


def _extract_achievements(text: str) -> list[str]:
    signals = ("完成", "负责", "主导", "搭建", "上线", "提升", "签约", "交付", "构建", "实现", "培养")
    achievements = []
    current = ""
    for raw in [line.strip(" -•\t") for line in text.splitlines()]:
        line = raw.strip()
        if not line:
            if current:
                achievements.append(_clean_achievement(current))
                current = ""
            continue
        is_header = _is_resume_header_line(line)
        starts_new = bool(re.match(r"^(?:\d+[、.．]|[一二三四五六七八九十]+、)", line))
        has_signal = any(sig in line for sig in signals)
        if is_header:
            if current:
                achievements.append(_clean_achievement(current))
                current = ""
            continue
        if has_signal:
            if current and starts_new:
                achievements.append(_clean_achievement(current))
                current = line
            elif current and len(current) < 160 and not starts_new:
                current += line
            else:
                current = line
        elif current and len(current) < 160 and not starts_new:
            current += line
        else:
            if current:
                achievements.append(_clean_achievement(current))
                current = ""
        if len(achievements) >= 8:
            break
    if current and len(achievements) < 8:
        achievements.append(_clean_achievement(current))
    achievements = [item for item in achievements if 12 <= len(item) <= 180]
    return achievements[:8]


def _is_resume_header_line(line: str) -> bool:
    return (
        bool(re.search(r"(?:19|20)\d{2}/\d{2}", line))
        or _looks_like_company(line)
        or line in ("工作经历", "教育经历", "内容:", "业绩:")
    )


def _merge_wrapped_resume_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        starts_new = bool(re.match(r"^(?:\d+[、.．]|[一二三四五六七八九十]+、)", line))
        is_header = _is_resume_header_line(line)
        if merged and not starts_new and not is_header and len(merged[-1]) < 150:
            merged[-1] += line
        else:
            merged.append(line)
    return merged


def _clean_achievement(line: str) -> str:
    line = re.sub(r"^\d+[、.．]\s*", "", line).strip()
    line = re.sub(r"\s+", " ", line)
    return line.rstrip("；;。")


def _extract_projects(text: str, achievements: list[str], skills: list[str]) -> list[dict[str, Any]]:
    names = []
    for keyword in ("数据平台", "数据中台", "小木机器人", "教学大数据分析平台", "全球舆情信息管理系统", "海外大数据内容分析平台", "BI可视化"):
        if keyword in text:
            names.append(keyword)
    return [
        {
            "name": name,
            "role": "产品负责人" if "负责人" in text or "主产品经理" in text else "产品经理",
            "description": f"{name}相关项目经验",
            "achievements": achievements[:2],
            "metrics": _extract_metrics(" ".join(achievements)),
            "techStack": skills[:6],
            "duration": "",
        }
        for name in names[:6]
    ]


def _extract_metrics(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?\s*(?:万|亿|%|人|年|个月|K|k|万\+|百万)", text)[:8]


def _extract_certifications(text: str) -> list[str]:
    certs = []
    if "PMP" in text:
        certs.append("PMP项目管理认证")
    return certs


def _extract_languages(text: str) -> list[str]:
    langs = []
    if "英语" in text:
        langs.append("英语")
    if "普通话" in text:
        langs.append("普通话")
    return langs


def _skill_level(skill: str, text: str, years: int) -> str:
    if skill in ("AI", "产品设计", "数据分析", "数据中台", "BI", "项目管理") and years >= 5:
        return "expert"
    if skill.lower() in text.lower():
        return "proficient"
    return "familiar"


def _skill_years(skill: str, text: str, years: int) -> int:
    if skill in ("数据分析", "BI", "数据中台", "项目管理"):
        return min(years, 8) if years else 5
    if skill in ("AI", "Agent", "LLM", "大模型"):
        return 2 if skill in text else 1
    return min(years, 5) if years else 1


def _career_level(years: int, text: str) -> str:
    if any(term in text for term in ("负责人", "管理", "团队", "VP", "总监")):
        return "manager"
    if years >= 8:
        return "staff"
    if years >= 5:
        return "senior"
    if years >= 2:
        return "mid"
    return "junior"


def _career_trend(companies: list[dict[str, Any]], text: str) -> str:
    if any(term in text for term in ("负责人", "高级", "主产品经理", "管理")):
        return "upward"
    return "stable"


def _stability(companies: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(companies)
    return {
        "avgTenure": "",
        "jobCount": count,
        "riskLevel": "low" if count <= 5 else "medium",
    }


def _company_background(companies: list[dict[str, Any]]) -> list[dict[str, str]]:
    result = []
    for item in companies[:6]:
        company = item.get("company", "")
        tier = "wellKnown" if any(k in company for k in ("平安", "叠纸", "京东", "腾讯", "字节")) else "known"
        scale = "large" if tier == "wellKnown" else "medium"
        result.append({"company": company, "scale": scale, "tier": tier})
    return result


def _leadership(text: str) -> dict[str, Any]:
    m = re.search(r"带领\s*(\d+)\s*余?人", text)
    team_size = int(m.group(1)) if m else (3 if "团队" in text or "管理" in text else 0)
    return {"hasManaged": team_size > 0, "teamSize": team_size, "levels": "team_lead" if team_size > 0 else "individual"}


def _evidence_level(text: str, keywords: list[str]) -> str:
    count = sum(text.count(keyword) for keyword in keywords)
    if count >= 6:
        return "strong"
    if count >= 2:
        return "moderate"
    return "weak"


def _target_roles(text: str, target_role: str | None) -> list[tuple[str, float]]:
    roles = []
    if target_role:
        roles.append((target_role, 0.98))
    for role in ("AI产品经理", "Agent产品经理", "数据产品负责人", "高级产品经理", "产品负责人", "产品经理"):
        if role in text or ("AI" in role and "AI" in text) or ("数据" in role and "数据" in text):
            if role not in [r[0] for r in roles]:
                roles.append((role, 0.9 if role != "产品经理" else 0.75))
    return roles[:6] or [("产品经理", 0.7)]


def _target_cities(text: str) -> list[str]:
    cities = []
    for city in ("深圳", "北京", "上海", "杭州", "广州"):
        if city in text and city not in cities:
            cities.append(city)
    return cities[:3] or ["深圳", "北京"]


def _salary_expectation(text: str, roles: list[tuple[str, float]]) -> dict[str, Any]:
    m = re.search(r"(\d{2})\s*[-~到]\s*(\d{2})\s*[Kk]", text)
    if m:
        return {"minK": int(m.group(1)), "maxK": int(m.group(2)), "currency": "CNY", "period": "monthly"}
    if any("负责人" in role for role, _ in roles):
        return {"minK": 40, "maxK": 70, "currency": "CNY", "period": "monthly"}
    return {"minK": 30, "maxK": 60, "currency": "CNY", "period": "monthly"}


def _company_preference(text: str) -> list[str]:
    prefs = []
    if "大模型" in text or "AI" in text:
        prefs.append("AI/大模型方向")
    if "数据" in text:
        prefs.append("数据驱动型团队")
    if "企业级" in text or "中台" in text:
        prefs.append("B端/平台型产品")
    return prefs


def _current_city(text: str, cities: list[str]) -> str:
    m = re.search(r"(北京|上海|深圳|广州|杭州|成都|武汉|西安|郑州)", text)
    return m.group(1) if m else (cities[0] if cities else "")


def _completeness(text: str, skills: list[str], companies: list[dict[str, Any]], achievements: list[str]) -> dict[str, Any]:
    missing = []
    if not skills:
        missing.append("hardSkills.skills")
    if not companies:
        missing.append("career.workHistory")
    if not achievements:
        missing.append("hardSkills.achievements")
    score = max(0.0, 1.0 - len(missing) * 0.15)
    if len(text) < 1000:
        score = min(score, 0.75)
    return {"score": round(score, 2), "missingFields": missing}


def _quantification_rate(achievements: list[str]) -> float:
    if not achievements:
        return 0.0
    quantified = sum(1 for item in achievements if re.search(r"\d", item))
    return round(quantified / len(achievements), 2)


def _structure_score(text: str) -> float:
    signals = sum(1 for sig in ("工作经历", "教育经历", "项目", "业绩", "内容", "资格证书") if sig in text)
    return round(min(1.0, 0.45 + signals * 0.1), 2)


def _first_int(values: Any, default: int = 0) -> int:
    if isinstance(values, list) and values:
        try:
            return int(values[0])
        except Exception:
            return default
    return default


def _to_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    digits = re.findall(r"\d+", str(value))
    return int(digits[0]) if digits else default
