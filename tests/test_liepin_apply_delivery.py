from __future__ import annotations

import json

import pytest

from jobagent.application.delivery import (
    UserInterventionRequired,
    _raise_for_apply_user_intervention,
)
from jobagent.domain.models import SendAttempt
from jobagent.platforms.liepin.apply import (
    LiepinApplySender,
    _liepin_apply_click_message_send_script,
    _liepin_apply_click_resume_script,
    _liepin_apply_inspect_script,
    _click_liepin_resume_confirm,
    _liepin_attempt_delivery_parts,
    _liepin_delivery_detected,
    _liepin_page_requires_login,
    _poll_liepin_state,
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
    assert ".im-ui-txt-content" in inspect
    assert ".action-resume" in _liepin_apply_click_resume_script()
    assert "投简历" in _liepin_apply_click_resume_script()
    assert "chat_resume_action_not_ready" in _liepin_apply_click_resume_script()
    assert "立即投递" in inspect
    assert "!resumeDelivered" in inspect
    assert ".im-ui-basic-send-btn" in _liepin_apply_click_message_send_script()
    assert "message_moderation_required" in inspect
    assert "你的发言疑似存在不良信息" in inspect
    assert ".im-ui-system-tip,.im-ui-message-item" in inspect
    assert "moderation_notice_blocking" in inspect


def test_liepin_stops_before_actions_on_moderation_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr("jobagent.platforms.liepin.apply.time.sleep", lambda _: None)

    class ModeratedChatDriver:
        def __init__(self):
            self.resume_clicks = 0

        def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
            return {"ok": True, "url": url}

        def _exec_js(self, script: str):
            if "resume_action_not_found" in script:
                self.resume_clicks += 1
                return {"ok": True, "clicked": "发简历"}
            return {
                "ok": True,
                "title": "岗位详情",
                "loginRequired": False,
                "chatOpen": True,
                "requires_user_action": True,
                "user_action": "message_moderation_required",
                "user_prompt": "你的发言疑似存在不良信息，请文明沟通。如需平台介入请及时反馈",
            }

    driver = ModeratedChatDriver()
    attempts = LiepinApplySender(
        driver=driver,
        audit_log=LiepinAuditLog(path=tmp_path / "audit.json"),
    ).send_batch([{
        "name": "AI产品经理",
        "company": "Example",
        "url": "https://www.liepin.com/job/moderated.shtml",
        "cloud_greeting": "您好，我对这个岗位很感兴趣。",
    }])

    assert attempts[0].delivered is False
    assert attempts[0].error == "message_moderation_required"
    assert driver.resume_clicks == 0


def test_apply_delivery_surfaces_exact_liepin_user_prompt():
    prompt = "你的发言疑似存在不良信息，请文明沟通。如需平台介入请及时反馈"
    attempt = SendAttempt(
        job_url="https://www.liepin.com/job/moderated.shtml",
        message="您好",
        delivered=False,
        error="message_moderation_required",
        steps=[{
            "step": "inspect_before_apply",
            "requires_user_action": True,
            "user_action": "message_moderation_required",
            "user_prompt": prompt,
        }],
    )

    with pytest.raises(UserInterventionRequired) as caught:
        _raise_for_apply_user_intervention("liepin", [attempt])

    assert caught.value.code == "message_moderation_required"
    assert caught.value.prompt == prompt


def test_liepin_audit_collects_resume_from_freshest_chat_state():
    attempt = SendAttempt(
        job_url="https://www.liepin.com/job/fresh-state.shtml",
        message="您好",
        delivered=True,
        steps=[{
            "step": "verify_liepin_resume_delivery",
            "delivered": False,
            "resumeDelivered": False,
        }, {
            "step": "verify_liepin_personalized_greeting",
            "delivered": True,
            "resumeDelivered": True,
        }],
    )

    assert _liepin_attempt_delivery_parts(attempt) == (True, True)


def test_liepin_chat_poll_waits_for_slow_im_modal(monkeypatch):
    monkeypatch.setattr("jobagent.platforms.liepin.apply.time.sleep", lambda _: None)

    class DelayedChatDriver:
        def __init__(self):
            self.inspections = 0

        def _exec_js(self, _script: str):
            self.inspections += 1
            return {
                "ok": True,
                "title": "岗位详情",
                "loginRequired": False,
                "chatOpen": self.inspections >= 3,
            }

    driver = DelayedChatDriver()
    state = _poll_liepin_state(driver, attempts=5, require_chat=True)

    assert state["chatOpen"] is True
    assert driver.inspections == 3


def test_liepin_resume_confirm_waits_for_stable_button_coordinates(monkeypatch):
    monkeypatch.setattr("jobagent.platforms.liepin.apply.time.sleep", lambda _: None)

    class AnimatedConfirmDriver:
        def __init__(self):
            self.targets = iter([(765.0, 388.0), (911.0, 437.0), (911.0, 437.0)])
            self.clicks = []

        def _exec_js(self, _script: str):
            x, y = next(self.targets)
            return {"ok": True, "clicked": "立即投递", "x": x, "y": y}

        def _click_at(self, x: float, y: float):
            self.clicks.append((x, y))

    driver = AnimatedConfirmDriver()
    result = _click_liepin_resume_confirm(driver)

    assert result["ok"] is True
    assert driver.clicks == [(911.0, 437.0)]


def test_liepin_resume_delivery_confirms_selected_attachment(monkeypatch, tmp_path):
    monkeypatch.setattr("jobagent.platforms.liepin.apply.time.sleep", lambda _: None)

    class TwoStageDriver:
        def __init__(self):
            self.dialog_open = False
            self.resume_delivered = False
            self.message = ""
            self.message_sent = False
            self.native_clicks = []

        def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
            return {"ok": True, "url": url}

        def _click_at(self, x: float, y: float):
            self.native_clicks.append((x, y))
            if x == 100.0:
                self.dialog_open = True
            elif x == 200.0:
                self.resume_delivered = True

        def _exec_js(self, script: str):
            if "resume_action_not_found" in script:
                return {"ok": True, "clicked": "发简历", "x": 100.0, "y": 300.0}
            if "resume_confirm_button_not_found" in script:
                return {"ok": True, "clicked": "立即投递", "x": 200.0, "y": 400.0}
            if "editor_not_found" in script:
                encoded = script.split("const message = ", 1)[1].split(";", 1)[0].strip()
                self.message = json.loads(encoded)
                return {"ok": True, "filled": True}
            if "message_send_button_not_found" in script:
                self.message_sent = True
                return {"ok": True, "clicked": "发送"}
            return {
                "ok": True,
                "title": "岗位详情",
                "loginRequired": False,
                "chatOpen": True,
                "canSendResume": not self.resume_delivered,
                "canConfirmResume": self.dialog_open and not self.resume_delivered,
                "resumeAttachmentSelected": self.dialog_open,
                "resumeDelivered": self.resume_delivered,
                "outgoingMessages": [self.message] if self.message_sent else [],
                "requires_user_action": False,
            }

    driver = TwoStageDriver()
    attempts = LiepinApplySender(
        driver=driver,
        audit_log=LiepinAuditLog(path=tmp_path / "audit.json"),
    ).send_batch(
        [{
            "name": "AI产品经理",
            "company": "Example",
            "url": "https://www.liepin.com/job/2.shtml",
            "cloud_greeting": "您好，我对这个岗位很感兴趣。",
        }]
    )

    assert attempts[0].delivered is True
    assert driver.native_clicks == [(100.0, 300.0), (200.0, 400.0)]
    assert any(
        step["step"] == "click_liepin_resume_confirm"
        for step in attempts[0].steps
    )


def test_liepin_resume_action_retries_when_first_click_is_swallowed(monkeypatch, tmp_path):
    monkeypatch.setattr("jobagent.platforms.liepin.apply.time.sleep", lambda _: None)

    class SwallowedResumeActionDriver:
        def __init__(self):
            self.resume_action_clicks = 0
            self.dialog_open = False
            self.resume_delivered = False
            self.message = ""
            self.message_sent = False

        def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
            return {"ok": True, "url": url}

        def _click_at(self, x: float, _y: float):
            if x == 100.0:
                self.resume_action_clicks += 1
                if self.resume_action_clicks >= 2:
                    self.dialog_open = True
            elif x == 200.0:
                self.dialog_open = False
                self.resume_delivered = True

        def _exec_js(self, script: str):
            if "resume_action_not_found" in script:
                return {"ok": True, "clicked": "发简历", "x": 100.0, "y": 300.0}
            if "resume_confirm_button_not_found" in script:
                return {"ok": True, "clicked": "立即投递", "x": 200.0, "y": 400.0}
            if "editor_not_found" in script:
                encoded = script.split("const message = ", 1)[1].split(";", 1)[0].strip()
                self.message = json.loads(encoded)
                return {"ok": True, "filled": True}
            if "message_send_button_not_found" in script:
                self.message_sent = True
                return {"ok": True, "clicked": "发送"}
            return {
                "ok": True,
                "title": "岗位详情",
                "loginRequired": False,
                "chatOpen": True,
                "canSendResume": not self.resume_delivered,
                "canSendChatResume": not self.dialog_open and not self.resume_delivered,
                "canConfirmResume": self.dialog_open,
                "resumeAttachmentSelected": self.dialog_open,
                "resumeDelivered": self.resume_delivered,
                "outgoingMessages": [self.message] if self.message_sent else [],
                "requires_user_action": False,
            }

    driver = SwallowedResumeActionDriver()
    attempts = LiepinApplySender(
        driver=driver,
        audit_log=LiepinAuditLog(path=tmp_path / "audit.json"),
    ).send_batch([{
        "name": "AI产品经理",
        "company": "Example",
        "url": "https://www.liepin.com/job/retry-action.shtml",
        "cloud_greeting": "您好，我对这个岗位很感兴趣。",
    }])

    assert attempts[0].delivered is True
    assert driver.resume_action_clicks == 2


def test_liepin_resume_action_uses_dom_fallback_after_native_clicks(monkeypatch, tmp_path):
    monkeypatch.setattr("jobagent.platforms.liepin.apply.time.sleep", lambda _: None)

    class DomFallbackResumeDriver:
        def __init__(self):
            self.native_resume_clicks = 0
            self.dom_resume_clicks = 0
            self.dialog_open = False
            self.resume_delivered = False
            self.message = ""
            self.message_sent = False

        def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
            return {"ok": True, "url": url}

        def _click_at(self, x: float, _y: float):
            if x == 100.0:
                self.native_resume_clicks += 1
            elif x == 200.0:
                self.dialog_open = False
                self.resume_delivered = True

        def _exec_js(self, script: str):
            if "elementFromPoint" in script:
                self.dom_resume_clicks += 1
                self.dialog_open = True
                return {"ok": True}
            if "resume_action_not_found" in script:
                return {"ok": True, "clicked": "发简历", "x": 100.0, "y": 300.0}
            if "resume_confirm_button_not_found" in script:
                return {"ok": True, "clicked": "立即投递", "x": 200.0, "y": 400.0}
            if "editor_not_found" in script:
                encoded = script.split("const message = ", 1)[1].split(";", 1)[0].strip()
                self.message = json.loads(encoded)
                return {"ok": True, "filled": True}
            if "message_send_button_not_found" in script:
                self.message_sent = True
                return {"ok": True, "clicked": "发送"}
            return {
                "ok": True,
                "title": "岗位详情",
                "loginRequired": False,
                "chatOpen": True,
                "canSendResume": not self.resume_delivered,
                "canSendChatResume": not self.dialog_open and not self.resume_delivered,
                "canConfirmResume": self.dialog_open,
                "resumeAttachmentSelected": self.dialog_open,
                "resumeDelivered": self.resume_delivered,
                "outgoingMessages": [self.message] if self.message_sent else [],
                "requires_user_action": False,
            }

    driver = DomFallbackResumeDriver()
    attempts = LiepinApplySender(
        driver=driver,
        audit_log=LiepinAuditLog(path=tmp_path / "audit.json"),
    ).send_batch([{
        "name": "AI产品经理",
        "company": "Example",
        "url": "https://www.liepin.com/job/dom-fallback.shtml",
        "cloud_greeting": "您好，我对这个岗位很感兴趣。",
    }])

    assert attempts[0].delivered is True
    assert driver.native_resume_clicks == 3
    assert driver.dom_resume_clicks == 1
    assert any(
        step["step"] == "click_liepin_resume_action_dom_fallback"
        for step in attempts[0].steps
    )


def test_liepin_resume_confirm_uses_dom_fallback_after_native_clicks(monkeypatch, tmp_path):
    monkeypatch.setattr("jobagent.platforms.liepin.apply.time.sleep", lambda _: None)

    class DomFallbackConfirmDriver:
        def __init__(self):
            self.dialog_open = False
            self.resume_delivered = False
            self.native_confirm_clicks = 0
            self.dom_confirm_clicks = 0
            self.message = ""
            self.message_sent = False

        def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
            return {"ok": True, "url": url}

        def _click_at(self, x: float, _y: float):
            if x == 100.0:
                self.dialog_open = True
            elif x == 200.0:
                self.native_confirm_clicks += 1

        def _exec_js(self, script: str):
            if "elementFromPoint" in script:
                if "200.0" in script:
                    self.dom_confirm_clicks += 1
                    self.resume_delivered = True
                return {"ok": True}
            if "resume_action_not_found" in script:
                return {"ok": True, "clicked": "发简历", "x": 100.0, "y": 300.0}
            if "resume_confirm_button_not_found" in script:
                return {"ok": True, "clicked": "立即投递", "x": 200.0, "y": 400.0}
            if "editor_not_found" in script:
                encoded = script.split("const message = ", 1)[1].split(";", 1)[0].strip()
                self.message = json.loads(encoded)
                return {"ok": True, "filled": True}
            if "message_send_button_not_found" in script:
                self.message_sent = True
                return {"ok": True, "clicked": "发送"}
            return {
                "ok": True,
                "title": "岗位详情",
                "loginRequired": False,
                "chatOpen": True,
                "canSendResume": not self.resume_delivered,
                "canSendChatResume": not self.dialog_open and not self.resume_delivered,
                "canConfirmResume": self.dialog_open,
                "resumeAttachmentSelected": self.dialog_open,
                "resumeDelivered": self.resume_delivered,
                "outgoingMessages": [self.message] if self.message_sent else [],
                "requires_user_action": False,
            }

    driver = DomFallbackConfirmDriver()
    attempts = LiepinApplySender(
        driver=driver,
        audit_log=LiepinAuditLog(path=tmp_path / "audit.json"),
    ).send_batch([{
        "name": "AI产品经理",
        "company": "Example",
        "url": "https://www.liepin.com/job/dom-confirm.shtml",
        "cloud_greeting": "您好，我对这个岗位很感兴趣。",
    }])

    assert attempts[0].delivered is True
    assert driver.native_confirm_clicks == 3
    assert driver.dom_confirm_clicks == 1
    assert any(
        step["step"] == "click_liepin_resume_confirm_dom_fallback"
        for step in attempts[0].steps
    )


def test_liepin_resume_confirm_retries_only_while_dialog_remains(monkeypatch, tmp_path):
    monkeypatch.setattr("jobagent.platforms.liepin.apply.time.sleep", lambda _: None)

    class SwallowedFirstConfirmDriver:
        def __init__(self):
            self.dialog_open = False
            self.resume_delivered = False
            self.confirm_clicks = 0
            self.message = ""
            self.message_sent = False

        def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
            return {"ok": True, "url": url}

        def _click_at(self, x: float, _y: float):
            if x == 100.0:
                self.dialog_open = True
            elif x == 200.0:
                self.confirm_clicks += 1
                if self.confirm_clicks >= 2:
                    self.resume_delivered = True
                    self.dialog_open = False

        def _exec_js(self, script: str):
            if "resume_action_not_found" in script:
                return {"ok": True, "clicked": "发简历", "x": 100.0, "y": 300.0}
            if "resume_confirm_button_not_found" in script:
                return {"ok": True, "clicked": "立即投递", "x": 200.0, "y": 400.0}
            if "editor_not_found" in script:
                encoded = script.split("const message = ", 1)[1].split(";", 1)[0].strip()
                self.message = json.loads(encoded)
                return {"ok": True, "filled": True}
            if "message_send_button_not_found" in script:
                self.message_sent = True
                return {"ok": True, "clicked": "发送"}
            return {
                "ok": True,
                "title": "岗位详情",
                "loginRequired": False,
                "chatOpen": True,
                "canSendResume": not self.resume_delivered,
                "canConfirmResume": self.dialog_open,
                "resumeAttachmentSelected": self.dialog_open,
                "resumeDelivered": self.resume_delivered,
                "outgoingMessages": [self.message] if self.message_sent else [],
                "requires_user_action": False,
            }

    driver = SwallowedFirstConfirmDriver()
    attempts = LiepinApplySender(
        driver=driver,
        audit_log=LiepinAuditLog(path=tmp_path / "audit.json"),
    ).send_batch([{
        "name": "AI产品经理",
        "company": "Example",
        "url": "https://www.liepin.com/job/retry.shtml",
        "cloud_greeting": "您好，我对这个岗位很感兴趣。",
    }])

    assert attempts[0].delivered is True
    assert driver.confirm_clicks == 2


def test_liepin_reconciles_delayed_resume_after_greeting_send(monkeypatch, tmp_path):
    monkeypatch.setattr("jobagent.platforms.liepin.apply.time.sleep", lambda _: None)

    class DelayedResumeDriver:
        def __init__(self):
            self.dialog_open = False
            self.confirm_clicked = False
            self.message = ""
            self.message_sent = False
            self.post_message_inspections = 0

        def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
            return {"ok": True, "url": url}

        def _click_at(self, x: float, _y: float):
            if x == 100.0:
                self.dialog_open = True
            elif x == 200.0:
                self.confirm_clicked = True

        def _exec_js(self, script: str):
            if "resume_action_not_found" in script:
                return {"ok": True, "clicked": "发简历", "x": 100.0, "y": 300.0}
            if "resume_confirm_button_not_found" in script:
                return {"ok": True, "clicked": "立即投递", "x": 200.0, "y": 400.0}
            if "editor_not_found" in script:
                encoded = script.split("const message = ", 1)[1].split(";", 1)[0].strip()
                self.message = json.loads(encoded)
                return {"ok": True, "filled": True}
            if "message_send_button_not_found" in script:
                self.message_sent = True
                return {"ok": True, "clicked": "发送"}
            if self.message_sent:
                self.post_message_inspections += 1
            resume_delivered = (
                self.confirm_clicked
                and self.message_sent
                and self.post_message_inspections >= 3
            )
            return {
                "ok": True,
                "title": "岗位详情",
                "loginRequired": False,
                "chatOpen": True,
                "canSendResume": not resume_delivered,
                "canSendChatResume": not self.dialog_open,
                "canConfirmResume": self.dialog_open and not resume_delivered,
                "resumeAttachmentSelected": self.dialog_open,
                "resumeDelivered": resume_delivered,
                "outgoingMessages": [self.message] if self.message_sent else [],
                "requires_user_action": False,
            }

    attempts = LiepinApplySender(
        driver=DelayedResumeDriver(),
        audit_log=LiepinAuditLog(path=tmp_path / "audit.json"),
    ).send_batch([{
        "name": "AI产品经理",
        "company": "Example",
        "url": "https://www.liepin.com/job/delayed-resume.shtml",
        "cloud_greeting": "您好，我对这个岗位很感兴趣。",
    }])

    assert attempts[0].delivered is True
    assert attempts[0].steps[-1]["step"] == "reconcile_liepin_resume_after_greeting"
    assert attempts[0].steps[-1]["delivered"] is True
    records = json.loads((tmp_path / "audit.json").read_text(encoding="utf-8"))
    assert records[0]["status"] == "delivered"
    assert records[0]["evidence"]["resume_delivered"] is True
    assert records[0]["evidence"]["greeting_delivered"] is True


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
