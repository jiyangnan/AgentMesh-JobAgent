"""Read-only diagnostics for the dedicated Job Agent Chrome session."""

from __future__ import annotations

import json
import time
from typing import Any

from jobagent.drivers.boss.cdp_client import CDPClient
from jobagent.drivers.boss.chrome_manager import ChromeInstanceManager, find_chrome
from jobagent.infra.platform_tabs import list_targets, platform_for_url


def _inspection_script(platform: str) -> str:
    safe_platform = json.dumps(platform)
    return f"""
    (function(){{
      const platform = {safe_platform};
      const text = (document.body && (document.body.innerText || document.body.textContent) || '').slice(0, 3000);
      const href = location.href || '';
      const title = document.title || '';
      const authTerms = {{
        boss: ['消息', '简历', '职位'],
        liepin: ['我的简历', '我的沟通', '职位'],
        zhilian: ['求职管理', '我的简历', '职位'],
        '51job': ['我的简历', '个人中心', '职位']
      }}[platform] || [];
      const loginUrl = /passport|[/]login/.test(href);
      const loginUi = /登录[/]注册|扫码登录|验证码登录|请登录/.test(title + '\\n' + text.slice(0, 800));
      const authUi = authTerms.filter((term) => text.includes(term)).length >= 2;
      const resources = performance.getEntriesByType('resource') || [];
      return JSON.stringify({{
        url: href,
        title,
        readyState: document.readyState || '',
        loginUrl,
        loginUi,
        authUi,
        resourceCount: resources.length,
        slowResourceCount: resources.filter((item) => Number(item.duration || 0) >= 5000).length,
        navigationTimingMs: Math.round((performance.getEntriesByType('navigation')[0] || {{}}).duration || 0)
      }});
    }})()
    """


def diagnose_browser(platform: str, *, port: int = ChromeInstanceManager.DEFAULT_PORT) -> dict[str, Any]:
    """Inspect an existing platform tab without launching Chrome or navigating."""
    started = time.monotonic()
    manager = ChromeInstanceManager(port=port)
    chrome_path = find_chrome()
    base: dict[str, Any] = {
        "platform": platform,
        "read_only": True,
        "browser_launched": False,
        "navigation_performed": False,
        "chrome_available": bool(chrome_path),
        "profile_exists": manager.user_data_dir.exists(),
        "cdp_port": port,
    }
    try:
        targets = list_targets(port)
    except Exception as exc:
        return {
            "ok": False,
            **base,
            "status": "cdp_unreachable",
            "cdp_reachable": False,
            "error": type(exc).__name__,
            "probe_elapsed_ms": round((time.monotonic() - started) * 1000),
            "next_suggested": f"jobagent {platform} login --check",
        }
    pages = [target for target in targets if target.get("type") == "page"]
    matching = [
        target
        for target in pages
        if platform_for_url(str(target.get("url") or "")) == platform
    ]
    if not matching:
        return {
            "ok": False,
            **base,
            "status": "platform_tab_missing",
            "cdp_reachable": True,
            "page_target_count": len(pages),
            "matching_target_count": 0,
            "probe_elapsed_ms": round((time.monotonic() - started) * 1000),
            "next_suggested": f"jobagent {platform} login --check",
        }
    target = matching[0]
    websocket_url = str(target.get("webSocketDebuggerUrl") or "")
    if not websocket_url:
        return {
            "ok": False,
            **base,
            "status": "platform_tab_not_debuggable",
            "cdp_reachable": True,
            "page_target_count": len(pages),
            "matching_target_count": len(matching),
            "next_suggested": f"jobagent {platform} login --check",
        }
    client = CDPClient()
    try:
        client.connect(websocket_url, timeout=5)
        result = client.evaluate(_inspection_script(platform), timeout=5)
        raw = result.get("result", {}).get("value", "{}")
        page = json.loads(raw) if isinstance(raw, str) else raw
        page = page if isinstance(page, dict) else {}
    except Exception as exc:
        return {
            "ok": False,
            **base,
            "status": "page_probe_failed",
            "cdp_reachable": True,
            "error": type(exc).__name__,
            "page_target_count": len(pages),
            "matching_target_count": len(matching),
            "probe_elapsed_ms": round((time.monotonic() - started) * 1000),
            "next_suggested": f"jobagent {platform} login --check",
        }
    finally:
        client.disconnect()
    page_url = str(page.get("url") or target.get("url") or "")
    correct_platform = platform_for_url(page_url) == platform
    page_ready = bool(
        correct_platform
        and page.get("readyState") in {"interactive", "complete"}
        and page_url != "about:blank"
    )
    if page.get("loginUrl"):
        login_state = "login_required"
    elif page.get("loginUi") and page.get("authUi"):
        login_state = "conflicting"
    elif page.get("authUi"):
        login_state = "authenticated"
    elif page.get("loginUi"):
        login_state = "login_required"
    else:
        login_state = "unknown"
    ready_for_platform_work = bool(page_ready and login_state == "authenticated")
    return {
        "ok": page_ready,
        **base,
        "status": "ready" if ready_for_platform_work else "page_observed",
        "cdp_reachable": True,
        "page_target_count": len(pages),
        "matching_target_count": len(matching),
        "page": {
            "url": page_url,
            "title": str(page.get("title") or target.get("title") or ""),
            "ready_state": page.get("readyState"),
            "correct_platform": correct_platform,
            "resource_count": int(page.get("resourceCount") or 0),
            "slow_resource_count": int(page.get("slowResourceCount") or 0),
            "navigation_timing_ms": int(page.get("navigationTimingMs") or 0),
        },
        "login": {
            "state": login_state,
            "login_url_detected": bool(page.get("loginUrl")),
            "login_ui_detected": bool(page.get("loginUi")),
            "authenticated_ui_detected": bool(page.get("authUi")),
        },
        "ready_for_platform_work": ready_for_platform_work,
        "probe_elapsed_ms": round((time.monotonic() - started) * 1000),
        "next_suggested": None if ready_for_platform_work else f"jobagent {platform} login --check",
    }
