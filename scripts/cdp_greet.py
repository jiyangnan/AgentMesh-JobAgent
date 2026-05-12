#!/usr/bin/env python3
"""
CDP Batch Greet — uses existing Chrome at localhost:9222 (白羊武士的真实浏览器)
每个岗位单独开一个 tab，避免 Page.navigate WebSocket 断连问题。
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── 必须最先设置：绕过 SOCKS 代理 ──
for k in list(os.environ.keys()):
    if 'proxy' in k.lower():
        os.environ[k] = ''
os.environ['NO_PROXY'] = 'localhost,127.0.0.1'
os.environ['no_proxy'] = 'localhost,127.0.0.1'

import websockets

CDP_HOST = "localhost"
CDP_PORT = 9222


# ── HTTP 辅助 ──
def get_tabs_http() -> list:
    import urllib.request
    req = urllib.request.Request(f"http://{CDP_HOST}:{CDP_PORT}/json")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


# ── CDP WebSocket 客户端 ──
class CDP:
    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self.ws = None
        self._id = 0

    async def connect(self):
        self.ws = await websockets.connect(self.ws_url, max_size=10 * 1024 * 1024)

    async def send(self, method: str, params: dict = None) -> dict:
        self._id += 1
        await self.ws.send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
        while True:
            raw = await self.ws.recv()
            resp = json.loads(raw)
            if resp.get("id") == self._id:
                return resp.get("result", {})

    async def close(self):
        if self.ws:
            await self.ws.close()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.close()


# ── 每个岗位独立开 tab ──
async def create_job_tab(job_url: str) -> tuple[str, str]:
    """用已有 tab 的 WS 创建新 tab，直接导航到 job_url。返回 (tab_id, ws_url)。"""
    tabs = get_tabs_http()
    if not tabs:
        return "", ""
    first_ws = tabs[0].get("webSocketDebuggerUrl", "")

    async with CDP(first_ws) as c:
        resp = await c.send("Target.createTarget", {
            "url": job_url,
            "setAutoAttach": True,
            "waitForDebuggerOnStart": False,
        })
    new_id = resp.get("targetId", "")
    return new_id, f"ws://{CDP_HOST}:{CDP_PORT}/devtools/page/{new_id}"


async def close_tab(tab_id: str):
    """关闭指定 tab。"""
    tabs = get_tabs_http()
    if not tabs:
        return
    first_ws = tabs[0].get("webSocketDebuggerUrl", "")
    async with CDP(first_ws) as c:
        await c.send("Target.closeTarget", {"targetId": tab_id})


async def wait_for(condition_js: str, client: CDP, timeout: int = 15) -> dict | None:
    """轮询等待 condition_js 返回 truthy。"""
    for _ in range(timeout):
        result = await client.send("Runtime.evaluate", {
            "expression": condition_js,
            "returnByValue": True
        })
        val = result.get("result", {}).get("value", "{}")
        try:
            parsed = json.loads(val)
            if parsed:
                return parsed
        except:
            pass
        await asyncio.sleep(1)
    return None


async def check_state(client: CDP) -> dict:
    result = await client.send("Runtime.evaluate", {
        "expression": r"""
(function(){
  var loginDialog = !!document.querySelector('.sign-content,.login-dialog,.passport-login-container,.dialog-wrap .sign-form');
  var qrLogin = !![...document.querySelectorAll('div,span,p')].find(function(x){
    return /\u626b\u7801\u767b\u5f55|\u8bf7\u5728App\u7aef\u786e\u8ba4\u767b\u5f55/.test((x.innerText||'').trim());
  });
  var userNav = !![...document.querySelectorAll('a,span,div')].find(function(x){
    return ['\u6d88\u606f','\u7b80\u5386','\u804c\u4f4d'].includes((x.innerText||'').trim());
  });
  var chatEntry = !!document.querySelector('.btn-startchat-wrap');
  var chatEditor = !!document.querySelector('.chat-input');
  var delivered = (document.body ? document.body.innerText : '').includes('[\u9001\u8fbe]') || (document.body ? document.body.innerText : '').includes('\u5df2\u9001\u8fbe');
  return JSON.stringify({loginDialog, qrLogin, userNav, chatEntry, chatEditor, delivered, url: location.href});
})()
""",
        "returnByValue": True
    })
    val = result.get("result", {}).get("value", "{}")
    try:
        return json.loads(val)
    except:
        return {}


async def click_chat(client: CDP) -> dict:
    result = await client.send("Runtime.evaluate", {
        "expression": r"""
(function(){
  var wrap = [...document.querySelectorAll('.btn-startchat-wrap')].find(function(x){
    return (x.innerText||'').trim().includes('\u6c9f\u901a') || (x.innerText||'').trim().includes('\u8054\u7cfb');
  });
  if (!wrap) {
    var links = [...document.querySelectorAll('a')].filter(function(a){
      var t = (a.innerText||'').trim();
      return t.includes('\u6c9f\u901a') || t.includes('\u7ee7\u7eed\u6c9f\u901a') || t.includes('\u7acb\u5373\u6c9f\u901a');
    });
    if (links.length > 0) { links[0].click(); return JSON.stringify({ok:true, step:'clicked_link'}); }
    return JSON.stringify({ok:false, step:'no_chat_entry'});
  }
  var link = wrap.querySelector('a') || wrap;
  link.click();
  return JSON.stringify({ok:true, step:'clicked'});
})()
""",
        "returnByValue": True
    })
    val = result.get("result", {}).get("value", "{}")
    try:
        return json.loads(val)
    except:
        return {"ok": False}


async def fill_and_send(client: CDP, message: str) -> dict:
    msg_esc = (message
               .replace("\\", "\\\\")
               .replace('"', '\\"')
               .replace("\n", "\\n")
               .replace("%", "%25"))

    fill_js = f'(function(){{var e=document.querySelector(".chat-input");if(!e)return JSON.stringify({{ok:false}});e.focus();e.textContent=decodeURIComponent("{msg_esc}");e.dispatchEvent(new InputEvent("input",{{bubbles:true}}));return JSON.stringify({{ok:true,len:e.textContent.length}});}})()'
    r = await client.send("Runtime.evaluate", {"expression": fill_js, "returnByValue": True})
    val = r.get("result", {}).get("value", "{}")
    try:
        p = json.loads(val)
        if not p.get("ok"):
            return {"ok": False, "error": p.get("err", "fill_failed")}
    except:
        return {"ok": False, "error": "fill_parse_error"}

    await asyncio.sleep(0.5)

    send_js = r"""
(function(){
  var b = [...document.querySelectorAll('button')].find(function(x){ return (x.innerText||'').trim()==='\u53d1\u9001'; });
  if (!b) return JSON.stringify({ok:false, error:'no_send_btn'});
  b.disabled=false; b.removeAttribute('disabled');
  b.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));
  return JSON.stringify({ok:true});
})()
"""
    r = await client.send("Runtime.evaluate", {"expression": send_js, "returnByValue": True})
    val = r.get("result", {}).get("value", "{}")
    try:
        p = json.loads(val)
        if not p.get("ok"):
            return {"ok": False, "error": p.get("error", "send_failed")}
    except:
        return {"ok": False, "error": "send_parse_error"}

    await asyncio.sleep(4)
    state = await check_state(client)
    return {"ok": True, "delivered": state.get("delivered", False)}


async def greet_job(job_url: str, message: str) -> dict:
    """打开一个岗位 tab，打招呼，关闭 tab。"""
    # 创建新 tab
    tab_id, ws_url = await create_job_tab(job_url)
    if not tab_id:
        return {"status": "error", "error": "create_tab_failed"}

    client = CDP(ws_url)
    try:
        await client.connect()
        await asyncio.sleep(4)  # 等页面加载

        # 激活 tab
        await client.send("Target.activateTarget", {"targetId": tab_id})

        # 检查登录状态
        state = await check_state(client)
        if state.get("loginDialog") or state.get("qrLogin"):
            return {"status": "not_logged_in", "error": "boss_not_logged_in"}

        # 如果已经在聊天页面
        if state.get("chatEditor"):
            print("   → Already on chat page, sending directly")
            result = await fill_and_send(client, message)
            return {
                "status": "ok" if result.get("delivered") else "failed",
                "delivered": result.get("delivered"),
                "error": result.get("error"),
            }

        # 如果有聊天入口按钮
        if state.get("chatEntry"):
            print("   → Clicking chat entry...")
            click_r = await click_chat(client)
            if not click_r.get("ok"):
                return {"status": "failed", "error": click_r.get("step", "chat_entry_failed")}

            # 等待聊天输入框
            found = await wait_for(
                "(function(){var e=document.querySelector('.chat-input');return e?JSON.stringify({found:true}):null})()",
                client, timeout=20
            )
            if not found:
                return {"status": "failed", "error": "chat_editor_not_found"}

            result = await fill_and_send(client, message)
            return {
                "status": "ok" if result.get("delivered") else "failed",
                "delivered": result.get("delivered"),
                "error": result.get("error"),
            }

        return {"status": "failed", "error": "no_chat_entry_button"}

    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        await client.close()
        await close_tab(tab_id)


# ── 主流程 ──
async def main_async(jobs_file: str, template: str, delay: float, limit: int, dry_run: bool):
    jobs = json.loads(Path(jobs_file).read_text(encoding="utf-8"))
    if limit > 0:
        jobs = jobs[:limit]
    print(f"📥 {len(jobs)} jobs | Template: {template[:40]}...")

    results = []
    for i, job in enumerate(jobs, 1):
        name = job.get("name", "") or job.get("jobName", "unknown")
        company = job.get("company", "unknown")
        url = job.get("url", "")
        print(f"\n[{i}/{len(jobs)}] {name} | {company}")

        if dry_run:
            print(f"   🟡 DRY RUN → {url}")
            continue

        result = await greet_job(url, template)
        status = result.get("status", "failed")
        results.append({
            "name": name, "company": company, "url": url,
            "status": status, "greeting": template,
            "error_msg": result.get("error"),
            "greeted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        })

        if status == "ok":
            print(f"   ✅ Delivered!")
        elif status == "not_logged_in":
            print(f"   ⚠️  Boss not logged in — stopping")
            break
        else:
            print(f"   ❌ {result.get('error', 'unknown')}")

        if i < len(jobs):
            print(f"   ⏳ Delay {delay}s...")
            await asyncio.sleep(delay)

    if not dry_run:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out = Path("data/results") / f"cdp_greet_{ts}.json"
        Path("data/results").mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n💾 Saved {len(results)} → {out}")

    ok = sum(1 for r in results if r.get("status") == "ok")
    print(f"\n{'='*50}\n📊 {ok}/{len(results)} delivered\n{'='*50}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", "-i", required=True)
    ap.add_argument("--delay", type=float, default=12)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    config_path = Path("config/config.yaml")
    if config_path.exists():
        import yaml
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        template = raw.get("greeter", {}).get("template", "").strip()
    else:
        template = "您好，看了您的招聘简章，我认为比较符合。目前我在做 vibe Coding 和 agent 相关的工作，希望进一步沟通。"

    asyncio.run(main_async(args.input, template, args.delay, args.limit, args.dry_run))
