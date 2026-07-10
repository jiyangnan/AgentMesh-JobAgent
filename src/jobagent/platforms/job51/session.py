"""51Job read-only session checks and login guide."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any

from jobagent.drivers.boss import create_driver

from .collect import build_job51_search_url
from .constants import JOB51_BROWSER_JS_USER_PROMPT, JOB51_LOGIN_USER_PROMPT, JOB51_SEARCH_URL


@dataclass(frozen=True)
class Job51SessionStatus:
    ok: bool
    logged_in: bool
    login_required: bool
    url: str = ""
    title: str = ""
    error: str = ""
    evidence: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if _browser_js_permission_required(self.error):
            payload["requires_user_action"] = True
            payload["user_action"] = "enable_chrome_javascript_automation"
            payload["user_prompt"] = JOB51_BROWSER_JS_USER_PROMPT
            payload["next_suggested"] = "jobagent 51job login --check"
        elif self.login_required:
            payload["requires_user_action"] = True
            payload["user_action"] = "login_51job"
            payload["user_prompt"] = JOB51_LOGIN_USER_PROMPT
            payload["next_suggested"] = "jobagent 51job login"
        return payload


class Job51SessionGuide:
    """Open 51Job and inspect login state without applying."""

    def __init__(self, driver: Any | None = None):
        self.driver = driver or create_driver(platform="51job")

    def check(self, query: str = "AI产品经理", city: str = "深圳", wait_seconds: int = 5) -> Job51SessionStatus:
        url = build_job51_search_url(query, city)
        open_result = self.driver.open_url_in_new_tab(url, wait_seconds=wait_seconds)
        if not open_result.get("ok"):
            return Job51SessionStatus(
                ok=False,
                logged_in=False,
                login_required=True,
                url=url,
                error=str(open_result.get("error", "open_url_failed")),
                evidence={"open_result": open_result},
            )
        return self.inspect_current_page()

    def open_login(self, wait_seconds: int = 3) -> Job51SessionStatus:
        open_result = self.driver.open_url_in_new_tab(JOB51_SEARCH_URL, wait_seconds=wait_seconds)
        if not open_result.get("ok"):
            return Job51SessionStatus(
                ok=False,
                logged_in=False,
                login_required=True,
                url=JOB51_SEARCH_URL,
                error=str(open_result.get("error", "open_url_failed")),
                evidence={"open_result": open_result},
            )
        return self.inspect_current_page()

    def wait_for_login(self, timeout: int = 300, poll_interval: int = 3, wait_seconds: int = 3) -> Job51SessionStatus:
        status = self.open_login(wait_seconds=wait_seconds)
        if status.logged_in:
            return status
        deadline = time.time() + timeout
        last = status
        while time.time() < deadline:
            time.sleep(max(1, poll_interval))
            last = self.inspect_current_page()
            if last.logged_in:
                return last
        return Job51SessionStatus(
            ok=False,
            logged_in=False,
            login_required=True,
            url=last.url,
            title=last.title,
            error="job51_login_timeout",
            evidence=last.evidence,
        )

    def inspect_current_page(self) -> Job51SessionStatus:
        js = """
        (function(){
          const href = location.href || '';
          const title = document.title || '';
          const bodyText = (document.body && (document.body.innerText || document.body.textContent) || '').trim();
          const snippet = bodyText.slice(0, 1000);
          const hasLoginEntry = /登录[/]注册/.test(snippet);
          const loginRequired = /passport|login/.test(href) || hasLoginEntry;
          return JSON.stringify({ok:true, url:href, title, loginRequired, bodySnippet:snippet});
        })()
        """
        result = self.driver._exec_js(js)
        data = _unwrap_js_result(result)
        if not data.get("ok"):
            error = str(data.get("error", "job51_session_inspect_failed"))
            return Job51SessionStatus(
                ok=False,
                logged_in=False,
                login_required=not _browser_js_permission_required(error),
                error=error,
                evidence=data,
            )
        login_required = bool(data.get("loginRequired"))
        return Job51SessionStatus(
            ok=True,
            logged_in=not login_required,
            login_required=login_required,
            url=str(data.get("url", "")),
            title=str(data.get("title", "")),
            evidence={"bodySnippet": data.get("bodySnippet", "")},
        )


def _unwrap_js_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict) and "raw" in result:
        try:
            parsed = json.loads(result["raw"])
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {"ok": False, "error": "job51_session_parse_failed"}
    return result if isinstance(result, dict) else {"ok": False, "error": "job51_session_empty_result"}


def _browser_js_permission_required(error: str) -> bool:
    return "JavaScript through AppleScript is turned off" in error or "Allow JavaScript from Apple Events" in error
