from __future__ import annotations

import json
from pathlib import Path

import pytest

import jobagent.cli as cli
from jobagent.cli import _cmd_greet_preview, _cmd_jobs_rank, _cmd_resume_analyze, build_parser
from jobagent.domain.local_profile import analyze_resume_local, simplify_profile
from jobagent.domain.profile_builder import ProfileBuilder


RESUME_TEXT = """
冀先生
在职，看看新机会 · 31岁 · 本科 · 工作10年3个月
郑州大学
双一流
计算机科学与技术
上海叠纸科技有限公司
2023/01-至今
产品负责人
负责规划公司整体数据平台系统，建设统一的数据采集、处理、查询、治理平台。
负责 deviceid 与 oneid 产品体系，并赋能 AI 要素、用户研究、BI 系统、广告投放系统。
负责管理数据产品团队，确保团队目标一致。
麦吉太文（北京）科技有限公司
2020/09-2023/01
广告投放产品负责人
负责广告投放管理平台和数据集成平台产品工作，推进数据治理平台建设。
中国平安
2019/04-2020/03
数据产品部负责人
构建数据分析平台，实现 BI 决策子系统、用户轨迹分析子系统、续保追踪管理子系统。
北京慕华信息科技有限公司
2017/10-2019/04
人工智能产品经理
负责人工智能项目“小木机器人”，与清华大学实验室合作，促进研究成果商业化。
负责教学大数据分析平台，重构三端数据埋点策略。
项目业绩：建行数据中心签约800万进行定制化支持。
资格证书
PMP项目管理认证
英语（商务洽谈）；普通话（商务洽谈）
"""


def parse_args(*args: str):
    return build_parser().parse_args(list(args))


def test_analyze_resume_local_outputs_36_field_shape():
    analysis = analyze_resume_local(
        RESUME_TEXT,
        file_name="resume.txt",
        target_role="AI产品经理",
        target_cities=["深圳", "北京"],
    )

    profile = analysis.profile
    assert profile["_meta"]["analysisMode"] == "local"
    assert profile["_meta"]["fieldCount"] == 36
    assert set(["basic", "hardSkills", "career", "softSkills", "preferences", "qualitySignals"]).issubset(profile)
    assert profile["basic"]["name"] == "冀先生"
    assert profile["basic"]["education"]["school"] == "郑州大学"
    assert profile["career"]["workHistory"][0]["company"] == "上海叠纸科技有限公司"
    assert profile["career"]["workHistory"][0]["title"] == "产品负责人"
    assert all("\n" not in item["company"] for item in profile["career"]["workHistory"])
    assert profile["hardSkills"]["achievements"]
    assert any(item["title"] == "AI产品经理" for item in profile["preferences"]["targetRoles"])

    simplified = simplify_profile(profile)
    assert simplified["years_experience"] >= 10
    assert "AI产品经理" in simplified["target_roles"]
    assert "数据平台" in simplified["skills"] or "数据分析" in simplified["skills"]


def test_profile_builder_accepts_36_field_profile():
    profile = analyze_resume_local(RESUME_TEXT).profile

    candidate = ProfileBuilder.build(profile)

    assert candidate.years_experience >= 10
    assert candidate.target_roles
    assert candidate.skills
    assert candidate.salary_expectation["min_k"] > 0


def test_analyze_resume_local_handles_pdf_text_order_noise():
    noisy_pdf_text = """
上海叠纸科技有限公司
2023/01-至今
产品负责人
中国平安
2019/04-2020/03
冀先生
在职，看看新机会 · 31岁 · 本科 · 工作10年3个月
arises_fighter
工作经历
数据产品部负责人
负责构建数据分析平台，实现 BI 决策子系统。
2、与清华大学实验室合作，进行产研结合。
郑州大学
双一流
2010/09-2014/06
教育经历
计算机科学与技术
本科
"""

    profile = analyze_resume_local(noisy_pdf_text).profile

    assert profile["basic"]["name"] == "冀先生"
    assert profile["basic"]["education"]["school"] == "郑州大学"
    assert profile["career"]["workHistory"][1]["company"] == "中国平安"
    assert profile["career"]["workHistory"][1]["title"] == "数据产品部负责人"


def test_resume_analyze_local_saves_36_field_profile(monkeypatch, tmp_path):
    resume_path = tmp_path / "resume.txt"
    profile_path = tmp_path / "profile.json"
    resume_path.write_text(RESUME_TEXT, encoding="utf-8")
    monkeypatch.setattr("jobagent.infra.state.profile_path", lambda: profile_path)
    monkeypatch.setattr("jobagent.infra.credentials.load_license_key", lambda: None)
    args = parse_args(
        "resume",
        "analyze",
        "--local",
        "--file",
        str(resume_path),
        "--target-role",
        "AI产品经理",
        "--target-cities",
        "深圳",
        "北京",
    )

    _cmd_resume_analyze(args)

    saved = json.loads(profile_path.read_text(encoding="utf-8"))
    assert saved["_meta"]["fieldCount"] == 36
    assert saved["preferences"]["targetCities"][0]["city"] == "深圳"


def test_local_rank_uses_36_field_profile_evidence(monkeypatch, tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(analyze_resume_local(RESUME_TEXT).profile, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("jobagent.infra.state.profile_path", lambda: profile_path)
    input_path = tmp_path / "jobs.json"
    output_path = tmp_path / "ranked.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "AI数据产品经理",
                    "salary": "40-70K",
                    "company": "Example AI",
                    "city": "深圳",
                    "experience": "5-10年",
                    "skills": "AI, 数据平台, BI",
                    "url": "https://example.test/job/1",
                    "platform": "zhipin",
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    args = parse_args("boss", "rank", "--local", "--input", str(input_path), "--output", str(output_path))

    _cmd_jobs_rank(args)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["profile_via"] == "local_36_fields"
    assert payload["jobs"][0]["profile_evidence"]
    assert any("36维画像" in reason for reason in payload["jobs"][0]["reasons"])


def test_local_greet_uses_36_field_profile_material(monkeypatch, tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(analyze_resume_local(RESUME_TEXT).profile, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("jobagent.infra.state.profile_path", lambda: profile_path)
    input_path = tmp_path / "ranked.json"
    output_path = tmp_path / "ready.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "AI数据产品经理",
                    "salary": "40-70K",
                    "company": "Example AI",
                    "city": "深圳",
                    "experience": "5-10年",
                    "skills": "AI, 数据平台, BI",
                    "url": "https://example.test/job/1",
                    "platform": "zhipin",
                    "score": 88,
                    "match_level": "high",
                    "reasons": ["36维画像领域匹配：过往有数据平台经验"],
                    "risk_flags": [],
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    args = parse_args(
        "boss",
        "greet",
        "preview",
        "--local",
        "--config",
        str(tmp_path / "missing.yaml"),
        "--input",
        str(input_path),
        "--output",
        str(output_path),
    )

    _cmd_greet_preview(args)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    greeting = payload["jobs"][0]["greeting"]
    assert "数据" in greeting or "AI" in greeting
    assert "希望进一步沟通" in greeting
    assert payload["jobs"][0]["greeting_source"] == "local"


def test_local_greet_prioritizes_36_profile_over_template(monkeypatch, tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(analyze_resume_local(RESUME_TEXT).profile, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("jobagent.infra.state.profile_path", lambda: profile_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "greeter:\n"
        "  template: \"{boss}您好，模板招呼语。\"\n",
        encoding="utf-8",
    )
    input_path = tmp_path / "ranked.json"
    output_path = tmp_path / "ready.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "AI数据产品经理",
                    "salary": "40-70K",
                    "company": "Example AI",
                    "city": "深圳",
                    "experience": "5-10年",
                    "skills": "AI, 数据平台, BI",
                    "boss": "陈先生",
                    "url": "https://example.test/job/1",
                    "platform": "zhipin",
                    "score": 88,
                    "match_level": "high",
                    "reasons": [],
                    "risk_flags": [],
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    args = parse_args(
        "boss",
        "greet",
        "preview",
        "--local",
        "--config",
        str(config_path),
        "--input",
        str(input_path),
        "--output",
        str(output_path),
    )

    _cmd_greet_preview(args)

    greeting = json.loads(output_path.read_text(encoding="utf-8"))["jobs"][0]["greeting"]
    assert "模板招呼语" not in greeting
    assert "数据" in greeting or "AI" in greeting
