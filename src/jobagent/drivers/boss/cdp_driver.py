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

    def _exec_js(self, js_code: str, timeout: int = 30) -> dict[str, Any]:
        """AppleScript-driver compatible JavaScript evaluator.

        BossDataDriver historically called the AppleScript driver's private
        _exec_js helper. CDP is now the preferred driver, so keep that call path
        working while returning the same shapes: parsed JSON dicts when possible,
        otherwise {"ok": True, "raw": "..."}.
        """
        self._ensure_connected()
        try:
            result = self.cdp.evaluate(js_code, timeout=timeout)
            value = result.get("result", {}).get("value")
        except Exception as e:
            return {"ok": False, "error": str(e)}

        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
                return {"ok": True, "raw": value}
            except Exception:
                return {"ok": True, "raw": value}
        return {"ok": True, "raw": "" if value is None else str(value)}

    def _unwrap(self, result: dict[str, Any]) -> dict[str, Any]:
        """Unwrap _exec_js output into a JSON dict."""
        if "raw" not in result:
            return result
        try:
            parsed = json.loads(result["raw"])
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    # ── BossActionDriver interface ──────────────────────────

    def _cdp_click_at(self, x: float, y: float, retries: int = 3) -> None:
        """Send a real hardware-level mouse click at viewport coords (x, y).

        Uses CDP Input.dispatchMouseEvent which produces isTrusted=true events.
        Boss's anti-automation silently drops synthetic dispatchEvent(MouseEvent)
        calls, so this is required for any click that triggers real platform
        behavior (立即沟通, 继续沟通, etc.).

        Retries on CDP timeout — under rapid sequential operations Chrome's CDP
        channel can briefly stall (response arrives after the 30s timeout),
        even though the underlying browser is healthy. A short backoff + retry
        resolves this without manual intervention.
        """
        self._ensure_connected()
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                self.cdp.send("Input.dispatchMouseEvent", {
                    "type": "mouseMoved", "x": x, "y": y, "button": "none",
                })
                self.cdp.send("Input.dispatchMouseEvent", {
                    "type": "mousePressed", "x": x, "y": y,
                    "button": "left", "clickCount": 1,
                })
                self.cdp.send("Input.dispatchMouseEvent", {
                    "type": "mouseReleased", "x": x, "y": y,
                    "button": "left", "clickCount": 1,
                })
                return  # success
            except Exception as e:
                last_err = e
                # Brief backoff before retry; CDP stalls are usually transient
                time.sleep(2.0 * (attempt + 1))
        # All retries exhausted — re-raise so caller can record the failure
        if last_err:
            raise last_err

    def _find_clickable_by_text(self, texts: list[str]) -> dict[str, Any]:
        """Find the most specific visible element whose trimmed text matches one
        of `texts`, and return its viewport center coordinates.

        Boss renders the "立即沟通" button as nested elements (a.btn-startchat
        wrapping a span). To avoid clicking the wrapper and the inner span at
        the same position, we pick the smallest visible element by area.

        Returns: {"ok": true, "x":, "y":, "text":, "tag":, "cls":} or
                 {"ok": false, "error": "not_found"}.
        """
        js = """
        (function(texts){
          var matches = [];
          var all = document.querySelectorAll('a,button,div,span');
          for (var i = 0; i < all.length; i++) {
            var el = all[i];
            var t = (el.innerText || el.textContent || '').trim();
            if (texts.indexOf(t) < 0) continue;
            // Must be visible & have meaningful size
            var rect = el.getBoundingClientRect();
            if (rect.width < 20 || rect.height < 10) continue;
            var cs = getComputedStyle(el);
            if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity || '1') === 0) continue;
            matches.push({
              text: t, tag: el.tagName,
              cls: (el.className || '').toString().slice(0, 100),
              x: rect.left + rect.width/2,
              y: rect.top + rect.height/2,
              area: rect.width * rect.height
            });
          }
          if (!matches.length) return JSON.stringify({ok: false, error: 'not_found'});
          // Pick the smallest-area match — usually the innermost clickable element
          matches.sort(function(a, b){ return a.area - b.area; });
          return JSON.stringify({ok: true, pick: matches[0], all: matches.length});
        })(%s)
        """ % json.dumps(texts)
        try:
            result = self.cdp.evaluate(js)
            data = json.loads(result.get("result", {}).get("value", "{}"))
            if not data.get("ok"):
                return data
            pick = data["pick"]
            return {
                "ok": True,
                "x": pick["x"], "y": pick["y"],
                "text": pick["text"], "tag": pick["tag"], "cls": pick["cls"],
                "candidates": data["all"],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

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
          function _isVisible(el){
            if(!el) return false;
            const cs = getComputedStyle(el);
            if(cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity || '1') === 0) return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 10 && rect.height > 10;
          }
          const loginDialog = [...document.querySelectorAll('.sign-content, .login-dialog, .passport-login-container, .dialog-wrap .sign-form')].some(_isVisible);
          const qrLoginDialog = [...document.querySelectorAll('div,span,p')].filter(_isVisible).find(x => /扫码登录|请在App端确认登录|发送验证码/.test((x.innerText||x.textContent||'').trim()));
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
        """Click the '立即沟通' button with a real hardware-level mouse event.

        Boss's anti-automation silently drops synthetic dispatchEvent(MouseEvent)
        calls (isTrusted=false), so we MUST use CDP Input.dispatchMouseEvent
        (isTrusted=true) for any click that triggers real platform behavior.

        Strategy:
        1. Locate the chat button via Boss's canonical class .btn-startchat
           (most reliable; falls back to text matching if missing).
        2. Send real CDP mouse press/release at its viewport center.
        3. Poll for either:
           - the "继续沟通" popup (shown after auto-greeting), click it, OR
           - the chat sidebar opening (editor / send button visible).
        4. Stop on risk-control redirect.
        """
        self._ensure_connected()

        # Risk-control check first
        try:
            cur = self.cdp.evaluate("JSON.stringify({href: location.href})")
            cur_href = json.loads(cur.get("result", {}).get("value", "{}")).get("href", "")
            if "verify" in cur_href or "code=36" in cur_href:
                return {"ok": False, "step": "risk_control", "url": cur_href}
        except Exception:
            pass

        # Step 1: locate chat button via canonical selector, fallback to text
        locate_js = """
        (function(){
          function visible(el){
            if(!el) return false;
            var cs = getComputedStyle(el);
            if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity || '1') === 0) return false;
            var rect = el.getBoundingClientRect();
            return rect.width > 20 && rect.height > 10;
          }
          // Preferred: Boss's canonical chat button class
          var candidates = document.querySelectorAll('a.btn-startchat, .btn-startchat, .btn-startchat-wrap');
          for (var i = 0; i < candidates.length; i++) {
            if (visible(candidates[i])) {
              var rect = candidates[i].getBoundingClientRect();
              return JSON.stringify({
                ok: true, source: 'class_btn-startchat',
                text: (candidates[i].innerText || '').trim(),
                cls: (candidates[i].className || '').toString().slice(0, 80),
                x: rect.left + rect.width/2, y: rect.top + rect.height/2
              });
            }
          }
          // Fallback: visible text matching, prefer larger-area (main button)
          var texts = ['立即沟通', '继续沟通', '继续聊', '开聊'];
          var matches = [];
          var all = document.querySelectorAll('a,button,div,span');
          for (var j = 0; j < all.length; j++) {
            var el = all[j];
            var t = (el.innerText || el.textContent || '').trim();
            if (texts.indexOf(t) < 0) continue;
            if (!visible(el)) continue;
            var r = el.getBoundingClientRect();
            matches.push({text: t, cls: (el.className || '').toString().slice(0, 80),
                          x: r.left + r.width/2, y: r.top + r.height/2,
                          area: r.width * r.height});
          }
          if (!matches.length) return JSON.stringify({ok: false, error: 'not_found'});
          // Pick LARGEST area (main button is bigger than any sidebar/recommendation badge)
          matches.sort(function(a, b){ return b.area - a.area; });
          var p = matches[0];
          return JSON.stringify({ok: true, source: 'text_fallback', text: p.text,
                                 cls: p.cls, x: p.x, y: p.y});
        })()
        """
        try:
            result = self.cdp.evaluate(locate_js)
            target = json.loads(result.get("result", {}).get("value", "{}"))
        except Exception as e:
            return {"ok": False, "step": "locate_failed", "error": str(e)}

        if not target.get("ok"):
            return {"ok": False, "step": "no_chat_entry", "error": target.get("error", "not_found")}

        try:
            self._cdp_click_at(target["x"], target["y"])
        except Exception as e:
            return {"ok": False, "step": "click_failed", "error": str(e)}

        clicked_text = target.get("text", "")
        clicked_source = target.get("source", "")
        popup_clicked = False  # track to avoid re-clicking 继续沟通 on each poll

        # Step 2: poll up to 8s for sidebar open OR 继续沟通 popup.
        # (Don't early-return even if "继续沟通" was clicked — sidebar may still
        # need time to render, and a stray "继续沟通" element could be clicked
        # by mistake; only the sidebar-open signal counts as success.)
        for attempt in range(8):
            time.sleep(1)
            # Risk-control check
            try:
                href_data = self.cdp.evaluate("JSON.stringify({href: location.href})")
                href = json.loads(href_data.get("result", {}).get("value", "{}")).get("href", "")
                if "verify" in href or "code=36" in href:
                    return {"ok": False, "step": "risk_control", "url": href}
            except Exception:
                pass

            # Check for 继续沟通 popup (only when we initially clicked 立即沟通,
            # and only once per session — once clicked, set popup_clicked to
            # avoid sending duplicate clicks).
            if clicked_text == "立即沟通" and not popup_clicked:
                popup = self._find_clickable_by_text(["继续沟通"])
                if popup.get("ok"):
                    try:
                        self._cdp_click_at(popup["x"], popup["y"])
                        popup_clicked = True
                        time.sleep(0.5)
                        # After clicking 继续沟通, fall through to detect sidebar
                    except Exception as e:
                        return {"ok": False, "step": "popup_click_failed", "error": str(e)}

            # Check for chat sidebar open
            sidebar_js = """
            (function(){
              function visible(el){
                if(!el) return false;
                var cs = getComputedStyle(el);
                if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity || '1') === 0) return false;
                var rect = el.getBoundingClientRect();
                return rect.width > 10 && rect.height > 5;
              }
              var editorSelectors = ['.chat-input', '.edit-input', '.message-input',
                                     '[contenteditable="true"].chat-input',
                                     '[contenteditable="true"]', '[contenteditable]'];
              var editor = null;
              for (var s = 0; s < editorSelectors.length; s++) {
                var found = document.querySelector(editorSelectors[s]);
                if (found && visible(found)) { editor = found; break; }
              }
              var send = null;
              var btns = document.querySelectorAll('button');
              for (var j = 0; j < btns.length; j++) {
                if ((btns[j].innerText || '').trim() === '发送' && visible(btns[j])) { send = btns[j]; break; }
              }
              if (!send) {
                var sc = document.querySelectorAll('.btn-send, button.btn-send');
                for (var k = 0; k < sc.length; k++) if (visible(sc[k])) { send = sc[k]; break; }
              }
              return JSON.stringify({
                hasEditor: !!editor,
                hasSend: !!send
              });
            })()
            """
            try:
                sidebar_result = self.cdp.evaluate(sidebar_js)
                sidebar = json.loads(sidebar_result.get("result", {}).get("value", "{}"))
                if sidebar.get("hasEditor") or sidebar.get("hasSend"):
                    return {"ok": True, "step": "chat_sidebar_open",
                            "autoSent": False, "clicked_text": clicked_text}
            except Exception:
                pass

        # Click was sent (real hardware event) but sidebar didn't open within 8s.
        # Boss may have established the conversation server-side without showing
        # the sidebar (or the sidebar uses unknown selectors). Leave delivery to
        # the explicit fill+send flow — it has its own editor-finding fallback.
        return {"ok": True, "step": "no_sidebar_after_click", "autoSent": False,
                "clicked_text": clicked_text, "clicked_source": clicked_source}

    def inspect_chat_editor(self) -> dict[str, Any]:
        """Inspect the chat editor and send button.

        Waits for the sidebar chat panel to load. Returns the state with
        multiple signals (editorFound, sendFound, chatOpened) so callers can
        decide how aggressively to retry.

        Boss's chat sidebar DOM varies across versions — selectors we try:
        - .chat-input (legacy)
        - .edit-input / .message-input (newer)
        - [contenteditable="true"] (universal fallback)
        - [contenteditable] without value (defensive)
        """
        self._ensure_connected()
        # Poll for editor appearance (up to 20 attempts, ~0.8s each = ~16s)
        last_data: dict[str, Any] = {}
        for _attempt in range(20):
            js = """
            (function(){
              function visible(el){
                if(!el) return false;
                var cs = getComputedStyle(el);
                if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity || '1') === 0) return false;
                var rect = el.getBoundingClientRect();
                return rect.width > 10 && rect.height > 5;
              }
              // Try multiple editor selectors
              var editor = null;
              var editorSource = '';
              var editorSelectors = [
                '.chat-input', '.edit-input', '.message-input',
                '[contenteditable="true"].chat-input',
                '[contenteditable="true"]',
                '[contenteditable]'
              ];
              for (var s = 0; s < editorSelectors.length; s++) {
                var found = document.querySelector(editorSelectors[s]);
                if (found && visible(found)) {
                  editor = found; editorSource = editorSelectors[s]; break;
                }
              }
              // Send button
              var send = null;
              var btns = document.querySelectorAll('button');
              for (var j = 0; j < btns.length; j++) {
                var t = (btns[j].innerText || '').trim();
                if (t === '发送' && visible(btns[j])) { send = btns[j]; break; }
              }
              if (!send) {
                var sendCandidates = document.querySelectorAll('.btn-send, button.btn-send');
                for (var k = 0; k < sendCandidates.length; k++) {
                  if (visible(sendCandidates[k])) { send = sendCandidates[k]; break; }
                }
              }
              // Detect chat sidebar container (signals "chat opened" even without editor)
              var chatContainer = !!(document.querySelector('.chat-message, .chat-conversation, .chat-footer, .chat-wrap, .main-message, .message-content'));
              // Login dialog (visibility-checked)
              var loginDialog = (function(){
                var candidates = document.querySelectorAll('.sign-content, .login-dialog, .passport-login-container');
                for (var k = 0; k < candidates.length; k++) {
                  var el = candidates[k];
                  if (visible(el)) return true;
                }
                return false;
              })();
              var href = location.href || '';
              var riskControl = href.indexOf('verify') >= 0 || href.indexOf('code=36') >= 0;
              return JSON.stringify({
                ok: true,
                editorFound: !!editor,
                editorSource: editorSource,
                editorTag: editor ? editor.tagName : '',
                editorClass: editor ? (editor.className || '').toString().slice(0, 100) : '',
                sendFound: !!send,
                sendClass: send ? (send.className || '').toString().slice(0, 80) : '',
                chatContainer: chatContainer,
                loginDialog: loginDialog,
                riskControl: riskControl,
                href: href
              });
            })()
            """
            try:
                result = self.cdp.evaluate(js)
                data = json.loads(result.get("result", {}).get("value", "{}"))
                last_data = data
                if data.get("riskControl"):
                    return {"ok": False, "error": "risk_control", "url": data.get("href", "")}
                if data.get("loginDialog"):
                    return data
                if data.get("editorFound"):
                    return data
            except Exception:
                pass
            time.sleep(0.8)
        # Editor not found after waiting. Return rich state so caller can decide
        # whether to retry with alternative flow (e.g., if chatContainer=true &
        # sendFound=true, the conversation IS open — just the editor selector
        # doesn't match. Caller should try fill+send anyway.)
        last_data["autoSent"] = False
        last_data["step"] = "editor_not_found"
        return last_data

    def fill_chat_message(self, message: str) -> dict[str, Any]:
        """Fill the chat input with the given message.

        Uses CDP-native text input so the page receives trusted keyboard/input
        events. Direct DOM text assignment can leave BOSS's frontend state stale:
        the text appears in the editor, but the send action does not fire.
        """
        self._ensure_connected()
        js = """
        (function(){
          var editor = null;
          var all = document.querySelectorAll('*');
          for (var i = 0; i < all.length; i++) {
            if (all[i].className && typeof all[i].className === 'string' && all[i].className.indexOf('chat-input') >= 0) {
              editor = all[i];
              break;
            }
          }
          if (!editor) {
            var editables = document.querySelectorAll('[contenteditable="true"]');
            if (editables.length > 0) editor = editables[0];
          }
          if (!editor) return JSON.stringify({ok: false, error: 'no_editor'});

          editor.focus();
          var range = document.createRange();
          range.selectNodeContents(editor);
          var sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(range);
          return JSON.stringify({ok: true, step: 'editor_selected'});
        })()
        """
        try:
            result = self.cdp.evaluate(js)
            data = json.loads(result.get("result", {}).get("value", "{}"))
            if not data.get("ok"):
                return data

            self.cdp.send(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyDown",
                    "key": "Backspace",
                    "code": "Backspace",
                    "windowsVirtualKeyCode": 8,
                    "nativeVirtualKeyCode": 8,
                },
            )
            self.cdp.send(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyUp",
                    "key": "Backspace",
                    "code": "Backspace",
                    "windowsVirtualKeyCode": 8,
                    "nativeVirtualKeyCode": 8,
                },
            )
            time.sleep(0.5)
            self.cdp.send("Input.insertText", {"text": message})
            time.sleep(0.2)
            self.cdp.evaluate("""
            (function(){
              var editor = document.querySelector('.chat-input');
              if (!editor) return;
              editor.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: ''}));
              editor.dispatchEvent(new Event('change', {bubbles: true}));
            })()
            """)
            time.sleep(0.2)

            verify_js = """
            (function(){
              var editor = document.querySelector('.chat-input');
              var text = editor ? (editor.innerText || editor.textContent || '') : '';
              return JSON.stringify({ok: true, step: 'filled', len: text.length, text: text});
            })()
            """
            verify_result = self.cdp.evaluate(verify_js)
            verify_data = json.loads(verify_result.get("result", {}).get("value", "{}"))
            if verify_data.get("text") != message:
                return {
                    "ok": False,
                    "error": "message_fill_mismatch",
                    "len": verify_data.get("len", 0),
                }
            verify_data.pop("text", None)
            return verify_data
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def click_send(self) -> dict[str, Any]:
        """Click the send button using native CDP mouse events."""
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
          if (sendBtn.disabled || (sendBtn.className || '').indexOf('disabled') >= 0) {
            var editor = document.querySelector('.chat-input');
            var editorText = editor ? (editor.innerText || editor.textContent || '') : '';
            if (!editorText.trim()) {
              return JSON.stringify({ok: false, error: 'send_button_disabled_empty_editor'});
            }
            sendBtn.disabled = false;
            sendBtn.removeAttribute('disabled');
            if (typeof sendBtn.className === 'string') {
              sendBtn.className = sendBtn.className.replace(/\\bdisabled\\b/g, '').trim();
            }
          }

          var rect = sendBtn.getBoundingClientRect();
          return JSON.stringify({
            ok: true,
            step: 'send_button_found',
            disabled: !!sendBtn.disabled,
            x: rect.left + rect.width / 2,
            y: rect.top + rect.height / 2
          });
        })()
        """
        try:
            result = self.cdp.evaluate(js)
            data = json.loads(result.get("result", {}).get("value", "{}"))
            if not data.get("ok"):
                return data
            if data.get("disabled"):
                return {"ok": False, "error": "send_disabled"}

            try:
                self.cdp.send("Page.bringToFront")
            except Exception:
                pass
            focus_js = """
            (function(){
              var editor = document.querySelector('.chat-input');
              if (!editor) return JSON.stringify({ok: false, error: 'no_editor'});
              editor.focus();
              var text = editor.innerText || editor.textContent || '';
              return JSON.stringify({ok: true, len: text.length});
            })()
            """
            focus_result = self.cdp.evaluate(focus_js)
            focus_data = json.loads(focus_result.get("result", {}).get("value", "{}"))
            if not focus_data.get("ok"):
                return focus_data

            self.cdp.send(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyDown",
                    "key": "Enter",
                    "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                    "nativeVirtualKeyCode": 13,
                    "unmodifiedText": "\r",
                    "text": "\r",
                },
            )
            self.cdp.send(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyUp",
                    "key": "Enter",
                    "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                    "nativeVirtualKeyCode": 13,
                },
            )
            time.sleep(1.0)
            after_enter_js = """
            (function(){
              var editor = document.querySelector('.chat-input');
              var text = editor ? (editor.innerText || editor.textContent || '') : '';
              return JSON.stringify({ok: true, editorLen: text.length});
            })()
            """
            after_enter_result = self.cdp.evaluate(after_enter_js)
            after_enter = json.loads(after_enter_result.get("result", {}).get("value", "{}"))
            if after_enter.get("ok") and after_enter.get("editorLen", 0) == 0:
                return {"ok": True, "step": "pressed_enter_send"}

            x = data["x"]
            y = data["y"]
            self.cdp.send(
                "Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": x, "y": y, "button": "none"},
            )
            self.cdp.send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mousePressed",
                    "x": x,
                    "y": y,
                    "button": "left",
                    "clickCount": 1,
                },
            )
            self.cdp.send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseReleased",
                    "x": x,
                    "y": y,
                    "button": "left",
                    "clickCount": 1,
                },
            )
            time.sleep(1.5)
            return {"ok": True, "step": "enter_then_clicked_send", "x": x, "y": y}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def verify_delivery(self, message: str) -> dict[str, Any]:
        """Verify that the message was delivered."""
        self._ensure_connected()
        msg_preview = message[:20]
        js = f"""
        (function(){{
          var preview = {json.dumps(msg_preview)};
          var fullMessage = {json.dumps(message)};
          var editor = document.querySelector('.chat-input');
          var editorText = editor ? (editor.innerText || editor.textContent || '') : '';
          var stillInEditor = !!preview && editorText.indexOf(preview) >= 0;

          var root = document.body;
          var textWithoutEditor = '';
          if (root) {{
            var clone = root.cloneNode(true);
            var editors = clone.querySelectorAll('.chat-input, [contenteditable="true"]');
            for (var i = 0; i < editors.length; i++) {{
              editors[i].textContent = '';
            }}
            textWithoutEditor = clone.innerText || clone.textContent || '';
          }}

          var index = preview ? textWithoutEditor.lastIndexOf(preview) : -1;
          var around = index >= 0
            ? textWithoutEditor.slice(Math.max(0, index - 160), index + fullMessage.length + 160)
            : '';
          var hasMsg = index >= 0;
          var hasDeliveredNearMsg = /\\[?送达\\]?|已送达|\\[?已读\\]?/.test(around);
          // Delivery signal priority:
          //   1. Strong: message text appears in chat body AND editor is empty
          //      (text was sent, not still being typed).
          //   2. Bonus: explicit "送达" / "已读" marker near the message
          //      (Boss may delay or omit this — recipients must read first).
          // Prior versions required (hasMsg && hasDeliveredNearMsg), which fails
          // for newly-sent messages where Boss hasn't yet shown 送达. That left
          // every freshly-sent greeting stuck in "delivery_not_verified" limbo.
          var delivered = hasMsg && !stillInEditor;
          return JSON.stringify({{
            ok: true,
            delivered: delivered,
            stillInEditor,
            hasMsg,
            hasDeliveredNearMsg,
            editorLen: editorText.length
          }});
        }})()
        """
        try:
            result = self.cdp.evaluate(js)
            return json.loads(result.get("result", {}).get("value", "{}"))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def recover_draft_delivery(self, message: str) -> dict[str, Any]:
        """Open a matching Boss chat draft and send it with Enter.

        This recovers from the state where a previous click filled the chat
        editor but did not trigger Boss's send event.
        """
        self._ensure_connected()
        try:
            self.cdp.send("Page.navigate", {"url": "https://www.zhipin.com/web/geek/chat"})
            time.sleep(2.0)
            preview = message[:20]
            find_js = f"""
            (function(){{
              var preview = {json.dumps(preview)};
              var items = Array.prototype.slice.call(document.querySelectorAll('li')).filter(function(el) {{
                var text = (el.innerText || el.textContent || '').trim();
                return text.indexOf('[草稿]') >= 0 && text.indexOf(preview) >= 0;
              }});
              if (!items.length) return JSON.stringify({{ok: false, delivered: false, error: 'draft_not_found'}});
              var el = items[0];
              var rect = el.getBoundingClientRect();
              return JSON.stringify({{
                ok: true,
                count: items.length,
                text: (el.innerText || el.textContent || '').trim().slice(0, 220),
                x: rect.left + rect.width / 2,
                y: rect.top + rect.height / 2
              }});
            }})()
            """
            found = json.loads(self.cdp.evaluate(find_js).get("result", {}).get("value", "{}"))
            if not found.get("ok"):
                return found

            x = found["x"]
            y = found["y"]
            self.cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none"})
            self.cdp.send("Input.dispatchMouseEvent", {
                "type": "mousePressed",
                "x": x,
                "y": y,
                "button": "left",
                "clickCount": 1,
            })
            self.cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseReleased",
                "x": x,
                "y": y,
                "button": "left",
                "clickCount": 1,
            })
            time.sleep(1.0)

            editor_js = f"""
            (function(){{
              var preview = {json.dumps(preview)};
              var editor = document.querySelector('.chat-input');
              var text = editor ? (editor.innerText || editor.textContent || '') : '';
              if (editor) editor.focus();
              return JSON.stringify({{
                ok: true,
                editorFound: !!editor,
                editorLen: text.length,
                matches: text.indexOf(preview) >= 0
              }});
            }})()
            """
            editor = json.loads(self.cdp.evaluate(editor_js).get("result", {}).get("value", "{}"))
            if not editor.get("editorFound"):
                return {"ok": False, "delivered": False, "error": "draft_editor_not_found", "draft": found}
            if not editor.get("matches"):
                return {"ok": False, "delivered": False, "error": "draft_editor_mismatch", "draft": found, "editor": editor}

            self.cdp.send("Input.dispatchKeyEvent", {
                "type": "keyDown",
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
                "nativeVirtualKeyCode": 13,
                "unmodifiedText": "\r",
                "text": "\r",
            })
            self.cdp.send("Input.dispatchKeyEvent", {
                "type": "keyUp",
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
                "nativeVirtualKeyCode": 13,
            })
            time.sleep(1.3)
            verify = self.verify_delivery(message)
            verify["draft"] = found
            return verify
        except Exception as e:
            return {"ok": False, "delivered": False, "error": str(e)}

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
