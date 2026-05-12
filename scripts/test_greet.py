#!/usr/bin/env python3
"""
Job Agent - Direct Greeter Test (standalone, no framework)
Debug and verify the full greet flow works.
"""
import subprocess, json, time, re

BROWSER_PAGE_ID = None

def open_job(url: str) -> str | None:
    """Open job page, return page_id"""
    r = subprocess.run(["opencli", "browser", "open", url], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        info = json.loads(r.stdout.strip())
        page_id = info.get("page")
        return page_id
    except Exception:
        return None

def click_contact(page_id: str) -> bool:
    """Click 继续沟通/立即沟通 button"""
    r = subprocess.run(
        ["opencli", "browser", "state", "--tab", page_id],
        capture_output=True, text=True
    )
    for line in r.stdout.split("\n"):
        if "继续沟通" in line or "立即沟通" in line:
            m = re.search(r"\[(\d+)\]", line)
            if m:
                r2 = subprocess.run(
                    ["opencli", "browser", "click", m.group(1), "--tab", page_id],
                    capture_output=True, text=True
                )
                return '"clicked": true' in r2.stdout
    return False

def eval_js(script: str, page_id: str, timeout: int = 30) -> str | None:
    """Run eval_js with explicit --tab page_id"""
    cmd = ["opencli", "browser", "eval", "--tab", page_id, script]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None

def greet_at_chat(page_id: str, message: str) -> dict:
    """
    Single eval_js that:
    1. Finds .chat-input (by scanning all elements' className, not CSS selector)
    2. Fills message
    3. Finds + enables + clicks send button
    4. Returns result
    """
    msg_esc = (message
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n"))

    script = f"""
(function(){{
    // Find .chat-input by scanning all elements (BOSS blocks CSS selector on fast navigation)
    var el = null;
    var all = document.querySelectorAll("*");
    for (var i = 0; i < all.length; i++) {{
        if (all[i].className === "chat-input") {{ el = all[i]; break; }}
    }}
    if (!el) {{
        // Fallback: find by tag + partial class match
        var divs = document.querySelectorAll("div");
        for (var i = 0; i < divs.length; i++) {{
            if (divs[i].className.indexOf("chat-input") >= 0) {{ el = divs[i]; break; }}
        }}
    }}
    if (!el) return JSON.stringify({{status: "failed", phase: "find_editor", error: "no_el"}});

    // Fill - use textContent not innerText (BOSS normalizes on innerText access)
    el.textContent = "{msg_esc}";

    // Find send button - look for type=send attribute
    var btn = null;
    var buttons = document.querySelectorAll("button");
    for (var i = 0; i < buttons.length; i++) {{
        if (buttons[i].getAttribute("type") === "send") {{ btn = buttons[i]; break; }}
    }}
    if (!btn) return JSON.stringify({{status: "failed", phase: "find_btn", error: "no_btn"}});

    // Enable + send
    btn.disabled = false;
    btn.removeAttribute("disabled");
    btn.click();

    return JSON.stringify({{status: "ok", phase: "sent"}});
}})()
"""
    return eval_js(script, page_id, timeout=60)

def main():
    global BROWSER_PAGE_ID

    job_url = "https://www.zhipin.com/job_detail/18664be8eced2a8c0nZ83t6_FlJX.html"
    message = "您好，我对贵司的AI产品经理职位很感兴趣，请问方便进一步沟通吗？"

    print(f"=== Step 1: Open job page ===")
    page_id = open_job(job_url)
    if not page_id:
        print("FAIL: cannot open job page")
        return
    BROWSER_PAGE_ID = page_id
    print(f"page_id: {page_id}")

    # Verify job page is accessible
    r = subprocess.run(["opencli", "browser", "state", "--tab", page_id], capture_output=True, text=True)
    print(f"State: {r.stdout.split(chr(10))[0]}")

    print(f"\n=== Step 2: Click contact ===")
    ok = click_contact(page_id)
    print(f"Click contact: {'ok' if ok else 'fail'}")

    # Check URL right after click
    url = eval_js("window.location.href", page_id)
    print(f"URL after click: {url}")

    print(f"\n=== Step 3: Wait 1.5s for chat modal ===")
    time.sleep(1.5)

    url2 = eval_js("window.location.href", page_id)
    print(f"URL at t=1.5s: {url2}")

    print(f"\n=== Step 4: Fill + Send (single eval_js) ===")
    result = greet_at_chat(page_id, message)
    print(f"Result: {result}")

    print(f"\n=== Step 5: Verify ===")
    time.sleep(2)
    page_text = eval_js("(function(){return document.body.innerText.slice(0,300);})()", page_id)
    print(f"Page text: {page_text[:200] if page_text else 'empty'}")

if __name__ == "__main__":
    main()