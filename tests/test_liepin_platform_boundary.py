from __future__ import annotations

import json
from pathlib import Path

import pytest

import jobagent.cli as cli
from jobagent.application.doctor_liepin import run_liepin_doctor
from jobagent.cli import (
    _cmd_jobs_rank_cloud,
    _cmd_liepin_apply_open,
    _cmd_liepin_apply_send,
    _cmd_liepin_audit,
    _cmd_liepin_greet_preview,
    _cmd_liepin_greet_send,
    _cmd_liepin_collect,
    _cmd_liepin_login,
    _cmd_liepin_rank,
    build_parser,
)
from jobagent.platforms.liepin import (
    LIEPIN_LOGIN_URL,
    LIEPIN_SELECTOR_VERSION,
    LiepinApplyOpener,
    LiepinApplySender,
    LiepinAuditLog,
    LiepinCollectResult,
    LiepinReadOnlyCollector,
    LiepinSessionGuide,
    LiepinSessionStatus,
    build_liepin_search_url,
    build_liepin_snapshot_script,
    collect_liepin_fixture,
    liepin_job_id,
    parse_liepin_job,
)


FIXTURE = Path(__file__).parent / "fixtures" / "liepin" / "search_joblist_page1.json"
LOGIN_REQUIRED_FIXTURE = Path(__file__).parent / "fixtures" / "liepin" / "login_required_snapshot.json"
LIVE_SNAPSHOT_FIXTURE = Path(__file__).parent / "fixtures" / "liepin" / "live_snapshot_logged_in.json"
REAL_SHAPE_SNAPSHOT_FIXTURE = Path(__file__).parent / "fixtures" / "liepin" / "live_snapshot_real_shape_20260612.json"


def parse_args(*args: str):
    return build_parser().parse_args(list(args))


def test_liepin_parser_uses_platform_boundary_fixture():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]["jobList"][0]

    job = parse_liepin_job(raw)

    assert liepin_job_id(raw) == "liepin-1001"
    assert job.platform == "liepin"
    assert job.name == "AI产品负责人"
    assert job.salary == "40-70k·15薪"
    assert job.company == "Example Robotics"
    assert job.city == "深圳"
    assert job.area == "南山区·粤海街道"
    assert job.experience == "5-10年"
    assert job.degree == "本科及以上"
    assert job.skills == "AI产品, 机器人, 商业化"
    assert job.boss == "李顾问 · 猎头顾问"
    assert job.url == "https://www.liepin.com/job/liepin-1001.shtml"


def test_liepin_collect_fixture_outputs_shared_jobs():
    jobs = collect_liepin_fixture(FIXTURE)

    assert len(jobs) == 1
    assert jobs[0].platform == "liepin"
    assert jobs[0].name == "AI产品负责人"


def test_liepin_collect_fixture_accepts_live_snapshot_cards():
    jobs = collect_liepin_fixture(LIVE_SNAPSHOT_FIXTURE)

    assert len(jobs) == 1
    assert jobs[0].platform == "liepin"
    assert jobs[0].name == "AI产品负责人"
    assert jobs[0].url == "https://www.liepin.com/job/liepin-live-1001.shtml"


def test_liepin_collect_fixture_accepts_real_shape_cleaned_snapshot():
    payload = json.loads(REAL_SHAPE_SNAPSHOT_FIXTURE.read_text(encoding="utf-8"))
    jobs = collect_liepin_fixture(REAL_SHAPE_SNAPSHOT_FIXTURE)

    assert payload["selectorVersion"] == LIEPIN_SELECTOR_VERSION
    assert payload["rejected"]["missingIdentity"] == 2
    assert len(jobs) == 2
    assert [job.company for job in jobs] == ["Example AI Platform", "Example Retail"]
    assert all(job.url.startswith("https://www.liepin.com/job/") for job in jobs)
    assert all(job.company != "【" for job in jobs)


def test_liepin_collect_fixture_accepts_multi_page_snapshot(tmp_path):
    fixture = tmp_path / "liepin_pages.json"
    fixture.write_text(
        json.dumps({
            "pages": [
                {
                    "cards": [
                        {
                            "jobId": "liepin-live-1",
                            "jobTitle": "AI产品经理",
                            "companyName": "Page One",
                            "jobUrl": "https://www.liepin.com/job/liepin-live-1.shtml",
                        }
                    ]
                },
                {
                    "cards": [
                        {
                            "jobId": "liepin-live-2",
                            "jobTitle": "AI Agent 产品负责人",
                            "companyName": "Page Two",
                            "jobUrl": "https://www.liepin.com/job/liepin-live-2.shtml",
                        }
                    ]
                },
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    jobs = collect_liepin_fixture(fixture, city_name="深圳")

    assert [job.name for job in jobs] == ["AI产品经理", "AI Agent 产品负责人"]
    assert all(job.platform == "liepin" for job in jobs)


def test_liepin_snapshot_script_exposes_diagnostics_contract():
    script = build_liepin_snapshot_script(limit=7)

    assert LIEPIN_SELECTOR_VERSION in script
    assert "candidateCount" in script
    assert "rejected" in script
    assert "missingIdentity" in script
    assert "!url && !id" in script
    assert "cleanLine" in script
    assert "companyStart" in script
    assert "lines.length < 4" in script
    assert "cards.length >= limit" in script


class FakeLiepinDriver:
    def __init__(self):
        self.calls: list[str] = []
        self.last_js = ""

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        self.calls.append(f"open:{url}:{wait_seconds}")
        return {"ok": True, "url": url}

    def _exec_js(self, js_code: str):
        self.calls.append("extract_snapshot")
        self.last_js = js_code
        return {
            "ok": True,
            "url": "https://www.liepin.com/zhaopin/?key=AI",
            "selectorVersion": LIEPIN_SELECTOR_VERSION,
            "candidateCount": 1,
            "rejected": {"emptyText": 0, "weakSignal": 0, "duplicate": 0, "missingIdentity": 0},
            "cards": [
                {
                    "jobId": "liepin-live-1",
                    "jobTitle": "AI商业化产品经理",
                    "salary": "35-60k·14薪",
                    "companyName": "Live Example",
                    "cityName": "深圳",
                    "workYear": "3-5年",
                    "education": "本科",
                    "jobUrl": "https://www.liepin.com/job/liepin-live-1.shtml",
                },
            ],
        }


class MultiPageLiepinDriver:
    def __init__(self):
        self.urls: list[str] = []
        self.extract_count = 0

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        self.urls.append(url)
        return {"ok": True, "url": url}

    def _exec_js(self, js_code: str):
        self.extract_count += 1
        if self.extract_count == 1:
            cards = [
                {
                    "jobId": "liepin-live-1",
                    "jobTitle": "AI商业化产品经理",
                    "salary": "35-60k·14薪",
                    "companyName": "Live Example",
                    "cityName": "深圳",
                    "jobUrl": "https://www.liepin.com/job/liepin-live-1.shtml",
                }
            ]
        else:
            cards = [
                {
                    "jobId": "liepin-live-1",
                    "jobTitle": "AI商业化产品经理",
                    "salary": "35-60k·14薪",
                    "companyName": "Live Example",
                    "cityName": "深圳",
                    "jobUrl": "https://www.liepin.com/job/liepin-live-1.shtml",
                },
                {
                    "jobId": "liepin-live-2",
                    "jobTitle": "AI Agent 产品负责人",
                    "salary": "50-80k·15薪",
                    "companyName": "Second Page Example",
                    "cityName": "深圳",
                    "jobUrl": "https://www.liepin.com/job/liepin-live-2.shtml",
                },
            ]
        return {
            "ok": True,
            "url": self.urls[-1],
            "selectorVersion": LIEPIN_SELECTOR_VERSION,
            "candidateCount": len(cards),
            "rejected": {"emptyText": 0, "weakSignal": 0, "duplicate": 0, "missingIdentity": 0},
            "cards": cards,
        }


class LoginRedirectDriver:
    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        return {"ok": True, "url": url}

    def _exec_js(self, js_code: str):
        return json.loads(LOGIN_REQUIRED_FIXTURE.read_text(encoding="utf-8"))


class LoginPromptSearchPageDriver:
    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        return {"ok": True, "url": url}

    def _exec_js(self, js_code: str):
        return {
            "ok": True,
            "url": "https://www.liepin.com/zhaopin/?key=AI",
            "title": "【招聘信息_人才网招聘信息】-猎聘",
            "loginRequired": False,
            "loginPromptPresent": True,
            "bodySnippet": "职位 搜索 非常抱歉！暂时没有合适的职位 登录/注册 密码登录 获取验证码",
            "cardCount": 0,
            "cards": [],
        }


class NavigationNoiseDriver:
    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        return {"ok": True, "url": url}

    def _exec_js(self, js_code: str):
        return {
            "ok": True,
            "url": "https://www.liepin.com/zhaopin/?key=AI",
            "title": "【招聘信息_人才网招聘信息】-猎聘",
            "loginRequired": False,
            "loginPromptPresent": False,
            "bodySnippet": "我是招聘方 NEW",
            "cardCount": 0,
            "cards": [],
        }


class FakeOpenDriver:
    def __init__(self):
        self.opened: list[str] = []

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        self.opened.append(f"{url}:{wait_seconds}")
        return {"ok": True, "url": url}


class FakeLiepinSendDriver:
    def __init__(self, delivered_after_confirm: bool = True):
        self.opened: list[str] = []
        self.scripts: list[str] = []
        self.delivered_after_confirm = delivered_after_confirm
        self.confirm_clicked = False

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        self.opened.append(f"{url}:{wait_seconds}")
        return {"ok": True, "url": url}

    def _exec_js(self, script: str):
        self.scripts.append(script)
        if "apply_entry_not_found" in script:
            return {"ok": True, "clicked": "立即沟通"}
        if "filled:false" in script:
            return {"ok": True, "filled": True, "tag": "TEXTAREA", "len": 12}
        if "confirm_button_not_found" in script:
            self.confirm_clicked = True
            return {"ok": True, "clicked": "发送"}
        delivered = self.delivered_after_confirm and self.confirm_clicked
        return {
            "ok": True,
            "title": "猎聘",
            "href": "https://www.liepin.com/job/lp-1.shtml",
            "loginRequired": False,
            "delivered": delivered,
            "requires_user_action": False,
            "bodySnippet": "投递成功" if delivered else "岗位详情",
        }


class FakeLiepinSessionDriver:
    def __init__(self, login_required_sequence: list[bool] | None = None):
        self.calls: list[str] = []
        self.login_required_sequence = list(login_required_sequence or [False])

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        self.calls.append(f"open:{url}:{wait_seconds}")
        return {"ok": True, "url": url}

    def _exec_js(self, js_code: str):
        self.calls.append("inspect_session")
        login_required = (
            self.login_required_sequence.pop(0)
            if self.login_required_sequence
            else False
        )
        return {
            "raw": json.dumps({
                "ok": True,
                "url": "https://www.liepin.com/zhaopin/?key=AI",
                "title": "猎聘",
                "loginRequired": login_required,
                "bodySnippet": "岗位列表" if not login_required else "登录/注册",
            })
        }


class FakeLiepinDoctorDriver:
    def __init__(self, login_required: bool = False):
        self.login_required = login_required
        self.calls: list[str] = []
        self.extract_count = 0

    def chrome_running(self):
        self.calls.append("chrome_running")
        return True

    def applescript_js_enabled(self):
        self.calls.append("browser_js_ready")
        return True, "cdp"

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        self.calls.append(f"open:{url}:{wait_seconds}")
        return {"ok": True, "url": url, "title": "猎聘"}

    def _exec_js(self, js_code: str):
        self.extract_count += 1
        if self.extract_count == 1:
            return {
                "raw": json.dumps({
                    "ok": True,
                    "url": "https://www.liepin.com/zhaopin/?key=AI",
                    "title": "猎聘",
                    "loginRequired": self.login_required,
                    "bodySnippet": "登录/注册" if self.login_required else "岗位列表",
                })
            }
        return {
            "ok": True,
            "url": "https://www.liepin.com/zhaopin/?key=AI",
            "selectorVersion": LIEPIN_SELECTOR_VERSION,
            "candidateCount": 1,
            "rejected": {"emptyText": 0, "weakSignal": 0, "duplicate": 0, "missingIdentity": 0},
            "cards": [
                {
                    "jobId": "liepin-doctor-1",
                    "jobTitle": "AI产品经理",
                    "companyName": "Doctor Example",
                    "cityName": "深圳",
                    "jobUrl": "https://www.liepin.com/job/liepin-doctor-1.shtml",
                }
            ],
        }


class BrowserNotReadyLiepinDoctorDriver(FakeLiepinDoctorDriver):
    def applescript_js_enabled(self):
        self.calls.append("browser_js_ready")
        return False, "browser automation disabled"


def test_liepin_search_url_encodes_query_and_city():
    url = build_liepin_search_url("AI产品", "深圳")
    page_url = build_liepin_search_url("AI产品", "深圳", page=3)

    assert url.startswith("https://www.liepin.com/zhaopin/?")
    assert "key=AI" in url
    assert "dq=" in url
    assert "currentPage=" not in url
    assert "currentPage=3" in page_url


def test_liepin_live_read_only_collector_uses_visible_cards_only():
    driver = FakeLiepinDriver()

    result = LiepinReadOnlyCollector(driver=driver).collect(
        query="AI产品",
        city="深圳",
        wait_seconds=3,
    )

    assert result.ok is True
    assert result.mode == "live_read_only"
    assert result.jobs[0].platform == "liepin"
    assert result.jobs[0].name == "AI商业化产品经理"
    assert driver.calls[0].startswith("open:https://www.liepin.com/zhaopin/")
    assert driver.calls[-1] == "extract_snapshot"
    assert LIEPIN_SELECTOR_VERSION in driver.last_js


def test_liepin_live_read_only_collector_fetches_pages_and_dedupes():
    driver = MultiPageLiepinDriver()

    result = LiepinReadOnlyCollector(driver=driver).collect(
        query="AI产品",
        city="深圳",
        limit=3,
        page=1,
        pages=2,
        page_delay=0,
    )

    assert result.ok is True
    assert result.page == 1
    assert result.pages == 2
    assert [job.name for job in result.jobs] == ["AI商业化产品经理", "AI Agent 产品负责人"]
    assert len(driver.urls) == 2
    assert "currentPage=2" in driver.urls[1]
    assert result.snapshot["pages"][0]["page"] == 1
    assert result.snapshot["pages"][1]["page"] == 2


def test_liepin_live_read_only_collector_reports_login_required():
    result = LiepinReadOnlyCollector(driver=LoginRedirectDriver()).collect(
        query="AI产品",
        city="深圳",
    )

    assert result.ok is False
    assert result.error == "liepin_login_required"
    assert result.jobs == []


def test_liepin_live_read_only_collector_requires_logged_in_session():
    result = LiepinReadOnlyCollector(driver=LoginPromptSearchPageDriver()).collect(
        query="AI产品",
        city="深圳",
    )

    assert result.ok is False
    assert result.error == "liepin_login_required"
    assert result.jobs == []
    assert result.snapshot["loginPromptPresent"] is True
    assert result.snapshot["loginRequired"] is False


def test_liepin_live_read_only_collector_ignores_navigation_noise():
    result = LiepinReadOnlyCollector(driver=NavigationNoiseDriver()).collect(
        query="AI产品",
        city="深圳",
    )

    assert result.ok is True
    assert result.jobs == []
    assert result.snapshot["cardCount"] == 0


def test_liepin_session_check_reports_login_required():
    driver = FakeLiepinSessionDriver([True])

    status = LiepinSessionGuide(driver=driver).check(
        query="AI产品",
        city="深圳",
        wait_seconds=2,
    )

    assert status.ok is True
    assert status.logged_in is False
    assert status.login_required is True
    assert status.to_dict()["next_suggested"] == "jobagent liepin login"
    assert driver.calls[0].startswith("open:https://www.liepin.com/zhaopin/")
    assert driver.calls[-1] == "inspect_session"


def test_liepin_session_open_login_uses_login_url():
    driver = FakeLiepinSessionDriver([True])

    status = LiepinSessionGuide(driver=driver).open_login(wait_seconds=4)

    assert status.login_required is True
    assert driver.calls[0] == f"open:{LIEPIN_LOGIN_URL}:4"


def test_liepin_session_wait_for_login_polls_until_logged_in(monkeypatch):
    driver = FakeLiepinSessionDriver([True, False])
    monkeypatch.setattr("jobagent.platforms.liepin.session.time.sleep", lambda seconds: None)

    status = LiepinSessionGuide(driver=driver).wait_for_login(
        timeout=10,
        poll_interval=1,
        wait_seconds=1,
    )

    assert status.ok is True
    assert status.logged_in is True
    assert status.login_required is False
    assert driver.calls.count("inspect_session") == 2


def test_liepin_doctor_stops_at_login_user_action(monkeypatch, tmp_path):
    driver = FakeLiepinDoctorDriver(login_required=True)
    monkeypatch.setattr(
        "jobagent.application.doctor_liepin.last_doctor_path",
        lambda: tmp_path / "last_doctor_report.json",
    )

    report = run_liepin_doctor(driver=driver, wait_seconds=1)

    assert report.status == "NOT_READY"
    checks = {check.name: check for check in report.checks}
    assert checks["chrome_running"].ok is True
    assert checks["browser_js_ready"].ok is True
    assert checks["liepin_logged_in"].ok is False
    assert checks["liepin_logged_in"].evidence["requires_user_action"] is True
    assert checks["liepin_selector_extracts_jobs"].evidence["skipped"] is True
    assert driver.extract_count == 1


def test_liepin_doctor_preserves_login_prompt_when_report_save_fails(monkeypatch, tmp_path):
    driver = FakeLiepinDoctorDriver(login_required=True)
    monkeypatch.setattr(
        "jobagent.application.doctor_liepin.last_doctor_path",
        lambda: tmp_path / "last_doctor_report.json",
    )

    def fail_save(*args, **kwargs):
        raise PermissionError("readonly state dir")

    monkeypatch.setattr("jobagent.application.doctor_liepin.save_json", fail_save)

    report = run_liepin_doctor(driver=driver, wait_seconds=1)

    checks = {check.name: check for check in report.checks}
    assert report.status == "NOT_READY"
    assert checks["liepin_logged_in"].evidence["requires_user_action"] is True
    assert checks["liepin_logged_in"].evidence["user_action"] == "login_liepin"
    assert checks["doctor_report_saved"].ok is False
    assert "readonly state dir" in checks["doctor_report_saved"].detail


def test_liepin_doctor_reports_browser_not_ready_separately(monkeypatch, tmp_path):
    driver = BrowserNotReadyLiepinDoctorDriver(login_required=True)
    monkeypatch.setattr(
        "jobagent.application.doctor_liepin.last_doctor_path",
        lambda: tmp_path / "last_doctor_report.json",
    )

    report = run_liepin_doctor(driver=driver, wait_seconds=1)

    checks = {check.name: check for check in report.checks}
    assert report.status == "NOT_READY"
    assert checks["browser_js_ready"].ok is False
    assert checks["liepin_logged_in"].detail == "Browser is not ready for Liepin login check"
    assert "requires_user_action" not in checks["liepin_logged_in"].evidence
    assert checks["liepin_selector_extracts_jobs"].evidence["reason"] == "browser_not_ready"
    assert not any(call.startswith("open:") for call in driver.calls)


def test_liepin_doctor_ready_when_logged_in_and_selector_extracts(monkeypatch, tmp_path):
    driver = FakeLiepinDoctorDriver(login_required=False)
    monkeypatch.setattr(
        "jobagent.application.doctor_liepin.last_doctor_path",
        lambda: tmp_path / "last_doctor_report.json",
    )

    report = run_liepin_doctor(driver=driver, wait_seconds=1)

    assert report.status == "READY"
    checks = {check.name: check for check in report.checks}
    assert checks["liepin_logged_in"].ok is True
    assert checks["liepin_selector_extracts_jobs"].ok is True
    assert checks["liepin_selector_extracts_jobs"].evidence["count"] == 1
    assert checks["doctor_report_saved"].ok is True
    assert driver.extract_count == 2


def test_liepin_doctor_with_cloud_reports_missing_license(monkeypatch, tmp_path):
    driver = FakeLiepinDoctorDriver(login_required=False)
    monkeypatch.setattr(
        "jobagent.application.doctor_liepin.last_doctor_path",
        lambda: tmp_path / "last_doctor_report.json",
    )
    monkeypatch.setattr("jobagent.application.doctor_liepin.load_license_key", lambda: None)

    report = run_liepin_doctor(driver=driver, wait_seconds=1, check_cloud=True)

    checks = {check.name: check for check in report.checks}
    assert checks["liepin_logged_in"].ok is True
    assert checks["liepin_selector_extracts_jobs"].ok is True
    assert checks["cloud_license_configured"].ok is False
    assert checks["cloud_license_configured"].evidence["error"] == "license_required"
    assert "jobagent init --key" in checks["cloud_license_configured"].evidence["hint"]
    assert report.status == "NOT_READY"


def test_liepin_collect_cli_accepts_read_only_fixture(tmp_path):
    output = tmp_path / "liepin_jobs.json"
    args = parse_args(
        "liepin",
        "collect",
        "--fixture",
        str(FIXTURE),
        "--query",
        "AI产品",
        "--output",
        str(output),
    )

    _cmd_liepin_collect(args)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["platform"] == "liepin"
    assert payload["mode"] == "fixture"
    assert payload["count"] == 1
    assert payload["jobs"][0]["platform"] == "liepin"
    assert payload["next_suggested"] == "jobagent liepin rank --input <liepin.raw.json> --output <liepin.ranked.json>"


def test_liepin_collect_cli_accepts_live_read_only_mode(monkeypatch, tmp_path):
    output = tmp_path / "liepin_live.json"
    fake_driver = object()

    class FakeSessionGuide:
        def __init__(self, driver=None):
            assert driver is fake_driver

        def check(self, query: str = "", city: str = "", wait_seconds: int = 5):
            return LiepinSessionStatus(ok=True, logged_in=True, login_required=False)

    class FakeCollector:
        def __init__(self, driver=None):
            assert driver is fake_driver

        def collect(self, query: str, city: str = "", limit: int = 20, wait_seconds: int = 8, **kwargs):
            raw = {
                "jobId": "liepin-cli-1",
                "jobTitle": "AI产品经理",
                "companyName": "CLI Example",
                "cityName": city,
            }
            return LiepinCollectResult(
                query=query,
                city=city,
                url="https://www.liepin.com/zhaopin/?key=AI",
                jobs=[parse_liepin_job(raw, city_name=city)],
                snapshot={"cards": [raw]},
                page=kwargs.get("page", 1),
                pages=kwargs.get("pages", 1),
            )

    monkeypatch.setattr("jobagent.drivers.boss.create_driver", lambda: fake_driver)
    monkeypatch.setattr("jobagent.platforms.liepin.LiepinSessionGuide", FakeSessionGuide)
    monkeypatch.setattr("jobagent.platforms.liepin.LiepinReadOnlyCollector", FakeCollector)
    args = parse_args(
        "liepin",
        "collect",
        "--query",
        "AI产品",
        "--city",
        "深圳",
        "--pages",
        "2",
        "--output",
        str(output),
    )

    _cmd_liepin_collect(args)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["mode"] == "live_read_only"
    assert payload["pages"] == 2
    assert payload["count"] == 1
    assert payload["jobs"][0]["platform"] == "liepin"
    assert payload["next_suggested"] == f"jobagent liepin rank --input {output} --output <liepin.ranked.json>"


def test_liepin_collect_cli_stops_before_collect_when_login_required(monkeypatch, capsys):
    fake_driver = object()
    called = {"collector": False}

    class FakeSessionGuide:
        def __init__(self, driver=None):
            assert driver is fake_driver

        def check(self, query: str = "", city: str = "", wait_seconds: int = 5):
            return LiepinSessionStatus(ok=True, logged_in=False, login_required=True)

    class FakeCollector:
        def __init__(self, driver=None):
            called["collector"] = True

    monkeypatch.setattr("jobagent.drivers.boss.create_driver", lambda: fake_driver)
    monkeypatch.setattr("jobagent.platforms.liepin.LiepinSessionGuide", FakeSessionGuide)
    monkeypatch.setattr("jobagent.platforms.liepin.LiepinReadOnlyCollector", FakeCollector)
    args = parse_args("liepin", "collect", "--query", "AI产品", "--city", "深圳")

    with pytest.raises(SystemExit) as exc:
        _cmd_liepin_collect(args)

    assert exc.value.code == 2
    assert called["collector"] is False
    captured = capsys.readouterr()
    assert '"mode": "login_check"' in captured.out
    assert '"requires_user_action": true' in captured.out
    assert "猎聘需要登录" in captured.err


def test_liepin_collect_cli_writes_error_payload(monkeypatch, tmp_path, capsys):
    output = tmp_path / "liepin_error.json"

    class FakeCollector:
        def __init__(self, driver=None):
            assert driver is None

        def collect(self, query: str, city: str = "", limit: int = 20, wait_seconds: int = 8, **kwargs):
            return LiepinCollectResult(
                query=query,
                city=city,
                url="https://www.liepin.com/login",
                jobs=[],
                snapshot={"loginRequired": True},
                ok=False,
                error="liepin_login_required",
            )

    monkeypatch.setattr("jobagent.platforms.liepin.LiepinReadOnlyCollector", FakeCollector)
    args = parse_args(
        "liepin",
        "collect",
        "--query",
        "AI产品",
        "--skip-login-check",
        "--output",
        str(output),
    )

    with pytest.raises(SystemExit) as exc:
        _cmd_liepin_collect(args)

    assert exc.value.code == 2
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["error"] == "liepin_login_required"
    assert payload["requires_user_action"] is True
    assert payload["user_action"] == "login_liepin"
    assert "猎聘需要登录" in payload["user_prompt"]
    assert payload["next_suggested"] == "jobagent liepin login"
    err = capsys.readouterr().err
    assert "猎聘需要登录" in err
    assert "Next: jobagent liepin login" in err


def test_liepin_login_cli_check_exits_zero_when_logged_in(monkeypatch, capsys):
    class FakeSessionGuide:
        def check(self, query: str = "", city: str = "", wait_seconds: int = 5):
            return LiepinSessionStatus(ok=True, logged_in=True, login_required=False)

    monkeypatch.setattr("jobagent.platforms.liepin.LiepinSessionGuide", FakeSessionGuide)
    args = parse_args("liepin", "login", "--check")

    with pytest.raises(SystemExit) as exc:
        _cmd_liepin_login(args)

    assert exc.value.code == 0
    assert '"logged_in": true' in capsys.readouterr().out


def test_liepin_login_cli_check_exits_two_when_login_required(monkeypatch, capsys):
    class FakeSessionGuide:
        def check(self, query: str = "", city: str = "", wait_seconds: int = 5):
            return LiepinSessionStatus(ok=True, logged_in=False, login_required=True)

    monkeypatch.setattr("jobagent.platforms.liepin.LiepinSessionGuide", FakeSessionGuide)
    args = parse_args("liepin", "login", "--check")

    with pytest.raises(SystemExit) as exc:
        _cmd_liepin_login(args)

    assert exc.value.code == 2
    captured = capsys.readouterr()
    out = captured.out
    assert '"login_required": true' in out
    assert '"requires_user_action": true' in out
    assert '"user_action": "login_liepin"' in out
    assert '"next_suggested": "jobagent liepin login"' in out
    assert "猎聘需要登录" in captured.err


def test_liepin_rank_cli_rejects_non_liepin_input(monkeypatch, tmp_path, capsys):
    input_path = tmp_path / "boss_jobs.json"
    input_path.write_text(
        json.dumps({"jobs": [{"name": "AI产品经理", "platform": "zhipin"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "_require_license_or_exit", lambda command: None)
    args = parse_args("liepin", "rank", "--input", str(input_path))

    with pytest.raises(SystemExit) as exc:
        _cmd_liepin_rank(args)

    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "liepin_rank_input_platform_mismatch" in out


def test_liepin_rank_cli_passes_liepin_jobs_to_cloud_rank(monkeypatch, tmp_path):
    input_path = tmp_path / "liepin_jobs.json"
    output_path = tmp_path / "liepin_ranked.json"
    input_path.write_text(
        json.dumps({
            "platform": "liepin",
            "jobs": [
                {
                    "name": "AI产品经理",
                    "salary": "40-60k",
                    "company": "Liepin Example",
                    "city": "深圳",
                    "url": "https://www.liepin.com/job/lp-1.shtml",
                    "platform": "liepin",
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    captured = {}

    def fake_cloud_rank(args, raw_jobs, source_platform=""):
        captured["raw_jobs"] = raw_jobs
        captured["source_platform"] = source_platform
        Path(args.output).write_text("{}", encoding="utf-8")

    monkeypatch.setattr(cli, "_require_license_or_exit", lambda command: None)
    monkeypatch.setattr(cli, "_cmd_jobs_rank_cloud", fake_cloud_rank)
    args = parse_args("liepin", "rank", "--input", str(input_path), "--output", str(output_path))

    _cmd_liepin_rank(args)

    assert captured["source_platform"] == "liepin"
    assert captured["raw_jobs"][0]["platform"] == "liepin"


def test_liepin_rank_local_bypasses_license_and_cloud(monkeypatch, tmp_path):
    input_path = tmp_path / "liepin_jobs.json"
    output_path = tmp_path / "liepin_ranked.json"
    input_path.write_text(
        json.dumps({
            "platform": "liepin",
            "jobs": [
                {
                    "name": "AI Agent 产品负责人",
                    "salary": "40-60k",
                    "company": "Liepin Example",
                    "city": "深圳",
                    "url": "https://www.liepin.com/job/lp-1.shtml",
                    "platform": "liepin",
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli,
        "_require_license_or_exit",
        lambda command: pytest.fail("local Liepin rank must not require license"),
    )
    monkeypatch.setattr(
        cli,
        "_cmd_jobs_rank_cloud",
        lambda *args, **kwargs: pytest.fail("local Liepin rank must not call cloud rank"),
    )
    args = parse_args(
        "liepin", "rank",
        "--local",
        "--config", str(tmp_path / "missing.yaml"),
        "--input", str(input_path),
        "--output", str(output_path),
    )

    _cmd_liepin_rank(args)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["via"] == "local"
    assert payload["platform"] == "liepin"
    assert payload["jobs"][0]["platform"] == "liepin"
    assert "jobagent liepin greet preview --local" in payload["next_suggested"]


def test_cloud_rank_preserves_liepin_platform_context(monkeypatch, tmp_path):
    output_path = tmp_path / "ranked.json"
    raw_jobs = [
        {
            "id": "lp-1",
            "name": "AI产品经理",
            "salary": "40-60k",
            "company": "Liepin Example",
            "city": "深圳",
            "boss": "王顾问",
            "url": "https://www.liepin.com/job/lp-1.shtml",
            "platform": "liepin",
        }
    ]

    monkeypatch.setattr(cli, "_profile_for_cloud", lambda: {"target_roles": ["AI产品经理"]})
    monkeypatch.setattr(
        "jobagent.infra.cloud_client.jobs_rank",
        lambda profile, jobs: {
            "ranked": [
                {
                    "id": "lp-1",
                    "title": "AI产品经理",
                    "salary": "40-60k",
                    "company": "Liepin Example",
                    "area": "深圳",
                    "experience": "3-5年",
                    "degree": "本科",
                    "skills": "AI, Agent",
                    "url": "https://www.liepin.com/job/lp-1.shtml",
                    "score": 92,
                    "recommendation": "strong_match",
                    "matches": "AI 产品背景匹配",
                    "risks": "",
                }
            ]
        },
    )
    args = parse_args("liepin", "rank", "--input", str(output_path), "--output", str(output_path))

    _cmd_jobs_rank_cloud(args, raw_jobs, source_platform="liepin")

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    ranked = payload["jobs"][0]
    assert payload["platform"] == "liepin"
    assert payload["next_suggested"] == f"jobagent liepin greet preview --input {output_path} --limit 1"
    assert ranked["platform"] == "liepin"
    assert ranked["city"] == "深圳"
    assert ranked["boss"] == "王顾问"


def test_liepin_greet_preview_rejects_non_liepin_ranked_input(monkeypatch, tmp_path, capsys):
    input_path = tmp_path / "boss_ranked.json"
    input_path.write_text(
        json.dumps({"jobs": [{"name": "AI产品经理", "platform": "zhipin"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "_require_license_or_exit", lambda command: None)
    args = parse_args("liepin", "greet", "preview", "--input", str(input_path))

    with pytest.raises(SystemExit) as exc:
        _cmd_liepin_greet_preview(args)

    assert exc.value.code == 2
    assert "liepin_greet_preview_input_platform_mismatch" in capsys.readouterr().out


def test_liepin_greet_preview_calls_cloud_preview_with_safe_next_message(monkeypatch, tmp_path):
    input_path = tmp_path / "liepin_ranked.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "AI产品经理",
                    "salary": "40-60k",
                    "company": "Liepin Example",
                    "city": "深圳",
                    "url": "https://www.liepin.com/job/lp-1.shtml",
                    "platform": "liepin",
                    "score": 92,
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    captured = {}

    def fake_preview(args, next_message=None):
        captured["next_message"] = next_message

    monkeypatch.setattr(cli, "_require_license_or_exit", lambda command: None)
    monkeypatch.setattr(cli, "_cmd_greet_preview_cloud", fake_preview)
    args = parse_args("liepin", "greet", "preview", "--input", str(input_path))

    _cmd_liepin_greet_preview(args)

    assert "jobagent liepin apply open" in captured["next_message"]
    assert "Automatic Liepin send is not supported" in captured["next_message"]


def test_liepin_greet_preview_local_injects_manual_handoff_greeting(monkeypatch, tmp_path):
    input_path = tmp_path / "liepin_ranked.json"
    output_path = tmp_path / "liepin_ready.json"
    input_path.write_text(
        json.dumps({
            "via": "local",
            "platform": "liepin",
            "jobs": [
                {
                    "name": "AI产品经理",
                    "salary": "40-60k",
                    "company": "Liepin Example",
                    "city": "深圳",
                    "boss": "王顾问",
                    "url": "https://www.liepin.com/job/lp-1.shtml",
                    "platform": "liepin",
                    "score": 92,
                    "reasons": ["岗位方向与您的 AI 产品经验高度相关"],
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli,
        "_require_license_or_exit",
        lambda command: pytest.fail("local Liepin greet preview must not require license"),
    )
    monkeypatch.setattr(
        cli,
        "_cmd_greet_preview_cloud",
        lambda *args, **kwargs: pytest.fail("local Liepin greet preview must not call cloud greet"),
    )
    args = parse_args(
        "liepin", "greet", "preview",
        "--local",
        "--config", str(tmp_path / "missing.yaml"),
        "--input", str(input_path),
        "--output", str(output_path),
    )

    _cmd_liepin_greet_preview(args)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    job = payload["jobs"][0]
    assert payload["greeting_via"] == "local"
    assert job["greeting_source"] == "local"
    assert "王顾问您好" in job["greeting"]
    assert "岗位方向与您的 AI 产品经验高度相关" in job["greeting"]
    assert "cloud_greeting" not in job


def test_liepin_greet_send_rejects_automatic_send_with_manual_next(monkeypatch, tmp_path, capsys):
    input_path = tmp_path / "liepin_ready.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "AI产品经理",
                    "company": "Liepin Example",
                    "url": "https://www.liepin.com/job/lp-1.shtml",
                    "platform": "liepin",
                    "cloud_greeting": "您好，想进一步沟通这个岗位。",
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    args = parse_args(
        "liepin",
        "greet",
        "send",
        "--input",
        str(input_path),
        "--limit",
        "1",
    )

    with pytest.raises(SystemExit) as exc:
        _cmd_liepin_greet_send(args)

    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "liepin_automatic_send_not_supported"
    assert payload["next_suggested"] == f"jobagent liepin apply open --input {input_path} --limit 1"


def test_liepin_apply_opener_opens_jobs_and_writes_audit(tmp_path):
    audit_path = tmp_path / "liepin_audit.json"
    audit_log = LiepinAuditLog(path=audit_path)
    driver = FakeOpenDriver()
    jobs = [
        {
            "name": "AI产品经理",
            "company": "Liepin Example",
            "url": "https://www.liepin.com/job/lp-1.shtml",
            "platform": "liepin",
            "cloud_greeting": "您好，我对这个 AI 产品岗位很感兴趣。",
            "score": 92,
        }
    ]

    result = LiepinApplyOpener(driver=driver, audit_log=audit_log).open_jobs(
        jobs,
        limit=1,
        wait_seconds=2,
    )

    assert result.ok is True
    assert result.opened == 1
    assert result.planned == 0
    assert result.requires_user_action is True
    assert "handoff greeting" in result.next_suggested
    assert result.events[0]["evidence"]["has_greeting"] is True
    assert result.events[0]["evidence"]["greeting"] == "您好，我对这个 AI 产品岗位很感兴趣。"
    assert result.events[0]["evidence"]["score"] == 92
    assert result.handoff[0]["action"] == "copy_greeting_to_liepin_page"
    assert result.handoff[0]["greeting"] == "您好，我对这个 AI 产品岗位很感兴趣。"
    assert result.to_payload()["handoff"][0]["company"] == "Liepin Example"
    assert result.to_payload()["user_prompt"].startswith("请在已打开的猎聘页面中人工确认岗位")
    assert driver.opened == ["https://www.liepin.com/job/lp-1.shtml:2"]
    records = json.loads(audit_path.read_text(encoding="utf-8"))
    assert records[0]["action"] == "apply_open"
    assert records[0]["status"] == "opened"
    assert records[0]["platform"] == "liepin"
    assert records[0]["evidence"]["greeting"] == "您好，我对这个 AI 产品岗位很感兴趣。"


def test_liepin_apply_opener_dry_run_does_not_need_driver(tmp_path):
    audit_path = tmp_path / "liepin_audit.json"
    jobs = [
        {
            "name": "AI产品经理",
            "company": "Liepin Example",
            "url": "https://www.liepin.com/job/lp-1.shtml",
            "platform": "liepin",
            "cloud_greeting": "您好，想进一步沟通。",
        }
    ]

    result = LiepinApplyOpener(audit_log=LiepinAuditLog(path=audit_path)).open_jobs(
        jobs,
        dry_run=True,
    )

    assert result.ok is True
    assert result.opened == 0
    assert result.planned == 1
    assert result.requires_user_action is False
    assert "without `--dry-run`" in result.next_suggested
    assert result.handoff[0]["status"] == "planned"
    assert result.handoff[0]["action"] == "copy_greeting_to_liepin_page"
    assert "user_prompt" not in result.to_payload()
    records = json.loads(audit_path.read_text(encoding="utf-8"))
    assert records[0]["status"] == "planned"
    assert records[0]["evidence"]["has_greeting"] is True


def test_liepin_apply_sender_delivers_and_writes_audit(tmp_path):
    audit_path = tmp_path / "liepin_audit.json"
    driver = FakeLiepinSendDriver()
    jobs = [
        {
            "name": "AI产品经理",
            "company": "Liepin Example",
            "url": "https://www.liepin.com/job/lp-1.shtml",
            "platform": "liepin",
            "greeting": "您好，想进一步沟通这个岗位。",
            "score": 92,
        }
    ]

    attempts = LiepinApplySender(
        driver=driver,
        audit_log=LiepinAuditLog(path=audit_path),
    ).send_batch(jobs, limit=1, wait_seconds=2)

    assert len(attempts) == 1
    assert attempts[0].delivered is True
    assert attempts[0].error == ""
    assert driver.opened == ["https://www.liepin.com/job/lp-1.shtml:2"]
    assert [step["step"] for step in attempts[0].steps] == [
        "open_job_url",
        "inspect_before_apply",
        "click_apply_or_contact_entry",
        "inspect_apply_state",
        "fill_liepin_message",
        "click_liepin_confirm",
        "inspect_after_confirm",
    ]
    records = json.loads(audit_path.read_text(encoding="utf-8"))
    assert records[0]["action"] == "apply_send"
    assert records[0]["status"] == "delivered"
    assert records[0]["evidence"]["has_greeting"] is True
    assert records[0]["evidence"]["greeting"] == "您好，想进一步沟通这个岗位。"


def test_liepin_apply_sender_dry_run_does_not_open_browser(tmp_path):
    audit_path = tmp_path / "liepin_audit.json"
    jobs = [
        {
            "name": "AI产品经理",
            "company": "Liepin Example",
            "url": "https://www.liepin.com/job/lp-1.shtml",
            "platform": "liepin",
            "greeting": "您好，想进一步沟通。",
        }
    ]

    attempts = LiepinApplySender(
        audit_log=LiepinAuditLog(path=audit_path),
    ).send_batch(jobs, dry_run=True)

    assert attempts[0].delivered is False
    assert attempts[0].error == "dry_run"
    assert attempts[0].steps == [
        {"step": "plan_liepin_apply_send", "ok": True, "url": "https://www.liepin.com/job/lp-1.shtml"}
    ]
    records = json.loads(audit_path.read_text(encoding="utf-8"))
    assert records[0]["action"] == "apply_send"
    assert records[0]["status"] == "planned"


def test_liepin_apply_sender_skips_previously_delivered_url(tmp_path):
    audit_path = tmp_path / "liepin_audit.json"
    audit_path.write_text(
        json.dumps([
            {
                "platform": "liepin",
                "action": "apply_send",
                "status": "delivered",
                "job_url": "https://www.liepin.com/job/lp-1.shtml",
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    driver = FakeLiepinSendDriver()
    jobs = [
        {
            "name": "已投岗位",
            "company": "Liepin Example",
            "url": "https://www.liepin.com/job/lp-1.shtml",
            "platform": "liepin",
            "greeting": "您好，想进一步沟通。",
        },
        {
            "name": "新岗位",
            "company": "Liepin Example",
            "url": "https://www.liepin.com/job/lp-2.shtml",
            "platform": "liepin",
            "greeting": "您好，想进一步沟通。",
        },
    ]

    attempts = LiepinApplySender(driver=driver, audit_log=LiepinAuditLog(path=audit_path)).send_batch(
        jobs,
        limit=2,
    )

    assert [attempt.error for attempt in attempts] == ["already_delivered", ""]
    assert attempts[0].steps[0]["step"] == "skip_liepin_apply_send"
    assert driver.opened == ["https://www.liepin.com/job/lp-2.shtml:3"]
    records = json.loads(audit_path.read_text(encoding="utf-8"))
    assert records[-2]["status"] == "skipped"
    assert records[-1]["status"] == "delivered"


def test_liepin_apply_sender_stops_on_first_failure(monkeypatch, tmp_path):
    monkeypatch.setattr("jobagent.platforms.liepin.apply.time.sleep", lambda _: None)
    audit_path = tmp_path / "liepin_audit.json"
    driver = FakeLiepinSendDriver(delivered_after_confirm=False)
    jobs = [
        {
            "name": "失败岗位",
            "company": "Liepin Example",
            "url": "https://www.liepin.com/job/lp-1.shtml",
            "platform": "liepin",
            "greeting": "您好，想进一步沟通。",
        },
        {
            "name": "不应继续岗位",
            "company": "Liepin Example",
            "url": "https://www.liepin.com/job/lp-2.shtml",
            "platform": "liepin",
            "greeting": "您好，想进一步沟通。",
        },
    ]

    attempts = LiepinApplySender(driver=driver, audit_log=LiepinAuditLog(path=audit_path)).send_batch(
        jobs,
        limit=2,
    )

    assert len(attempts) == 1
    assert attempts[0].delivered is False
    assert attempts[0].error == "delivery_not_verified"
    assert driver.opened == ["https://www.liepin.com/job/lp-1.shtml:3"]
    records = json.loads(audit_path.read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["status"] == "failed"


def test_liepin_apply_sender_accepts_liepin_default_chat_success(tmp_path):
    audit_path = tmp_path / "liepin_audit.json"

    class FakeDefaultChatDriver:
        def __init__(self):
            self.inspect_count = 0

        def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
            return {"ok": True, "url": url, "title": "岗位详情"}

        def _exec_js(self, script: str):
            if "apply_entry_not_found" in script:
                return {"ok": True, "clicked": "聊一聊"}
            if "loginRequired" in script:
                self.inspect_count += 1
                return {
                    "ok": True,
                    "href": "https://www.liepin.com/job/lp-1.shtml",
                    "title": "岗位详情",
                    "loginRequired": False,
                    "delivered": self.inspect_count >= 2,
                    "requires_user_action": False,
                    "bodySnippet": "我对您在招的AI产品经理职位很感兴趣，希望可以详聊。\n未读"
                    if self.inspect_count >= 2
                    else "岗位详情 聊一聊",
                }
            return {"ok": True}

    jobs = [
        {
            "name": "AI产品经理",
            "company": "Liepin Example",
            "url": "https://www.liepin.com/job/lp-1.shtml",
            "platform": "liepin",
            "greeting": "您好，想进一步沟通。",
        }
    ]

    attempts = LiepinApplySender(
        driver=FakeDefaultChatDriver(),
        audit_log=LiepinAuditLog(path=audit_path),
    ).send_batch(jobs)

    assert attempts[0].delivered is True
    assert attempts[0].error == ""
    assert [step["step"] for step in attempts[0].steps] == [
        "open_job_url",
        "inspect_before_apply",
        "click_apply_or_contact_entry",
        "inspect_apply_state",
    ]


def test_liepin_apply_open_cli_rejects_non_liepin_input(tmp_path, capsys):
    input_path = tmp_path / "boss_ready.json"
    input_path.write_text(
        json.dumps({"jobs": [{"name": "AI产品经理", "platform": "boss"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    args = parse_args("liepin", "apply", "open", "--input", str(input_path), "--dry-run")

    with pytest.raises(SystemExit) as exc:
        _cmd_liepin_apply_open(args)

    assert exc.value.code == 2
    assert "liepin_apply_open_input_platform_mismatch" in capsys.readouterr().out


def test_liepin_apply_open_cli_dry_run_writes_payload(monkeypatch, tmp_path, capsys):
    audit_path = tmp_path / "liepin_audit.json"
    input_path = tmp_path / "liepin_ready.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "AI产品经理",
                    "company": "Liepin Example",
                    "url": "https://www.liepin.com/job/lp-1.shtml",
                    "platform": "liepin",
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr("jobagent.platforms.liepin.audit.liepin_audit_log_path", lambda: audit_path)
    args = parse_args("liepin", "apply", "open", "--input", str(input_path), "--dry-run")

    with pytest.raises(SystemExit) as exc:
        _cmd_liepin_apply_open(args)

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert '"mode": "manual_apply_open"' in out
    assert '"planned": 1' in out
    assert '"opened": 0' in out
    assert '"handoff": [' in out
    assert '"requires_user_action": false' in out
    assert '"warning": "selected_jobs_missing_greeting"' in out
    assert '"missing_greeting_indexes": [' in out


def test_liepin_apply_open_cli_require_greeting_blocks_missing_greeting(tmp_path, capsys):
    input_path = tmp_path / "liepin_ranked.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "AI产品经理",
                    "company": "Liepin Example",
                    "url": "https://www.liepin.com/job/lp-1.shtml",
                    "platform": "liepin",
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    args = parse_args(
        "liepin",
        "apply",
        "open",
        "--input",
        str(input_path),
        "--require-greeting",
        "--dry-run",
    )

    with pytest.raises(SystemExit) as exc:
        _cmd_liepin_apply_open(args)

    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "liepin_apply_open_missing_greeting"
    assert payload["missing_indexes"] == [0]
    assert "liepin greet preview" in payload["next_suggested"]


def test_liepin_apply_send_cli_rejects_non_liepin_input(tmp_path, capsys):
    input_path = tmp_path / "boss_ready.json"
    input_path.write_text(
        json.dumps({"jobs": [{"name": "AI产品经理", "platform": "boss"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    args = parse_args("liepin", "apply", "send", "--input", str(input_path), "--dry-run")

    with pytest.raises(SystemExit) as exc:
        _cmd_liepin_apply_send(args)

    assert exc.value.code == 2
    assert "liepin_apply_send_input_platform_mismatch" in capsys.readouterr().out


def test_liepin_apply_send_cli_require_greeting_blocks_missing_greeting(tmp_path, capsys):
    input_path = tmp_path / "liepin_ranked.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "AI产品经理",
                    "company": "Liepin Example",
                    "url": "https://www.liepin.com/job/lp-1.shtml",
                    "platform": "liepin",
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    args = parse_args(
        "liepin",
        "apply",
        "send",
        "--input",
        str(input_path),
        "--require-greeting",
        "--dry-run",
    )

    with pytest.raises(SystemExit) as exc:
        _cmd_liepin_apply_send(args)

    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "liepin_apply_send_missing_greeting"
    assert payload["missing_indexes"] == [0]


def test_liepin_apply_send_cli_dry_run_writes_payload(monkeypatch, tmp_path, capsys):
    audit_path = tmp_path / "liepin_audit.json"
    input_path = tmp_path / "liepin_ready.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "AI产品经理",
                    "company": "Liepin Example",
                    "url": "https://www.liepin.com/job/lp-1.shtml",
                    "platform": "liepin",
                    "greeting": "您好，想进一步沟通。",
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr("jobagent.platforms.liepin.audit.liepin_audit_log_path", lambda: audit_path)
    args = parse_args("liepin", "apply", "send", "--input", str(input_path), "--dry-run", "--require-greeting")

    with pytest.raises(SystemExit) as exc:
        _cmd_liepin_apply_send(args)

    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "automatic_apply_send"
    assert payload["planned"] == 1
    assert payload["delivered"] == 0
    assert payload["failed"] == 0
    assert payload["attempts"][0]["error"] == "dry_run"


def test_liepin_apply_open_cli_stops_before_open_when_login_required(monkeypatch, tmp_path, capsys):
    input_path = tmp_path / "liepin_ready.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "AI产品经理",
                    "company": "Liepin Example",
                    "url": "https://www.liepin.com/job/lp-1.shtml",
                    "platform": "liepin",
                    "cloud_greeting": "您好，想进一步沟通。",
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    fake_driver = object()
    opened = {"called": False}

    class FakeSessionGuide:
        def __init__(self, driver=None):
            assert driver is fake_driver

        def check(self, wait_seconds: int = 5, **kwargs):
            return LiepinSessionStatus(ok=True, logged_in=False, login_required=True)

    class FakeOpener:
        def __init__(self, driver=None):
            opened["called"] = True

    monkeypatch.setattr("jobagent.drivers.boss.create_driver", lambda: fake_driver)
    monkeypatch.setattr("jobagent.platforms.liepin.LiepinSessionGuide", FakeSessionGuide)
    monkeypatch.setattr("jobagent.platforms.liepin.LiepinApplyOpener", FakeOpener)
    args = parse_args("liepin", "apply", "open", "--input", str(input_path))

    with pytest.raises(SystemExit) as exc:
        _cmd_liepin_apply_open(args)

    assert exc.value.code == 2
    assert opened["called"] is False
    captured = capsys.readouterr()
    assert '"mode": "apply_open_login_check"' in captured.out


def test_liepin_apply_send_cli_dry_run_does_not_require_confirmation(monkeypatch, tmp_path, capsys):
    audit_path = tmp_path / "liepin_audit.json"
    input_path = tmp_path / "liepin_ready.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "AI产品经理",
                    "company": "Liepin Example",
                    "url": "https://www.liepin.com/job/lp-1.shtml",
                    "platform": "liepin",
                    "greeting": "您好，想进一步沟通。",
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr("jobagent.platforms.liepin.audit.liepin_audit_log_path", lambda: audit_path)
    args = parse_args("liepin", "apply", "send", "--input", str(input_path), "--limit", "1", "--dry-run")

    with pytest.raises(SystemExit) as exc:
        _cmd_liepin_apply_send(args)

    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["planned"] == 1
    assert payload["failed"] == 0


def test_liepin_apply_send_cli_skips_delivered_from_audit(monkeypatch, tmp_path, capsys):
    audit_path = tmp_path / "liepin_audit.json"
    audit_path.write_text(
        json.dumps([
            {
                "platform": "liepin",
                "action": "apply_send",
                "status": "delivered",
                "job_url": "https://www.liepin.com/job/lp-1.shtml",
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    input_path = tmp_path / "liepin_ready.json"
    input_path.write_text(
        json.dumps({
            "jobs": [
                {
                    "name": "AI产品经理",
                    "company": "Liepin Example",
                    "url": "https://www.liepin.com/job/lp-1.shtml",
                    "platform": "liepin",
                    "greeting": "您好，想进一步沟通。",
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr("jobagent.platforms.liepin.audit.liepin_audit_log_path", lambda: audit_path)
    args = parse_args("liepin", "apply", "send", "--input", str(input_path), "--limit", "1", "--dry-run")

    with pytest.raises(SystemExit) as exc:
        _cmd_liepin_apply_send(args)

    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected"] == 1
    assert payload["total"] == 1
    assert payload["skipped"] == 1
    assert payload["planned"] == 0
    assert payload["attempts"][0]["error"] == "already_delivered"


def test_liepin_apply_sender_submits_resume_without_greeting(tmp_path):
    audit_path = tmp_path / "liepin_audit.json"

    class FakeSubmitDriver:
        def __init__(self):
            self.inspect_count = 0
            self.scripts: list[str] = []

        def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
            return {"ok": True, "url": url, "title": "岗位详情"}

        def _exec_js(self, script: str):
            self.scripts.append(script)
            if "apply_entry_not_found" in script:
                return {"ok": True, "clicked": "投递简历"}
            if "filled:false" in script:
                return {"ok": True, "filled": False, "reason": "editor_not_found"}
            if "confirm_button_not_found" in script:
                return {"ok": True, "clicked": "立即投递"}
            if "loginRequired" in script:
                self.inspect_count += 1
                return {
                    "ok": True,
                    "href": "https://www.liepin.com/job/1.shtml",
                    "title": "投递成功" if self.inspect_count >= 3 else "岗位详情",
                    "loginRequired": False,
                    "delivered": self.inspect_count >= 3,
                    "requires_user_action": False,
                    "user_action": "",
                    "bodySnippet": "投递成功" if self.inspect_count >= 3 else "选择附件简历 立即投递",
                }
            return {"ok": True}

    driver = FakeSubmitDriver()
    jobs = [
        {
            "name": "AI产品经理",
            "company": "Liepin Example",
            "url": "https://www.liepin.com/job/lp-1.shtml",
            "platform": "liepin",
        }
    ]

    attempts = LiepinApplySender(driver=driver, audit_log=LiepinAuditLog(path=audit_path)).send_batch(
        jobs,
        limit=1,
    )

    assert len(attempts) == 1
    assert attempts[0].delivered is True
    assert attempts[0].error == ""
    assert any(step["step"] == "click_apply_or_contact_entry" and step["clicked"] == "投递简历" for step in attempts[0].steps)
    assert any(step["step"] == "click_liepin_confirm" and step["clicked"] == "立即投递" for step in attempts[0].steps)
    records = json.loads(audit_path.read_text(encoding="utf-8"))
    assert records[0]["action"] == "apply_send"
    assert records[0]["status"] == "delivered"


def test_liepin_apply_login_detection_ignores_share_qr_text():
    from jobagent.platforms.liepin.apply import _liepin_page_requires_login

    state = {
        "title": "【北京 C端AI产品经理招聘】-京东北京招聘信息-猎聘",
        "bodySnippet": "你好，冀先生\n投简历 聊一聊\n收藏 微信分享扫码\n职位介绍",
    }

    assert _liepin_page_requires_login(state) is False


def test_liepin_audit_cli_reads_platform_events(monkeypatch, tmp_path, capsys):
    audit_path = tmp_path / "liepin_audit.json"
    audit_path.write_text(
        json.dumps([
            {
                "platform": "liepin",
                "action": "apply_open",
                "status": "opened",
                "job_name": "AI产品经理",
                "evidence": {
                    "has_greeting": True,
                    "greeting": "您好，想进一步沟通这个岗位。",
                },
            },
            {
                "platform": "liepin",
                "action": "apply_open",
                "status": "opened",
                "job_name": "增长产品经理",
                "evidence": {
                    "has_greeting": False,
                    "greeting": "",
                },
            },
            {
                "platform": "liepin",
                "action": "apply_send",
                "status": "delivered",
                "job_name": "AI产品经理",
                "evidence": {
                    "has_greeting": True,
                    "greeting": "您好，想进一步沟通这个岗位。",
                },
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr("jobagent.platforms.liepin.audit.liepin_audit_log_path", lambda: audit_path)
    args = parse_args("liepin", "audit", "--recent", "1")

    _cmd_liepin_audit(args)

    out = capsys.readouterr().out
    assert '"platform": "liepin"' in out
    assert '"apply_open": 2' in out
    assert '"apply_send": 1' in out
    assert '"with_greeting": 1' in out
    assert '"missing_greeting": 1' in out
    assert '"delivered": 1' in out


def test_liepin_collect_without_fixture_or_query_fails_explicitly(capsys):
    args = parse_args("liepin", "collect")

    with pytest.raises(SystemExit) as exc:
        _cmd_liepin_collect(args)

    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "liepin_query_required" in out
    assert "next_suggested" in out
