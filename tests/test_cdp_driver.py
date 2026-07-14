"""Tests for CDP driver compatibility helpers."""

from jobagent.drivers.boss import cdp_driver
from jobagent.drivers.boss.cdp_driver import CDPBossDriver


class FakeCDP:
    def __init__(self, value):
        self.values = list(value) if isinstance(value, list) else [value]
        self.last_value = self.values[-1]
        self.connected = True
        self.js_calls: list[str] = []
        self.evaluate_timeouts: list[int] = []
        self.send_calls: list[tuple[str, dict | None]] = []

    def evaluate(self, js_code: str, timeout: int = 30, **_kwargs):
        self.js_calls.append(js_code)
        self.evaluate_timeouts.append(timeout)
        value = self.values.pop(0) if self.values else self.last_value
        self.last_value = value
        return {"result": {"value": value}}

    def send(self, method: str, params: dict | None = None):
        self.send_calls.append((method, params))
        return {}


def make_driver(value):
    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.cdp = FakeCDP(value)
    return driver


def test_exec_js_parses_json_string():
    driver = make_driver('{"ok": true, "status": "already"}')

    assert driver._exec_js("1") == {"ok": True, "status": "already"}


def test_exec_js_returns_raw_for_plain_string():
    driver = make_driver("not json")

    assert driver._exec_js("1") == {"ok": True, "raw": "not json"}


def test_exec_js_passes_through_json_object():
    driver = make_driver({"code": 0, "zpData": {"jobList": []}})

    assert driver._exec_js("1") == {"code": 0, "zpData": {"jobList": []}}


def test_unwrap_parses_raw_json():
    driver = make_driver("{}")

    assert driver._unwrap({"raw": '{"ok": true}'}) == {"ok": True}


def test_unwrap_returns_empty_dict_for_invalid_raw():
    driver = make_driver("{}")

    assert driver._unwrap({"raw": "not json"}) == {}


def test_reload_current_page_uses_cdp_and_returns_location(monkeypatch):
    driver = make_driver(
        '{"url":"https://we.51job.com/pc/search","title":"前程无忧"}'
    )
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda _seconds: None)

    result = driver.reload_current_page(wait_seconds=1)

    assert result == {
        "ok": True,
        "url": "https://we.51job.com/pc/search",
        "title": "前程无忧",
    }
    assert ("Page.reload", {"ignoreCache": False}) in driver.cdp.send_calls


def test_api_fetch_uses_same_origin_page_and_retries_startup_fetch(monkeypatch):
    driver = make_driver([
        {"__error": "Failed to fetch"},
        {"code": 0, "message": "Success"},
    ])
    driver.platform = "boss"
    connected_urls = []
    sleeps = []
    monkeypatch.setattr(
        driver,
        "_ensure_connected_for_url",
        lambda url: connected_urls.append(url),
    )
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = driver.api_fetch("/wapi/zpuser/wap/getUserInfo.json")

    assert result["code"] == 0
    assert connected_urls == ["https://www.zhipin.com/"]
    assert sleeps == [0.5]


def test_login_status_falls_back_to_visible_authenticated_navigation(monkeypatch):
    driver = make_driver("{}")
    monkeypatch.setattr(
        driver,
        "api_fetch",
        lambda _path: (_ for _ in ()).throw(RuntimeError("startup race")),
    )
    monkeypatch.setattr(
        driver,
        "inspect_page",
        lambda: {
            "ok": True,
            "userNav": True,
            "geekNav": True,
            "loginDialog": False,
            "qrLoginDialog": False,
        },
    )

    assert driver.check_login_status() is True


def test_login_status_does_not_override_explicit_api_logout(monkeypatch):
    driver = make_driver("{}")
    monkeypatch.setattr(driver, "api_fetch", lambda _path: {"code": 37})
    monkeypatch.setattr(
        driver,
        "inspect_page",
        lambda: (_ for _ in ()).throw(AssertionError("UI fallback should not run")),
    )

    assert driver.check_login_status() is False


def test_exec_js_surfaces_cdp_errors():
    class BrokenCDP:
        connected = True

        def evaluate(self, js_code: str, timeout: int = 30):
            raise RuntimeError("boom")

    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.cdp = BrokenCDP()

    assert driver._exec_js("1") == {"ok": False, "error": "boom"}


def test_target_domain_check_forces_reconnect_from_about_blank(monkeypatch):
    driver = make_driver("about:blank")
    driver.platform = "boss"
    driver.current_platform = "boss"
    driver.manager = object()
    calls = []

    def fake_ensure_connected(platform=None, initial_url=None, force=False):
        calls.append((platform, initial_url, force))

    monkeypatch.setattr(driver, "_ensure_connected", fake_ensure_connected)

    current_url = driver._ensure_connected_for_url(
        "https://www.zhipin.com/wapi/zpuser/wap/getUserInfo.json"
    )

    assert calls == [
        (
            "boss",
            "https://www.zhipin.com/wapi/zpuser/wap/getUserInfo.json",
            True,
        )
    ]
    assert current_url == ""


def test_target_domain_check_keeps_existing_matching_tab(monkeypatch):
    driver = make_driver("https://www.zhipin.com/web/geek/jobs")
    driver.platform = "boss"
    driver.current_platform = "boss"
    driver.manager = object()
    calls = []
    monkeypatch.setattr(driver, "_ensure_connected", lambda **kwargs: calls.append(kwargs))

    current_url = driver._ensure_connected_for_url(
        "https://www.zhipin.com/wapi/zpuser/wap/getUserInfo.json"
    )

    assert calls == []
    assert current_url == "https://www.zhipin.com/web/geek/jobs"


def test_login_wait_is_passive_after_opening_login_page_once(monkeypatch):
    driver = make_driver("{}")
    driver.platform = "boss"
    driver.current_platform = "boss"
    driver.manager = object()
    states = iter([False, True])
    monkeypatch.setattr(driver, "check_login_status", lambda: next(states))
    monkeypatch.setattr(driver, "_ensure_connected", lambda **_kwargs: None)
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda _seconds: None)

    assert driver.ensure_logged_in(timeout=1, poll_interval=0) is True

    navigations = [params["url"] for method, params in driver.cdp.send_calls if method == "Page.navigate"]
    assert navigations == ["https://www.zhipin.com/web/user/?ka=header-login"]
    assert any("[Job Agent]" in script for script in driver.cdp.js_calls)


def test_snapshot_search_page_uses_one_navigation_and_bounded_evaluation(monkeypatch):
    driver = make_driver('{"ok":true,"cards":[{"jobId":"job-1"}]}')
    driver.platform = "boss"
    driver.current_platform = "boss"
    driver.manager = object()
    monkeypatch.setattr(driver, "_ensure_connected_for_url", lambda _url: None)
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda _seconds: None)

    result = driver.snapshot_search_page(
        "https://www.zhipin.com/web/geek/jobs?query=AI",
        "snapshot-script",
        wait_seconds=2,
        timeout=8,
    )

    assert result == {"ok": True, "cards": [{"jobId": "job-1"}]}
    assert driver.cdp.send_calls == [
        (
            "Page.navigate",
            {"url": "https://www.zhipin.com/web/geek/jobs?query=AI"},
        )
    ]
    assert driver.cdp.js_calls == ["snapshot-script"]
    assert driver.cdp.evaluate_timeouts == [8]


def test_snapshot_search_page_polls_until_slow_cards_are_ready(monkeypatch):
    driver = make_driver([
        '{"ok":true,"readyState":"interactive","cards":[]}',
        '{"ok":true,"readyState":"complete","cards":[{"jobId":"job-1"}]}',
    ])
    driver.platform = "boss"
    driver.current_platform = "boss"
    driver.manager = object()
    sleeps = []
    monkeypatch.setattr(driver, "_ensure_connected_for_url", lambda _url: "about:blank")
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = driver.snapshot_search_page(
        "https://www.zhipin.com/web/geek/jobs?query=AI",
        "snapshot-script",
        wait_seconds=2,
        timeout=8,
    )

    assert result["cards"] == [{"jobId": "job-1"}]
    assert sleeps == [1.0]
    assert driver.cdp.js_calls == ["snapshot-script", "snapshot-script"]
    assert len(driver.cdp.send_calls) == 1


def test_snapshot_search_page_does_not_reload_same_search(monkeypatch):
    url = "https://www.zhipin.com/web/geek/jobs?query=AI&city=101280600&page=1"
    driver = make_driver('{"ok":true,"cards":[{"jobId":"job-1"}]}')
    driver.platform = "boss"
    driver.current_platform = "boss"
    driver.manager = object()
    monkeypatch.setattr(
        driver,
        "_ensure_connected_for_url",
        lambda _url: url + "&source=jobagent",
    )

    result = driver.snapshot_search_page(url, "snapshot-script", wait_seconds=2)

    assert result["cards"] == [{"jobId": "job-1"}]
    assert driver.cdp.send_calls == []


def test_open_url_reuses_same_stable_job_page(monkeypatch):
    url = "https://www.zhipin.com/job_detail/job-1.html"
    driver = make_driver(
        '{"url":"https://www.zhipin.com/job_detail/job-1.html",'
        '"title":"Job 1","readyState":"complete","hasChatEntry":true}'
    )
    monkeypatch.setattr(driver, "_ensure_connected_for_url", lambda _url: url)
    sleeps = []
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = driver.open_url_in_new_tab(url, wait_seconds=6)

    assert result == {
        "ok": True,
        "url": url,
        "title": "Job 1",
        "reused": True,
        "readyState": "complete",
    }
    assert driver.cdp.send_calls == []
    assert sleeps == []


def test_open_url_waits_for_two_stable_boss_job_snapshots(monkeypatch):
    url = "https://www.zhipin.com/job_detail/job-1.html"
    driver = make_driver([
        '{"url":"' + url + '","title":"Job 1","readyState":"interactive",'
        '"hasChatEntry":false}',
        '{"url":"' + url + '","title":"Job 1","readyState":"complete",'
        '"hasChatEntry":true}',
        '{"url":"' + url + '","title":"Job 1","readyState":"complete",'
        '"hasChatEntry":true}',
    ])
    monkeypatch.setattr(driver, "_ensure_connected_for_url", lambda _url: "about:blank")
    sleeps = []
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = driver.open_url_in_new_tab(url, wait_seconds=6)

    assert result["ok"] is True
    assert result["reused"] is False
    assert result["readyState"] == "complete"
    assert len(driver.cdp.js_calls) == 3
    assert sleeps == [2, 2]
    assert driver.cdp.send_calls == [("Page.navigate", {"url": url})]


def test_open_url_waits_for_late_liepin_job_cards(monkeypatch):
    url = (
        "https://www.liepin.com/zhaopin/?city=050090&dq=050090"
        "&currentPage=0&pageSize=40&key=AI"
    )
    driver = make_driver([
        '{"url":"' + url + '","title":"猎聘","readyState":"interactive",'
        '"jobLinkCount":0,"noResults":false,"loginRequired":false}',
        '{"url":"' + url + '","title":"猎聘","readyState":"interactive",'
        '"jobLinkCount":3,"noResults":false,"loginRequired":false}',
        '{"url":"' + url + '","title":"猎聘","readyState":"complete",'
        '"jobLinkCount":5,"noResults":false,"loginRequired":false}',
    ])
    monkeypatch.setattr(driver, "_ensure_connected_for_url", lambda _url: "about:blank")
    sleeps = []
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = driver.open_url_in_new_tab(url, wait_seconds=6)

    assert result["ok"] is True
    assert result["readyState"] == "complete"
    assert result["reused"] is False
    assert len(driver.cdp.js_calls) == 3
    assert sleeps == [2.0, 2.0]
    assert driver.cdp.send_calls == [("Page.navigate", {"url": url})]


def test_search_url_reuse_distinguishes_liepin_pages():
    current = (
        "https://www.liepin.com/zhaopin/?city=%E4%B8%8A%E6%B5%B7"
        "&dq=%E4%B8%8A%E6%B5%B7&currentPage=2&pageSize=40&key=AI"
    )
    first = (
        "https://www.liepin.com/zhaopin/?city=%E4%B8%8A%E6%B5%B7"
        "&dq=%E4%B8%8A%E6%B5%B7&currentPage=0&pageSize=40&key=AI"
    )

    assert CDPBossDriver._same_search_url(current, first) is False


def test_open_url_waits_for_liepin_job_auth_hydration(monkeypatch):
    url = "https://www.liepin.com/job/1983061929.shtml"
    driver = make_driver([
        '{"url":"' + url + '","title":"BI负责人","readyState":"interactive",'
        '"authenticated":false,"hasAction":true,"loginRequired":true}',
        '{"url":"' + url + '","title":"BI负责人","readyState":"interactive",'
        '"authenticated":true,"hasAction":true,"loginRequired":false}',
        '{"url":"' + url + '","title":"BI负责人","readyState":"complete",'
        '"authenticated":true,"hasAction":true,"loginRequired":false}',
    ])
    monkeypatch.setattr(driver, "_ensure_connected_for_url", lambda _url: "about:blank")
    sleeps = []
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = driver.open_url_in_new_tab(url, wait_seconds=3)

    assert result["ok"] is True
    assert result["readyState"] == "complete"
    assert result["reused"] is False
    assert len(driver.cdp.js_calls) == 3
    assert sleeps == [2.0, 2.0]
    assert driver.cdp.send_calls == [("Page.navigate", {"url": url})]


def test_snapshot_search_page_returns_sanitized_load_timeout(monkeypatch):
    driver = make_driver(
        '{"ok":true,"url":"https://www.zhipin.com/web/geek/jobs",'
        '"title":"职位搜索","readyState":"interactive","cards":[],'
        '"candidateCount":0,"cardCount":0,"bodySnippet":"private page text"}'
    )
    driver.platform = "boss"
    driver.current_platform = "boss"
    driver.manager = object()
    monkeypatch.setattr(driver, "_ensure_connected_for_url", lambda _url: "about:blank")
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda _seconds: None)

    result = driver.snapshot_search_page(
        "https://www.zhipin.com/web/geek/jobs?query=AI",
        "snapshot-script",
        wait_seconds=2,
        poll_interval=1,
    )

    assert result == {
        "ok": False,
        "error": "search_page_load_timeout",
        "url": "https://www.zhipin.com/web/geek/jobs",
        "title": "职位搜索",
        "readyState": "interactive",
        "candidateCount": 0,
        "cardCount": 0,
        "waitedSeconds": 2.0,
        "lastError": "",
    }
    assert len(driver.cdp.js_calls) == 3
    assert "bodySnippet" not in result


def test_click_chat_entry_no_popup_is_not_auto_sent(monkeypatch):
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda _seconds: None)
    driver = make_driver([
        '{"ok": true, "step": "target_立即沟通", "label": "立即沟通", "x": 42, "y": 24}',
        '{"ok": false, "step": "no_popup_yet"}',
        '{"ok": false, "step": "no_popup_yet"}',
        '{"ok": false, "step": "no_popup_yet"}',
        '{"ok": false, "step": "no_popup_yet"}',
        '{"ok": false, "step": "no_popup_yet"}',
    ])

    result = driver.click_chat_entry()

    assert result["ok"] is True
    assert result["autoSent"] is False
    assert result["step"] == "no_popup_after_click"


def test_click_chat_entry_follows_trusted_existing_chat_redirect(monkeypatch):
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda _seconds: None)
    redirect_url = (
        "https://www.zhipin.com/web/geek/chat?id=conversation-1&securityId=signed"
    )
    driver = make_driver([
        '{"ok": true, "step": "target_继续沟通", "label": "继续沟通", "x": 42, "y": 24}',
        '{"ok": false, "step": "no_popup_yet"}',
        '{"ok": false, "step": "no_popup_yet"}',
        '{"ok": false, "step": "no_popup_yet"}',
        '{"ok": false, "step": "no_popup_yet"}',
        '{"ok": false, "step": "no_popup_yet"}',
        '{"ok": true, "url": "' + redirect_url + '"}',
    ])

    result = driver.click_chat_entry()

    assert result == {
        "ok": True,
        "step": "navigated_chat_redirect",
        "label": "继续沟通",
        "x": 42,
        "y": 24,
        "clicked": True,
        "autoSent": False,
        "chatPath": "/web/geek/chat",
    }
    assert ("Page.navigate", {"url": redirect_url}) in driver.cdp.send_calls
    assert "securityId" not in str(result)


def test_click_chat_entry_follows_trusted_initial_chat_redirect(monkeypatch):
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda _seconds: None)
    redirect_url = (
        "https://www.zhipin.com/web/geek/chat?id=conversation-1&securityId=signed"
    )
    driver = make_driver([
        '{"ok": true, "step": "target_立即沟通", "label": "立即沟通",'
        ' "jobId": "job-1", "x": 42, "y": 24}',
        '{"ok": false, "step": "no_popup_yet"}',
        '{"ok": false, "step": "no_popup_yet"}',
        '{"ok": false, "step": "no_popup_yet"}',
        '{"ok": false, "step": "no_popup_yet"}',
        '{"ok": false, "step": "no_popup_yet"}',
        '{"ok": true, "url": "' + redirect_url + '"}',
    ])

    result = driver.click_chat_entry()

    assert result["ok"] is True
    assert result["step"] == "navigated_chat_redirect"
    assert result["label"] == "立即沟通"
    assert result["jobId"] == "job-1"
    assert result["clicked"] is True
    assert result["autoSent"] is False
    assert ("Page.navigate", {"url": redirect_url}) in driver.cdp.send_calls
    assert "securityId" not in str(result)


def test_click_chat_entry_targets_visible_chat_buttons(monkeypatch):
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda _seconds: None)
    driver = make_driver([
        '{"ok": true, "step": "target_立即沟通", "label": "立即沟通", "x": 42, "y": 24}',
        '{"ok": false, "step": "no_popup_yet"}',
        '{"ok": false, "step": "no_popup_yet"}',
        '{"ok": false, "step": "no_popup_yet"}',
        '{"ok": false, "step": "no_popup_yet"}',
        '{"ok": false, "step": "no_popup_yet"}',
    ])

    driver.click_chat_entry()
    click_js = driver.cdp.js_calls[0]
    popup_js = driver.cdp.js_calls[1]

    assert "function isVisible" in click_js
    assert ".btn-startchat" in click_js
    assert "targetInfo(el, labels[l])" in click_js
    assert "text === '继续沟通' && isVisible" in popup_js
    assert ".startchat-dialog" in popup_js
    assert "platformDefaultSent" in popup_js
    assert ("Input.dispatchMouseEvent", {
        "type": "mousePressed",
        "x": 42,
        "y": 24,
        "button": "left",
        "clickCount": 1,
    }) in driver.cdp.send_calls


def test_click_chat_entry_preserves_completed_click_on_later_timeout(monkeypatch):
    class TimeoutAfterTargetCDP(FakeCDP):
        def __init__(self):
            super().__init__(
                '{"ok":true,"step":"target_继续沟通","label":"继续沟通",'
                '"jobId":"job-1","x":42,"y":24}'
            )
            self.calls = 0

        def evaluate(self, js_code: str, timeout: int = 30, **kwargs):
            self.calls += 1
            if self.calls > 1:
                raise TimeoutError("Runtime.evaluate timed out")
            return super().evaluate(js_code, timeout=timeout, **kwargs)

    monkeypatch.setattr(cdp_driver.time, "sleep", lambda _seconds: None)
    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.cdp = TimeoutAfterTargetCDP()

    result = driver.click_chat_entry()

    assert result == {
        "ok": False,
        "error": "Runtime.evaluate timed out",
        "clicked": True,
        "label": "继续沟通",
        "jobId": "job-1",
    }


def test_click_chat_entry_retries_read_only_target_discovery(monkeypatch):
    class FirstTargetTimeoutCDP(FakeCDP):
        def __init__(self):
            super().__init__([
                '{"ok":true,"step":"target_继续沟通","label":"继续沟通",'
                '"jobId":"job-1","x":42,"y":24}',
                '{"ok":true,"step":"chat_opened","autoSent":false}',
            ])
            self.calls = 0

        def evaluate(self, js_code: str, timeout: int = 30, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("transient target timeout")
            return super().evaluate(js_code, timeout=timeout, **kwargs)

    sleeps = []
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda seconds: sleeps.append(seconds))
    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.cdp = FirstTargetTimeoutCDP()

    result = driver.click_chat_entry()

    assert result["ok"] is True
    assert result["clicked"] is True
    assert result["jobId"] == "job-1"
    assert 2 in sleeps


def test_inspect_chat_editor_timeout_is_not_auto_sent(monkeypatch):
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda _seconds: None)
    driver = make_driver('{"ok": true, "editorFound": false}')

    result = driver.inspect_chat_editor()

    assert result["ok"] is True
    assert result["autoSent"] is False
    assert result["editorFound"] is False
    assert result["step"] == "editor_not_found"


def test_inspect_chat_editor_only_counts_visible_login_dialog(monkeypatch):
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda _seconds: None)
    driver = make_driver('{"ok": true, "editorFound": false}')

    driver.inspect_chat_editor()
    inspect_js = driver.cdp.js_calls[0]

    assert "var loginEls = Array.prototype.slice.call" in inspect_js
    assert "var loginDialog = loginEls.some(isVisible)" in inspect_js


def test_inspect_chat_editor_supports_startchat_modal_textarea(monkeypatch):
    driver = make_driver(
        '{"ok":true,"editorFound":true,"editorTag":"TEXTAREA",'
        '"editorClass":"input-area","sendFound":true}'
    )

    result = driver.inspect_chat_editor()

    assert result["editorTag"] == "TEXTAREA"
    inspect_js = driver.cdp.js_calls[0]
    assert ".startchat-dialog textarea" in inspect_js
    assert ".send-message" in inspect_js
    assert "searchParams.get('jobId')" in inspect_js
    assert "editor.closest('.startchat-dialog, .dialog-container')" in inspect_js


def test_inspect_chat_editor_sanitizes_chat_query(monkeypatch):
    driver = make_driver(
        '{"ok":true,"editorFound":true,"jobId":"job-1",'
        '"href":"https://www.zhipin.com/web/geek/chat?jobId=job-1&securityId=secret"}'
    )

    result = driver.inspect_chat_editor()

    assert result["href"] == "https://www.zhipin.com/web/geek/chat"
    assert result["jobId"] == "job-1"
    assert "secret" not in str(result)


def test_fill_chat_message_supports_startchat_modal_textarea(monkeypatch):
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda _seconds: None)
    driver = make_driver([
        '{"ok":true,"step":"editor_selected","editorTag":"TEXTAREA","formControl":true}',
        None,
        '{"ok":true,"step":"filled","len":5,"text":"hello"}',
    ])

    result = driver.fill_chat_message("hello")

    assert result == {"ok": True, "step": "filled", "len": 5}
    assert ".startchat-dialog textarea" in driver.cdp.js_calls[0]
    assert "editor.select()" in driver.cdp.js_calls[0]
    assert "formControl ? editor.value" in driver.cdp.js_calls[2]


def test_click_send_targets_modal_button_for_textarea(monkeypatch):
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda _seconds: None)
    driver = make_driver([
        '{"ok":true,"step":"send_button_found","disabled":false,"x":42,"y":24}',
        '{"ok":true,"len":5,"formControl":true}',
    ])

    result = driver.click_send()

    assert result["ok"] is True
    assert result["step"] == "clicked_send_button"
    assert "scope || document" in driver.cdp.js_calls[0]
    assert ".send-message" in driver.cdp.js_calls[0]
    assert ".startchat-dialog textarea" in driver.cdp.js_calls[1]
    assert ("Input.dispatchMouseEvent", {
        "type": "mousePressed",
        "x": 42,
        "y": 24,
        "button": "left",
        "clickCount": 1,
    }) in driver.cdp.send_calls


def test_verify_delivery_excludes_modal_draft_and_accepts_sent_marker():
    driver = make_driver(
        '{"ok":true,"delivered":true,"stillInEditor":false,'
        '"hasMsg":true,"hasDeliveredNearMsg":true,"editorLen":0}'
    )

    result = driver.verify_delivery("hello")

    assert result["delivered"] is True
    verify_js = driver.cdp.js_calls[0]
    assert "textarea, input" in verify_js
    assert "formControl ? editor.value" in verify_js
    assert "已发送" in verify_js
