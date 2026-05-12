from __future__ import annotations

from jobagent.domain.models import CheckResult, DoctorReport
from jobagent.drivers.boss import create_driver
from jobagent.infra.state import last_doctor_path, save_json

DEFAULT_JOB_URL = "https://www.zhipin.com/job_detail/f4055548ddc66b070nd52dq6EltQ.html"


def run_boss_doctor(job_url: str = DEFAULT_JOB_URL) -> DoctorReport:
    driver = create_driver()
    checks: list[CheckResult] = []

    chrome_ok = driver.chrome_running()
    checks.append(CheckResult("chrome_running", chrome_ok, "Google Chrome process detected" if chrome_ok else "Google Chrome not running"))

    js_ok, js_detail = driver.applescript_js_enabled()
    checks.append(CheckResult("applescript_js_enabled", js_ok, js_detail))

    open_result = driver.open_url_in_new_tab(job_url, wait_seconds=5) if chrome_ok and js_ok else {"ok": False, "error": "chrome_or_js_not_ready"}
    checks.append(CheckResult("job_page_openable", bool(open_result.get("ok")), open_result.get("title") or open_result.get("error", ""), open_result))

    page_result = driver.inspect_page() if open_result.get("ok") else {"ok": False, "error": "job_page_not_opened"}
    logged_in = bool(page_result.get("ok")) and not bool(page_result.get("loginDialog") or page_result.get("qrLoginDialog"))
    checks.append(CheckResult("boss_logged_in", logged_in, "No blocking login dialog detected" if logged_in else "Blocking login dialog detected", page_result))

    geek_ready = bool(page_result.get("ok")) and bool(page_result.get("userNav") or page_result.get("geekNav"))
    checks.append(CheckResult("geek_identity_detected", geek_ready, "Geek-side navigation detected" if geek_ready else "Geek identity not clearly detected", page_result))

    chat_entry_ready = bool(page_result.get("ok")) and bool(page_result.get("hasChatEntry"))
    checks.append(CheckResult("chat_entry_visible", chat_entry_ready, "Chat entry found" if chat_entry_ready else "No chat entry found", page_result))

    chat_result = driver.click_chat_entry() if chat_entry_ready else {"ok": False, "error": "chat_entry_not_visible"}
    checks.append(CheckResult("chat_page_openable", bool(chat_result.get("ok")), chat_result.get("step") or chat_result.get("error", ""), chat_result))

    editor_result = driver.inspect_chat_editor() if chat_result.get("ok") else {"ok": False, "error": "chat_not_opened"}
    editor_ready = (
        bool(editor_result.get("editorFound"))
        or bool(editor_result.get("autoSent"))
    ) and not bool(editor_result.get("loginDialog"))
    checks.append(CheckResult(
        "chat_editor_detectable", editor_ready,
        "Chat editor found" if editor_ready else "Chat editor not found or blocked by login dialog",
        editor_result,
    ))

    verify_ready = bool(editor_result.get("sendFound")) and editor_ready
    checks.append(CheckResult("delivery_verification_path_ready", verify_ready, "Send button detectable" if verify_ready else "Send button not ready", editor_result))

    overall = "READY" if all(c.ok for c in checks) else "NOT_READY"
    report = DoctorReport(status=overall, checks=checks)
    save_json(last_doctor_path(), report.to_dict())
    return report
