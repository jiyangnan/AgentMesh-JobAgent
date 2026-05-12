"""Tests for greeter template variable substitution."""

from jobagent.domain.models import Job
from jobagent.infra.config import GreeterConfig


class TestGreeterTemplate:
    def _make_job(self) -> Job:
        return Job(
            name="AI产品经理",
            salary="50-80K·16薪",
            company="字节跳动",
            area="南山·科技园",
            experience="3-5年",
            degree="本科",
            skills="Python, AI, 产品设计",
            boss="张经理 · HR",
            city="深圳",
            url="https://example.com/job/1",
        )

    def test_all_variables(self) -> None:
        config = GreeterConfig(
            template="{job_name}@{company} {boss} {experience} {salary} {area} {skills} {city}"
        )
        job = self._make_job()
        result = config.get_template(job)
        assert "AI产品经理" in result
        assert "字节跳动" in result
        assert "张经理 · HR" in result
        assert "3-5年" in result
        assert "50-80K·16薪" in result
        assert "南山·科技园" in result
        assert "Python, AI, 产品设计" in result
        assert "深圳" in result

    def test_unknown_variable_preserved(self) -> None:
        config = GreeterConfig(template="Hello {unknown_var}")
        job = self._make_job()
        result = config.get_template(job)
        assert "{unknown_var}" in result

    def test_empty_template(self) -> None:
        config = GreeterConfig(template="")
        job = self._make_job()
        result = config.get_template(job)
        assert result == ""

    def test_no_variables(self) -> None:
        config = GreeterConfig(template="固定文本，无变量")
        job = self._make_job()
        result = config.get_template(job)
        assert result == "固定文本，无变量"
