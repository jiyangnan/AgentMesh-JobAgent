"""Local browser acceptance: temporary profile, no website/account access."""
import os
import sys
import socket
import subprocess
from pathlib import Path
import pytest
from jobagent.drivers.boss.chrome_manager import find_chrome, _wait_for_cdp_port
from jobagent.drivers.playwright_runtime import PlaywrightRuntime
from jobagent.infra.browser_work import BrowserWorkError


def test_query_job_identity_cannot_reuse_another_51job_page():
    url = "https://we.51job.com/pc/jobdetail?jobId=123"
    assert PlaywrightRuntime.same_route(url + "&trace=one", url + "&trace=two")
    assert not PlaywrightRuntime.same_route(url, url.replace("123", "456"))
    assert not PlaywrightRuntime.same_route(url, "https://we.51job.com/pc/jobdetail")
    assert not PlaywrightRuntime.same_route(url + "&jobId=456", url)


def test_unvalidated_platform_does_not_fall_through_to_missing_import():
    runtime = PlaywrightRuntime({})
    with pytest.raises(BrowserWorkError, match="not passed validation"):
        runtime.adapter({"binding": {"platform": "unsupported"}})


@pytest.fixture
def chrome(tmp_path):
    if os.environ.get("JOBAGENT_RUN_BROWSER_TESTS") != "1":
        pytest.skip("Explicit opt-in required for isolated browser processes")
    executable = find_chrome()
    if not executable:
        pytest.skip("Local Chrome required for isolated DOM acceptance")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    profile = tmp_path / "isolated-profile"
    args = [executable, "--headless=new", "--no-first-run", "--no-default-browser-check", "--enable-logging=stderr",
            "--disable-background-networking", "--disable-sync", "--disable-extensions",
            "--password-store=basic", f"--remote-debugging-port={port}", f"--user-data-dir={profile}"]
    # This flag is confined to fresh temporary test profiles. Never add it
    # to the product browser: existing encrypted cookies must stay intact.
    if sys.platform == "darwin":
        args.append("--use-mock-keychain")
    if sys.platform == "linux" and Path("/.dockerenv").exists():
        args.append("--no-sandbox")  # Disposable, offline fixture container only.
    args.append("about:blank")
    log = tmp_path / "chrome-startup.log"
    with log.open("wb") as output:
        process = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT)
    try:
        assert _wait_for_cdp_port(port), (
            f"Isolated Chrome did not open CDP: executable={executable!r}, exit={process.poll()}; "
            + log.read_text(encoding="utf-8", errors="replace")[-8000:]
        )
        yield {"id": "isolated", "profile_directory": str(profile.resolve()), "port": port}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_dom_action_and_disconnect_preserve_browser(chrome):
    with PlaywrightRuntime(chrome) as runtime:
        page = runtime.context.pages[0]
        page.set_content('<textarea></textarea><button onclick="document.querySelector(\'output\').textContent=document.querySelector(\'textarea\').value">发送</button><output></output>')
        page.locator("textarea").fill("exact authorized fixture text")
        page.get_by_role("button", name="发送", exact=True).click()
        assert page.locator("output").inner_text() == "exact authorized fixture text"
        target = runtime.target_id(page)
    with PlaywrightRuntime(chrome) as runtime:
        page = runtime.context.pages[0]
        assert runtime.target_id(page) == target
        assert page.locator("output").inner_text() == "exact authorized fixture text"


def test_wrong_profile_cannot_attach(chrome, tmp_path):
    with pytest.raises(BrowserWorkError, match="another Chrome profile"):
        with PlaywrightRuntime({**chrome, "profile_directory": str(tmp_path / "wrong-profile")}):
            pytest.fail("Wrong profile connected")


@pytest.mark.parametrize("legacy_alias", [False, True])
def test_session_resume_inspection_cannot_claim_owned_account_tab(chrome, monkeypatch, tmp_path, legacy_alias):
    from jobagent.infra import state
    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("jobagent.application.playwright_work.pace", lambda session: None)
    resume = "https://www.51job.com/resume/center"
    job = "https://jobs.51job.com/shenzhen/1000.html"
    history = "https://i.51job.com/userset/my_apply.php"
    work = {"binding": {"platform": "51job"}, "task": {"url": resume}}
    with PlaywrightRuntime(chrome) as runtime:
        runtime.context.route("**/*", lambda route: route.fulfill(body="<p>offline fixture</p>"))
        account, _ = runtime.auxiliary_page(work, resume, purpose="resume")
        account_id = runtime.target_id(account)
        if legacy_alias:
            path = state.STATE_DIR / "playwright_tabs.json"
            tabs = state.load_json(path)
            tabs[chrome["id"]]["51job"] = account_id
            state.save_json(path, tabs)
        main = runtime.job_page(work)
        main_id = runtime.target_id(main)
        assert main_id != account_id
    with PlaywrightRuntime(chrome) as runtime:
        runtime.context.route("**/*", lambda route: route.fulfill(body="<p>offline fixture</p>"))
        main = runtime.job_page({**work, "task": {"url": job}})
        assert runtime.target_id(main) == main_id
        account, _ = runtime.auxiliary_page(work, history, purpose="history")
        assert runtime.target_id(account) == account_id
        assert main.url == job and account.url == history


def test_legacy_auxiliary_alias_does_not_navigate_active_job(chrome, monkeypatch, tmp_path):
    from jobagent.infra import state
    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("jobagent.application.playwright_work.pace", lambda session: None)
    job = "https://jobs.51job.com/shenzhen/1000.html"
    work = {"binding": {"platform": "51job"}, "task": {"url": job}}
    with PlaywrightRuntime(chrome) as runtime:
        runtime.context.route("**/*", lambda route: route.fulfill(body="<p>offline fixture</p>"))
        main = runtime.job_page(work)
        path = state.STATE_DIR / "playwright_tabs.json"
        tabs = state.load_json(path)
        tabs[chrome["id"]]["51job:account"] = runtime.target_id(main)
        state.save_json(path, tabs)
        account, _ = runtime.auxiliary_page(work, "https://i.51job.com/userset/my_apply.php", purpose="history")
        assert runtime.target_id(account) != runtime.target_id(main)
        assert main.url == job


def test_navigation_during_target_switch_uses_its_own_page_and_latest_guard(chrome, monkeypatch, tmp_path):
    from jobagent.infra import state
    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("jobagent.application.playwright_work.pace", lambda session: None)
    with PlaywrightRuntime(chrome) as runtime:
        runtime.context.route("**/*", lambda route: route.fulfill(body="<p>offline job fixture</p>"))
        first_url = "https://www.zhaopin.com/jobdetail/CC1.htm"
        second_url = "https://www.zhaopin.com/jobdetail/CC2.htm"
        work = {"binding": {"platform": "zhilian"}, "task": {"url": first_url}}
        page = runtime.job_page(work)
        first_guard = runtime.guard
        # A pending main-frame event may arrive during target enumeration.
        runtime.page = None
        page.goto(second_url)
        assert first_guard.chain[-1] == second_url
        runtime.job_page({**work, "task": {"url": second_url}})
        latest_guard = runtime.guard
        assert latest_guard is not first_guard
        before = list(first_guard.chain)
        page.goto("about:blank")
        assert first_guard.chain == before  # No accumulated stale listeners.
        with pytest.raises(BrowserWorkError, match="blank page"):
            latest_guard.check()
    assert not runtime._navigation_observers
