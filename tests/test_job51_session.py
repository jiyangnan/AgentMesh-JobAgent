"""Focused tests for the 51Job SPA login probe."""

import json

from jobagent.platforms.job51.session import Job51SessionGuide


class FakeDriver:
    def __init__(self, states):
        self.states = list(states)
        self.last = self.states[-1]

    def open_url_in_new_tab(self, _url, wait_seconds=0):
        return {"ok": True, "url": "https://we.51job.com/pc/search"}

    def _exec_js(self, _script):
        state = self.states.pop(0) if self.states else self.last
        self.last = state
        return {"raw": json.dumps(state, ensure_ascii=False)}


def test_job51_session_waits_past_javascript_placeholder(monkeypatch):
    monkeypatch.setattr("jobagent.platforms.job51.session.time.sleep", lambda _seconds: None)
    driver = FakeDriver(
        [
            {
                "ok": True,
                "url": "https://we.51job.com/pc/search",
                "title": "前程无忧",
                "pageReady": False,
                "placeholder": True,
                "loginRequired": False,
                "bodySnippet": "We're sorry but 51job doesn't work properly without JavaScript enabled.",
            },
            {
                "ok": True,
                "url": "https://we.51job.com/pc/search",
                "title": "前程无忧",
                "pageReady": True,
                "hasLoginEntry": True,
                "hasAuthenticatedEntry": False,
                "loginRequired": True,
                "bodySnippet": "我要招人 登录/注册",
            },
        ]
    )

    status = Job51SessionGuide(driver=driver).inspect_current_page(
        wait_seconds=1,
        poll_interval=0.5,
    )

    assert status.ok is True
    assert status.logged_in is False
    assert status.login_required is True


def test_job51_session_requires_positive_authenticated_evidence(monkeypatch):
    monkeypatch.setattr("jobagent.platforms.job51.session.time.sleep", lambda _seconds: None)
    driver = FakeDriver(
        [
            {
                "ok": True,
                "url": "https://we.51job.com/pc/search",
                "title": "前程无忧",
                "pageReady": False,
                "placeholder": False,
                "loginRequired": False,
                "bodySnippet": "搜索页面正在加载",
            }
        ]
    )

    status = Job51SessionGuide(driver=driver).inspect_current_page(
        wait_seconds=0,
    )

    assert status.ok is False
    assert status.logged_in is False
    assert status.login_required is False
    assert status.error == "job51_page_not_ready"


def test_job51_session_accepts_online_resume_as_authenticated_evidence():
    driver = FakeDriver(
        [
            {
                "ok": True,
                "url": "https://we.51job.com/pc/search",
                "title": "前程无忧",
                "pageReady": True,
                "hasLoginEntry": False,
                "hasAuthenticatedEntry": True,
                "loginRequired": False,
                "bodySnippet": "APP下载 在线简历 费南德",
            }
        ]
    )

    status = Job51SessionGuide(driver=driver).inspect_current_page()

    assert status.ok is True
    assert status.logged_in is True
    assert status.login_required is False
    assert status.evidence["hasAuthenticatedEntry"] is True
