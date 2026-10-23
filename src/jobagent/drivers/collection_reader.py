"""The platform collectors' read-only interface to the dedicated Chrome.

Search-control interactions are used by existing list adapters. Recruiting
actions are intentionally absent; native BrowserWork owns those capabilities.
"""
from __future__ import annotations

import json
import time
from urllib.parse import parse_qsl, urlencode, urlsplit


class CollectionReader:
    bulk_readonly = True
    _READ_METHODS = frozenset({"open_url_in_new_tab", "snapshot_search_page",
        "check_login_status", "ensure_logged_in", "_exec_js", "_click_at",
        "capture_platform_target_state", "adopt_platform_target_transition",
        "dismiss_javascript_dialog"})

    def __init__(self, platform="boss", *, driver=None):
        from jobagent.drivers.boss.chrome_manager import ChromeInstanceManager
        from jobagent.infra.collection_guard import CollectionGuard
        manager = driver.manager if driver is not None else ChromeInstanceManager()
        self._boss_guard = CollectionGuard(manager.user_data_dir) if platform == "boss" else None
        if self._boss_guard is not None:
            self._boss_guard.assert_available()  # Before connecting, launching or selecting a tab.
        if driver is None:
            from jobagent.drivers.boss.cdp_driver import CDPBossDriver
            driver = CDPBossDriver(platform=platform, manager=manager)
        self._reader = driver
        self.manager = driver.manager
        self.platform = platform

    def __getattr__(self, name):
        if name in self._READ_METHODS:
            return getattr(self._reader, name)
        if name == "cdp":
            return _SearchControl(self._reader.cdp)
        raise AttributeError(f"CollectionReader does not expose {name}")

    def api_fetch(self, url, method="GET", body=None):
        parsed = urlsplit(url)
        if (method != "GET" or body is not None or parsed.scheme != "https"
                or parsed.netloc != "www.zhipin.com"
                or parsed.path != "/wapi/zpgeek/search/joblist.json"):
            raise ValueError("Only the authenticated read-only job-list request is permitted")
        from jobagent.drivers.boss.page_reader import request_json
        if self._boss_guard is None:
            raise ValueError("Boss reads require a Boss profile guard")
        return self._boss_guard.call("list", lambda: request_json(self._reader.cdp, url))

    def read_boss_detail(self, item):
        from jobagent.drivers.boss.page_reader import request_json
        from jobagent.platforms.discovery import CollectionError
        from jobagent.platforms.detail_reader import detail_url_allowed
        if self.platform != "boss" or not detail_url_allowed("boss", item.get("url", "")):
            raise ValueError("Only a signed Boss detail can be read")
        if not item.get("security_id"):
            raise CollectionError("boss_detail_context_missing", "The preserved candidate lacks detail request context",
                details={"retryable": False, "request_preserved": True})
        url = "https://www.zhipin.com/wapi/zpgeek/job/detail.json?" + urlencode({"securityId": item["security_id"]})
        result = self._boss_guard.call("detail", lambda: request_json(self._reader.cdp, url))
        info = (result.get("zpData") or {}).get("jobInfo") or {}
        if str(info.get("encryptId") or "") != item["id"]:
            raise CollectionError("detail_identity_mismatch", "Detail response does not match the signed job")
        jd = str(info.get("postDescription") or "").strip()
        if len(jd) > 20000:
            raise CollectionError("detail_content_too_large", "Full job description exceeds the current transport limit",
                details={"retryable": False, "request_preserved": True})
        return {"url": item["url"], "jd": jd}

    def read_detail_document(self, url):
        """Fetch one signed detail document without navigating or rendering it.

        The caller supplies a verified task URL. Same-origin credentials stay
        inside Chrome; no cookies are exported, redirects followed, scripts
        executed, or images/styles loaded from the returned document.
        """
        from jobagent.platforms.detail_reader import detail_url_allowed
        if not detail_url_allowed(self.platform, url):
            raise ValueError("Only an official job detail document can be read")
        script = """(async () => {
          const url = DETAIL_URL;
          if (location.origin !== new URL(url).origin)
            return {error:'detail_origin_unavailable'};
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), 8000);
          try {
            const response = await fetch(url, {method:'GET', credentials:'same-origin',
              redirect:'manual', signal:controller.signal});
            if ([401,403,429].includes(response.status))
              return {error:'detail_user_action_required'};
            if (response.type === 'opaqueredirect')
              return {url, html:'', unavailable:'redirect'};
            if ([404,410].includes(response.status)) return {url,html:'',unavailable:'removed'};
            if (!response.ok) return {error:'detail_transport_failed'};
            if (!response.headers.get('content-type')?.includes('text/html'))
              return {url,html:'',unavailable:'unsupported_content'};
            const stream = response.body.getReader(), decoder = new TextDecoder();
            let bytes = 0, html = '';
            while (true) {
              const part = await stream.read();
              if (part.done) break;
              bytes += part.value.byteLength;
              if (bytes > 1048576) { await stream.cancel(); return {url,html:'',unavailable:'size_limit'}; }
              html += decoder.decode(part.value, {stream:true});
            }
            html += decoder.decode();
            return {url:response.url, html, bytes};
          } catch (error) { return {error:'detail_transport_failed'}; }
          finally { clearTimeout(timer); }
        })()""".replace("DETAIL_URL", json.dumps(url))
        # CollectionReader construction already bound the dedicated tab. Do
        # not call a navigation helper here, even when this fetch fails.
        result = self._reader.cdp.evaluate(script, await_promise=True, timeout=12)
        value = result.get("result", {}).get("value") if isinstance(result, dict) else None
        if not isinstance(value, dict):
            return {"error": "detail_transport_failed"}
        return value

    def open_url_in_new_tab(self, url, wait_seconds=3):
        from jobagent.platforms.detail_reader import detail_url_allowed
        from jobagent.infra.platform_tabs import platform_for_url
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.port not in {None, 443}
                or parsed.username or parsed.password or platform_for_url(url) != self.platform):
            raise ValueError("Collection navigation must stay on the official platform")
        if not detail_url_allowed(self.platform, url):
            return self._reader.open_url_in_new_tab(url, wait_seconds=wait_seconds)
        # Detail collection waits for document readiness, not a recruiting
        # button. Expired jobs may have readable details but no chat/apply UI.
        self._reader._ensure_connected_for_url(url)
        self._reader.cdp.send("Page.navigate", {"url": url})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            observed = self._reader._exec_js("JSON.stringify({url:location.href,ready:document.readyState})")
            if isinstance(observed, dict) and observed.get("ready") == "complete":
                actual = urlsplit(str(observed.get("url") or ""))
                if ((actual.hostname, actual.path) == (parsed.hostname, parsed.path)
                        and (self.platform != "51job" or dict(parse_qsl(actual.query)).get("jobId")
                             == dict(parse_qsl(parsed.query)).get("jobId"))):
                    return {"ok": True}
                if any(marker in actual.path.lower() for marker in ("/user", "/login", "/verify", "/captcha")):
                    return {"ok": False, "error": "detail_user_action_required"}
            time.sleep(0.25)
        return {"ok": False, "error": "detail_navigation_timeout"}

    def disconnect(self):
        # Disconnecting the socket deliberately leaves the logged-in Chrome open.
        self._reader.cdp.disconnect()


class _SearchControl:
    def __init__(self, client):
        self._client = client

    def send(self, method, *args, **kwargs):
        if method not in {"Input.dispatchKeyEvent", "Input.dispatchMouseEvent"}:
            raise ValueError("Only list search controls are available through this interface")
        return self._client.send(method, *args, **kwargs)
