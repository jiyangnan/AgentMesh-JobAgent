"""Tests for FilterEngine."""

from jobagent.domain.filter import FilterEngine
from jobagent.domain.models import Job
from jobagent.infra.config import FilterConfig


class TestFilterEngine:
    def _make_job(
        self,
        name: str = "AI产品经理",
        salary: str = "50-80K",
        experience: str = "3-5年",
        degree: str = "本科",
        company: str = "字节跳动",
        city: str = "深圳",
    ) -> Job:
        return Job(
            name=name, salary=salary, company=company, area="", experience=experience,
            degree=degree, skills="", boss="", city=city, url="", platform="zhipin",
        )

    def test_keyword_exclusion(self) -> None:
        engine = FilterEngine()
        config = FilterConfig(exclude_keywords=["销售"])
        jobs = [
            self._make_job(name="AI产品经理"),
            self._make_job(name="销售经理"),
        ]
        result = engine.apply(jobs, config)
        assert len(result) == 1
        assert result[0].name == "AI产品经理"

    def test_company_keyword_exclusion(self) -> None:
        engine = FilterEngine()
        config = FilterConfig(exclude_keywords=["保险"])
        jobs = [
            self._make_job(company="字节跳动"),
            self._make_job(company="平安保险"),
        ]
        result = engine.apply(jobs, config)
        assert len(result) == 1
        assert result[0].company == "字节跳动"

    def test_salary_cap(self) -> None:
        engine = FilterEngine()
        config = FilterConfig(max_salary_k=40)
        jobs = [
            self._make_job(salary="30-50K"),   # max = 50 > 40 → exclude
            self._make_job(salary="20-35K"),   # max = 35 <= 40 → keep
            self._make_job(salary="15-25K"),   # max = 25 <= 40 → keep
        ]
        result = engine.apply(jobs, config)
        assert len(result) == 2
        assert result[0].salary == "20-35K"
        assert result[1].salary == "15-25K"

    def test_salary_monthly_rmb(self) -> None:
        engine = FilterEngine()
        config = FilterConfig(max_salary_k=20)
        jobs = [
            self._make_job(salary="8000-15000元/月"),  # max = 15K <= 20 → keep
            self._make_job(salary="25000-35000元/月"), # max = 35K > 20 → exclude
        ]
        result = engine.apply(jobs, config)
        assert len(result) == 1
        assert result[0].salary == "8000-15000元/月"

    def test_experience_filter(self) -> None:
        engine = FilterEngine()
        config = FilterConfig(max_experience="5年")
        jobs = [
            self._make_job(experience="3-5年"),   # ok
            self._make_job(experience="5-10年"),  # max = 10 > 5 → exclude
        ]
        result = engine.apply(jobs, config)
        assert len(result) == 1
        assert result[0].experience == "3-5年"

    def test_degree_filter(self) -> None:
        engine = FilterEngine()
        config = FilterConfig(require_degree_above="本科")
        jobs = [
            self._make_job(degree="本科"),  # ok
            self._make_job(degree="大专"),  # exclude
            self._make_job(degree="硕士"),  # ok
        ]
        result = engine.apply(jobs, config)
        assert len(result) == 2

    def test_no_filter_keeps_all(self) -> None:
        engine = FilterEngine()
        config = FilterConfig()
        jobs = [self._make_job() for _ in range(5)]
        result = engine.apply(jobs, config)
        assert len(result) == 5
