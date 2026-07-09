"""CDP target registry for one Chrome with one tab per recruiting platform."""

from __future__ import annotations

import http.client
import json
from typing import Any
from urllib.parse import quote, urlparse

from jobagent.infra.rounds import ensure_current_round, mark_browser_session, utc_now
from jobagent.infra.state import load_json, platform_tabs_path, save_json

PLATFORM_TAB_DEFAULTS: dict[str, dict[str, Any]] = {
    "boss": {
        "domains": ("zhipin.com",),
        "initial_url": "https://www.zhipin.com/",
    },
    "liepin": {
        "domains": ("liepin.com",),
        "initial_url": "https://www.liepin.com/",
    },
    "zhilian": {
        "domains": ("zhaopin.com", "sou.zhaopin.com", "passport.zhaopin.com"),
        "initial_url": "https://sou.zhaopin.com/",
    },
}


def platform_for_url(url: str) -> str | None:
    host = urlparse(url).hostname or ""
    for platform, cfg in PLATFORM_TAB_DEFAULTS.items():
        if any(host == domain or host.endswith(f".{domain}") for domain in cfg["domains"]):
            return platform
    return None


def default_url_for_platform(platform: str) -> str:
    return str(PLATFORM_TAB_DEFAULTS.get(platform, PLATFORM_TAB_DEFAULTS["boss"])["initial_url"])


def _request_json(port: int, path: str, *, method: str = "GET") -> Any:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        if resp.status >= 400:
            raise RuntimeError(f"CDP HTTP {resp.status} on {path}: {body[:200]}")
        return json.loads(body) if body else {}
    finally:
        conn.close()


def list_targets(port: int) -> list[dict[str, Any]]:
    data = _request_json(port, "/json")
    return data if isinstance(data, list) else []


def _create_target(port: int, url: str) -> dict[str, Any]:
    path = "/json/new?" + quote(url, safe=":/?&=%#")
    try:
        return _request_json(port, path, method="PUT")
    except Exception:
        return _request_json(port, path, method="GET")


def _activate_target(port: int, target_id: str) -> None:
    try:
        _request_json(port, f"/json/activate/{target_id}")
    except Exception:
        pass


def _target_id(target: dict[str, Any]) -> str:
    return str(target.get("id") or target.get("targetId") or "")


def _target_matches_platform(target: dict[str, Any], platform: str) -> bool:
    url = str(target.get("url") or "")
    return platform_for_url(url) == platform


def _find_target_by_id(targets: list[dict[str, Any]], target_id: str) -> dict[str, Any] | None:
    if not target_id:
        return None
    for target in targets:
        if _target_id(target) == target_id and target.get("type") == "page":
            return target
    return None


def _find_target_by_platform(targets: list[dict[str, Any]], platform: str) -> dict[str, Any] | None:
    for target in targets:
        if target.get("type") == "page" and _target_matches_platform(target, platform):
            return target
    return None


def _load_registry() -> dict[str, Any]:
    return load_json(platform_tabs_path()) or {"tabs": {}}


def _save_registry(registry: dict[str, Any]) -> None:
    save_json(platform_tabs_path(), registry)


def ensure_platform_tab(
    *,
    platform: str,
    port: int,
    initial_url: str | None = None,
) -> dict[str, Any]:
    """Return a CDP page target for the requested platform and persist it."""
    platform = platform if platform in PLATFORM_TAB_DEFAULTS else "boss"
    round_state = ensure_current_round()
    mark_browser_session(f"local-cdp-{port}")

    registry = _load_registry()
    tabs = registry.setdefault("tabs", {})
    item = tabs.get(platform, {})
    targets = list_targets(port)

    target = _find_target_by_id(targets, str(item.get("target_id") or ""))
    if target and not _target_matches_platform(target, platform):
        target = None
    if target is None:
        target = _find_target_by_platform(targets, platform)
    if target is None:
        target = _create_target(port, initial_url or default_url_for_platform(platform))

    target_id = _target_id(target)
    if target_id:
        _activate_target(port, target_id)

    tabs[platform] = {
        "target_id": target_id,
        "url": target.get("url", ""),
        "title": target.get("title", ""),
        "webSocketDebuggerUrl": target.get("webSocketDebuggerUrl", ""),
        "last_seen_at": utc_now(),
    }
    registry["round_id"] = round_state["round_id"]
    registry["updated_at"] = utc_now()
    _save_registry(registry)

    if not target.get("webSocketDebuggerUrl"):
        raise RuntimeError(f"CDP target for platform `{platform}` has no WebSocket URL")
    return target
