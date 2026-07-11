from __future__ import annotations

import json

from jobagent.platforms.liepin.apply import (
    LiepinApplySender,
    _liepin_apply_click_message_send_script,
    _liepin_apply_click_resume_script,
    _liepin_apply_inspect_script,
    _liepin_delivery_detected,
    _liepin_page_requires_login,
)
from jobagent.platforms.liepin.audit import LiepinAuditLog


def test_liepin_default_chat_is_not_resume_or_personalized_delivery():
    state = {
        "title": "岗位详情",
        "href": "https://www.liepin.com/job/1.shtml",
        "loginRequired": False,
        "delivered": False,
        "resumeDelivered": False,
        "outgoingMessages": ["我对您在招的AI产品经理职位很感兴趣，希望可以详聊。"],
        "bodySnippet": "未读\n请登录猎聘APP查看消息\n发简历",
    }

    assert _liepin_delivery_detected(state) is False
    assert _liepin_page_requires_login(state) is False


def test_liepin_scripts_use_live_resume_editor_send_and_transcript_selectors():
    inspect = _liepin_apply_inspect_script()

    assert ".action-resume" in inspect
    assert "textarea.im-ui-textarea" in inspect
    assert ".im-ui-txt.send .text" in inspect
    assert ".action-resume" in _liepin_apply_click_resume_script()
    assert ".im-ui-basic-send-btn" in _liepin_apply_click_message_send_script()


def test_liepin_chat_only_job_verifies_resume_and_exact_signed_greeting(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("jobagent.platforms.liepin.apply.time.sleep", lambda _: None)

    class CompositeDriver:
        def __init__(self):
            self.chat_clicked = False
            self.resume_clicked = False
            self.message = ""
            self.message_sent = False

        def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
            return {"ok": True, "url": url}

        def _exec_js(self, script: str):
            if "chat_entry_not_found" in script:
                self.chat_clicked = True
                return {"ok": True, "clicked": "聊一聊"}
            if "resume_action_not_found" in script:
                self.resume_clicked = True
                return {"ok": True, "clicked": "发简历"}
            if "editor_not_found" in script:
                encoded = script.split("const message = ", 1)[1].split(";", 1)[0].strip()
                self.message = json.loads(encoded)
                return {"ok": True, "filled": True}
            if "message_send_button_not_found" in script:
                self.message_sent = True
                return {"ok": True, "clicked": "发送"}
            if "loginRequired" in script:
                outgoing = ["平台默认招呼语"] if self.chat_clicked else []
                if self.message_sent:
                    outgoing.append(self.message)
                return {
                    "ok": True,
                    "title": "岗位详情",
                    "loginRequired": False,
                    "chatOpen": self.chat_clicked,
                    "canSendResume": self.chat_clicked and not self.resume_clicked,
                    "resumeDelivered": self.resume_clicked,
                    "outgoingMessages": outgoing,
                    "requires_user_action": False,
                }
            return {"ok": True}

    greeting = "您好，我有11年产品经验，对贵司AI产品岗位很感兴趣。"
    attempts = LiepinApplySender(
        driver=CompositeDriver(),
        audit_log=LiepinAuditLog(path=tmp_path / "audit.json"),
    ).send_batch(
        [
            {
                "name": "AI产品经理",
                "company": "Example",
                "url": "https://www.liepin.com/job/1.shtml",
                "cloud_greeting": greeting,
            }
        ]
    )

    assert attempts[0].delivered is True
    assert attempts[0].message == greeting
    records = json.loads((tmp_path / "audit.json").read_text(encoding="utf-8"))
    assert records[0]["evidence"]["resume_delivered"] is True
    assert records[0]["evidence"]["greeting_delivered"] is True
