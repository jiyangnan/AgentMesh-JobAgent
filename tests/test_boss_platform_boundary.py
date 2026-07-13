from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobagent.drivers.boss.data_driver import BossDataDriver as LegacyBossDataDriver
from jobagent.domain.models import SendAttempt
from jobagent.infra.exceptions import LoginRequiredError, UserActionRequiredError
from jobagent.platforms.boss import BossDataDriver, boss_job_id, parse_boss_job
from jobagent.platforms.boss.send_flow import execute_boss_greeting_flow


FIXTURE = Path(__file__).parent / "fixtures" / "boss" / "search_joblist_page1.json"


def test_boss_parser_uses_platform_boundary_fixture():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))["zpData"]["jobList"][0]

    job = parse_boss_job(raw, city_name="深圳")

    assert boss_job_id(raw) == "abc123"
    assert job.platform == "boss"
    assert job.name == "AI产品经理"
    assert job.company == "Example AI"
    assert job.area == "南山区·科技园"
    assert job.skills == "AI, LLM, 产品设计"
    assert job.boss == "张经理 · HR"
    assert job.url == "https://www.zhipin.com/job_detail/abc123.html"


def test_legacy_boss_data_driver_import_still_points_to_platform_flow():
    assert LegacyBossDataDriver is BossDataDriver


class BossSearchDriver:
    def __init__(self, response=None):
        self.response = response or {
            "code": 0,
            "zpData": {"jobList": [
                {
                    "encryptJobId": "api-job-1",
                    "jobName": "AI产品经理",
                    "salaryDesc": "30-50K·16薪",
                    "brandName": "Example AI",
                    "cityName": "深圳",
                    "areaDistrict": "南山区",
                    "businessDistrict": "科技园",
                    "jobExperience": "5-10年",
                    "jobDegree": "本科",
                    "jobUrl": "https://www.zhipin.com/job_detail/api-job-1.html",
                }
            ]},
        }
        self.api_calls = []

    def api_fetch(self, url):
        self.api_calls.append(url)
        return self.response


def test_boss_collect_queries_browser_api_without_opening_search_page():
    driver = BossSearchDriver()
    jobs = BossDataDriver(driver=driver).fetch_jobs(
        "AI产品经理",
        "101280600",
        city_name="深圳",
        page=2,
        page_size=15,
    )

    assert len(jobs) == 1
    assert jobs[0].name == "AI产品经理"
    assert jobs[0].salary == "30-50K·16薪"
    assert jobs[0].company == "Example AI"
    assert jobs[0].area == "南山区·科技园"
    assert boss_job_id(jobs[0].raw_data) == "api-job-1"
    assert len(driver.api_calls) == 1
    assert driver.api_calls[0].startswith(BossDataDriver.API_URL)
    assert "query=AI%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86" in driver.api_calls[0]
    assert "city=101280600" in driver.api_calls[0]
    assert "page=2" in driver.api_calls[0]


def test_boss_collect_preserves_login_intervention():
    driver = BossSearchDriver(response={"code": 1, "message": "请先登录"})

    with pytest.raises(LoginRequiredError):
        BossDataDriver(driver=driver).fetch_jobs("AI产品经理", "101280600")


def test_boss_collect_preserves_security_verification_intervention():
    driver = BossSearchDriver(response={"code": 37, "message": "请完成安全验证"})

    with pytest.raises(UserActionRequiredError) as error:
        BossDataDriver(driver=driver).fetch_jobs("AI产品经理", "101280600")

    assert error.value.code == "verification_required"
    assert "完成安全验证" in error.value.user_prompt


def test_boss_collect_surfaces_unknown_api_failure():
    driver = BossSearchDriver(response={"code": 5001, "message": "service unavailable"})

    with pytest.raises(RuntimeError, match="5001"):
        BossDataDriver(driver=driver).fetch_jobs("AI产品经理", "101280600")


class FlowDriver:
    def __init__(self, delivered: bool = True, delivered_sequence: list[bool] | None = None):
        self.delivered = delivered
        self.delivered_sequence = list(delivered_sequence or [])
        self.calls: list[str] = []

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        self.calls.append("open_url_in_new_tab")
        return {"ok": True}

    def click_chat_entry(self):
        self.calls.append("click_chat_entry")
        return {"ok": True, "autoSent": False}

    def inspect_chat_editor(self):
        self.calls.append("inspect_chat_editor")
        return {"ok": True, "editorFound": True}

    def fill_chat_message(self, message: str):
        self.calls.append("fill_chat_message")
        return {"ok": True}

    def click_send(self):
        self.calls.append("click_send")
        return {"ok": True}

    def verify_delivery(self, message: str):
        self.calls.append("verify_delivery")
        delivered = self.delivered_sequence.pop(0) if self.delivered_sequence else self.delivered
        return {"ok": True, "delivered": delivered}


class NoEditorDeliveredDriver(FlowDriver):
    def inspect_chat_editor(self):
        self.calls.append("inspect_chat_editor")
        return {"ok": True, "editorFound": False}


class LoginDialogDriver(FlowDriver):
    def inspect_chat_editor(self):
        self.calls.append("inspect_chat_editor")
        return {"ok": True, "editorFound": False, "loginDialog": True}


class LoginDialogRecoveredDriver(LoginDialogDriver):
    def recover_draft_delivery(self, message: str):
        self.calls.append("recover_draft_delivery")
        return {"ok": True, "delivered": True}


class PlatformDefaultSentDriver(FlowDriver):
    def click_chat_entry(self):
        self.calls.append("click_chat_entry")
        return {
            "ok": True,
            "autoSent": True,
            "platformDefaultSent": True,
            "sentMessage": "已发送 这是我的资料，希望能够成为贵团队的一员。",
        }


def test_boss_send_flow_preserves_verified_step_order():
    driver = FlowDriver(delivered_sequence=[False, True])

    attempt = execute_boss_greeting_flow(
        driver,
        "https://example.test/job/1",
        "hello",
    )

    assert isinstance(attempt, SendAttempt)
    assert attempt.delivered is True
    assert attempt.error == ""
    assert driver.calls == [
        "open_url_in_new_tab",
        "click_chat_entry",
        "inspect_chat_editor",
        "verify_delivery",
        "fill_chat_message",
        "click_send",
        "verify_delivery",
    ]


def test_boss_probe_flow_can_disable_retry():
    driver = FlowDriver(delivered=False)

    attempt = execute_boss_greeting_flow(
        driver,
        "https://example.test/job/1",
        "hello",
        retry_on_unverified=False,
    )

    assert attempt.delivered is False
    assert attempt.error == "delivery_not_verified"
    assert driver.calls == [
        "open_url_in_new_tab",
        "click_chat_entry",
        "inspect_chat_editor",
        "verify_delivery",
        "fill_chat_message",
        "click_send",
        "verify_delivery",
    ]


def test_boss_send_flow_skips_duplicate_when_already_delivered():
    driver = FlowDriver(delivered=True)

    attempt = execute_boss_greeting_flow(
        driver,
        "https://example.test/job/1",
        "hello",
    )

    assert attempt.delivered is True
    assert attempt.error == ""
    assert driver.calls == [
        "open_url_in_new_tab",
        "click_chat_entry",
        "inspect_chat_editor",
        "verify_delivery",
    ]


def test_boss_send_flow_continues_after_platform_default_sent_dialog():
    driver = PlatformDefaultSentDriver(delivered_sequence=[False, True])

    attempt = execute_boss_greeting_flow(
        driver,
        "https://example.test/job/1",
        "hello",
    )

    assert attempt.delivered is True
    assert attempt.error == ""
    assert driver.calls == [
        "open_url_in_new_tab",
        "click_chat_entry",
        "inspect_chat_editor",
        "verify_delivery",
        "fill_chat_message",
        "click_send",
        "verify_delivery",
    ]
    assert attempt.steps[1]["platformDefaultSent"] is True
    assert attempt.steps[2]["step"] == "platform_default_does_not_complete_custom_greeting"


def test_boss_send_flow_verifies_before_failing_missing_editor():
    driver = NoEditorDeliveredDriver(delivered=True)

    attempt = execute_boss_greeting_flow(
        driver,
        "https://example.test/job/1",
        "hello",
    )

    assert attempt.delivered is True
    assert attempt.error == ""
    assert driver.calls == [
        "open_url_in_new_tab",
        "click_chat_entry",
        "inspect_chat_editor",
        "verify_delivery",
    ]


def test_boss_send_flow_reports_login_dialog_as_login_required():
    driver = LoginDialogDriver(delivered=False)

    attempt = execute_boss_greeting_flow(
        driver,
        "https://example.test/job/1",
        "hello",
    )

    assert attempt.delivered is False
    assert attempt.error == "login_required"
    assert driver.calls == [
        "open_url_in_new_tab",
        "click_chat_entry",
        "inspect_chat_editor",
    ]


def test_boss_send_flow_recovers_matching_draft_from_login_dialog():
    driver = LoginDialogRecoveredDriver(delivered=False)

    attempt = execute_boss_greeting_flow(
        driver,
        "https://example.test/job/1",
        "hello",
    )

    assert attempt.delivered is True
    assert attempt.error == ""
    assert driver.calls == [
        "open_url_in_new_tab",
        "click_chat_entry",
        "inspect_chat_editor",
        "recover_draft_delivery",
    ]
