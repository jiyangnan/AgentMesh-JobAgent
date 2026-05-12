"""Tests for resume text extraction."""

from pathlib import Path

import pytest

from jobagent.domain.resume_parser import ResumeParser


class TestResumeParser:
    def test_parse_txt(self, tmp_path: Path) -> None:
        parser = ResumeParser()
        f = tmp_path / "resume.txt"
        f.write_text(
            "张三\n10年产品经理经验，深耕AI和SaaS领域\n"
            "目标岗位: AI产品经理, 高级产品经理, 产品总监\n"
            "技能: Python, AI, LLM, 产品设计, 数据分析, 用户研究, 敏捷开发, 项目管理\n"
            "期望城市: 深圳, 杭州, 上海\n"
            "期望薪资: 50K-80K\n"
            "行业偏好: 人工智能, 互联网, 企业服务\n"
            "教育背景: 北京大学计算机科学本科\n"
            "工作经历:\n"
            "- 2015-2018 腾讯 高级产品经理\n"
            "- 2018-2021 字节跳动 AI产品负责人\n"
            "- 2021-至今 某AI创业公司 产品VP\n"
            "项目经验:\n"
            "- 主导某大模型对话产品设计，DAU 100万+\n"
            "- 负责企业级SaaS平台从0到1建设\n"
            "自我评价: 对AI产品有深刻理解和丰富实战经验\n",
            encoding="utf-8",
        )
        text = parser.parse(f)
        assert "张三" in text
        assert "10年产品经理经验" in text
        assert len(text) > 100

    def test_parse_md(self, tmp_path: Path) -> None:
        parser = ResumeParser()
        f = tmp_path / "resume.md"
        f.write_text(
            "# 张三的简历\n\n"
            "## 工作经历\n\n"
            "- 2015-2018 腾讯 高级产品经理\n"
            "- 2018-2021 字节跳动 AI产品负责人\n"
            "- 2021-至今 某AI创业公司 产品VP\n\n"
            "## 技能\n\n"
            "Python, AI, LLM, 产品设计, 数据分析, 用户研究\n\n"
            "## 项目经验\n\n"
            "- 主导某大模型对话产品设计，DAU 100万+\n"
            "- 负责企业级SaaS平台从0到1建设\n\n"
            "## 自我评价\n\n"
            "对AI产品有深刻理解和丰富实战经验\n",
            encoding="utf-8",
        )
        text = parser.parse(f)
        assert "腾讯" in text
        assert "字节跳动" in text
        assert len(text) > 100

    def test_clean_text_collapses_blank_lines(self, tmp_path: Path) -> None:
        parser = ResumeParser()
        f = tmp_path / "resume.txt"
        f.write_text(
            "Line 1\n\n\n\n\nLine 2\n"
            "Additional content to make the text longer than 100 characters. "
            "This ensures the parser does not raise a short-text error. "
            "Python AI LLM product design data analysis user research.\n",
            encoding="utf-8",
        )
        text = parser.parse(f)
        # Multiple blank lines should be collapsed to at most 2
        assert "\n\n\n" not in text

    def test_short_file_raises(self, tmp_path: Path) -> None:
        parser = ResumeParser()
        f = tmp_path / "short.txt"
        f.write_text("Hi", encoding="utf-8")
        with pytest.raises(ValueError, match="too short"):
            parser.parse(f)

    def test_unsupported_extension_raises(self, tmp_path: Path) -> None:
        parser = ResumeParser()
        f = tmp_path / "resume.png"
        f.write_text("fake", encoding="utf-8")
        with pytest.raises(ValueError, match="Unsupported"):
            parser.parse(f)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        parser = ResumeParser()
        with pytest.raises(FileNotFoundError):
            parser.parse(tmp_path / "nonexistent.pdf")
