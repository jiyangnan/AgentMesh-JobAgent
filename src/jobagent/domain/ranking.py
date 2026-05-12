"""Ranking Engine — rule-based scoring + optional LLM semantic rerank."""

from __future__ import annotations

import re
from typing import Callable, Optional

from jobagent.domain.models import CandidateProfile, Job, RankedJob

# ── 省份/相邻城市映射（简化版） ──────────────────────────────
_CITY_PROVINCE: dict[str, str] = {
    "深圳": "广东", "广州": "广东", "东莞": "广东", "佛山": "广东",
    "珠海": "广东", "惠州": "广东", "中山": "广东",
    "北京": "北京",
    "上海": "上海",
    "杭州": "浙江", "宁波": "浙江", "温州": "浙江",
    "成都": "四川", "重庆": "重庆",
    "南京": "江苏", "苏州": "江苏", "无锡": "江苏",
    "武汉": "湖北",
    "西安": "陕西",
    "长沙": "湖南",
    "厦门": "福建", "福州": "福建",
}


def _parse_salary_range(salary_str: str) -> Optional[tuple[float, float]]:
    """Parse '30-50K·16薪' → (30.0, 50.0), unit = K.

    Handles formats:
      - '30-50K·16薪'  → (30.0, 50.0)
      - '8000-15000元/月' → (8.0, 15.0)
      - '10-15K'         → (10.0, 15.0)
    """
    if not salary_str:
        return None
    is_monthly_rmb = '元' in salary_str and 'K' not in salary_str.upper()
    m = re.findall(r'(\d+)', salary_str.replace(',', ''))
    if len(m) >= 2:
        lo, hi = float(m[0]), float(m[1])
    elif len(m) == 1:
        lo = hi = float(m[0])
    else:
        return None
    if is_monthly_rmb:
        lo, hi = lo / 1000, hi / 1000
    return lo, hi


def _parse_experience_range(exp_str: str) -> Optional[tuple[int, int]]:
    """Parse '3-5年' → (3, 5). Returns None for 不限/应届."""
    if not exp_str or exp_str in ("不限", "无要求", "经验不限"):
        return 0, 99
    if "应届" in exp_str:
        return 0, 1
    m = re.findall(r'(\d+)', exp_str)
    if len(m) >= 2:
        return int(m[0]), int(m[1])
    if len(m) == 1:
        v = int(m[0])
        return v, v
    return None


class RankingEngine:
    """AI 精选排序引擎 — 规则打分 + 可选 LLM 语义排序.

    Scoring dimensions:
        - 城市匹配:  0-20
        - 薪资匹配:  0-25
        - 经验匹配:  0-20
        - 关键词匹配: 0-20
        - 行业匹配:  0-15
    """

    def __init__(self, profile: CandidateProfile, llm_callable: Optional[Callable] = None):
        self.profile = profile
        self.llm = llm_callable

    # ── Dimension scoring ─────────────────────────────────────

    def _score_city(self, job: Job) -> float:
        """城市匹配: 20 = 完全匹配, 10 = 同省, 0 = 不匹配."""
        if not self.profile.preferred_cities:
            return 10  # 无偏好给一半分
        if job.city in self.profile.preferred_cities:
            return 20
        # 同省份
        job_prov = _CITY_PROVINCE.get(job.city, "")
        for c in self.profile.preferred_cities:
            if _CITY_PROVINCE.get(c, "") == job_prov and job_prov:
                return 10
        return 0

    def _score_salary(self, job: Job) -> float:
        """薪资匹配: 25 = 在范围内, 20 = 高于上限, 15 = 低于下限10%以内, 5 = 低于10%以上, 12 = 无法解析."""
        if not self.profile.salary_expectation:
            return 12  # 无期望给中间分
        sal_range = _parse_salary_range(job.salary)
        if sal_range is None:
            return 12
        job_min, job_max = sal_range

        min_k = self.profile.salary_expectation.get("min_k", 0)
        max_k = self.profile.salary_expectation.get("max_k", 999)
        if min_k <= 0 and max_k >= 999:
            return 12

        # 岗位薪资范围与期望有交集
        if job_min >= min_k and job_min <= max_k:
            return 25
        if job_max >= min_k and job_max <= max_k:
            return 25
        if job_min <= max_k and job_max >= min_k:
            return 25

        # 高于期望上限
        if job_min > max_k:
            return 20

        # 低于期望下限
        if job_max < min_k:
            gap_ratio = (min_k - job_max) / min_k if min_k > 0 else 1
            return 15 if gap_ratio <= 0.10 else 5

        return 12

    def _score_experience(self, job: Job) -> float:
        """经验匹配: 20 = 候选人经验在岗位要求范围内, 15 = 岗位上限>=候选经验, 10 = 部分匹配, 0 = 不匹配."""
        if self.profile.years_experience <= 0:
            return 10  # 未设定经验，给中间分
        exp_range = _parse_experience_range(job.experience)
        if exp_range is None:
            return 10

        req_min, req_max = exp_range
        candidate = self.profile.years_experience

        # 候选人经验在岗位要求范围内
        if req_min <= candidate <= req_max:
            return 20
        # 岗位要求上限 >= 候选经验（候选可能略超但可接受）
        if req_max >= candidate:
            return 15
        # 候选经验略高于岗位上限（经验丰富不算坏事）
        if candidate <= req_max + 3:
            return 15
        # 候选经验远超岗位要求
        return 10

    def _score_keywords(self, job: Job) -> float:
        """关键词匹配: 按 target_roles 和 skills 匹配岗位标题和技能标签."""
        if not self.profile.target_roles and not self.profile.skills:
            return 10

        text = f"{job.name} {job.skills} {job.company}".lower()
        matched = 0
        total = len(self.profile.target_roles) + len(self.profile.skills)
        if total == 0:
            return 10

        for role in self.profile.target_roles:
            if role.lower() in text:
                matched += 1
        for skill in self.profile.skills:
            if skill.lower() in text:
                matched += 1

        ratio = matched / total
        return min(20, ratio * 20)

    def _score_industry(self, job: Job) -> float:
        """行业匹配: 候选人偏好的行业出现在公司名/岗位描述中."""
        if not self.profile.industry_preferences:
            return 7  # 无偏好给一半分
        text = f"{job.company} {job.name} {job.skills}".lower()
        for ind in self.profile.industry_preferences:
            if ind.lower() in text:
                return 15
        return 0

    # ── Composite scoring ─────────────────────────────────────

    def score_job(self, job: Job) -> tuple[float, list[str], list[str]]:
        """对单个岗位打分，返回 (score, reasons, risk_flags)."""
        city_s = self._score_city(job)
        salary_s = self._score_salary(job)
        exp_s = self._score_experience(job)
        kw_s = self._score_keywords(job)
        ind_s = self._score_industry(job)

        total = city_s + salary_s + exp_s + kw_s + ind_s
        reasons, risks = self._generate_reasons(job, city_s, salary_s, exp_s, kw_s, ind_s)
        return total, reasons, risks

    def _generate_reasons(
        self,
        job: Job,
        city_s: float,
        salary_s: float,
        exp_s: float,
        kw_s: float,
        ind_s: float,
    ) -> tuple[list[str], list[str]]:
        """基于各维度得分生成推荐理由和风险提示."""
        reasons: list[str] = []
        risks: list[str] = []

        # 城市维度
        if city_s >= 20:
            reasons.append(f"城市匹配：{job.city} 在您的目标城市中")
        elif city_s >= 10:
            reasons.append(f"城市相邻：{job.city} 与目标城市同省")

        # 薪资维度
        if self.profile.salary_expectation:
            min_k = self.profile.salary_expectation.get("min_k", 0)
            max_k = self.profile.salary_expectation.get("max_k", 999)
            if salary_s >= 25:
                reasons.append(f"薪资匹配您的期望（{min_k}-{max_k}K）：{job.salary}")
            elif salary_s >= 20:
                reasons.append(f"薪资高于您的期望上限：{job.salary}")
            elif salary_s >= 15:
                reasons.append(f"薪资接近您的期望下限：{job.salary}")
                risks.append("薪资略低于您期望的下限")
            elif salary_s <= 5:
                risks.append(f"薪资低于您期望的下限较多（期望 {min_k}K+）：{job.salary}")

        # 经验维度
        if exp_s >= 20:
            reasons.append(f"经验要求与您的背景匹配：{job.experience}")
        elif exp_s >= 15:
            reasons.append(f"经验要求基本匹配：{job.experience}")
        elif exp_s <= 10 and self.profile.years_experience > 0:
            risks.append("该岗位经验要求可能偏低")

        # 关键词维度
        if kw_s >= 15:
            matched_roles = [r for r in self.profile.target_roles if r.lower() in f"{job.name} {job.skills}".lower()]
            matched_skills = [s for s in self.profile.skills if s.lower() in f"{job.name} {job.skills}".lower()]
            tags = matched_roles + matched_skills
            if tags:
                reasons.append(f"岗位方向与您的 {'/'.join(tags[:3])} 经验高度相关")
        elif kw_s >= 10:
            reasons.append("岗位方向与您的技能部分匹配")

        # 行业维度
        if ind_s >= 15:
            reasons.append(f"行业方向匹配您的偏好")
        elif ind_s <= 0 and self.profile.industry_preferences:
            risks.append("公司行业与您的偏好不完全匹配")

        # 确保至少有 1 条 reason
        if not reasons:
            reasons.append("综合评分通过初步筛选")

        return reasons[:3], risks[:2]

    # ── Main rank API ─────────────────────────────────────────

    def rank(self, jobs: list[Job], top_n: int = 20) -> list[RankedJob]:
        """规则打分排序 + 可选 AI 重排."""
        # 1. 规则打分
        ranked: list[RankedJob] = []
        for job in jobs:
            score, reasons, risks = self.score_job(job)
            match_level = "high" if score >= 75 else ("medium" if score >= 50 else "low")
            ranked.append(RankedJob(
                job=job, score=score, match_level=match_level,
                reasons=reasons, risk_flags=risks,
            ))

        # 2. 按 score 降序
        ranked.sort(key=lambda r: r.score, reverse=True)

        # 3. 可选 AI 重排 — 对 Top 30 做 LLM 语义重排
        if self.llm is not None and len(ranked) > 0:
            top_for_ai = ranked[:30]
            reranked = self._ai_rerank(top_for_ai, min(top_n, len(top_for_ai)))
            # 拼接：AI 重排后的 + 剩余的
            reranked_ids = {rj.job.url for rj in reranked}
            rest = [rj for rj in ranked if rj.job.url not in reranked_ids]
            ranked = reranked + rest

        # 4. 取 Top N
        return ranked[:top_n]

    def _ai_rerank(self, ranked_jobs: list[RankedJob], top_n: int) -> list[RankedJob]:
        """用 LLM 做语义重排（可选）."""
        if self.llm is None or not ranked_jobs:
            return ranked_jobs[:top_n]

        # 构造 prompt
        job_lines = []
        for i, rj in enumerate(ranked_jobs):
            j = rj.job
            job_lines.append(
                f"[{i}] {j.name} @ {j.company} | {j.salary} | {j.experience} | {j.city} | {j.skills}"
            )
        profile_desc = (
            f"候选人画像：{self.profile.years_experience}年经验，"
            f"目标岗位 {self.profile.target_roles}，"
            f"技能 {self.profile.skills}，"
            f"期望城市 {self.profile.preferred_cities}，"
            f"期望薪资 {self.profile.salary_expectation}K，"
            f"行业偏好 {self.profile.industry_preferences}"
        )
        prompt = (
            f"你是求职匹配专家。根据候选人画像，对以下岗位按匹配度从高到低重新排序。\n\n"
            f"{profile_desc}\n\n"
            f"岗位列表：\n" + "\n".join(job_lines) +
            "\n\n请返回重新排序后的岗位编号列表（从高到低），只返回 JSON 数组，例如 [2, 0, 5, 1, ...]"
        )

        try:
            raw = self.llm(prompt)
            # 解析 LLM 返回的索引列表
            text = raw.strip() if isinstance(raw, str) else str(raw)
            # 提取 JSON 数组
            m = re.search(r'\[.*?\]', text, re.DOTALL)
            if m:
                import json
                indices = json.loads(m.group())
                index_set = set()
                result = []
                for idx in indices:
                    idx = int(idx)
                    if 0 <= idx < len(ranked_jobs) and idx not in index_set:
                        result.append(ranked_jobs[idx])
                        index_set.add(idx)
                # 补上 LLM 没提到的
                for i, rj in enumerate(ranked_jobs):
                    if i not in index_set:
                        result.append(rj)
                return result[:top_n]
        except Exception:
            pass  # LLM 失败则保持规则排序

        return ranked_jobs[:top_n]
