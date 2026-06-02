"""Tests for CDP driver compatibility helpers."""

from jobagent.drivers.boss import cdp_driver
from jobagent.drivers.boss.cdp_driver import CDPBossDriver


class FakeCDP:
    def __init__(self, value):
        self.values = list(value) if isinstance(value, list) else [value]
        self.last_value = self.values[-1]
        self.connected = True

    def evaluate(self, js_code: str, timeout: int = 30):
        value = self.values.pop(0) if self.values else self.last_value
        self.last_value = value
        return {"result": {"value": value}}


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
        '{"ok": true, "step": "clicked_立即沟通"}',
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


def test_inspect_chat_editor_timeout_is_not_auto_sent(monkeypatch):
    monkeypatch.setattr(cdp_driver.time, "sleep", lambda _seconds: None)
    driver = make_driver('{"ok": true, "editorFound": false}')

    result = driver.inspect_chat_editor()

    assert result["ok"] is True
    assert result["autoSent"] is False
    assert result["editorFound"] is False
    assert result["step"] == "editor_not_found"
