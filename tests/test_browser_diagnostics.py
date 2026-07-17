from __future__ import annotations

import json

from jobagent.infra.browser_diagnostics import _inspection_script, diagnose_browser


def test_inspection_script_preserves_javascript_newline_escapes():
    script = _inspection_script("boss")

    assert "title + '\\n' + text" in script


class _FakeClient:
    def connect(self, _url, timeout=5):
        self.connected = timeout

    def evaluate(self, _script, timeout=5):
        return {
            "result": {
                "value": json.dumps(
                    {
                        "url": "https://www.zhipin.com/web/geek/job",
                        "title": "Boss直聘",
                        "readyState": "complete",
                        "loginUrl": False,
                        "loginUi": False,
                        "authUi": True,
                        "resourceCount": 42,
                        "slowResourceCount": 2,
                        "navigationTimingMs": 1200,
                    }
                )
            }
        }

    def disconnect(self):
        self.connected = False


def test_browser_diagnose_is_read_only_and_reports_existing_authenticated_tab(monkeypatch):
    monkeypatch.setattr(
        "jobagent.infra.browser_diagnostics.find_chrome",
        lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )
    monkeypatch.setattr(
        "jobagent.infra.browser_diagnostics.list_targets",
        lambda _port: [
            {
                "id": "boss-1",
                "type": "page",
                "url": "https://www.zhipin.com/web/geek/job",
                "title": "Boss直聘",
                "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/boss-1",
            }
        ],
    )
    monkeypatch.setattr("jobagent.infra.browser_diagnostics.CDPClient", _FakeClient)

    result = diagnose_browser("boss")

    assert result["ok"] is True
    assert result["read_only"] is True
    assert result["browser_launched"] is False
    assert result["navigation_performed"] is False
    assert result["login"]["state"] == "authenticated"
    assert result["ready_for_platform_work"] is True
    assert result["page"]["slow_resource_count"] == 2


def test_browser_diagnose_reports_conflicting_login_evidence_without_claiming_logout(
    monkeypatch,
):
    class ConflictingClient(_FakeClient):
        def evaluate(self, _script, timeout=5):
            return {
                "result": {
                    "value": json.dumps(
                        {
                            "url": "https://www.zhipin.com/web/geek/job",
                            "title": "Boss直聘",
                            "readyState": "complete",
                            "loginUrl": False,
                            "loginUi": True,
                            "authUi": True,
                        }
                    )
                }
            }

    monkeypatch.setattr("jobagent.infra.browser_diagnostics.find_chrome", lambda: "/chrome")
    monkeypatch.setattr(
        "jobagent.infra.browser_diagnostics.list_targets",
        lambda _port: [
            {
                "id": "boss-1",
                "type": "page",
                "url": "https://www.zhipin.com/web/geek/job",
                "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/boss-1",
            }
        ],
    )
    monkeypatch.setattr("jobagent.infra.browser_diagnostics.CDPClient", ConflictingClient)

    result = diagnose_browser("boss")

    assert result["login"]["state"] == "conflicting"
    assert result["ready_for_platform_work"] is False


def test_browser_diagnose_reports_missing_platform_tab_without_creating_one(monkeypatch):
    monkeypatch.setattr("jobagent.infra.browser_diagnostics.find_chrome", lambda: "/chrome")
    monkeypatch.setattr(
        "jobagent.infra.browser_diagnostics.list_targets",
        lambda _port: [{"id": "blank", "type": "page", "url": "about:blank"}],
    )

    result = diagnose_browser("liepin")

    assert result["ok"] is False
    assert result["status"] == "platform_tab_missing"
    assert result["browser_launched"] is False
    assert result["next_suggested"] == "jobagent liepin login --check"
