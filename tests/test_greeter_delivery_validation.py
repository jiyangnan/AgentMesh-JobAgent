from __future__ import annotations

from jobagent.domain.greeter import GreeterEngine
from jobagent.domain.models import Job
from jobagent.infra.config import GreeterConfig


class AutoSentDriver:
    def __init__(self, auto_sent_at: str, delivered: bool):
        self.auto_sent_at = auto_sent_at
        self.delivered = delivered
        self.calls: list[str] = []

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        self.calls.append("open_url_in_new_tab")
        return {"ok": True}

    def click_chat_entry(self):
        self.calls.append("click_chat_entry")
        return {"ok": True, "autoSent": self.auto_sent_at == "click"}

    def inspect_chat_editor(self):
        self.calls.append("inspect_chat_editor")
        return {
            "ok": True,
            "editorFound": False,
            "autoSent": self.auto_sent_at == "editor",
        }

    def fill_chat_message(self, message: str):
        self.calls.append("fill_chat_message")
        return {"ok": True}

    def click_send(self):
        self.calls.append("click_send")
        return {"ok": True}

    def verify_delivery(self, message: str):
        self.calls.append("verify_delivery")
        return {"ok": True, "delivered": self.delivered}


def make_job() -> Job:
    return Job(
        name="Product Manager",
        salary="30-50K",
        company="Example Co",
        area="南山",
        experience="3-5年",
        degree="本科",
        skills="AI",
        boss="张经理",
        city="深圳",
        url="https://example.test/job/1",
    )


def test_chat_click_auto_sent_requires_delivery_verification():
    driver = AutoSentDriver(auto_sent_at="click", delivered=False)
    engine = GreeterEngine(GreeterConfig(verify=True), driver=driver)

    attempt = engine._send_one(make_job(), "hello")

    assert attempt.delivered is False
    assert attempt.error == "auto_sent_not_verified"
    assert driver.calls == [
        "open_url_in_new_tab",
        "click_chat_entry",
        "verify_delivery",
    ]


def test_editor_auto_sent_requires_delivery_verification():
    driver = AutoSentDriver(auto_sent_at="editor", delivered=False)
    engine = GreeterEngine(GreeterConfig(verify=True), driver=driver)

    attempt = engine._send_one(make_job(), "hello")

    assert attempt.delivered is False
    assert attempt.error == "auto_sent_not_verified"
    assert driver.calls == [
        "open_url_in_new_tab",
        "click_chat_entry",
        "inspect_chat_editor",
        "verify_delivery",
    ]
