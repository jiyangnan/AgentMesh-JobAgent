from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from jobagent.drivers.boss.cdp_driver import CDPBossDriver
from jobagent.infra import platform_lock, platform_tabs, rounds


def test_round_state_is_created_and_platform_skip_is_round_local(monkeypatch, tmp_path):
    current_path = tmp_path / "current_round.json"
    rounds_path = tmp_path / "rounds"
    monkeypatch.setattr(rounds, "current_round_path", lambda: current_path)
    monkeypatch.setattr(rounds, "rounds_dir", lambda: rounds_path)
    monkeypatch.setattr(rounds, "new_round_id", lambda: "round-1")

    state = rounds.ensure_current_round()

    assert state["round_id"] == "round-1"
    assert state["platforms"]["boss"]["status"] == "pending"

    updated = rounds.set_platform_status("liepin", "skipped_this_round", command="test")

    assert updated["platforms"]["liepin"]["status"] == "skipped_this_round"
    assert "enabled" not in updated["platforms"]["liepin"]
    assert json.loads(current_path.read_text(encoding="utf-8"))["round_id"] == "round-1"
    assert (rounds_path / "round-1.json").exists()


def test_platform_session_lock_rejects_live_busy_lock(monkeypatch, tmp_path):
    lock_path = tmp_path / "browser-session.lock"
    lock_path.write_text(json.dumps({"pid": 999999, "platform": "boss"}), encoding="utf-8")
    monkeypatch.setattr(platform_lock, "browser_session_lock_path", lambda: lock_path)
    monkeypatch.setattr(platform_lock, "ensure_current_round", lambda: {"round_id": "round-1"})
    monkeypatch.setattr(platform_lock, "set_platform_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(platform_lock, "_pid_alive", lambda pid: True)

    with pytest.raises(platform_lock.PlatformLockError) as exc:
        platform_lock.PlatformSessionLock("liepin", "jobagent liepin collect").acquire()

    assert exc.value.payload["error"] == "browser_session_lock_busy"
    assert exc.value.payload["current"]["platform"] == "boss"


def test_platform_session_lock_cleans_stale_lock(monkeypatch, tmp_path):
    lock_path = tmp_path / "browser-session.lock"
    lock_path.write_text(json.dumps({"pid": 999999, "platform": "boss"}), encoding="utf-8")
    statuses: list[tuple[str, str]] = []
    monkeypatch.setattr(platform_lock, "browser_session_lock_path", lambda: lock_path)
    monkeypatch.setattr(platform_lock, "ensure_current_round", lambda: {"round_id": "round-1"})
    monkeypatch.setattr(
        platform_lock,
        "set_platform_status",
        lambda platform, status, **kwargs: statuses.append((platform, status)),
    )
    monkeypatch.setattr(platform_lock, "_pid_alive", lambda pid: False)

    with platform_lock.PlatformSessionLock("zhilian", "jobagent zhilian collect"):
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["platform"] == "zhilian"
        assert payload["pid"] == os.getpid()

    assert not lock_path.exists()
    assert statuses == [("zhilian", "active")]


def test_platform_tab_registry_reuses_existing_domain_target(monkeypatch, tmp_path):
    registry_path = tmp_path / "platform_tabs.json"
    activated: list[str] = []
    monkeypatch.setattr(platform_tabs, "platform_tabs_path", lambda: registry_path)
    monkeypatch.setattr(platform_tabs, "ensure_current_round", lambda: {"round_id": "round-1"})
    monkeypatch.setattr(platform_tabs, "mark_browser_session", lambda session_id: {"session_id": session_id})
    monkeypatch.setattr(
        platform_tabs,
        "list_targets",
        lambda port: [
            {
                "id": "target-1",
                "type": "page",
                "url": "https://www.liepin.com/zhaopin/?key=AI",
                "title": "Liepin",
                "webSocketDebuggerUrl": "ws://liepin",
            }
        ],
    )
    monkeypatch.setattr(platform_tabs, "_activate_target", lambda port, target_id: activated.append(target_id))

    target = platform_tabs.ensure_platform_tab(platform="liepin", port=19222)

    assert target["webSocketDebuggerUrl"] == "ws://liepin"
    assert activated == ["target-1"]
    saved = json.loads(registry_path.read_text(encoding="utf-8"))
    assert saved["tabs"]["liepin"]["target_id"] == "target-1"


def test_platform_tab_registry_creates_missing_platform_target(monkeypatch, tmp_path):
    registry_path = tmp_path / "platform_tabs.json"
    created: list[str] = []
    monkeypatch.setattr(platform_tabs, "platform_tabs_path", lambda: registry_path)
    monkeypatch.setattr(platform_tabs, "ensure_current_round", lambda: {"round_id": "round-1"})
    monkeypatch.setattr(platform_tabs, "mark_browser_session", lambda session_id: {"session_id": session_id})
    monkeypatch.setattr(platform_tabs, "list_targets", lambda port: [])

    def fake_create(port: int, url: str):
        created.append(url)
        return {
            "id": "target-new",
            "type": "page",
            "url": url,
            "title": "",
            "webSocketDebuggerUrl": "ws://new",
        }

    monkeypatch.setattr(platform_tabs, "_create_target", fake_create)
    monkeypatch.setattr(platform_tabs, "_activate_target", lambda port, target_id: None)

    target = platform_tabs.ensure_platform_tab(platform="zhilian", port=19222)

    assert created == ["https://sou.zhaopin.com/"]
    assert target["webSocketDebuggerUrl"] == "ws://new"


class FakeManager:
    port = 19222

    def __init__(self):
        self.ensure_calls = 0

    def ensure_running(self):
        self.ensure_calls += 1
        return "ws://unused"


class FakeCDP:
    def __init__(self):
        self.connected = False
        self.ws_urls: list[str] = []
        self.sent: list[tuple[str, dict]] = []

    def connect(self, ws_url: str):
        self.connected = True
        self.ws_urls.append(ws_url)

    def disconnect(self):
        self.connected = False

    def send(self, method: str, params=None, timeout: float = 30.0):
        self.sent.append((method, params or {}))
        return {}

    def evaluate(self, expression: str, **kwargs):
        return {"result": {"value": '{"url":"https://www.liepin.com/job/1.shtml","title":"Liepin"}'}}


def test_cdp_driver_switches_to_platform_tab_for_url(monkeypatch):
    selected: list[tuple[str, str]] = []

    def fake_ensure_platform_tab(*, platform: str, port: int, initial_url: str | None = None):
        selected.append((platform, initial_url or ""))
        return {"webSocketDebuggerUrl": f"ws://{platform}"}

    monkeypatch.setattr("jobagent.drivers.boss.cdp_driver.ensure_platform_tab", fake_ensure_platform_tab)
    monkeypatch.setattr("jobagent.drivers.boss.cdp_driver.time.sleep", lambda _: None)

    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.manager = FakeManager()
    driver.platform = "boss"
    driver.current_platform = ""
    driver.cdp = FakeCDP()

    result = driver.open_url_in_new_tab("https://www.liepin.com/job/1.shtml")

    assert result["ok"] is True
    assert selected == [("liepin", "https://www.liepin.com/job/1.shtml")]
    assert driver.cdp.ws_urls == ["ws://liepin"]
    assert driver.current_platform == "liepin"
