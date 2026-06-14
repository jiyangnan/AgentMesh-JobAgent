"""Zhilian read-only detail-page extraction."""

from __future__ import annotations

import json
import re
from typing import Any

from jobagent.domain.models import Job


ZHILIAN_DETAIL_SELECTOR_VERSION = "2026-06-13.0"


def build_zhilian_detail_snapshot_script() -> str:
    return f"""
    (function(){{
      const selectorVersion = "{ZHILIAN_DETAIL_SELECTOR_VERSION}";
      const href = location.href || '';
      const title = document.title || '';
      const text = (document.body && (document.body.innerText || document.body.textContent) || '').trim();
      const loginRequired = /passport|login|登录[/]注册|请登录|扫码登录|验证码登录|手机验证码|安全验证|滑块/.test(href + '\\n' + title + '\\n' + text.slice(0, 1000));
      function clean(value){{
        return String(value || '').replace(/\\s+/g, ' ').trim();
      }}
      const heading = Array.from(document.querySelectorAll('h1,h2,[class*="title"],[class*="name"]'))
        .map(el => clean(el.innerText || el.textContent || ''))
        .find(value => value.length >= 2 && value.length <= 80) || '';
      const meta = {{}};
      const rows = Array.from(document.querySelectorAll('li,span,p,div')).slice(0, 500);
      for (const el of rows) {{
        const value = clean(el.innerText || el.textContent || '');
        if (!value || value.length > 120) continue;
        if (!meta.salary && /\\d+(?:\\.\\d+)?[-~—至]\\d+(?:\\.\\d+)?(?:万|[kK])|\\d+-\\d+元|面议/.test(value)) meta.salary = value.match(/\\d+(?:\\.\\d+)?[-~—至]\\d+(?:\\.\\d+)?(?:万|[kK])(?:·\\d+薪)?|\\d+-\\d+元(?:[/]月)?|面议/)[0];
        if (!meta.experience && /(经验不限|应届|\\d+-\\d+年|\\d+年以上)/.test(value)) meta.experience = value.match(/经验不限|应届|\\d+-\\d+年|\\d+年以上/)[0];
        if (!meta.degree && /(博士|硕士|本科|大专|学历不限)/.test(value)) meta.degree = value.match(/博士|硕士|本科|大专|学历不限/)[0];
      }}
      const companyCandidates = Array.from(document.querySelectorAll('a[href*="company"],a[href*="comdetail"],[class*="company"]'))
        .map(el => clean(el.innerText || el.textContent || ''))
        .filter(value => value.length >= 2 && value.length <= 80 && !/职位|招聘|登录|立即/.test(value));
      return JSON.stringify({{
        ok: true,
        platform: 'zhilian',
        mode: 'detail_read_only',
        selectorVersion,
        url: href,
        title,
        loginRequired,
        jobTitle: heading,
        companyName: companyCandidates[0] || '',
        salary: meta.salary || '',
        experience: meta.experience || '',
        degree: meta.degree || '',
        rawText: text.slice(0, 5000)
      }});
    }})()
    """


def parse_zhilian_detail_snapshot(snapshot: dict[str, Any]) -> dict[str, str]:
    """Extract useful fields from a Zhilian detail snapshot."""
    text = re.sub(r"\s+", " ", str(snapshot.get("rawText") or "")).strip()
    fields = {
        "name": _clean(snapshot.get("jobTitle") or _from_title(snapshot.get("title"))),
        "company": _clean_company(_company_from_text(text) or snapshot.get("companyName")),
        "salary": _clean(snapshot.get("salary") or _match_first(text, [
            r"\d+(?:\.\d+)?[-~—至]\d+(?:\.\d+)?万(?:·\d+薪)?",
            r"\d+(?:\.\d+)?[-~—至]\d+(?:\.\d+)?[kK](?:·\d+薪)?",
            r"\d+-\d+元(?:/月)?(?:·\d+薪)?",
            r"面议",
        ])),
        "city": _clean(_match_first(text, [
            r"(?:北京|上海|深圳|广州|杭州|成都|武汉|南京|苏州|西安|郑州|天津|重庆|东莞|泉州|保定|沈阳|长沙|临沂|青岛|合肥|佛山|福州|厦门|济南|无锡|大连|长春|石家庄|南昌|贵阳|南宁)(?=·|\s|$)",
        ])),
        "area": _clean(_area_from_text(text)),
        "experience": _clean(snapshot.get("experience") or _match_first(text, [r"\d+-\d+年", r"\d+年以上", r"经验不限", r"应届"])),
        "degree": _clean(snapshot.get("degree") or _match_first(text, [r"博士", r"硕士", r"本科", r"大专", r"学历不限"])),
        "boss": _clean(_boss_from_text(text)),
    }
    return {key: value for key, value in fields.items() if value}


def merge_zhilian_detail_into_job(job: Job, snapshot: dict[str, Any]) -> Job:
    """Fill missing search-card fields from a read-only detail snapshot."""
    fields = parse_zhilian_detail_snapshot(snapshot)
    raw_data = dict(job.raw_data or {})
    raw_data["detail_snapshot"] = snapshot
    return Job(
        name=job.name or fields.get("name", ""),
        salary=job.salary or fields.get("salary", ""),
        company=job.company or fields.get("company", ""),
        area=job.area or fields.get("area", ""),
        experience=job.experience or fields.get("experience", ""),
        degree=job.degree or fields.get("degree", ""),
        skills=job.skills,
        boss=job.boss or fields.get("boss", ""),
        city=fields.get("city", "") or job.city,
        url=job.url,
        platform=job.platform,
        raw_data=raw_data,
    )


def unwrap_zhilian_detail_js_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict) and "raw" in result:
        try:
            parsed = json.loads(result["raw"])
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {"ok": False, "error": "detail_snapshot_parse_failed", "raw": result["raw"]}
    return result if isinstance(result, dict) else {"ok": False, "error": "detail_snapshot_empty_result"}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _from_title(value: Any) -> str:
    title = _clean(value)
    return re.split(r"[-_丨|]", title, maxsplit=1)[0].strip()


def _area_from_text(text: str) -> str:
    location = _match_first(text, [
        r"(?:北京|上海|深圳|广州|杭州|成都|武汉|南京|苏州|西安|郑州|天津|重庆|东莞|泉州|保定|沈阳|长沙|临沂|青岛|合肥|佛山|福州|厦门|济南|无锡|大连|长春|石家庄|南昌|贵阳|南宁)(?:·[\u4e00-\u9fa5A-Za-z0-9]+){1,3}",
    ])
    return location.split("·", 1)[1] if "·" in location else ""


def _company_from_text(text: str) -> str:
    patterns = [
        r"公司信息\s+([\u4e00-\u9fa5A-Za-z0-9()（）·\-]{2,80})",
        r"企业名称\s+([\u4e00-\u9fa5A-Za-z0-9()（）·\-]{2,80})",
        r"公司[:：]\s*([\u4e00-\u9fa5A-Za-z0-9()（）·\-]{2,80})",
        r"企业[:：]\s*([\u4e00-\u9fa5A-Za-z0-9()（）·\-]{2,80})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def _clean_company(value: Any) -> str:
    company = _clean(value)
    if not company:
        return ""
    for marker in (" 未融资", " 不需要融资", " 已上市", " 合资", " 民营", " · ", " 20-", " 100-"):
        if marker in company:
            company = company.split(marker, 1)[0].strip()
    labels = ("客户公司名称 ", "公司名称 ")
    for label in labels:
        if company.startswith(label):
            company = company[len(label):].strip()
    return company


def _boss_from_text(text: str) -> str:
    publisher = re.search(r"职位发布者\s+([\u4e00-\u9fa5]{2,4}(?:女士|先生|HR)?)", text)
    if publisher:
        return publisher.group(1)
    return _match_first(text, [r"[\u4e00-\u9fa5]{1,3}(?:女士|先生|HR)"])


def _match_first(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return ""
