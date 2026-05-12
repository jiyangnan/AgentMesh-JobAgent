"""CDP Boss Driver — cross-platform browser control via Chrome DevTools Protocol.

Replaces AppleScript on Windows/Linux and serves as a fallback on macOS.
All DOM operations are performed via CDP Runtime.evaluate inside a real Chrome instance.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .base import BossActionDriver
from .chrome_manager import ChromeInstanceManager
from .cdp_client import CDPClient


class CDPBossDriver(BossActionDriver):
    """Boss driver implementation using Chrome CDP.

    Launches a dedicated Chrome instance (visible window, independent profile)
    and controls it via WebSocket CDP commands.
    """

    def __init__(self, manager: ChromeInstanceManager | None = None):
        self.manager = manager or ChromeInstanceManager()
        self.cdp = CDPClient()
        self._ensure_connected()

    def _ensure_connected(self) -> None:
        """Ensure Chrome is running and CDP WebSocket is connected."""
        if self.cdp.connected:
            return
        ws_url = self.manager.ensure_running()
        self.cdp.connect(ws_url)

    # ── BossActionDriver interface ──────────────────────────

    def chrome_running(self) -> bool:
        return self.manager.is_running()

    def applescript_js_enabled(self) -> tuple[bool, str]:
        # CDP does not use AppleScript; always report ready.
        return True, "cdp"

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5) -> dict[str, Any]:
        """Navigate the current CDP page to the given URL.

        Checks for risk-control redirects (verify / code=36) after navigation.
        """
        self._ensure_connected()
        try:
            self.cdp.send("Page.navigate", {"url": url})
            time.sleep(wait_seconds)
            result = self.cdp.evaluate(
                "JSON.stringify({url: location.href, title: document.title})"
            )
            info = json.loads(result.get("result", {}).get("value", "{}"))
            current_url = info.get("url", "")
            # Risk-control detection: BOSS redirects to verify page when flagged
            if "verify" in current_url or "code=36" in current_url:
                return {
                    "ok": False,
                    "error": "risk_control",
                    "url": current_url,
                    "title": info.get("title", ""),
                }
            return {"ok": True, "url": current_url, "title": info.get("title", "")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def inspect_page(self) -> dict[str, Any]:
        """Inspect the current page for login state and UI elements."""
        self._ensure_connected()
        js = r"""
        (function(){
          const txt = document.body ? (document.body.innerText || '') : '';
          const title = document.title || '';
          const href = location.href || '';
          const loginDialog = !!document.querySelector('.sign-content, .login-dialog, .passport-login-container, .dialog-wrap .sign-form');
          const qrLoginDialog = !![...document.querySelectorAll('div,span,p')].find(x => /扫码登录|请在App端确认登录|发送验证码/.test((x.innerText||x.textContent||'').trim()));
          const userNav = !!document.querySelector('.user-nav');
          const geekNav = !![...document.querySelectorAll('a,span,div')].find(x => ['消息','简历','职位'].includes((x.innerText||x.textContent||'').trim()));
          const resumeActions = !![...document.querySelectorAll('a,button,div')].find(x => /完善在线简历|新增附件简历/.test((x.innerText||x.textContent||'').trim()));
          const hasChatEntry = [...document.querySelectorAll('a,button,div')].some(x=>{const s=(x.innerText||x.textContent||'').trim(); return s==='立即沟通' || s==='继续沟通'});
          return JSON.stringify({
            ok:true,
            href,
            title,
            loginDialog,
            qrLoginDialog,
            userNav,
            geekNav,
            resumeActions,
            hasChatEntry,
            textSnippet: txt.slice(0,500)
          });
        })()
        """
        try:
            result = self.cdp.evaluate(js)
            raw = result.get("result", {}).get("value", "{}")
            return json.loads(raw)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def click_chat_entry(self) -> dict[str, Any]:
        """Click the '立即沟通' button and handle the popup dialog.

        After clicking 立即沟通, a popup "已向BOSS发送消息" may appear.
        We must click "继续沟通" in that popup to open the chat sidebar.
        If no popup appears, the greeting may have been auto-sent.

        Matches boss-radar's verified 6-step flow (2026-05-07).
        """
        self._ensure_connected()
        # Step 1: click 立即沟通
        click_js = """
        (function(){
          var all = document.querySelectorAll('*');
          for (var i = all.length - 1; i >= 0; i--) {
            var text = (all[i].innerText || all[i].textContent || '').trim();
            if (text === '立即沟通') {
              all[i].dispatchEvent(new MouseEvent('click', {
                bubbles: true, cancelable: true, view: window
              }));
              return JSON.stringify({ok: true, step: 'clicked_立即沟通'});
            }
          }
          // Fallback: href matching
          var links = document.querySelectorAll('a[href*="opchat"], a[href*="chat"]');
          if (links.length > 0) {
            links[0].dispatchEvent(new MouseEvent('click', {
              bubbles: true, cancelable: true, view: window
            }));
            return JSON.stringify({ok: true, step: 'clicked_chat_fallback'});
          }
          return JSON.stringify({ok: false, step: 'no_chat_entry'});
        })()
        """
        try:
            result = self.cdp.evaluate(click_js)
            click_data = json.loads(result.get("result", {}).get("value", "{}"))
            if not click_data.get("ok"):
                return click_data

            # Step 2: wait for popup and click 继续沟通 (up to 5 retries, 1s each)
            for attempt in range(5):
                time.sleep(1)
                popup_js = """
                (function(){
                  var all = document.querySelectorAll('*');
                  for (var i = all.length - 1; i >= 0; i--) {
                    var text = (all[i].innerText || all[i].textContent || '').trim();
                    if (text === '继续沟通') {
                      all[i].dispatchEvent(new MouseEvent('click', {
                        bubbles: true, cancelable: true, view: window
                      }));
                      return JSON.stringify({
                        ok: true, step: 'clicked_继续沟通', autoSent: false
                      });
                    }
                  }
                  // Chat sidebar already open?
                  var editor = document.querySelector('.chat-input, [contenteditable="true"]');
                  if (editor) {
                    return JSON.stringify({
                      ok: true, step: 'chat_opened', autoSent: false
                    });
                  }
                  // Risk-control redirect?
                  var href = location.href || '';
                  if (href.indexOf('verify') >= 0 || href.indexOf('code=36') >= 0) {
                    return JSON.stringify({
                      ok: false, step: 'risk_control', url: href
                    });
                  }
                  return JSON.stringify({ok: false, step: 'no_popup_yet'});
                })()
                """
                popup_result = self.cdp.evaluate(popup_js)
                popup_data = json.loads(popup_result.get("result", {}).get("value", "{}"))
                if popup_data.get("ok"):
                    click_data.update(popup_data)
                    return click_data
                if popup_data.get("step") == "risk_control":
                    return popup_data

            # No popup found after 5s — likely auto-sent
            click_data["autoSent"] = True
            click_data["step"] = "auto_sent_no_popup"
            return click_data
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def inspect_chat_editor(self) -> dict[str, Any]:
        """Inspect the chat editor and send button.

        Waits for the sidebar chat panel to load. If no editor is found,
        returns autoSent=True (the greeting may have been sent automatically).
        """
        self._ensure_connected()
        # Poll for editor appearance (up to 15 attempts, ~0.8s each = ~12s)
        for _attempt in range(15):
            js = """
            (function(){
              // Prefer class containing 'chat-input', fallback to contenteditable
              var editor = null;
              var all = document.querySelectorAll('*');
              for (var i = 0; i < all.length; i++) {
                if (all[i].className && typeof all[i].className === 'string'
                    && all[i].className.indexOf('chat-input') >= 0) {
                  editor = all[i]; break;
                }
              }
              if (!editor) {
                var editables = document.querySelectorAll('[contenteditable="true"]');
                if (editables.length > 0) editor = editables[0];
              }
              var send = null;
              var btns = document.querySelectorAll('button');
              for (var j = 0; j < btns.length; j++) {
                var t = (btns[j].innerText || '').trim();
                if (t === '\u53d1\u9001') { send = btns[j]; break; }
              }
              if (!send) send = document.querySelector('.btn-send');
              var loginDialog = !!document.querySelector('.sign-content, .login-dialog, .passport-login-container');
              var href = location.href || '';
              var riskControl = href.indexOf('verify') >= 0 || href.indexOf('code=36') >= 0;
              return JSON.stringify({
                ok: true,
                editorFound: !!editor,
                editorTag: editor ? editor.tagName : '',
                editorClass: editor ? (editor.className || '') : '',
                sendFound: !!send,
                sendClass: send ? (send.className || '') : '',
                loginDialog: loginDialog,
                riskControl: riskControl,
                href: href
              });
            })()
            """
            try:
                result = self.cdp.evaluate(js)
                data = json.loads(result.get("result", {}).get("value", "{}"))
                if data.get("riskControl"):
                    return {"ok": False, "error": "risk_control", "url": data.get("href", "")}
                if data.get("loginDialog"):
                    return data
                if data.get("editorFound"):
                    return data
            except Exception:
                pass
            time.sleep(0.8)
        # Editor not found after waiting — greeting may have been auto-sent
        return {"ok": True, "autoSent": True, "editorFound": False,
                "step": "auto_sent_no_editor"}

    def fill_chat_message(self, message: str) -> dict[str, Any]:
        """Fill the chat input with the given message.

        Uses document.execCommand('insertText') to trigger React/Vue onChange
        (inspired by boss-radar's approach).
        """
        self._ensure_connected()
        # Escape for JSON embedding
        msg_json = json.dumps(message)
        js = f"""
        (function(){{
          var text = {msg_json};
          var editor = null;
          var all = document.querySelectorAll('*');
          for (var i = 0; i < all.length; i++) {{
            if (all[i].className && typeof all[i].className === 'string' && all[i].className.indexOf('chat-input') >= 0) {{
              editor = all[i];
              break;
            }}
          }}
          if (!editor) {{
            var editables = document.querySelectorAll('[contenteditable="true"]');
            if (editables.length > 0) editor = editables[0];
          }}
          if (!editor) return JSON.stringify({{ok: false, error: 'no_editor'}});

          editor.focus();
          editor.textContent = '';
          document.execCommand('insertText', false, text);

          // Also dispatch InputEvent for frameworks that listen to it
          editor.dispatchEvent(new InputEvent('input', {{bubbles: true}}));

          return JSON.stringify({{ok: true, step: 'filled', len: editor.textContent.length}});
        }})()
        """
        try:
            result = self.cdp.evaluate(js)
            return json.loads(result.get("result", {}).get("value", "{}"))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def click_send(self) -> dict[str, Any]:
        """Click the send button."""
        self._ensure_connected()
        js = """
        (function(){
          var sendBtn = null;
          var btns = document.querySelectorAll('button');
          for (var j = 0; j < btns.length; j++) {
            var t = (btns[j].innerText || '').trim();
            if (t === '\u53d1\u9001') { sendBtn = btns[j]; break; }
          }
          if (!sendBtn) sendBtn = document.querySelector('.btn-send');
          if (!sendBtn) return JSON.stringify({ok: false, error: 'no_send'});

          sendBtn.disabled = false;
          sendBtn.removeAttribute('disabled');
          sendBtn.click();
          return JSON.stringify({ok: true, step: 'clicked_send'});
        })()
        """
        try:
            result = self.cdp.evaluate(js)
            return json.loads(result.get("result", {}).get("value", "{}"))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def verify_delivery(self, message: str) -> dict[str, Any]:
        """Verify that the message was delivered."""
        self._ensure_connected()
        msg_preview = message[:20]
        js = f"""
        (function(){{
          var txt = document.body ? document.body.innerText : '';
          var hasDelivered = txt.includes('[送达]') || txt.includes('已送达');
          var hasMsg = txt.includes({json.dumps(msg_preview)});
          return JSON.stringify({{ok: true, delivered: hasDelivered && hasMsg, hasDelivered, hasMsg}});
        }})()
        """
        try:
            result = self.cdp.evaluate(js)
            return json.loads(result.get("result", {}).get("value", "{}"))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Extra helpers ───────────────────────────────────────

    def navigate(self, url: str) -> None:
        """CDP-native page navigation."""
        self._ensure_connected()
        self.cdp.send("Page.navigate", {"url": url})

    def api_fetch(self, path: str, method: str = "GET", body: str | None = None) -> Any:
        """Execute a fetch() inside Chrome and return JSON.

        This is the CDP equivalent of AppleScript XHR — all requests
        carry the real Chrome TLS fingerprint and cookies automatically.
        """
        self._ensure_connected()
        url = path if path.startswith("http") else f"https://www.zhipin.com{path}"
        fetch_opts: dict[str, Any] = {"credentials": "include"}
        if method != "GET":
            fetch_opts["method"] = method
            if body:
                fetch_opts["body"] = body

        url_json = json.dumps(url)
        opts_json = json.dumps(fetch_opts)
        expression = (
            f"(async()=>{{try{{const r=await fetch({url_json},{opts_json});"
            f"if(!r.ok)throw new Error('HTTP '+r.status);"
            f"const t=await r.text();return JSON.parse(t)}}"
            f"catch(e){{return{{__error:e.message}}}}}})()"
        )
        result = self.cdp.evaluate(expression, await_promise=True)
        value = result.get("result", {}).get("value")
        if not value:
            raise RuntimeError(f"API 请求无返回: {path}")
        if isinstance(value, dict) and value.get("__error"):
            raise RuntimeError(f"API 请求失败: {value['__error']} ({path})")
        return value

    # ── Passive login guide ─────────────────────────────────

    def check_login_status(self) -> bool:
        """Check if user is logged in to Boss直聘 via CDP.

        Queries the user info API inside Chrome. code === 0 means logged in.
        """
        try:
            result = self.api_fetch("/wapi/zpuser/wap/getUserInfo.json")
            return isinstance(result, dict) and result.get("code") == 0
        except Exception:
            return False

    def ensure_logged_in(
        self,
        timeout: int = 300,
        poll_interval: int = 3,
        on_waiting: Any = None,
    ) -> bool:
        """Passive login guide — auto-open Chrome → login page → poll → continue.

        Designed for agent-driven workflows: no terminal UI, silent polling.
        The caller (agent) is responsible for notifying the human user.

        Args:
            timeout: Max seconds to wait for login.
            poll_interval: Seconds between login checks.
            on_waiting: Optional callback called once when entering wait state.
                        Signature: on_waiting(chrome_visible: bool) -> None

        Returns:
            True if login succeeded (or was already active).
            False if timeout exceeded.
        """
        if self.check_login_status():
            return True

        self._ensure_connected()

        # Navigate to BOSS login page
        self.cdp.send("Page.navigate", {
            "url": "https://www.zhipin.com/web/user/?ka=header-login"
        })

        # Notify the caller (agent) that Chrome is open and waiting
        if callable(on_waiting):
            on_waiting(True)

        start = time.time()
        while time.time() - start < timeout:
            if self.check_login_status():
                # Navigate to home page for subsequent operations
                try:
                    self.cdp.send("Page.navigate", {"url": "https://www.zhipin.com/"})
                except Exception:
                    pass
                return True
            time.sleep(poll_interval)

        return False
