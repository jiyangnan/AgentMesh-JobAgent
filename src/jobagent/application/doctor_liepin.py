from __future__ import annotations

from typing import Any

from jobagent.domain.models import CheckResult, DoctorReport
from jobagent.drivers.boss import create_driver
from jobagent.infra.credentials import load_api_key
from jobagent.infra.state import last_doctor_path, save_json
from jobagent.platforms.liepin import LiepinReadOnlyCollector, LiepinSessionGuide


def run_liepin_doctor(
    query: str = "产品",
    city: str = "",
    wait_seconds: int = 5,
    limit: int = 5,
    check_cloud: bool = False,
    driver: Any | None = None,
) -> DoctorReport:
    """Run a read-only Liepin readiness diagnostic.

    The doctor intentionally stops when login is required. That makes the user
    intervention point explicit and avoids turning a login/session issue into a
    misleading selector or collect failure.
    """
    active_driver = driver or create_driver(platform="liepin")
    checks: list[CheckResult] = []

    chrome_ok = _call_bool(active_driver, "chrome_running", default=True)
    checks.append(CheckResult(
        "chrome_running",
        chrome_ok,
        "Chrome driver is available" if chrome_ok else "Chrome driver is not available",
    ))

    js_ok, js_detail = _js_ready(active_driver)
    checks.append(CheckResult(
        "browser_js_ready",
        js_ok,
        js_detail,
    ))

    if chrome_ok and js_ok:
        session = LiepinSessionGuide(driver=active_driver).check(
            query=query,
            city=city,
            wait_seconds=wait_seconds,
        )
    else:
        session = None

    logged_in = bool(session and session.logged_in)
    session_evidence = session.to_dict() if session else {"error": "browser_not_ready"}
    if logged_in:
        login_detail = "Liepin logged-in session detected"
    elif session:
        login_detail = "Liepin login is required before live collect"
    else:
        login_detail = "Browser is not ready for Liepin login check"
    checks.append(CheckResult(
        "liepin_logged_in",
        logged_in,
        login_detail,
        session_evidence,
    ))

    if logged_in:
        result = LiepinReadOnlyCollector(driver=active_driver).collect(
            query=query,
            city=city,
            limit=max(1, limit),
            wait_seconds=wait_seconds,
            pages=1,
            page_delay=0,
        )
        selector_ready = bool(result.ok and result.jobs)
        checks.append(CheckResult(
            "liepin_selector_extracts_jobs",
            selector_ready,
            f"Extracted {len(result.jobs)} visible Liepin jobs"
            if selector_ready
            else (result.error or "No visible Liepin jobs extracted"),
            result.to_payload(include_snapshot=True),
        ))
    else:
        skip_reason = "login_required" if session and session.login_required else "browser_not_ready"
        checks.append(CheckResult(
            "liepin_selector_extracts_jobs",
            False,
            "Skipped until Liepin login is completed"
            if skip_reason == "login_required"
            else "Skipped until browser readiness checks pass",
            {"skipped": True, "reason": skip_reason},
        ))

    if check_cloud:
        checks.append(_cloud_api_key_check())

    report = _build_report(checks)
    checks.append(_save_report_check(report))
    return _build_report(checks, created_at=report.created_at)


def _call_bool(obj: Any, name: str, default: bool = False) -> bool:
    fn = getattr(obj, name, None)
    if not callable(fn):
        return default
    try:
        return bool(fn())
    except Exception:
        return False


def _js_ready(driver: Any) -> tuple[bool, str]:
    fn = getattr(driver, "applescript_js_enabled", None)
    if not callable(fn):
        return True, "browser JavaScript execution interface is available"
    try:
        ok, detail = fn()
        return bool(ok), str(detail)
    except Exception as exc:
        return False, str(exc)


def _build_report(checks: list[CheckResult], created_at: str | None = None) -> DoctorReport:
    overall = "READY" if all(check.ok for check in checks) else "NOT_READY"
    if created_at:
        return DoctorReport(status=overall, checks=checks, created_at=created_at)
    return DoctorReport(status=overall, checks=checks)


def _save_report_check(report: DoctorReport) -> CheckResult:
    path = last_doctor_path()
    try:
        save_json(path, {"platform": "liepin", **report.to_dict()})
    except OSError as exc:
        return CheckResult(
            "doctor_report_saved",
            False,
            f"Failed to save Liepin doctor report: {exc}",
            {"path": str(path), "error": str(exc)},
        )
    return CheckResult(
        "doctor_report_saved",
        True,
        "Liepin doctor report saved",
        {"path": str(path)},
    )


def _cloud_api_key_check() -> CheckResult:
    key = load_api_key()
    if key:
        return CheckResult(
            "cloud_api_key_configured",
            True,
            "Cloud API key is configured for Liepin rank/greet",
            {"key_prefix": key[:14] + "..."},
        )
    return CheckResult(
        "cloud_api_key_configured",
        False,
        "Cloud API key is required for Liepin rank/greet",
        {
            "error": "api_key_required",
            "hint": "Run `jobagent init --key <your_api_key>`. Register at https://agentmesh360.com/app/",
        },
    )
