"""Tests for CDP driver compatibility helpers."""

from jobagent.drivers.boss import cdp_driver
from jobagent.drivers.boss.cdp_driver import CDPBossDriver


class FakeCDP:
    def __init__(self, value):
        self.values = list(value) if isinstance(value, list) else [value]
        self.last_value = self.values[-1]
        self.connected = True
        self.js_calls: list[str] = []
        self.send_calls: list[tuple[str, dict | None]] = []

    def evaluate(self, js_code: str, timeout: int = 30):
        self.js_calls.append(js_code)
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


def test_exec_js_surfaces_cdp_errors():
    class BrokenCDP:
        connected = True

        def evaluate(self, js_code: str, timeout: int = 30):
            raise RuntimeError("boom")

    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.cdp = BrokenCDP()

    assert driver._exec_js("1") == {"ok": False, "error": "boom"}


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
