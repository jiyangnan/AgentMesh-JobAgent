from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobagent.drivers.boss.cdp_driver import CDPBossDriver
from jobagent.drivers.boss import create_driver
from jobagent.infra import activity, platform_lock, platform_tabs, rounds


def test_round_state_is_created_and_platform_skip_is_round_local(monkeypatch, tmp_path):
    current_path = tmp_path / "current_round.json"
    rounds_path = tmp_path / "rounds"
    monkeypatch.setattr(rounds, "current_round_path", lambda: current_path)
    monkeypatch.setattr(rounds, "rounds_dir", lambda: rounds_path)
    monkeypatch.setattr(rounds, "new_round_id", lambda: "round-1")

    state = rounds.start_new_round()

    assert state["round_id"] == "round-1"
    assert state["platforms"]["boss"]["status"] == "pending"
    assert state["browser_executor"] == "codex_native"
    assert state["browser_session_id"] == "native-unbound"
    assert state["native_session"] is None

    workflow = rounds.round_status()
    assert workflow["execution_policy"] == {
        "mode": "vertical_end_to_end",
        "prelogin_future_platforms": False,
        "advance_only_after": "audit",
            "stages": [
                "login",
                "discover",
                "review",
                "delivery_preview",
                "delivery_confirmation",
                "send",
                "audit",
            ],
    }

    updated = rounds.set_platform_status("liepin", "skipped_this_round", command="test")

    assert updated["platforms"]["liepin"]["status"] == "skipped_this_round"
    assert "enabled" not in updated["platforms"]["liepin"]
    assert json.loads(current_path.read_text(encoding="utf-8"))["round_id"] == "round-1"
    assert (rounds_path / "round-1.json").exists()


def test_recent_login_verification_is_bound_to_round_platform_and_browser_session(
    monkeypatch,
    tmp_path,
):
    current_path = tmp_path / "current_round.json"
    rounds_path = tmp_path / "rounds"
    monkeypatch.setattr(rounds, "current_round_path", lambda: current_path)
    monkeypatch.setattr(rounds, "rounds_dir", lambda: rounds_path)
    monkeypatch.setattr(rounds, "new_round_id", lambda: "round-1")

    state = rounds.start_new_round()
    rounds.mark_browser_session("local-cdp-19222")
    verified_at = datetime.now(timezone.utc).isoformat()
    rounds.set_platform_status(
        "zhilian",
        "login_verified",
        evidence={
            "login": {
                "schema_version": 1,
                "logged_in": True,
                "platform": "zhilian",
                "round_id": "round-1",
                "browser_session_id": "local-cdp-19222",
                "verified_at": verified_at,
            }
        },
    )

    receipt = rounds.recent_platform_login_verification("zhilian")

    assert receipt is not None
    assert receipt["round_id"] == state["round_id"]
    assert receipt["browser_session_id"] == "local-cdp-19222"
    assert receipt["age_seconds"] >= 0

    current = json.loads(current_path.read_text(encoding="utf-8"))
    current["browser_session_id"] = "local-cdp-19333"
    current_path.write_text(json.dumps(current), encoding="utf-8")
    assert rounds.recent_platform_login_verification("zhilian") is None


def test_login_verification_expires_instead_of_becoming_permanent(monkeypatch, tmp_path):
    current_path = tmp_path / "current_round.json"
    rounds_path = tmp_path / "rounds"
    monkeypatch.setattr(rounds, "current_round_path", lambda: current_path)
    monkeypatch.setattr(rounds, "rounds_dir", lambda: rounds_path)
    monkeypatch.setattr(rounds, "new_round_id", lambda: "round-1")

    rounds.start_new_round()
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    rounds.set_platform_status(
        "zhilian",
        "login_verified",
        evidence={
            "login": {
                "schema_version": 1,
                "logged_in": True,
                "platform": "zhilian",
                "round_id": "round-1",
                "browser_session_id": "local-cdp-19222",
                "verified_at": stale,
            }
        },
    )

    assert rounds.recent_platform_login_verification("zhilian") is None


def test_round_status_and_platform_guard_do_not_create_a_round(monkeypatch, tmp_path):
    current_path = tmp_path / "current_round.json"
    rounds_path = tmp_path / "rounds"
    monkeypatch.setattr(rounds, "current_round_path", lambda: current_path)
    monkeypatch.setattr(rounds, "rounds_dir", lambda: rounds_path)

    workflow = rounds.round_status()

    assert workflow["status"] == "not_started"
    assert workflow["next_suggested"] == "jobagent round start"
    assert not current_path.exists()
    with pytest.raises(rounds.RoundOrderError) as error:
        rounds.assert_platform_turn("boss")
    assert error.value.payload["error"] == "round_not_started"
    assert not current_path.exists()
    with pytest.raises(rounds.RoundOrderError) as state_error:
        rounds.set_platform_status("boss", "active")
    assert state_error.value.payload["error"] == "round_not_started"
    assert not current_path.exists()


def test_round_workflow_continues_to_liepin_after_boss_completion(monkeypatch, tmp_path):
    current_path = tmp_path / "current_round.json"
    rounds_path = tmp_path / "rounds"
    monkeypatch.setattr(rounds, "current_round_path", lambda: current_path)
    monkeypatch.setattr(rounds, "rounds_dir", lambda: rounds_path)
    monkeypatch.setattr(rounds, "new_round_id", lambda: "round-1")

    rounds.start_new_round()
    rounds.set_platform_status("boss", "completed", command="jobagent boss audit")
    workflow = rounds.round_status()

    assert workflow["workflow_complete"] is False
    assert workflow["continue_required"] is True
    assert workflow["current_platform"] == "liepin"
    assert workflow["next_suggested"] == "jobagent liepin login --check"
    assert workflow["remaining_platforms"] == ["liepin", "zhilian", "51job"]
    assert workflow["delivery_policy"] == {
        "selected": "user_confirmed_after_preview",
        "review": "explicit_override_only",
        "rejected": "never",
        "per_platform_confirmation": True,
    }


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("boss", "jobagent boss greet send"),
        ("liepin", "jobagent liepin apply send"),
        ("zhilian", "jobagent zhilian apply send"),
        ("51job", "jobagent 51job apply send"),
    ],
)
def test_reviewed_platform_next_command_auto_sends_selected(
    platform, expected, monkeypatch, tmp_path
):
    current_path = tmp_path / "current_round.json"
    rounds_path = tmp_path / "rounds"
    monkeypatch.setattr(rounds, "current_round_path", lambda: current_path)
    monkeypatch.setattr(rounds, "rounds_dir", lambda: rounds_path)
    monkeypatch.setattr(rounds, "new_round_id", lambda: "round-1")

    rounds.start_new_round()
    for preceding in rounds.DEFAULT_PLATFORM_ORDER:
        if preceding == platform:
            break
        rounds.set_platform_status(preceding, "completed")
    rounds.set_platform_status(platform, "reviewed")

    assert rounds.round_status()["next_suggested"] == expected


def test_legacy_confirmation_flag_is_removed_from_persisted_next_command(
    monkeypatch, tmp_path
):
    current_path = tmp_path / "current_round.json"
    rounds_path = tmp_path / "rounds"
    monkeypatch.setattr(rounds, "current_round_path", lambda: current_path)
    monkeypatch.setattr(rounds, "rounds_dir", lambda: rounds_path)
    monkeypatch.setattr(rounds, "new_round_id", lambda: "round-1")

    rounds.start_new_round()
    rounds.set_platform_status("boss", "completed")
    rounds.set_platform_status(
        "liepin",
        "reviewed",
        next_suggested=(
            "jobagent liepin apply send --input /tmp/review.json "
            "--limit 100 --confirm-submit"
        ),
    )

    workflow = rounds.round_status()

    assert workflow["next_suggested"] == (
        "jobagent liepin apply send --input /tmp/review.json --limit 100"
    )
    persisted = json.loads(current_path.read_text(encoding="utf-8"))
    assert "--confirm-submit" not in persisted["platforms"]["liepin"]["next_suggested"]


def test_round_workflow_completes_only_when_every_platform_is_terminal(monkeypatch, tmp_path):
    current_path = tmp_path / "current_round.json"
    rounds_path = tmp_path / "rounds"
    monkeypatch.setattr(rounds, "current_round_path", lambda: current_path)
    monkeypatch.setattr(rounds, "rounds_dir", lambda: rounds_path)
    monkeypatch.setattr(rounds, "new_round_id", lambda: "round-1")

    rounds.start_new_round()
    rounds.set_platform_status("boss", "completed")
    rounds.set_platform_status("liepin", "skipped_this_round")
    rounds.set_platform_status("zhilian", "completed")
    rounds.set_platform_status("51job", "completed")
    workflow = rounds.round_status()

    assert workflow["workflow_complete"] is True
    assert workflow["continue_required"] is False
    assert workflow["current_platform"] is None
    assert workflow["next_suggested"] is None
    assert json.loads(current_path.read_text(encoding="utf-8"))["status"] == "completed"


def test_audit_only_advances_a_sent_platform(monkeypatch, tmp_path):
    current_path = tmp_path / "current_round.json"
    rounds_path = tmp_path / "rounds"
    monkeypatch.setattr(rounds, "current_round_path", lambda: current_path)
    monkeypatch.setattr(rounds, "rounds_dir", lambda: rounds_path)
    monkeypatch.setattr(rounds, "new_round_id", lambda: "round-1")

    rounds.start_new_round()
    unchanged = rounds.complete_platform_after_audit("boss")
    assert unchanged["platforms"]["boss"]["status"] == "pending"

    rounds.set_platform_status(
        "boss",
        "sent",
        command="jobagent boss greet send",
        next_suggested="jobagent boss audit",
    )
    advanced = rounds.complete_platform_after_audit("boss")

    assert advanced["platforms"]["boss"]["status"] == "completed"
    assert advanced["current_platform"] == "liepin"
    assert advanced["next_suggested"] == "jobagent liepin login --check"


def test_legacy_round_is_migrated_to_four_platform_pending_state(monkeypatch, tmp_path):
    current_path = tmp_path / "current_round.json"
    rounds_path = tmp_path / "rounds"
    current_path.write_text(
        json.dumps(
            {
                "round_id": "legacy-round",
                "status": "active",
                "platform_order": ["boss", "liepin", "zhilian"],
                "platforms": {
                    "boss": {"status": "active"},
                    "liepin": {"status": "active"},
                    "zhilian": {"status": "active"},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(rounds, "current_round_path", lambda: current_path)
    monkeypatch.setattr(rounds, "rounds_dir", lambda: rounds_path)

    workflow = rounds.round_status()

    assert workflow["platform_order"] == ["boss", "liepin", "zhilian", "51job"]
    assert workflow["remaining_platforms"] == ["boss", "liepin", "zhilian", "51job"]
    assert all(item["status"] == "pending" for item in workflow["platforms"].values())
    assert json.loads(current_path.read_text(encoding="utf-8"))["schema_version"] == rounds.ROUND_SCHEMA_VERSION


def test_auto_driver_reports_cdp_failure_without_switching_browser_profiles(monkeypatch):
    fallback_used = False

    class FailingCDPDriver:
        def __init__(self, platform="boss"):
            raise RuntimeError("cdp_start_failed")

    class UnexpectedAppleScriptDriver:
        def __init__(self):
            nonlocal fallback_used
            fallback_used = True

    monkeypatch.setattr("jobagent.drivers.boss.cdp_driver.CDPBossDriver", FailingCDPDriver)
    monkeypatch.setattr(
        "jobagent.drivers.boss.applescript_driver.AppleScriptBossDriver",
        UnexpectedAppleScriptDriver,
    )

    with pytest.raises(RuntimeError, match="cdp_start_failed"):
        create_driver(platform="liepin")

    assert fallback_used is False


def test_round_rejects_platforms_that_are_not_current(monkeypatch, tmp_path):
    current_path = tmp_path / "current_round.json"
    rounds_path = tmp_path / "rounds"
    monkeypatch.setattr(rounds, "current_round_path", lambda: current_path)
    monkeypatch.setattr(rounds, "rounds_dir", lambda: rounds_path)
    monkeypatch.setattr(rounds, "new_round_id", lambda: "round-1")

    rounds.start_new_round()

    with pytest.raises(rounds.RoundOrderError) as exc:
        rounds.assert_platform_turn("liepin")

    assert exc.value.payload["error"] == "platform_out_of_order"
    assert exc.value.payload["current_platform"] == "boss"
    assert exc.value.payload["next_suggested"] == "jobagent boss login --check"
    assert "Do not pre-login" in exc.value.payload["message"]
    assert exc.value.payload["execution_policy"]["prelogin_future_platforms"] is False

    rounds.set_platform_status("boss", "completed")
    rounds.assert_platform_turn("liepin")


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


def test_platform_session_lock_preserves_login_receipt_when_collection_fails(
    monkeypatch,
    tmp_path,
):
    lock_path = tmp_path / "browser-session.lock"
    statuses: list[tuple[str, str, dict | None]] = []
    login = {
        "schema_version": 1,
        "logged_in": True,
        "platform": "zhilian",
        "round_id": "round-1",
        "browser_session_id": "local-cdp-19222",
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    monkeypatch.setattr(platform_lock, "browser_session_lock_path", lambda: lock_path)
    monkeypatch.setattr(
        platform_lock,
        "ensure_current_round",
        lambda: {
            "round_id": "round-1",
            "platforms": {"zhilian": {"evidence": {"login": login}}},
        },
    )
    monkeypatch.setattr(
        platform_lock,
        "set_platform_status",
        lambda platform, status, **kwargs: statuses.append(
            (platform, status, kwargs.get("evidence"))
        ),
    )

    with pytest.raises(RuntimeError):
        with platform_lock.PlatformSessionLock("zhilian", "jobagent zhilian discover"):
            raise RuntimeError("zhilian_job_cards_not_found")

    assert statuses[-1] == (
        "zhilian",
        "blocked",
        {"login": login, "error": "zhilian_job_cards_not_found"},
    )


def test_dead_activity_lock_is_reclaimed(monkeypatch, tmp_path):
    lock_path = tmp_path / "activity.lock"
    lock_path.write_text(
        json.dumps({"pid": 999999, "command": "jobagent boss discover"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(activity, "activity_lock_path", lambda: lock_path)
    monkeypatch.setattr(activity, "_pid_alive", lambda _pid: False)

    assert activity.activity_lock_active() is False
    assert not lock_path.exists()


def test_live_activity_lock_is_preserved(monkeypatch, tmp_path):
    lock_path = tmp_path / "activity.lock"
    lock_path.write_text(
        json.dumps({"pid": 42, "command": "jobagent boss discover"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(activity, "activity_lock_path", lambda: lock_path)
    monkeypatch.setattr(activity, "_pid_alive", lambda _pid: True)

    assert activity.activity_lock_active() is True
    assert lock_path.exists()


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

    assert created == ["https://www.zhaopin.com/"]
    assert target["webSocketDebuggerUrl"] == "ws://new"


def test_untracked_platform_tab_does_not_require_or_write_round_state(
    monkeypatch,
    tmp_path,
):
    registry_path = tmp_path / "platform_tabs.json"
    monkeypatch.setattr(platform_tabs, "platform_tabs_path", lambda: registry_path)
    monkeypatch.setattr(
        platform_tabs,
        "ensure_current_round",
        lambda: (_ for _ in ()).throw(AssertionError("round must not be read")),
    )
    monkeypatch.setattr(
        platform_tabs,
        "mark_browser_session",
        lambda _session_id: (_ for _ in ()).throw(
            AssertionError("round must not be changed")
        ),
    )
    monkeypatch.setattr(
        platform_tabs,
        "list_targets",
        lambda _port: [
            {
                "id": "canary-target",
                "type": "page",
                "url": "https://www.zhipin.com/",
                "title": "Boss",
                "webSocketDebuggerUrl": "ws://canary",
            }
        ],
    )
    monkeypatch.setattr(
        platform_tabs,
        "_activate_target",
        lambda _port, _target_id: None,
    )

    target = platform_tabs.ensure_platform_tab(
        platform="boss",
        port=19322,
        track_round=False,
    )

    assert target["webSocketDebuggerUrl"] == "ws://canary"
    assert not registry_path.exists()


def test_adopt_platform_tab_target_updates_registry_before_closing_previous(
    monkeypatch,
    tmp_path,
):
    registry_path = tmp_path / "platform_tabs.json"
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(platform_tabs, "platform_tabs_path", lambda: registry_path)
    monkeypatch.setattr(platform_tabs, "ensure_current_round", lambda: {"round_id": "round-1"})
    monkeypatch.setattr(platform_tabs, "mark_browser_session", lambda session_id: {"session_id": session_id})
    monkeypatch.setattr(
        platform_tabs,
        "_activate_target",
        lambda _port, target_id: events.append(("activate", target_id)),
    )

    target = {
        "id": "target-search",
        "type": "page",
        "url": "https://www.zhaopin.com/sou/jl765/kwOPAQUE",
        "title": "深圳职位",
        "webSocketDebuggerUrl": "ws://target-search",
    }
    adopted = platform_tabs.adopt_platform_tab_target(
        platform="zhilian",
        port=19222,
        target=target,
    )

    assert adopted["id"] == "target-search"
    assert events == [("activate", "target-search")]
    saved = json.loads(registry_path.read_text(encoding="utf-8"))
    assert saved["tabs"]["zhilian"]["target_id"] == "target-search"
    assert saved["tabs"]["zhilian"]["url"].startswith("https://www.zhaopin.com/sou/")


def test_close_platform_target_uses_exact_cdp_target(monkeypatch):
    calls: list[str] = []
    target = {
        "id": "target-old",
        "type": "page",
        "url": "https://www.zhaopin.com/",
        "webSocketDebuggerUrl": "ws://target-old",
    }
    target_sets = iter([[target], []])
    monkeypatch.setattr(
        platform_tabs,
        "list_targets",
        lambda _port: next(target_sets),
    )
    monkeypatch.setattr(
        platform_tabs,
        "_request_json",
        lambda _port, path, method="GET": calls.append(f"{method} {path}") or {},
    )

    receipt = platform_tabs.close_platform_target(19222, "target-old")

    assert calls == ["GET /json/close/target-old"]
    assert receipt["closed"] is True
    assert receipt["outcome"] == "target_closed"


def test_close_platform_target_accepts_chrome_plain_text_success(monkeypatch):
    target = {
        "id": "target-old",
        "type": "page",
        "url": "https://www.zhaopin.com/",
        "webSocketDebuggerUrl": "ws://target-old",
    }
    target_sets = iter([[target], []])
    monkeypatch.setattr(
        platform_tabs,
        "list_targets",
        lambda _port: next(target_sets),
    )
    monkeypatch.setattr(
        platform_tabs,
        "_request_json",
        lambda _port, _path, method="GET": (_ for _ in ()).throw(
            json.JSONDecodeError("plain text", "Target is closing", 0)
        ),
    )

    receipt = platform_tabs.close_platform_target(19222, "target-old")

    assert receipt["closed"] is True
    assert receipt["outcome"] == "target_closed"


def test_close_platform_target_reports_unverified_when_exact_target_remains(
    monkeypatch,
):
    target = {
        "id": "target-action-child",
        "type": "page",
        "url": "https://www.zhaopin.com/",
        "webSocketDebuggerUrl": "ws://target-action-child",
    }
    monkeypatch.setattr(platform_tabs, "list_targets", lambda _port: [target])
    monkeypatch.setattr(platform_tabs, "_request_json", lambda *_args, **_kwargs: {})

    receipt = platform_tabs.close_platform_target(
        19222,
        "target-action-child",
        expected_websocket_url="ws://target-action-child",
        wait_seconds=0,
    )

    assert receipt == {
        "closed": False,
        "outcome": "target_close_not_observed",
        "target_id": "target-action-child",
    }


class FakeManager:
    port = 19222

    def __init__(self):
        self.ensure_calls = 0

    def ensure_running(self):
        self.ensure_calls += 1
        return "ws://unused"


def _record_closed_target(closed: list[str], target_id: str) -> dict[str, object]:
    closed.append(target_id)
    return {
        "closed": True,
        "outcome": "target_closed",
        "target_id": target_id,
    }


class FakeCDP:
    def __init__(self):
        self.connected = False
        self.ws_url = ""
        self.ws_urls: list[str] = []
        self.sent: list[tuple[str, dict]] = []
        self.target_infos: list[dict] = []

    def connect(self, ws_url: str):
        self.connected = True
        self.ws_url = ws_url
        self.ws_urls.append(ws_url)

    def disconnect(self):
        self.connected = False
        self.ws_url = ""

    def send(self, method: str, params=None, timeout: float = 30.0):
        self.sent.append((method, params or {}))
        if method == "Target.getTargets":
            return {"targetInfos": self.target_infos}
        return {}

    def evaluate(self, expression: str, **kwargs):
        return {
            "result": {
                "value": json.dumps(
                    {
                        "url": "https://www.liepin.com/job/1.shtml",
                        "title": "Liepin",
                        "readyState": "complete",
                        "authenticated": True,
                        "hasAction": True,
                        "loginRequired": False,
                    }
                )
            }
        }


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


def test_cdp_driver_adopts_one_new_official_zhilian_search_target(monkeypatch):
    target_sets = iter(
        [
            [
                {
                    "id": "target-old",
                    "type": "page",
                    "url": "https://www.zhaopin.com/shenzhen/",
                    "webSocketDebuggerUrl": "ws://target-old",
                }
            ],
            [
                {
                    "id": "target-old",
                    "type": "page",
                    "url": "https://www.zhaopin.com/shenzhen/",
                    "webSocketDebuggerUrl": "ws://target-old",
                },
                {
                    "id": "target-search",
                    "type": "page",
                    "url": "https://www.zhaopin.com/sou/jl765/kwOPAQUE",
                    "webSocketDebuggerUrl": "ws://target-search",
                },
            ],
        ]
    )
    adopted: list[str] = []
    closed: list[str] = []
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.list_targets",
        lambda _port: next(target_sets),
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.adopt_platform_tab_target",
        lambda **kwargs: adopted.append(kwargs["target"]["id"]) or kwargs["target"],
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.close_platform_target",
        lambda _port, target_id, **_kwargs: _record_closed_target(
            closed, target_id
        ),
    )

    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.manager = FakeManager()
    driver.platform = "zhilian"
    driver.current_platform = "zhilian"
    driver.current_target_id = "target-old"
    driver.track_round = True
    driver.cdp = FakeCDP()
    driver.cdp.connected = True

    before = driver.capture_platform_target_state("zhilian")
    result = driver.adopt_platform_target_transition(
        before,
        platform="zhilian",
        wait_seconds=0,
    )

    assert result["ok"] is True
    assert result["outcome"] == "new_target_adopted"
    assert result["new_target_count"] == 1
    assert adopted == ["target-search"]
    assert driver.cdp.ws_urls == ["ws://target-search"]
    assert driver.current_target_id == "target-search"
    assert driver.current_platform == "zhilian"
    assert closed == ["target-old"]


def test_cdp_driver_adopts_owned_zhilian_root_child_for_bounded_verification(
    monkeypatch,
):
    target_sets = iter(
        [
            [
                {
                    "id": "target-origin",
                    "type": "page",
                    "url": "https://www.zhaopin.com/city-example/",
                    "webSocketDebuggerUrl": "ws://target-origin",
                }
            ],
            [
                {
                    "id": "target-origin",
                    "type": "page",
                    "url": "https://www.zhaopin.com/city-example/",
                    "webSocketDebuggerUrl": "ws://target-origin",
                },
                {
                    "id": "target-action-child",
                    "type": "page",
                    "url": "https://www.zhaopin.com/",
                    "webSocketDebuggerUrl": "ws://target-action-child",
                },
            ],
        ]
    )
    adopted: list[str] = []
    closed: list[str] = []
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.list_targets",
        lambda _port: next(target_sets),
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.adopt_platform_tab_target",
        lambda **kwargs: adopted.append(kwargs["target"]["id"])
        or kwargs["target"],
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.close_platform_target",
        lambda _port, target_id, **_kwargs: _record_closed_target(
            closed, target_id
        ),
    )

    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.manager = FakeManager()
    driver.platform = "zhilian"
    driver.current_platform = "zhilian"
    driver.current_target_id = "target-origin"
    driver.track_round = True
    driver.cdp = FakeCDP()
    driver.cdp.connected = True
    driver.cdp.ws_url = "ws://target-origin"
    driver.cdp.target_infos = [
        {
            "targetId": "target-action-child",
            "openerId": "target-origin",
        }
    ]

    before = driver.capture_platform_target_state("zhilian")
    result = driver.adopt_platform_target_transition(
        before,
        platform="zhilian",
        wait_seconds=0,
    )

    assert result["ok"] is True
    assert result["outcome"] == "action_linked_provisional_target_adopted"
    assert result["new_target_count"] == 1
    assert result["previous_target_closed"] is True
    assert adopted == ["target-action-child"]
    assert closed == ["target-origin"]
    assert driver.cdp.ws_urls == ["ws://target-action-child"]
    assert driver.current_target_id == "target-action-child"


def test_cdp_driver_reuses_current_target_after_same_tab_search_navigation(monkeypatch):
    target = {
        "id": "target-current",
        "type": "page",
        "url": "https://www.zhaopin.com/sou/jl765/kwOPAQUE",
        "webSocketDebuggerUrl": "ws://target-current",
    }
    adopted: list[str] = []
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.list_targets",
        lambda _port: [target],
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.adopt_platform_tab_target",
        lambda **kwargs: adopted.append(kwargs["target"]["id"]) or kwargs["target"],
    )

    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.manager = FakeManager()
    driver.platform = "zhilian"
    driver.current_platform = "zhilian"
    driver.current_target_id = "target-current"
    driver.track_round = True
    driver.cdp = FakeCDP()
    driver.cdp.connected = True

    result = driver.adopt_platform_target_transition(
        {
            "platform": "zhilian",
            "target_ids": ["target-current"],
            "current_target_id": "target-current",
        },
        platform="zhilian",
        wait_seconds=0,
    )

    assert result == {
        "ok": True,
        "outcome": "current_target_reused",
        "new_target_count": 0,
        "previous_target_closed": False,
    }
    assert adopted == ["target-current"]
    assert driver.cdp.ws_urls == []


def test_cdp_driver_adopts_changed_existing_zhilian_target_after_city_selection(
    monkeypatch,
):
    before_targets = [
        {
            "id": "target-root",
            "type": "page",
            "url": "https://www.zhaopin.com/",
            "title": "智联招聘_求职_找工作",
            "webSocketDebuggerUrl": "ws://target-root",
        },
        {
            "id": "target-existing",
            "type": "page",
            "url": "https://www.zhaopin.com/jobs?jl=765&kw=OPAQUE",
            "title": "深圳热门职位招聘",
            "webSocketDebuggerUrl": "ws://target-existing",
        },
    ]
    after_targets = [
        before_targets[0],
        {
            "id": "target-existing",
            "type": "page",
            "url": "https://www.zhaopin.com/jobs?jl=901&kw=OPAQUE",
            "title": "郑州热门职位招聘",
            "webSocketDebuggerUrl": "ws://target-existing",
        },
    ]
    calls = 0

    def fake_list_targets(_port):
        nonlocal calls
        calls += 1
        return before_targets if calls == 1 else after_targets

    adopted: list[str] = []
    closed: list[str] = []
    monotonic_values = iter([0.0, 3.0])
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.list_targets",
        fake_list_targets,
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.adopt_platform_tab_target",
        lambda **kwargs: adopted.append(kwargs["target"]["id"]) or kwargs["target"],
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.close_platform_target",
        lambda _port, target_id, **_kwargs: _record_closed_target(
            closed, target_id
        ),
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.time.monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.time.sleep",
        lambda _seconds: None,
    )

    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.manager = FakeManager()
    driver.platform = "zhilian"
    driver.current_platform = "zhilian"
    driver.current_target_id = "target-root"
    driver.track_round = True
    driver.cdp = FakeCDP()
    driver.cdp.connected = True

    before = driver.capture_platform_target_state("zhilian")
    result = driver.adopt_platform_target_transition(
        before,
        platform="zhilian",
        wait_seconds=0,
        allow_changed_platform_page=True,
    )

    assert set(before["target_fingerprints"]) == {"target-root", "target-existing"}
    assert result == {
        "ok": True,
        "outcome": "changed_existing_target_adopted",
        "new_target_count": 0,
        "previous_target_closed": False,
    }
    assert adopted == ["target-existing"]
    assert driver.cdp.ws_urls == ["ws://target-existing"]
    assert driver.current_target_id == "target-existing"
    assert closed == []


def test_cdp_driver_capture_reconciles_current_target_from_active_websocket(
    monkeypatch,
):
    targets = [
        {
            "id": "target-stale-registry",
            "type": "page",
            "url": "https://www.zhaopin.com/jobs?jl=765&kw=OPAQUE",
            "title": "深圳热门职位招聘",
            "webSocketDebuggerUrl": "ws://target-stale-registry",
        },
        {
            "id": "target-action-origin",
            "type": "page",
            "url": "https://www.zhaopin.com/",
            "title": "智联招聘_求职_找工作",
            "webSocketDebuggerUrl": "ws://target-action-origin",
        },
    ]
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.list_targets",
        lambda _port: targets,
    )

    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.manager = FakeManager()
    driver.platform = "zhilian"
    driver.current_platform = "zhilian"
    driver.current_target_id = "target-stale-registry"
    driver.track_round = True
    driver.cdp = FakeCDP()
    driver.cdp.connected = True
    driver.cdp.ws_url = "ws://target-action-origin"

    before = driver.capture_platform_target_state("zhilian")

    assert before["current_target_id"] == "target-action-origin"
    assert driver.current_target_id == "target-action-origin"


def test_cdp_driver_prefers_action_linked_target_with_many_historical_targets(
    monkeypatch,
):
    before_targets = [
        {
            "id": "target-root",
            "type": "page",
            "url": "https://www.zhaopin.com/",
            "title": "智联招聘_求职_找工作",
            "webSocketDebuggerUrl": "ws://target-root",
        }
    ] + [
        {
            "id": f"target-history-{index}",
            "type": "page",
            "url": "https://www.zhaopin.com/jobs?jl=765&kw=OPAQUE",
            "title": f"深圳热门职位招聘 {index}",
            "webSocketDebuggerUrl": f"ws://target-history-{index}",
        }
        for index in range(15)
    ]
    after_targets = [dict(target) for target in before_targets]
    after_targets[1] = {
        **after_targets[1],
        "url": "https://www.zhaopin.com/jobs?jl=765&kw=OPAQUE-REFRESHED",
        "title": "深圳热门职位招聘 refreshed",
    }
    after_targets.append(
        {
            "id": "target-action-result",
            "type": "page",
            "url": "https://www.zhaopin.com/jobs?jl=901&kw=OPAQUE",
            "title": "郑州热门职位招聘",
            "webSocketDebuggerUrl": "ws://target-action-result",
        }
    )
    calls = 0

    def fake_list_targets(_port):
        nonlocal calls
        calls += 1
        return before_targets if calls == 1 else after_targets

    adopted: list[str] = []
    closed: list[str] = []
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.list_targets",
        fake_list_targets,
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.adopt_platform_tab_target",
        lambda **kwargs: adopted.append(kwargs["target"]["id"]) or kwargs["target"],
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.close_platform_target",
        lambda _port, target_id, **_kwargs: _record_closed_target(
            closed, target_id
        ),
    )

    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.manager = FakeManager()
    driver.platform = "zhilian"
    driver.current_platform = "zhilian"
    driver.current_target_id = "target-root"
    driver.track_round = True
    driver.cdp = FakeCDP()
    driver.cdp.connected = True
    driver.cdp.ws_url = "ws://target-root"
    driver.cdp.target_infos = [
        {
            "targetId": "target-action-result",
            "type": "page",
            "url": "https://www.zhaopin.com/jobs?jl=901&kw=OPAQUE",
            "title": "郑州热门职位招聘",
            "openerId": "target-root",
        }
    ]

    before = driver.capture_platform_target_state("zhilian")
    result = driver.adopt_platform_target_transition(
        before,
        platform="zhilian",
        wait_seconds=0,
        allow_changed_platform_page=True,
    )

    assert len(before["target_ids"]) == 16
    assert result["ok"] is True
    assert result["outcome"] == "action_linked_target_adopted"
    assert result["matching_target_count"] == 17
    assert result["action_candidate_count"] == 1
    assert adopted == ["target-action-result"]
    assert driver.current_target_id == "target-action-result"
    assert closed == ["target-root"]


def test_cdp_driver_reuses_action_origin_when_child_stays_on_generic_root(
    monkeypatch,
):
    before_targets = [
        {
            "id": "target-action-origin",
            "type": "page",
            "url": "https://www.zhaopin.com/",
            "title": "智联招聘_求职_找工作",
            "webSocketDebuggerUrl": "ws://target-action-origin",
        },
        {
            "id": "target-history",
            "type": "page",
            "url": "https://www.zhaopin.com/jobs?jl=765&kw=OPAQUE",
            "title": "深圳热门职位招聘",
            "webSocketDebuggerUrl": "ws://target-history",
        },
    ]
    after_targets = [
        {
            **before_targets[0],
            "url": "https://www.zhaopin.com/jobs?jl=901&kw=OPAQUE",
            "title": "郑州热门职位招聘",
        },
        before_targets[1],
        {
            "id": "target-action-child",
            "type": "page",
            "url": "https://www.zhaopin.com/",
            "title": "智联招聘_求职_找工作",
            "webSocketDebuggerUrl": "ws://target-action-child",
        },
    ]
    calls = 0

    def fake_list_targets(_port):
        nonlocal calls
        calls += 1
        return before_targets if calls == 1 else after_targets

    adopted: list[str] = []
    closed: list[str] = []
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.list_targets",
        fake_list_targets,
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.adopt_platform_tab_target",
        lambda **kwargs: adopted.append(kwargs["target"]["id"]) or kwargs["target"],
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.close_platform_target",
        lambda _port, target_id, **_kwargs: _record_closed_target(
            closed, target_id
        ),
    )

    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.manager = FakeManager()
    driver.platform = "zhilian"
    driver.current_platform = "zhilian"
    driver.current_target_id = "target-action-origin"
    driver.track_round = True
    driver.cdp = FakeCDP()
    driver.cdp.connected = True
    driver.cdp.ws_url = "ws://target-action-origin"
    driver.cdp.target_infos = []

    before = driver.capture_platform_target_state("zhilian")
    result = driver.adopt_platform_target_transition(
        before,
        platform="zhilian",
        wait_seconds=0,
        allow_changed_platform_page=True,
        expected_title_text="郑州",
    )

    assert result["ok"] is True
    assert result["outcome"] == "current_target_reused"
    assert result["previous_target_closed"] is False
    assert result["discarded_action_target_count"] == 1
    assert adopted == ["target-action-origin"]
    assert closed == ["target-action-child"]
    assert driver.current_target_id == "target-action-origin"
    assert driver.cdp.ws_urls == []


def test_cdp_driver_reuses_search_origin_and_discards_generic_action_child(
    monkeypatch,
):
    before_targets = [
        {
            "id": "target-action-origin",
            "type": "page",
            "url": "https://www.zhaopin.com/zhengzhou/",
            "title": "郑州招聘网",
            "webSocketDebuggerUrl": "ws://target-action-origin",
        }
    ] + [
        {
            "id": f"target-history-{index}",
            "type": "page",
            "url": "https://www.zhaopin.com/jobs?jl=765&kw=OPAQUE",
            "title": "深圳热门职位招聘",
            "webSocketDebuggerUrl": f"ws://target-history-{index}",
        }
        for index in range(22)
    ]
    after_targets = [
        {
            **before_targets[0],
            "url": "https://www.zhaopin.com/jobs?jl=991&kw=OPAQUE",
            "title": "郑州热门职位招聘",
        },
        *before_targets[1:],
        {
            "id": "target-action-child",
            "type": "page",
            "url": "https://www.zhaopin.com/",
            "title": "智联招聘_求职_找工作",
            "webSocketDebuggerUrl": "ws://target-action-child",
        },
    ]
    calls = 0

    def fake_list_targets(_port):
        nonlocal calls
        calls += 1
        return before_targets if calls == 1 else after_targets

    closed: list[str] = []
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.list_targets",
        fake_list_targets,
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.adopt_platform_tab_target",
        lambda **kwargs: kwargs["target"],
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.close_platform_target",
        lambda _port, target_id, **_kwargs: _record_closed_target(
            closed, target_id
        ),
    )

    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.manager = FakeManager()
    driver.platform = "zhilian"
    driver.current_platform = "zhilian"
    driver.current_target_id = "target-action-origin"
    driver.track_round = True
    driver.cdp = FakeCDP()
    driver.cdp.connected = True
    driver.cdp.ws_url = "ws://target-action-origin"

    before = driver.capture_platform_target_state("zhilian")
    result = driver.adopt_platform_target_transition(
        before,
        platform="zhilian",
        wait_seconds=0,
    )

    assert result["ok"] is True
    assert result["outcome"] == "current_target_reused"
    assert result["discarded_action_target_count"] == 1
    assert result["target_cleanup_verified"] is True
    assert closed == ["target-action-child"]
    assert driver.current_target_id == "target-action-origin"


def test_cdp_driver_discards_generic_search_child_when_navigation_never_finishes(
    monkeypatch,
):
    origin = {
        "id": "target-action-origin",
        "type": "page",
        "url": "https://www.zhaopin.com/zhengzhou/",
        "title": "郑州招聘网",
        "webSocketDebuggerUrl": "ws://target-action-origin",
    }
    child = {
        "id": "target-action-child",
        "type": "page",
        "url": "https://www.zhaopin.com/",
        "title": "智联招聘_求职_找工作",
        "webSocketDebuggerUrl": "ws://target-action-child",
    }
    target_sets = iter([[origin], [origin, child]])
    monotonic_values = iter([0.0, 3.0])
    closed: list[str] = []
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.list_targets",
        lambda _port: next(target_sets),
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.close_platform_target",
        lambda _port, target_id, **_kwargs: _record_closed_target(
            closed, target_id
        ),
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.time.monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.time.sleep",
        lambda _seconds: None,
    )

    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.manager = FakeManager()
    driver.platform = "zhilian"
    driver.current_platform = "zhilian"
    driver.current_target_id = "target-action-origin"
    driver.track_round = True
    driver.cdp = FakeCDP()
    driver.cdp.connected = True
    driver.cdp.ws_url = "ws://target-action-origin"

    before = driver.capture_platform_target_state("zhilian")
    result = driver.adopt_platform_target_transition(
        before,
        platform="zhilian",
        wait_seconds=0,
    )

    assert result == {
        "ok": False,
        "outcome": "action_target_navigation_not_observed",
        "new_target_count": 1,
        "previous_target_closed": False,
        "discarded_action_target_count": 1,
        "target_cleanup_verified": True,
    }
    assert closed == ["target-action-child"]


def test_cdp_driver_discards_unverified_action_child_without_closing_origin(
    monkeypatch,
):
    origin = {
        "id": "target-action-origin",
        "type": "page",
        "url": "https://www.zhaopin.com/",
        "title": "智联招聘_求职_找工作",
        "webSocketDebuggerUrl": "ws://target-action-origin",
    }
    child = {
        "id": "target-action-child",
        "type": "page",
        "url": "https://www.zhaopin.com/",
        "title": "智联招聘_求职_找工作",
        "webSocketDebuggerUrl": "ws://target-action-child",
    }
    target_sets = iter([[origin], [origin, child]])
    monotonic_values = iter([0.0, 3.0])
    adopted: list[str] = []
    closed: list[str] = []
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.list_targets",
        lambda _port: next(target_sets),
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.adopt_platform_tab_target",
        lambda **kwargs: adopted.append(kwargs["target"]["id"]) or kwargs["target"],
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.close_platform_target",
        lambda _port, target_id, **_kwargs: _record_closed_target(
            closed, target_id
        ),
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.time.monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.time.sleep",
        lambda _seconds: None,
    )

    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.manager = FakeManager()
    driver.platform = "zhilian"
    driver.current_platform = "zhilian"
    driver.current_target_id = "target-action-origin"
    driver.track_round = True
    driver.cdp = FakeCDP()
    driver.cdp.connected = True
    driver.cdp.ws_url = "ws://target-action-origin"
    driver.cdp.target_infos = [
        {
            "targetId": "target-action-child",
            "type": "page",
            "url": "https://www.zhaopin.com/",
            "title": "智联招聘_求职_找工作",
            "openerId": "target-action-origin",
        }
    ]

    before = driver.capture_platform_target_state("zhilian")
    result = driver.adopt_platform_target_transition(
        before,
        platform="zhilian",
        wait_seconds=0,
        allow_changed_platform_page=True,
        expected_title_text="郑州",
    )

    assert result == {
        "ok": False,
        "outcome": "action_target_navigation_not_observed",
        "new_target_count": 1,
        "previous_target_closed": False,
        "discarded_action_target_count": 1,
        "target_cleanup_verified": True,
    }
    assert adopted == []
    assert closed == ["target-action-child"]
    assert driver.current_target_id == "target-action-origin"
    assert driver.cdp.ws_urls == []


def test_cdp_driver_waits_for_action_linked_blank_target_to_become_official(
    monkeypatch,
):
    root = {
        "id": "target-root",
        "type": "page",
        "url": "https://www.zhaopin.com/",
        "title": "智联招聘_求职_找工作",
        "webSocketDebuggerUrl": "ws://target-root",
    }
    blank = {
        "id": "target-action-result",
        "type": "page",
        "url": "about:blank",
        "title": "",
        "webSocketDebuggerUrl": "ws://target-action-result",
    }
    result_target = {
        **blank,
        "url": "https://www.zhaopin.com/jobs?jl=901&kw=OPAQUE",
        "title": "郑州热门职位招聘",
    }
    target_sets = iter([[root], [root, blank], [root, result_target]])
    monotonic_values = iter([0.0, 1.0, 8.0])
    adopted: list[str] = []
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.list_targets",
        lambda _port: next(target_sets),
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.adopt_platform_tab_target",
        lambda **kwargs: adopted.append(kwargs["target"]["id"]) or kwargs["target"],
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.close_platform_target",
        lambda _port, target_id, **_kwargs: {
            "closed": True,
            "outcome": "target_closed",
            "target_id": target_id,
        },
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.time.monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.time.sleep",
        lambda _seconds: None,
    )

    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.manager = FakeManager()
    driver.platform = "zhilian"
    driver.current_platform = "zhilian"
    driver.current_target_id = "target-root"
    driver.track_round = True
    driver.cdp = FakeCDP()
    driver.cdp.connected = True
    driver.cdp.ws_url = "ws://target-root"
    driver.cdp.target_infos = [
        {
            "targetId": "target-action-result",
            "type": "page",
            "url": "about:blank",
            "title": "",
            "openerId": "target-root",
        }
    ]

    before = driver.capture_platform_target_state("zhilian")
    result = driver.adopt_platform_target_transition(
        before,
        platform="zhilian",
        wait_seconds=15,
        allow_changed_platform_page=True,
    )

    assert result["ok"] is True
    assert result["outcome"] == "action_linked_target_adopted"
    assert result["provisional_target_count"] == 1
    assert adopted == ["target-action-result"]


def test_cdp_driver_refuses_multiple_action_linked_targets(monkeypatch):
    targets = [
        {
            "id": target_id,
            "type": "page",
            "url": f"https://www.zhaopin.com/jobs?jl={code}&kw=OPAQUE",
            "title": f"{city}热门职位招聘",
            "webSocketDebuggerUrl": f"ws://{target_id}",
        }
        for target_id, code, city in (
            ("target-action-a", "901", "郑州"),
            ("target-action-b", "902", "杭州"),
        )
    ]
    monotonic_values = iter([0.0, 3.0])
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.list_targets",
        lambda _port: targets,
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.time.monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.time.sleep",
        lambda _seconds: None,
    )

    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.manager = FakeManager()
    driver.platform = "zhilian"
    driver.current_platform = "zhilian"
    driver.current_target_id = "target-root"
    driver.track_round = True
    driver.cdp = FakeCDP()
    driver.cdp.connected = True
    driver.cdp.ws_url = "ws://target-root"
    driver.cdp.target_infos = [
        {
            "targetId": target["id"],
            "type": "page",
            "url": target["url"],
            "title": target["title"],
            "openerId": "target-root",
        }
        for target in targets
    ]

    result = driver.adopt_platform_target_transition(
        {
            "platform": "zhilian",
            "target_ids": ["target-root"],
            "current_target_id": "target-root",
            "target_fingerprints": {"target-root": "root-before"},
        },
        platform="zhilian",
        wait_seconds=0,
        allow_changed_platform_page=True,
    )

    assert result == {
        "ok": False,
        "outcome": "ambiguous_search_targets",
        "new_target_count": 2,
        "previous_target_closed": False,
    }
    assert driver.current_target_id == "target-root"
    assert driver.cdp.ws_urls == []


def test_cdp_driver_refuses_ambiguous_changed_existing_zhilian_targets(monkeypatch):
    changed_targets = [
        {
            "id": target_id,
            "type": "page",
            "url": f"https://www.zhaopin.com/jobs?jl={code}&kw=OPAQUE",
            "title": f"{city}热门职位招聘",
            "webSocketDebuggerUrl": f"ws://{target_id}",
        }
        for target_id, code, city in (
            ("target-a", "901", "郑州"),
            ("target-b", "902", "杭州"),
        )
    ]
    monotonic_values = iter([0.0, 3.0])
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.list_targets",
        lambda _port: changed_targets,
    )
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.time.monotonic",
        lambda: next(monotonic_values),
    )

    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.manager = FakeManager()
    driver.platform = "zhilian"
    driver.current_platform = "zhilian"
    driver.current_target_id = "target-root"
    driver.track_round = True
    driver.cdp = FakeCDP()
    driver.cdp.connected = True

    result = driver.adopt_platform_target_transition(
        {
            "platform": "zhilian",
            "target_ids": ["target-root", "target-a", "target-b"],
            "current_target_id": "target-root",
            "target_fingerprints": {
                "target-root": "root-before",
                "target-a": "target-a-before",
                "target-b": "target-b-before",
            },
        },
        platform="zhilian",
        wait_seconds=0,
        allow_changed_platform_page=True,
    )

    assert result == {
        "ok": False,
        "outcome": "ambiguous_search_targets",
        "new_target_count": 2,
        "previous_target_closed": False,
    }
    assert driver.current_target_id == "target-root"
    assert driver.cdp.ws_urls == []


def test_cdp_driver_accepts_official_zhilian_jobs_route_with_trailing_slash():
    assert CDPBossDriver._trusted_platform_search_target(
        {
            "id": "target-jobs",
            "type": "page",
            "url": "https://www.zhaopin.com/jobs/",
            "webSocketDebuggerUrl": "ws://target-jobs",
        },
        "zhilian",
    ) is True


def test_cdp_driver_refuses_ambiguous_new_search_targets(monkeypatch):
    targets = [
        {
            "id": target_id,
            "type": "page",
            "url": f"https://www.zhaopin.com/sou/jl765/kw{suffix}",
            "webSocketDebuggerUrl": f"ws://{target_id}",
        }
        for target_id, suffix in (("target-a", "AAAA"), ("target-b", "BBBB"))
    ]
    monkeypatch.setattr(
        "jobagent.drivers.boss.cdp_driver.list_targets",
        lambda _port: targets,
    )

    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.manager = FakeManager()
    driver.platform = "zhilian"
    driver.current_platform = "zhilian"
    driver.current_target_id = "target-old"
    driver.track_round = True
    driver.cdp = FakeCDP()
    driver.cdp.connected = True

    result = driver.adopt_platform_target_transition(
        {
            "platform": "zhilian",
            "target_ids": ["target-old"],
            "current_target_id": "target-old",
        },
        platform="zhilian",
        wait_seconds=0,
    )

    assert result == {
        "ok": False,
        "outcome": "ambiguous_search_targets",
        "new_target_count": 2,
        "previous_target_closed": False,
    }
    assert driver.current_target_id == "target-old"
    assert driver.cdp.ws_urls == []
