from __future__ import annotations

from jobagent.platforms.liepin.apply import (
    LiepinApplySender,
    _liepin_delivery_detected,
    _liepin_page_requires_login,
)
from jobagent.platforms.liepin.audit import LiepinAuditLog


def test_liepin_default_chat_is_not_resume_delivery():
    state = {
        "title": "岗位详情",
        "href": "https://www.liepin.com/job/1.shtml",
        "loginRequired": False,
        "delivered": False,
        "bodySnippet": (
            "我对您在招的AI产品经理职位很感兴趣，希望可以详聊。\n"
            "未读\n不支持此消息查看，请登录“猎聘APP”查看消息内容！\n发简历"
        ),
    }

    assert _liepin_delivery_detected(state) is False
    assert _liepin_page_requires_login(state) is False


def test_liepin_chat_only_job_continues_to_send_resume(monkeypatch, tmp_path):
    monkeypatch.setattr("jobagent.platforms.liepin.apply.time.sleep", lambda _: None)

    class ChatOnlyDriver:
        def __init__(self):
            self.chat_clicked = False
            self.resume_clicked = False

        def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
            return {"ok": True, "url": url}

        def _exec_js(self, script: str):
            if "apply_entry_not_found" in script:
                self.chat_clicked = True
                return {"ok": True, "clicked": "聊一聊"}
            if "resume_action_not_found" in script:
                self.resume_clicked = True
                return {"ok": True, "clicked": "发简历"}
            if "loginRequired" in script:
                return {
                    "ok": True,
                    "title": "岗位详情",
                    "loginRequired": False,
                    "delivered": self.resume_clicked,
                    "canSendResume": self.chat_clicked and not self.resume_clicked,
                    "requires_user_action": False,
                    "bodySnippet": (
                        "简历发送成功"
                        if self.resume_clicked
                        else "默认聊天消息\n未读\n请登录猎聘APP查看消息\n发简历"
                    ),
                }
            return {"ok": True}

    attempts = LiepinApplySender(
        driver=ChatOnlyDriver(),
        audit_log=LiepinAuditLog(path=tmp_path / "audit.json"),
    ).send_batch(
        [
            {
                "name": "AI产品经理",
                "company": "Example",
                "url": "https://www.liepin.com/job/1.shtml",
            }
        ]
    )

    assert attempts[0].delivered is True
    assert [step["step"] for step in attempts[0].steps] == [
        "open_job_url",
        "inspect_before_apply",
        "click_apply_or_contact_entry",
        "inspect_apply_state",
        "click_liepin_resume_action",
        "inspect_after_resume_action",
    ]
