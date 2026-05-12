"""Tests for RankingEngine rule-based scoring."""

from jobagent.domain.models import CandidateProfile, Job
from jobagent.domain.ranking import RankingEngine


class TestRankingEngine:
    def _make_profile(self) -> CandidateProfile:
        return CandidateProfile(
            years_experience=5,
            target_roles=["AI产品经理", "产品经理"],
            skills=["Python", "AI", "LLM"],
            preferred_cities=["深圳", "杭州"],
            salary_expectation={"min_k": 40, "max_k": 70},
            industry_preferences=["人工智能", "互联网"],
        )

    def _make_job(
        self,
        name: str = "AI产品经理",
        salary: str = "50-80K",
        experience: str = "3-5年",
        city: str = "深圳",
        company: str = "字节跳动",
        skills: str = "Python, AI",
    ) -> Job:
        return Job(
            name=name, salary=salary, company=company, area="", experience=experience,
            degree="本科", skills=skills, boss="", city=city, url="", platform="zhipin",
        )

    def test_perfect_match_high_score(self) -> None:
        profile = self._make_profile()
        engine = RankingEngine(profile)
        job = self._make_job(
            name="AI产品经理",
            salary="50-70K",
            experience="3-5年",
            city="深圳",
            skills="Python, AI, LLM",
        )
        score, reasons, risks = engine.score_job(job)
        assert score >= 75
        assert any("深圳" in r for r in reasons)

    def test_city_mismatch_lower_score(self) -> None:
        profile = self._make_profile()
        engine = RankingEngine(profile)
        # Same job but in a non-matching city — city score = 0
        mismatch_job = self._make_job(city="哈尔滨")
        match_job = self._make_job(city="深圳")
        mismatch_score, _, _ = engine.score_job(mismatch_job)
        match_score, _, _ = engine.score_job(match_job)
        assert mismatch_score < match_score
        # City mismatch should drop the score by at least 10 points
        assert match_score - mismatch_score >= 10

    def test_salary_below_expectation(self) -> None:
        profile = self._make_profile()
        engine = RankingEngine(profile)
        job = self._make_job(salary="15-20K")
        score, reasons, risks = engine.score_job(job)
        assert any("薪资" in r for r in risks)

    def test_rank_sorts_descending(self) -> None:
        profile = self._make_profile()
        engine = RankingEngine(profile)
        jobs = [
            self._make_job(name="销售", salary="10-15K", city="哈尔滨"),
            self._make_job(name="AI产品经理", salary="50-70K", city="深圳"),
            self._make_job(name="产品经理", salary="40-60K", city="杭州"),
        ]
        ranked = engine.rank(jobs, top_n=3)
        assert len(ranked) == 3
        assert ranked[0].score >= ranked[1].score >= ranked[2].score

    def test_match_level_thresholds(self) -> None:
        profile = self._make_profile()
        engine = RankingEngine(profile)

        high_job = self._make_job(name="AI产品经理", salary="50-70K", city="深圳", skills="Python, AI, LLM")
        med_job = self._make_job(name="产品经理", salary="30-50K", city="广州", skills="产品")
        low_job = self._make_job(name="销售", salary="10-15K", city="哈尔滨")

        high = engine.rank([high_job], top_n=1)[0]
        med = engine.rank([med_job], top_n=1)[0]
        low = engine.rank([low_job], top_n=1)[0]

        assert high.match_level == "high"
        assert med.match_level in ("medium", "high")
        assert low.match_level == "low"

    def test_empty_profile_gives_mid_scores(self) -> None:
        profile = CandidateProfile()
        engine = RankingEngine(profile)
        job = self._make_job()
        score, _, _ = engine.score_job(job)
        assert 30 <= score <= 70
