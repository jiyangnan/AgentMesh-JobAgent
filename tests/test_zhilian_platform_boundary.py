from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobagent.cli import (
    _cmd_zhilian_apply_open,
    _cmd_zhilian_apply_send,
    _cmd_zhilian_audit,
    _cmd_zhilian_collect,
    _cmd_zhilian_greet_preview,
    _cmd_zhilian_rank,
    build_parser,
)
from jobagent.platforms.zhilian import (
    ZHILIAN_DETAIL_SELECTOR_VERSION,
    ZHILIAN_BROWSER_JS_USER_PROMPT,
    ZHILIAN_SELECTOR_VERSION,
    ZHILIAN_LOGIN_USER_PROMPT,
    ZhilianApplyOpener,
    ZhilianApplySender,
    ZhilianAuditLog,
    ZhilianReadOnlyCollector,
    ZhilianSessionGuide,
    build_zhilian_detail_snapshot_script,
    build_zhilian_search_url,
    build_zhilian_snapshot_script,
    collect_zhilian_fixture,
    merge_zhilian_detail_into_job,
    parse_zhilian_detail_snapshot,
    parse_zhilian_job,
    zhilian_job_id,
)


FIXTURE = Path(__file__).parent / "fixtures" / "zhilian" / "search_results_page1.json"
REAL_SHAPE_SNAPSHOT_FIXTURE = Path(__file__).parent / "fixtures" / "zhilian" / "live_snapshot_real_shape_20260613.json"
DETAIL_SNAPSHOT_FIXTURE = Path(__file__).parent / "fixtures" / "zhilian" / "job_detail_snapshot_20260613.json"


def parse_args(*args: str):
    return build_parser().parse_args(list(args))


def test_zhilian_parser_uses_platform_boundary_fixture():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]["results"][0]

    job = parse_zhilian_job(raw)

    assert zhilian_job_id(raw) == "CC123456780J00123456789"
    assert job.platform == "zhilian"
    assert job.name == "AI产品经理"
    assert job.salary == "35-55K"
    assert job.company == "智联样例科技"
    assert job.city == "深圳"
    assert job.area == "南山区"
    assert job.experience == "5-10年"
    assert job.degree == "本科"
    assert job.skills == "AI产品, 数据分析, 用户增长"
    assert job.boss == "王女士"
    assert job.url == "https://www.zhaopin.com/jobdetail/CC123456780J00123456789.htm"


def test_zhilian_collect_fixture_outputs_shared_jobs():
    jobs = collect_zhilian_fixture(FIXTURE)

    assert len(jobs) == 1
    assert jobs[0].platform == "zhilian"
    assert jobs[0].name == "AI产品经理"


def test_zhilian_collect_fixture_accepts_real_shape_snapshot():
    jobs = collect_zhilian_fixture(REAL_SHAPE_SNAPSHOT_FIXTURE)

    assert len(jobs) == 3
    assert [job.name for job in jobs] == ["AI产品经理", "AI产品经理", "产品经理"]
    assert jobs[0].company == "京东集团"
    assert jobs[0].salary == "2-4万·16薪"
    assert jobs[0].area == "通州·台湖"
    assert jobs[0].experience == "3-5年"
    assert jobs[0].degree == "本科"
    assert jobs[1].company == "企福云寰球(福建)智慧科技有限公司"
    assert jobs[1].salary == "1.3-2.5万"
    assert all("/jobdetail/" in job.url for job in jobs)


def test_zhilian_parser_prefers_card_city_over_search_city():
    raw = {
        "jobTitle": "AI产品经理",
        "jobUrl": "http://www.zhaopin.com/jobdetail/CC1.htm",
        "cityName": "北京",
        "rawText": "AI产品经理 2-4万·16薪 北京·通州·台湖 3-5年 本科 京东集团 立即投递",
    }

    job = parse_zhilian_job(raw, city_name="深圳")

    assert job.city == "北京"
    assert job.area == "通州·台湖"


def test_zhilian_parser_prefers_raw_location_over_noisy_city_name():
    raw = {
        "jobTitle": "it产品经理",
        "jobUrl": "http://www.zhaopin.com/jobdetail/CC1.htm",
        "cityName": "上海",
        "rawText": "it产品经理 1.2-1.6万 长沙·开福·伍家岭 3-5年 本科 上海邦芒人力资源有限公司 立即投递",
    }

    job = parse_zhilian_job(raw, city_name="深圳")

    assert job.city == "长沙"
    assert job.area == "开福·伍家岭"


def test_zhilian_detail_snapshot_fills_missing_card_fields():
    snapshot = json.loads(DETAIL_SNAPSHOT_FIXTURE.read_text(encoding="utf-8"))
    card_job = parse_zhilian_job({
        "jobTitle": "初级AI大模型产品经理",
        "jobUrl": "https://www.zhaopin.com/jobdetail/ZL-DETAIL-1.htm",
        "rawText": "初级AI大模型产品经理 9000-12000元 深圳 经验不限 本科 立即投递",
    })

    fields = parse_zhilian_detail_snapshot(snapshot)
    merged = merge_zhilian_detail_into_job(card_job, snapshot)

    assert fields["company"] == "深圳佰信时代数字科技有限公司"
    assert fields["area"] == "宝安·西乡"
    assert merged.company == "深圳佰信时代数字科技有限公司"
    assert merged.area == "宝安·西乡"
    assert merged.boss == "李女士"


def test_zhilian_detail_snapshot_cleans_real_company_and_publisher():
    snapshot = {
        "ok": True,
        "title": "初级AI大模型产品经理招聘_广州麦炳逸科技有限公司招聘 - 智联招聘",
        "companyName": "广州麦炳逸科技有限公司 未融资 · 20-99人 · 产业互联网平台 已审核",
        "rawText": (
            "冀先生 初级AI大模型产品经理 9000-12000元 深圳 宝安区 经验不限 本科 "
            "公司信息 广州麦炳逸科技有限公司 未融资 · 20-99人 · 产业互联网平台 "
            "职位发布者 牛女士 刚刚活跃 人事经理"
        ),
    }

    fields = parse_zhilian_detail_snapshot(snapshot)

    assert fields["company"] == "广州麦炳逸科技有限公司"
    assert fields["boss"] == "牛女士"


def test_zhilian_search_url_and_snapshot_script_contract():
    url = build_zhilian_search_url("AI 产品经理", city="深圳", page=2)
    script = build_zhilian_snapshot_script(limit=7)
    detail_script = build_zhilian_detail_snapshot_script()

    assert url.startswith("https://sou.zhaopin.com/?")
    assert "kw=AI%20%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86" in url
    assert "jl=%E6%B7%B1%E5%9C%B3" in url
    assert "p=2" in url
    assert ZHILIAN_SELECTOR_VERSION in script
    assert "loginRequired" in script
    assert "candidateCount" in script
    assert "cards" in script
    assert "navLabels" in script
    assert "hasJobSignal" in script
    assert "ctaLabels" in script
    assert "titleFrom" in script
    assert "cardRoot" in script
    assert "hasAction" in script
    assert "hasCompanySignal" in script
    assert "[/]jobdetail[/]" in script
    assert ZHILIAN_DETAIL_SELECTOR_VERSION in detail_script
    assert "detail_read_only" in detail_script
    assert "loginRequired" in detail_script


class FakeZhilianDriver:
    def __init__(self):
        self.calls: list[str] = []

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        self.calls.append(f"open:{url}:{wait_seconds}")
        return {"ok": True, "url": url}

    def _exec_js(self, js_code: str):
        self.calls.append("extract_snapshot")
        return {
            "ok": True,
            "platform": "zhilian",
            "selectorVersion": ZHILIAN_SELECTOR_VERSION,
            "url": "https://sou.zhaopin.com/?kw=AI",
            "title": "智联招聘",
            "loginRequired": False,
            "candidateCount": 1,
            "cards": [
                {
                    "positionId": "ZL-1",
                    "jobTitle": "AI商业化产品经理",
                    "companyName": "Zhilian Live Example",
                    "salary": "35-60K",
                    "cityName": "深圳",
                    "jobUrl": "https://www.zhaopin.com/jobdetail/ZL-1.htm",
                }
            ],
        }


class DetailHydrationZhilianDriver:
    def __init__(self):
        self.calls: list[str] = []

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        self.calls.append(f"open:{url}:{wait_seconds}")
        return {"ok": True, "url": url}

    def _exec_js(self, js_code: str):
        self.calls.append("extract_detail" if "detail_read_only" in js_code else "extract_snapshot")
        if "detail_read_only" in js_code:
            return {
                "raw": DETAIL_SNAPSHOT_FIXTURE.read_text(encoding="utf-8")
            }
        return {
            "ok": True,
            "platform": "zhilian",
            "selectorVersion": ZHILIAN_SELECTOR_VERSION,
            "url": "https://sou.zhaopin.com/?kw=AI",
            "title": "智联招聘",
            "loginRequired": False,
            "candidateCount": 1,
            "cards": [
                {
                    "positionId": "ZL-DETAIL-1",
                    "jobTitle": "初级AI大模型产品经理",
                    "salary": "9000-12000元",
                    "cityName": "深圳",
                    "jobUrl": "https://www.zhaopin.com/jobdetail/ZL-DETAIL-1.htm",
                    "rawText": "初级AI大模型产品经理 9000-12000元 深圳 经验不限 本科 立即投递",
                }
            ],
        }


class PrioritizedDetailHydrationZhilianDriver:
    def __init__(self):
        self.calls: list[str] = []

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        self.calls.append(f"open:{url}:{wait_seconds}")
        return {"ok": True, "url": url}

    def _exec_js(self, js_code: str):
        self.calls.append("extract_detail" if "detail_read_only" in js_code else "extract_snapshot")
        if "detail_read_only" in js_code:
            return {
                "ok": True,
                "platform": "zhilian",
                "mode": "detail_read_only",
                "selectorVersion": ZHILIAN_DETAIL_SELECTOR_VERSION,
                "url": "https://www.zhaopin.com/jobdetail/ZL-MISSING.htm",
                "title": "缺公司名岗位招聘_深圳补字段科技有限公司招聘 - 智联招聘",
                "loginRequired": False,
                "jobTitle": "缺公司名岗位",
                "companyName": "深圳补字段科技有限公司",
                "rawText": "职位发布者 陈女士 缺公司名岗位 1.2-2万 深圳 福田区 3-5年 本科 公司信息 深圳补字段科技有限公司 未融资 · 20-99人",
            }
        return {
            "ok": True,
            "platform": "zhilian",
            "selectorVersion": ZHILIAN_SELECTOR_VERSION,
            "url": "https://sou.zhaopin.com/?kw=AI",
            "title": "智联招聘",
            "loginRequired": False,
            "candidateCount": 2,
            "cards": [
                {
                    "positionId": "ZL-COMPLETE",
                    "jobTitle": "完整字段岗位",
                    "companyName": "深圳完整字段科技有限公司",
                    "recruiterName": "王女士",
                    "salary": "2-3万",
                    "cityName": "深圳",
                    "jobUrl": "https://www.zhaopin.com/jobdetail/ZL-COMPLETE.htm",
                    "rawText": "完整字段岗位 2-3万 深圳·南山 3-5年 本科 深圳完整字段科技有限公司 王女士·HR 立即投递",
                },
                {
                    "positionId": "ZL-MISSING",
                    "jobTitle": "缺公司名岗位",
                    "salary": "1.2-2万",
                    "cityName": "深圳",
                    "jobUrl": "https://www.zhaopin.com/jobdetail/ZL-MISSING.htm",
                    "rawText": "缺公司名岗位 1.2-2万 深圳·福田 3-5年 本科",
                },
            ],
        }


class DetailLoginRequiredZhilianDriver(DetailHydrationZhilianDriver):
    def _exec_js(self, js_code: str):
        self.calls.append("extract_detail" if "detail_read_only" in js_code else "extract_snapshot")
        if "detail_read_only" in js_code:
            return {
                "ok": True,
                "url": "https://passport.zhaopin.com/login",
                "title": "登录/注册",
                "loginRequired": True,
                "bodySnippet": "扫码登录 请登录",
            }
        return super()._exec_js(js_code)


class LoginRequiredZhilianDriver:
    def __init__(self):
        self.opened: list[str] = []

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        self.opened.append(f"{url}:{wait_seconds}")
        return {"ok": True, "url": url}

    def _exec_js(self, js_code: str):
        return {
            "raw": json.dumps({
                "ok": True,
                "url": "https://www.zhaopin.com/sou/jl%E6%B7%B1%E5%9C%B3/kw010G0IAEKTAC2VMFEG30",
                "title": "AI产品经理招聘-智联招聘",
                "loginRequired": True,
                "bodySnippet": "首页 职位推荐 登录/注册 AI产品经理 立即投递",
            })
        }


class BrowserJsDisabledZhilianDriver:
    def __init__(self):
        self.opened: list[str] = []

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        self.opened.append(f"{url}:{wait_seconds}")
        return {"ok": True, "url": url}

    def _exec_js(self, js_code: str):
        return {
            "ok": False,
            "error": (
                "Google Chrome got an error: Executing JavaScript through AppleScript is turned off. "
                "To turn it on, from the menu bar, go to View > Developer > Allow JavaScript from Apple Events."
            ),
        }


class FakeOpenDriver:
    def __init__(self):
        self.opened: list[str] = []

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        self.opened.append(f"{url}:{wait_seconds}")
        return {"ok": True, "url": url, "title": "岗位详情"}


class FakeZhilianSendDriver(FakeOpenDriver):
    def __init__(self, delivered_after_confirm: bool = True):
        super().__init__()
        self.delivered_after_confirm = delivered_after_confirm
        self.inspect_count = 0

    def _exec_js(self, script: str):
        if "zhilian_apply_entry_not_found" in script:
            return {"ok": True, "clicked": "立即投递"}
        if "zhilian_confirm_button_not_found" in script:
            return {"ok": True, "clicked": "发送"}
        if "filled" in script and "message" in script:
            return {"ok": True, "filled": True, "tag": "TEXTAREA", "len": 12}
        if "loginRequired" in script:
            self.inspect_count += 1
            return {
                "ok": True,
                "href": "https://www.zhaopin.com/jobdetail/ZL-1.htm",
                "title": "岗位详情",
                "loginRequired": False,
                "delivered": self.delivered_after_confirm and self.inspect_count >= 3,
                "requires_user_action": False,
                "bodySnippet": "投递成功" if self.delivered_after_confirm and self.inspect_count >= 3 else "岗位详情 立即投递",
            }
        return {"ok": True}


class NoEditorZhilianSendDriver(FakeZhilianSendDriver):
    def _exec_js(self, script: str):
        if "filled" in script and "message" in script:
            return {"ok": True, "filled": False, "reason": "editor_not_found"}
        return super()._exec_js(script)


def test_zhilian_live_collector_extracts_visible_cards():
    driver = FakeZhilianDriver()

    result = ZhilianReadOnlyCollector(driver=driver).collect(
        query="AI产品经理",
        city="深圳",
        limit=5,
        wait_seconds=2,
    )

    assert result.ok is True
    assert result.jobs[0].platform == "zhilian"
    assert result.jobs[0].name == "AI商业化产品经理"
    assert driver.calls[0].startswith("open:https://sou.zhaopin.com/")
    assert "extract_detail" not in driver.calls


def test_zhilian_live_collector_can_hydrate_detail_fields():
    driver = DetailHydrationZhilianDriver()

    result = ZhilianReadOnlyCollector(driver=driver).collect(
        query="AI产品经理",
        city="深圳",
        limit=1,
        wait_seconds=2,
        detail_limit=1,
    )

    assert result.ok is True
    assert result.jobs[0].company == "深圳佰信时代数字科技有限公司"
    assert result.jobs[0].area == "宝安·西乡"
    assert "extract_detail" in driver.calls
    assert result.snapshot["details"][0]["mode"] == "detail_read_only"


def test_zhilian_detail_hydration_prioritizes_missing_company():
    driver = PrioritizedDetailHydrationZhilianDriver()

    result = ZhilianReadOnlyCollector(driver=driver).collect(
        query="AI产品经理",
        city="深圳",
        limit=2,
        wait_seconds=2,
        detail_limit=1,
    )

    assert result.ok is True
    assert result.jobs[0].company == "深圳完整字段科技有限公司"
    assert result.jobs[1].company == "深圳补字段科技有限公司"
    detail_opens = [call for call in driver.calls if call.startswith("open:https://www.zhaopin.com/jobdetail/")]
    assert detail_opens == ["open:https://www.zhaopin.com/jobdetail/ZL-MISSING.htm:2"]


def test_zhilian_detail_login_required_requests_user_action():
    driver = DetailLoginRequiredZhilianDriver()

    result = ZhilianReadOnlyCollector(driver=driver).collect(
        query="AI产品经理",
        city="深圳",
        limit=1,
        wait_seconds=2,
        detail_limit=1,
    )
    payload = result.to_payload()

    assert result.ok is False
    assert result.error == "zhilian_login_required"
    assert payload["requires_user_action"] is True
    assert payload["user_action"] == "login_zhilian"


def test_zhilian_session_guide_reports_login_user_action():
    driver = LoginRequiredZhilianDriver()

    status = ZhilianSessionGuide(driver=driver).check(query="AI产品经理", city="深圳", wait_seconds=2)
    payload = status.to_dict()

    assert status.logged_in is False
    assert status.login_required is True
    assert payload["requires_user_action"] is True
    assert payload["user_action"] == "login_zhilian"
    assert payload["user_prompt"] == ZHILIAN_LOGIN_USER_PROMPT
    assert payload["next_suggested"] == "jobagent zhilian login"
    assert driver.opened[0].startswith("https://sou.zhaopin.com/")


def test_zhilian_session_guide_reports_browser_js_permission_action():
    driver = BrowserJsDisabledZhilianDriver()

    status = ZhilianSessionGuide(driver=driver).check(query="AI产品经理", city="深圳", wait_seconds=2)
    payload = status.to_dict()

    assert status.logged_in is False
    assert status.login_required is False
    assert payload["requires_user_action"] is True
    assert payload["user_action"] == "enable_chrome_javascript_automation"
    assert payload["user_prompt"] == ZHILIAN_BROWSER_JS_USER_PROMPT
    assert payload["next_suggested"] == "jobagent zhilian login --check"


def test_zhilian_collect_cli_fixture_writes_payload(tmp_path, capsys):
    output_path = tmp_path / "zhilian.raw.json"
    args = parse_args("zhilian", "collect", "--fixture", str(FIXTURE), "--output", str(output_path))

    _cmd_zhilian_collect(args)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["platform"] == "zhilian"
    assert payload["mode"] == "fixture"
    assert payload["count"] == 1
    assert payload["jobs"][0]["platform"] == "zhilian"
    assert "Saved 1 Zhilian jobs" in capsys.readouterr().out


def test_zhilian_collect_cli_requires_query_for_live(capsys):
    args = parse_args("zhilian", "collect")

    with pytest.raises(SystemExit) as exc:
        _cmd_zhilian_collect(args)

    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "zhilian_query_required"


def test_zhilian_rank_cli_rejects_non_zhilian_input(tmp_path, capsys):
    input_path = tmp_path / "liepin.raw.json"
    input_path.write_text(
        json.dumps({"jobs": [{"name": "AI产品经理", "platform": "liepin"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    args = parse_args("zhilian", "rank", "--input", str(input_path), "--local")

    with pytest.raises(SystemExit) as exc:
        _cmd_zhilian_rank(args)

    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "zhilian_rank_input_platform_mismatch"


def test_zhilian_rank_local_outputs_zhilian_platform(tmp_path):
    input_path = tmp_path / "zhilian.raw.json"
    output_path = tmp_path / "zhilian.ranked.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "AI产品经理",
                    "company": "智联样例科技",
                    "salary": "35-55K",
                    "city": "深圳",
                    "experience": "5-10年",
                    "skills": "AI产品, 数据分析",
                    "url": "https://www.zhaopin.com/jobdetail/ZL-1.htm",
                    "platform": "zhilian",
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    args = parse_args("zhilian", "rank", "--local", "--input", str(input_path), "--output", str(output_path))

    _cmd_zhilian_rank(args)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["platform"] == "zhilian"
    assert payload["jobs"][0]["platform"] == "zhilian"
    assert "jobagent zhilian greet preview --local" in payload["next_suggested"]


def test_zhilian_greet_preview_local_outputs_zhilian_ready_file(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("jobagent.cli._require_license_or_exit", lambda command: pytest.fail("local preview must not require license"))
    input_path = tmp_path / "zhilian_ranked.json"
    output_path = tmp_path / "zhilian_ready.json"
    input_path.write_text(
        json.dumps({
            "platform": "zhilian",
            "jobs": [
                {
                    "name": "AI产品经理",
                    "company": "智联样例科技",
                    "url": "https://www.zhaopin.com/jobdetail/ZL-1.htm",
                    "platform": "zhilian",
                    "score": 91,
                    "reasons": ["AI产品方向匹配"],
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    args = parse_args("zhilian", "greet", "preview", "--local", "--input", str(input_path), "--output", str(output_path))

    _cmd_zhilian_greet_preview(args)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["platform"] == "zhilian"
    assert payload["greeting_via"] == "local"
    assert payload["jobs"][0]["greeting_source"] == "local"
    assert payload["jobs"][0]["greeting"]
    assert "jobagent zhilian apply send" in capsys.readouterr().out


def test_zhilian_apply_opener_opens_jobs_and_writes_audit(tmp_path):
    audit_path = tmp_path / "zhilian_audit.json"
    driver = FakeOpenDriver()
    jobs = [
        {
            "name": "AI产品经理",
            "company": "Zhilian Example",
            "url": "https://www.zhaopin.com/jobdetail/ZL-1.htm",
            "platform": "zhilian",
            "greeting": "您好，想进一步沟通。",
            "score": 92,
        }
    ]

    result = ZhilianApplyOpener(driver=driver, audit_log=ZhilianAuditLog(path=audit_path)).open_jobs(
        jobs,
        limit=1,
        wait_seconds=2,
    )

    assert result.ok is True
    assert result.opened == 1
    assert result.requires_user_action is True
    assert result.handoff[0]["action"] == "review_zhilian_fit_before_resume_submit"
    assert driver.opened == ["https://www.zhaopin.com/jobdetail/ZL-1.htm:2"]
    records = json.loads(audit_path.read_text(encoding="utf-8"))
    assert records[0]["platform"] == "zhilian"
    assert records[0]["action"] == "apply_open"
    assert records[0]["status"] == "opened"


def test_zhilian_apply_sender_delivers_and_writes_audit(tmp_path):
    audit_path = tmp_path / "zhilian_audit.json"
    driver = FakeZhilianSendDriver()
    jobs = [
        {
            "name": "AI产品经理",
            "company": "Zhilian Example",
            "url": "https://www.zhaopin.com/jobdetail/ZL-1.htm",
            "platform": "zhilian",
            "greeting": "您好，想进一步沟通。",
            "score": 92,
        }
    ]

    attempts = ZhilianApplySender(driver=driver, audit_log=ZhilianAuditLog(path=audit_path)).send_batch(
        jobs,
        limit=1,
        wait_seconds=2,
    )

    assert attempts[0].delivered is True
    assert attempts[0].error == ""
    assert [step["step"] for step in attempts[0].steps] == [
        "open_job_url",
        "inspect_before_apply",
        "click_apply_or_contact_entry",
        "inspect_apply_state",
        "zhilian_greeting_not_supported",
        "click_zhilian_confirm",
        "inspect_after_confirm",
    ]
    records = json.loads(audit_path.read_text(encoding="utf-8"))
    assert records[0]["platform"] == "zhilian"
    assert records[0]["action"] == "apply_send"
    assert records[0]["status"] == "delivered"
    assert records[0]["evidence"]["greeting_generated"] is True
    assert records[0]["evidence"]["greeting_role"] == "review_note"
    assert records[0]["evidence"]["submit_action"] == "resume_submit"
    assert records[0]["evidence"]["greeting_delivery"]["status"] == "not_supported"


def test_zhilian_apply_sender_records_delivered_without_message_editor(tmp_path):
    audit_path = tmp_path / "zhilian_audit.json"
    driver = NoEditorZhilianSendDriver()
    jobs = [
        {
            "name": "AI产品经理",
            "company": "Zhilian Example",
            "url": "https://www.zhaopin.com/jobdetail/ZL-1.htm",
            "platform": "zhilian",
            "greeting": "您好，想进一步沟通。",
        }
    ]

    attempts = ZhilianApplySender(driver=driver, audit_log=ZhilianAuditLog(path=audit_path)).send_batch(jobs, limit=1)

    assert attempts[0].delivered is True
    records = json.loads(audit_path.read_text(encoding="utf-8"))
    evidence = records[0]["evidence"]
    assert records[0]["status"] == "delivered"
    assert evidence["greeting_generated"] is True
    assert evidence["greeting_delivery"] == {
        "status": "not_supported",
        "filled": False,
        "reason": "zhilian_resume_submit_has_no_message_editor",
    }


def test_zhilian_apply_sender_marks_greeting_as_review_only_for_resume_submit(tmp_path):
    audit_path = tmp_path / "zhilian_audit.json"
    driver = FakeZhilianSendDriver()
    jobs = [
        {
            "name": "AI产品经理",
            "company": "Zhilian Example",
            "url": "https://www.zhaopin.com/jobdetail/ZL-1.htm",
            "platform": "zhilian",
            "greeting": "您好，想进一步沟通。",
        }
    ]

    attempts = ZhilianApplySender(driver=driver, audit_log=ZhilianAuditLog(path=audit_path)).send_batch(jobs, limit=1)

    assert attempts[0].delivered is True
    assert "fill_zhilian_message" not in [step["step"] for step in attempts[0].steps]
    assert "zhilian_greeting_not_supported" in [step["step"] for step in attempts[0].steps]
    records = json.loads(audit_path.read_text(encoding="utf-8"))
    evidence = records[0]["evidence"]
    assert evidence["greeting_role"] == "review_note"
    assert evidence["submit_action"] == "resume_submit"
    assert evidence["greeting_delivery"]["status"] == "not_supported"


def test_zhilian_apply_sender_skips_previously_delivered_url(tmp_path):
    audit_path = tmp_path / "zhilian_audit.json"
    audit_path.write_text(
        json.dumps([
            {
                "platform": "zhilian",
                "action": "apply_send",
                "status": "delivered",
                "job_url": "https://www.zhaopin.com/jobdetail/ZL-1.htm",
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    driver = FakeZhilianSendDriver()
    jobs = [
        {
            "name": "已投岗位",
            "company": "Zhilian Example",
            "url": "https://www.zhaopin.com/jobdetail/ZL-1.htm",
            "platform": "zhilian",
            "greeting": "您好，想进一步沟通。",
        },
        {
            "name": "新岗位",
            "company": "Zhilian Example",
            "url": "https://www.zhaopin.com/jobdetail/ZL-2.htm",
            "platform": "zhilian",
            "greeting": "您好，想进一步沟通。",
        },
    ]

    attempts = ZhilianApplySender(driver=driver, audit_log=ZhilianAuditLog(path=audit_path)).send_batch(
        jobs,
        limit=2,
    )

    assert [attempt.error for attempt in attempts] == ["already_delivered", ""]
    assert attempts[0].steps[0]["step"] == "skip_zhilian_apply_send"
    assert driver.opened == ["https://www.zhaopin.com/jobdetail/ZL-2.htm:3"]


def test_zhilian_apply_sender_treats_edgeone_security_as_user_action(tmp_path):
    audit_path = tmp_path / "zhilian_audit.json"

    class SecurityDriver(FakeOpenDriver):
        def _exec_js(self, script: str):
            if "loginRequired" in script:
                return {
                    "ok": True,
                    "href": "https://www.zhaopin.com/jobdetail/ZL-1.htm",
                    "title": "Security Verification",
                    "loginRequired": False,
                    "delivered": False,
                    "requires_user_action": True,
                    "user_action": "captcha_required",
                    "bodySnippet": "Verifying the safety of the connection. Please check the box below. Protected by Tencent Cloud EdgeOne",
                }
            return {"ok": False, "error": "should_not_click_when_security_verification_blocks"}

    jobs = [
        {
            "name": "AI产品经理",
            "company": "Zhilian Example",
            "url": "https://www.zhaopin.com/jobdetail/ZL-1.htm",
            "platform": "zhilian",
            "greeting": "您好，想进一步沟通。",
        }
    ]

    attempts = ZhilianApplySender(driver=SecurityDriver(), audit_log=ZhilianAuditLog(path=audit_path)).send_batch(jobs)

    assert attempts[0].delivered is False
    assert attempts[0].error == "captcha_required"
    assert [step["step"] for step in attempts[0].steps] == ["open_job_url", "inspect_before_apply"]
    records = json.loads(audit_path.read_text(encoding="utf-8"))
    assert records[0]["status"] == "failed"
    assert records[0]["error"] == "captcha_required"


def test_zhilian_apply_send_cli_requires_confirmation(tmp_path, capsys):
    input_path = tmp_path / "zhilian_ready.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "AI产品经理",
                    "company": "Zhilian Example",
                    "url": "https://www.zhaopin.com/jobdetail/ZL-1.htm",
                    "platform": "zhilian",
                    "greeting": "您好，想进一步沟通。",
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    args = parse_args("zhilian", "apply", "send", "--input", str(input_path), "--limit", "1")

    with pytest.raises(SystemExit) as exc:
        _cmd_zhilian_apply_send(args)

    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "zhilian_apply_send_confirmation_required"


def test_zhilian_apply_send_cli_dry_run_writes_payload(monkeypatch, tmp_path, capsys):
    audit_path = tmp_path / "zhilian_audit.json"
    monkeypatch.setattr("jobagent.platforms.zhilian.audit.zhilian_audit_log_path", lambda: audit_path)
    input_path = tmp_path / "zhilian_ready.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "AI产品经理",
                    "company": "Zhilian Example",
                    "url": "https://www.zhaopin.com/jobdetail/ZL-1.htm",
                    "platform": "zhilian",
                    "greeting": "您好，想进一步沟通。",
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    args = parse_args("zhilian", "apply", "send", "--input", str(input_path), "--dry-run", "--require-greeting")

    with pytest.raises(SystemExit) as exc:
        _cmd_zhilian_apply_send(args)

    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["platform"] == "zhilian"
    assert payload["mode"] == "automatic_apply_send"
    assert payload["planned"] == 1
    assert payload["delivered"] == 0
    assert payload["failed"] == 0
    assert payload["batch_review"]["actionable"] == 1
    assert payload["batch_review"]["already_delivered"] == 0
    assert payload["batch_review"]["user_action_required"] == 0


def test_zhilian_apply_send_cli_reports_skip_and_harness_review(monkeypatch, tmp_path, capsys):
    audit_path = tmp_path / "zhilian_audit.json"
    monkeypatch.setattr("jobagent.platforms.zhilian.audit.zhilian_audit_log_path", lambda: audit_path)
    ZhilianAuditLog().append_event(
        action="apply_send",
        status="delivered",
        job_url="https://www.zhaopin.com/jobdetail/ZL-1.htm",
    )
    input_path = tmp_path / "zhilian_ready.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "已投岗位",
                    "company": "Zhilian Example",
                    "url": "https://www.zhaopin.com/jobdetail/ZL-1.htm",
                    "platform": "zhilian",
                    "greeting": "您好，想进一步沟通。",
                },
                {
                    "name": "待投岗位",
                    "company": "Zhilian Example",
                    "url": "https://www.zhaopin.com/jobdetail/ZL-2.htm",
                    "platform": "zhilian",
                    "greeting": "您好，想进一步沟通。",
                },
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    args = parse_args("zhilian", "apply", "send", "--input", str(input_path), "--dry-run", "--limit", "2", "--require-greeting")

    with pytest.raises(SystemExit) as exc:
        _cmd_zhilian_apply_send(args)

    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected"] == 2
    assert payload["skipped"] == 1
    assert payload["batch_review"]["actionable"] == 1
    assert payload["batch_review"]["already_delivered"] == 1
    assert payload["batch_review"]["already_delivered_indexes"] == [0]
    assert payload["safety_harness"]["skip_delivered"] is True
    assert payload["safety_harness"]["stop_on_failure"] is True


def test_zhilian_apply_send_cli_prompts_for_user_action(monkeypatch, tmp_path, capsys):
    from jobagent.domain.models import SendAttempt

    class CaptchaSender:
        def __init__(self, driver=None):
            pass

        def send_batch(self, *args, **kwargs):
            attempt = SendAttempt(
                job_url="https://www.zhaopin.com/jobdetail/ZL-1.htm",
                message="您好，想进一步沟通。",
                delivered=False,
                error="captcha_required",
            )
            attempt.steps = [
                {
                    "step": "inspect_before_apply",
                    "ok": True,
                    "requires_user_action": True,
                    "user_action": "captcha_required",
                }
            ]
            return [attempt]

    monkeypatch.setattr("jobagent.platforms.zhilian.ZhilianApplySender", CaptchaSender)
    input_path = tmp_path / "zhilian_ready.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "AI产品经理",
                    "company": "Zhilian Example",
                    "url": "https://www.zhaopin.com/jobdetail/ZL-1.htm",
                    "platform": "zhilian",
                    "greeting": "您好，想进一步沟通。",
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    args = parse_args(
        "zhilian", "apply", "send",
        "--input", str(input_path),
        "--confirm-submit",
        "--skip-login-check",
        "--require-greeting",
    )

    with pytest.raises(SystemExit) as exc:
        _cmd_zhilian_apply_send(args)

    assert exc.value.code == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["requires_user_action"] is True
    assert payload["user_action"] == "captcha_required"
    assert "请先完成智联安全验证" in payload["user_prompt"]
    assert "请先完成智联安全验证" in captured.err


def test_zhilian_audit_cli_reports_platform_summary(monkeypatch, tmp_path, capsys):
    audit_path = tmp_path / "zhilian_audit.json"
    monkeypatch.setattr("jobagent.platforms.zhilian.audit.zhilian_audit_log_path", lambda: audit_path)
    ZhilianAuditLog().append_event(action="apply_send", status="planned", job_url="https://www.zhaopin.com/jobdetail/ZL-1.htm")
    args = parse_args("zhilian", "audit", "--recent", "3")

    _cmd_zhilian_audit(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["platform"] == "zhilian"
    assert payload["summary"]["send"]["planned"] == 1
