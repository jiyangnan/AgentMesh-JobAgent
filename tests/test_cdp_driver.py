"""Tests for CDP driver compatibility helpers."""

from jobagent.drivers.boss import cdp_driver
from jobagent.drivers.boss.cdp_driver import CDPBossDriver


class FakeCDP:
    def __init__(self, value):
        self.values = list(value) if isinstance(value, list) else [value]
        self.last_value = self.values[-1]
        self.connected = True
        # Track send() calls so tests can assert that real CDP mouse events
        # were emitted (not synthetic dispatchEvent).
        self.sent_methods: list[str] = []

    def evaluate(self, js_code: str, timeout: int = 30):
        value = self.values.pop(0) if self.values else self.last_value
        self.last_value = value
        return {"result": {"value": value}}

    def send(self, method: str, params=None, timeout: float = 30.0):
        # Record the CDP method (e.g., Input.dispatchMouseEvent) so tests can
        # assert that real hardware-level events were issued.
        self.sent_methods.append(method)
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


def test_exec_js_surfaces_cdp_errors():
    class BrokenCDP:
        connected = True

        def evaluate(self, js_code: str, timeout: int = 30):
            raise RuntimeError("boom")

    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.cdp = BrokenCDP()

    assert driver._exec_js("1") == {"ok": False, "error": "boom"}


def test_click_chat_entry_no_sidebar_is_not_auto_sent(monkeypatch):
    """If click 立即沟通 succeeds (real CDP mouse event) but no sidebar opens
    within the polling window, the result must NOT be marked as autoSent.
    Delivery is left to the explicit fill+send flow.

    Regression: prior versions used dispatchEvent(MouseEvent) which produced
    ok=true from JS but Boss silently dropped the click. The new flow uses
    Input.dispatchMouseEvent via _cdp_click_at — verify the CDP method
    was actually emitted.
    """
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda _seconds: None)
    driver = make_driver([
        # Initial risk-control check: location.href
        '{"href": "https://www.zhipin.com/job_detail/abc.html"}',
        # Step 1: locate_js finds the chat button
        '{"ok": true, "source": "class_btn-startchat", "text": "立即沟通", '
        '"cls": "btn btn-startchat", "x": 300, "y": 220}',
        # Step 2: polling iterations (8 attempts) — each iteration does:
        #   1) risk-control check (location.href)
        #   2) popup check (only when clicked_text == "立即沟通")
        #   3) sidebar state check
        # All return "not found / not open" → eventually fall through.
        # The values below cover one iteration's 3 evaluates; the remaining
        # 7 iterations reuse last_value (which has hasEditor/hasSend = false).
        '{"href": "https://www.zhipin.com/job_detail/abc.html"}',
        '{"ok": false, "error": "not_found"}',  # popup _find_clickable_by_text
        '{"hasEditor": false, "hasSend": false}',  # sidebar state
    ])

    result = driver.click_chat_entry()

    # Click was sent (ok=true) but no sidebar confirmed → not autoSent
    assert result["ok"] is True
    assert result["autoSent"] is False
    assert result["step"] == "no_sidebar_after_click"
    # Real CDP mouse events were emitted (3 per click: move + press + release)
    assert "Input.dispatchMouseEvent" in driver.cdp.sent_methods
    assert driver.cdp.sent_methods.count("Input.dispatchMouseEvent") >= 3


def test_inspect_chat_editor_timeout_is_not_auto_sent(monkeypatch):
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda _seconds: None)
    driver = make_driver('{"ok": true, "editorFound": false}')

    result = driver.inspect_chat_editor()

    assert result["ok"] is True
    assert result["autoSent"] is False
    assert result["editorFound"] is False
    assert result["step"] == "editor_not_found"
