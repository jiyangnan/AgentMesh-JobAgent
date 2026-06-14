from __future__ import annotations

import json
import subprocess
import time
from typing import Any

from jobagent.platforms.boss.selectors import (
    CHAT_EDITOR_SELECTOR,
    CHAT_ENTRY_TEXTS,
    SEND_BUTTON_SELECTOR,
)

from .base import BossActionDriver


class AppleScriptBossDriver(BossActionDriver):
    def _run_applescript(self, script: str, *args: str, timeout: int = 30) -> tuple[bool, str, str]:
        proc = subprocess.run(
            ["osascript", "-", *args],
            input=script,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        return proc.returncode == 0, proc.stdout.strip(), proc.stderr.strip()

    def _exec_js(self, js_code: str, timeout: int = 30) -> dict[str, Any]:
        script = '''
        on run argv
          tell application "Google Chrome"
            if (count of windows) = 0 then error "no chrome window"
            tell front window
              set t to active tab
              set out to execute t javascript (item 1 of argv)
              return out
            end tell
          end tell
        end run
        '''
        ok, out, err = self._run_applescript(script, js_code, timeout=timeout)
        if not ok:
            return {"ok": False, "error": err or out or "osascript_failed"}
        try:
            return json.loads(out)
        except Exception:
            return {"ok": True, "raw": out}

    def _unwrap(self, result: dict[str, Any]) -> dict[str, Any]:
        """Unwrap _exec_js result into the parsed JSON dict.

        _exec_js returns either:
          - The parsed JSON dict directly (when JSON parsing succeeded)
          - {"ok": True, "raw": "<unparsed string>"} when JSON parsing failed
        This helper handles both cases and returns the inner dict.
        """
        if "raw" in result:
            try:
                return json.loads(result["raw"])
            except (json.JSONDecodeError, Exception):
                return {}
        return result

    def _poll_js(self, js_code: str, predicate, attempts: int = 8, interval: float = 1.0, timeout: int = 30) -> dict[str, Any]:
        last: dict[str, Any] = {"ok": False, "error": "no_attempts"}
        for _ in range(attempts):
            last = self._exec_js(js_code, timeout=timeout)
            try:
                if predicate(last):
                    return last
            except Exception:
                pass
            time.sleep(interval)
        return last

    def chrome_running(self) -> bool:
        result = subprocess.run(["pgrep", "-x", "Google Chrome"], capture_output=True, text=True)
        return result.returncode == 0

    def applescript_js_enabled(self) -> tuple[bool, str]:
        script = '''
        on run
          tell application "Google Chrome"
            if (count of windows) = 0 then make new window
            tell front window
              set t to active tab
              return execute t javascript "1+1"
            end tell
          end tell
        end run
        '''
        ok, out, err = self._run_applescript(script, timeout=15)
        if ok and out.strip() == "2":
            return True, "ok"
        return False, err or out or "javascript_from_apple_events_disabled"

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5) -> dict[str, Any]:
        """
        Open URL in a new tab of the front window, then activate that tab.
        Sets the new tab as active so subsequent operations work on it.
        """
        script = f'''
        on run argv
          set targetUrl to item 1 of argv
          set waitSeconds to item 2 of argv as integer
          tell application "Google Chrome"
            activate
            -- Find or create a usable window (prefer window 1)
            if (count of windows) = 0 then
              make new window
            end if
            -- Make sure window 1 exists and is accessible
            if (count of windows) >= 1 then
              tell window 1
                set newTab to make new tab with properties {{URL:targetUrl}}
                -- Activate the new tab (last tab = newly created one)
                set tabCount to count of tabs
                set active tab index to tabCount
                delay waitSeconds
                set t to active tab
                return URL of t & linefeed & title of t
              end tell
            end if
          end tell
        end run
        '''
        ok, out, err = self._run_applescript(script, url, str(wait_seconds), timeout=wait_seconds + 15)
        if not ok:
            return {"ok": False, "error": err or out}
        lines = out.splitlines()
        return {"ok": True, "url": lines[0] if lines else "", "title": lines[1] if len(lines) > 1 else ""}

    def inspect_page(self) -> dict[str, Any]:
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
        return self._exec_js(js)

    def click_chat_entry(self) -> dict[str, Any]:
        """
        Click the '继续沟通' or '立即沟通' button.
        The actual clickable element is an <a> tag inside .btn-startchat-wrap.

        Note: After clicking, Boss may immediately navigate to the chat page,
        which causes the JS return value to be lost (empty string).
        We handle this by checking the page URL after the click.
        """
        texts = json.dumps(CHAT_ENTRY_TEXTS, ensure_ascii=False)
        js = f"""
        (function(){{
          var wrap = [...document.querySelectorAll('.btn-startchat-wrap')].find(function(x){{
            var t = (x.innerText || '').trim();
            return {texts}.indexOf(t) >= 0;
          }});
          if (!wrap) return JSON.stringify({{ok: false, step: 'no_chat_entry'}});
          var link = wrap.querySelector('a');
          if (!link) return JSON.stringify({{ok: false, step: 'no_link_in_wrap'}});
          link.click();
          return JSON.stringify({{ok: true, step: 'clicked_chat_entry', text: (link.innerText || '').trim()}});
        }})()
        """
        result = self._exec_js(js)
        data = self._unwrap(result)

        if data.get("ok"):
            return {
                "ok": True,
                "step": data.get("step", ""),
                "text": data.get("text", ""),
            }

        # If unwrap failed (empty dict), the JS return value may have been lost
        # because the page navigated immediately after click.
        # Check if page actually navigated to chat URL.
        if not data:
            for attempt in range(3):
                time.sleep(1.0 + attempt * 0.5)
                nav_result = self._exec_js(
                    '(function(){return JSON.stringify({url: location.href})})()'
                )
                nav_data = self._unwrap(nav_result)
                if "/web/geek/chat" in str(nav_data.get("url", "")):
                    return {"ok": True, "step": "clicked_chat_entry_navigated", "text": ""}

        return {"ok": False, "step": data.get("step", ""), "text": data.get("text", ""),
                "note": "click_return_lost_and_no_chat_redirect"}

    def inspect_chat_editor(self) -> dict[str, Any]:
        selector = CHAT_EDITOR_SELECTOR.replace("'", "\\'")
        js = f"""
        (function(){{
          const editor = document.querySelector('{selector}');
          const send = document.querySelector('{SEND_BUTTON_SELECTOR}');
          const loginDialog = !!document.querySelector('.sign-content, .login-dialog, .passport-login-container, .dialog-wrap .sign-form');
          const sendHints = [...document.querySelectorAll('div,span,p')].map(x=>(x.innerText||x.textContent||'').trim()).filter(Boolean).filter(x=>/按Enter键发送|发送验证码|请简短描述您的问题|扫码登录/.test(x)).slice(0,20);
          return JSON.stringify({{
            ok:true,
            href: location.href || '',
            title: document.title || '',
            editorFound: !!editor,
            editorTag: editor ? editor.tagName : '',
            editorClass: editor ? (editor.className || '') : '',
            editorEditable: editor ? editor.getAttribute('contenteditable') : null,
            sendFound: !!send,
            sendClass: send ? (send.className || '') : '',
            sendDisabled: send ? !!send.disabled : null,
            loginDialog,
            sendHints
          }});
        }})()
        """
        return self._poll_js(
            js,
            lambda r: (
                bool(r.get("editorFound"))
                or bool(r.get("loginDialog"))
            ),
            attempts=15,
            interval=0.8,
        )

    def fill_chat_message(self, message: str) -> dict[str, Any]:
        """
        Fill chat input with message.
        Uses encodeURIComponent to safely embed message in JS, avoiding AppleScript quoting issues.
        """
        msg_encoded = message.replace("%", "%25")
        js = (
            f"(function(){{"
            f"var m=decodeURIComponent('{msg_encoded}');"
            f"var e=document.querySelector('.chat-input');"
            f"if(!e)return JSON.stringify({{ok:false,error:'no_editor'}});"
            f"e.focus();e.textContent=m;"
            f"e.dispatchEvent(new InputEvent('input',{{bubbles:true}}));"
            f"return JSON.stringify({{ok:true,len:e.textContent.length,preview:e.textContent.slice(0,20)}});"
            f"}})()"
        )
        result = self._exec_js(js)
        data = self._unwrap(result)
        return {"ok": data.get("ok", False), "step": "filled", "len": data.get("len", 0)}

    def click_send(self) -> dict[str, Any]:
        """
        Click the send button.
        Must remove 'disabled' attribute first - Boss buttons are often disabled until message is filled.
        Uses dispatchEvent(MouseEvent) instead of .click() to properly trigger
        Boss's React/Vue event system.
        """
        js = """
        (function(){
          var b = [...document.querySelectorAll('button')].find(function(x){
            return (x.innerText || '').trim() === '发送';
          });
          if (!b) return JSON.stringify({ok: false, error: 'no_send'});
          b.disabled = false;
          b.removeAttribute('disabled');
          // Use MouseEvent dispatchEvent to trigger Boss's event system
          var evt = new MouseEvent('click', {
            bubbles: true,
            cancelable: true,
            view: window
          });
          b.dispatchEvent(evt);
          return JSON.stringify({ok: true, step: 'clicked_send'});
        })()
        """
        result = self._exec_js(js)
        data = self._unwrap(result)
        return {"ok": data.get("ok", False), "step": data.get("step", "")}

    def verify_delivery(self, message: str) -> dict[str, Any]:
        """
        Verify message delivery by checking for '[送达]' in page text.
        Boss displays delivery status as '[送达]' not plain '已送达'.
        """
        msg_encoded = message[:20].replace("%", "%25")
        js = f"""
        (function(){{
          var txt = document.body ? document.body.innerText : '';
          // Check for [送达] delivery indicator
          var hasDelivered = txt.includes('[送达]') || txt.includes('已送达');
          // Check for message content in chat
          var hasMsg = txt.includes('{msg_encoded}');
          return JSON.stringify({{ok: true, delivered: hasDelivered && hasMsg, hasDelivered, hasMsg, text: txt.slice(0, 500)}});
        }})()
        """
        result = self._exec_js(js)
        data = self._unwrap(result)
        return {
            "ok": True,
            "delivered": data.get("delivered", False),
            "hasDelivered": data.get("hasDelivered", False),
            "hasMsg": data.get("hasMsg", False),
        }
