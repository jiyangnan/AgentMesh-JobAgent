"""Headed Liepin recovery gate using only local, network-isolated fixtures.

Run with the repository source on PYTHONPATH (under xvfb-run on Linux). Set
LIEPIN_GATE_CHROME to override Chrome executable discovery. A temporary profile
and an OS-selected CDP port are mandatory; this script cannot attach to an
existing browser. No account, resume, round, cookies, or recruiting action is
used. Every page request is either fulfilled with a fixture or rejected.
"""

from __future__ import annotations

import base64
import html
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import websocket

from jobagent.drivers.boss.cdp_client import CDPClient
from jobagent.drivers.boss.cdp_driver import CDPBossDriver
from jobagent.platforms.liepin.collect import (
    LiepinReadOnlyCollector,
    build_liepin_city_route_search_url,
    build_liepin_search_url,
)


BOOT_URL = "https://www.liepin.com/"
CHALLENGE_URL = "https://safe.liepin.com/captcha?fixture=collection-recovery"
QUERY = "Fixture AI"


def _document(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
        '<link rel="icon" href="data:,">'
        f"<title>{html.escape(title)}</title>"
        "<style>body{font:18px sans-serif;margin:32px}input{width:300px;height:36px}"
        ".job-card-pc-container{padding:20px;border:1px solid #777;width:500px}"
        ".ant-pagination{display:flex;gap:12px;margin-top:20px}"
        ".ant-pagination>span{display:block;min-width:32px;height:32px}</style>"
        f"</head><body>{body}</body></html>"
    )


def _search_document(mode: str) -> str:
    body = f'<input name="key" value="{html.escape(QUERY, quote=True)}">'
    if mode in {"modern_city", "nationwide_city"}:
        city = "深圳" if mode == "modern_city" else "全国"
        if city == "深圳":
            body += '<div>已选条件：</div><div>深圳</div><div>清空筛选条件</div>'
        body += f'<div>订阅</div><div>「{QUERY}·{city}」</div>'
    if mode == "empty":
        body += "<p>非常抱歉！暂时没有合适的职位</p>"
    elif mode in {"modern", "modern_city", "nationwide_city"}:
        for index in range(40):
            route = "a" if index % 2 else "job"
            body += (
                '<div class="jobCardPcContainer"><div class="jobDetailBox">'
                f'<a href="/{route}/modern-{index}.shtml">'
                f'<div>Fixture AI 产品经理 {index}</div><div>【</div><div>深圳-南山区</div>'
                '<div>】</div><div>20-30k</div><div>3-5年</div><div>本科</div></a>'
                f'<div>Fixture Company {index}</div><div>互联网1000人以上</div></div>'
                '<div>Fixture HR·招聘经理</div></div>'
            )
    else:
        body += (
            '<article class="job-card-pc-container" data-job-id="fixture-1">'
            '<a href="/job/fixture-1.shtml">Fixture AI 产品经理</a>'
            "<p>20-30k</p><p>【深圳-南山区】</p><p>3-5年经验</p>"
            "<p>本科</p><p>Fixture Company</p></article>"
            '<div class="ant-pagination">'
            '<span class="ant-pagination-item-active">1</span>'
            '<span class="ant-pagination-next ant-pagination-disabled" '
            'aria-disabled="true">下一页</span></div>'
        )
    title = "深圳招聘信息" if mode in {"modern_city", "nationwide_city"} else "猎聘离线搜索测试"
    page = _document(title, body)
    if mode in {"modern_city", "nationwide_city"}:
        page = page.replace('</head>', '<meta name="location" content="city=深圳"></head>')
    return page


class FixtureInterceptor:
    """Own a second CDP connection so production CDP event handling is unchanged."""

    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=0.5)
        self.mode = "challenge"
        self.request_count = 0
        self.fulfilled_count = 0
        self.rejected_count = 0
        self.search_pages: list[int] = []
        self.errors: list[str] = []
        self._next_id = 1
        self._stop = threading.Event()
        self.ws.send(json.dumps({
            "id": self._next_id, "method": "Fetch.enable",
            "params": {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]},
        }))
        while True:
            response = json.loads(self.ws.recv())
            if response.get("id") == self._next_id:
                if response.get("error"):
                    raise RuntimeError("fixture_interception_enable_failed")
                break
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _send(self, method: str, params: dict[str, Any]) -> None:
        # Never use Fetch.continueRequest: no request can reach the site.
        self._next_id += 1
        self.ws.send(json.dumps({"id": self._next_id, "method": method, "params": params}))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                message = json.loads(self.ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            except Exception as exc:
                if not self._stop.is_set():
                    self.errors.append(type(exc).__name__)
                return
            if message.get("error"):
                self.errors.append("fixture_interception_command_failed")
            if message.get("method") != "Fetch.requestPaused":
                continue
            try:
                self._respond(message["params"])
            except Exception as exc:
                self.errors.append(type(exc).__name__)
                return

    def _respond(self, event: dict[str, Any]) -> None:
        self.request_count += 1
        request = event["request"]
        request_id = event["requestId"]
        parsed = urlsplit(request["url"])
        document = None
        redirect = False
        trusted = (
            request.get("method") == "GET"
            and event.get("resourceType") == "Document"
            and parsed.scheme == "https"
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
        )
        if trusted and request["url"] == BOOT_URL:
            document = _document(
                "Fixture entry", "<p>Isolated fixture entry</p>"
                '<img src="https://blocked.invalid/gate-isolation-probe.png" alt="">',
            )
        elif trusted and request["url"] == CHALLENGE_URL:
            document = _document("安全验证", "<h1>安全验证</h1><p>本地测试页面</p>")
        elif trusted and parsed.hostname == "www.liepin.com" and parsed.path in {"/zhaopin/", "/city-sz/zhaopin/"}:
            params = parse_qs(parsed.query)
            if params.get("key") == [QUERY] and params.get("currentPage") in (["0"], ["1"]):
                self.search_pages.append(int(params["currentPage"][0]) + 1)
                redirect = self.mode == "challenge"
                document = "" if redirect else _search_document(self.mode)
        if document is None:
            self.rejected_count += 1
            self._send("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"})
            return
        self.fulfilled_count += 1
        headers = [{"name": "Content-Type", "value": "text/html; charset=utf-8"},
                   {"name": "Cache-Control", "value": "no-store"}]
        if redirect:
            headers.append({"name": "Location", "value": CHALLENGE_URL})
        self._send("Fetch.fulfillRequest", {
            "requestId": request_id,
            "responseCode": 302 if redirect else 200,
            "responseHeaders": headers,
            "body": base64.b64encode(document.encode()).decode(),
        })

    def close(self) -> None:
        self._stop.set()
        self.ws.close()
        self.thread.join(timeout=3)


class ObservedCDPClient(CDPClient):
    def __init__(self) -> None:
        super().__init__()
        self.navigation_count = 0
        self.input_count = 0

    def send(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
        if method == "Page.navigate":
            self.navigation_count += 1
        if method.startswith("Input."):
            self.input_count += 1
        return super().send(method, params, timeout)


def _local_json(port: int, path: str) -> Any:
    # Bypass environment proxy configuration only for this owned loopback CDP.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
        return json.loads(response.read())


def _owned_target(profile: Path, process: subprocess.Popen) -> tuple[int, str]:
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("isolated_chrome_exited_during_startup")
        try:
            lines = (profile / "DevToolsActivePort").read_text().splitlines()
            port = int(lines[0])
            if port == 19222 or not 1024 <= port <= 65535:
                raise RuntimeError("unsafe_cdp_port")
            version = _local_json(port, "/json/version")
            if urlsplit(version["webSocketDebuggerUrl"]).path != lines[1]:
                raise RuntimeError("isolated_chrome_identity_mismatch")
            pages = [target for target in _local_json(port, "/json/list") if target.get("type") == "page"]
            if len(pages) == 1 and pages[0].get("url") == "about:blank":
                ws_url = pages[0]["webSocketDebuggerUrl"]
                if urlsplit(ws_url).port != port:
                    raise RuntimeError("isolated_target_port_mismatch")
                return port, ws_url
        except (OSError, KeyError, ValueError, IndexError):
            pass
        time.sleep(0.1)
    raise RuntimeError("isolated_chrome_startup_timeout")


def _controller_boot(cdp: CDPClient) -> None:
    """Reset only the disposable fixture target between independent cases."""
    cdp.send("Page.navigate", {"url": BOOT_URL})
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = cdp.evaluate(
            "JSON.stringify({url:location.href,ready:document.readyState})"
        ).get("result", {}).get("value", "{}")
        info = json.loads(value)
        if info.get("url") == BOOT_URL and info.get("ready") == "complete":
            return
        time.sleep(0.05)
    raise RuntimeError("fixture_entry_did_not_load")


def _check_challenge(result: dict[str, Any]) -> None:
    assert result.get("error") == "liepin_verification_required", result
    assert result.get("requires_user_action") is True, result
    assert str(result.get("user_prompt") or "").strip(), result
    assert result.get("url") == "https://safe.liepin.com/captcha", result


def _run_cases(cdp: ObservedCDPClient, fixtures: FixtureInterceptor, root: Path) -> dict[str, Any]:
    _controller_boot(cdp)
    # Skip the constructor: it deliberately consults the user's platform registry.
    # The connected production methods operate only on the owned fixture target.
    # There is no manager fallback; any reconnect attempt fails closed.
    driver = CDPBossDriver.__new__(CDPBossDriver)
    driver.cdp = cdp
    driver.platform = "liepin"
    driver.current_platform = "liepin"
    driver.track_round = False

    search_url = build_liepin_search_url(QUERY)
    before = cdp.navigation_count
    started = time.monotonic()
    result = driver.open_url_in_new_tab(search_url, wait_seconds=1)
    elapsed = time.monotonic() - started
    _check_challenge(result)
    assert cdp.navigation_count - before == 1
    assert elapsed < 10, elapsed

    before = cdp.navigation_count
    started = time.monotonic()
    result = driver.open_url_in_new_tab(search_url, wait_seconds=1)
    _check_challenge(result)
    assert cdp.navigation_count == before
    assert time.monotonic() - started < 5

    checks: dict[str, Any] = {
        "search_redirect_stops_immediately": True,
        "challenge_elapsed_seconds": round(elapsed, 3),
        "existing_challenge_zero_navigation": True,
    }
    for mode, expected_reason, expected_jobs in (("empty", "no_results", 0), ("last", "last_page", 1)):
        fixtures.mode = mode
        _controller_boot(cdp)
        fixtures.search_pages.clear()
        before = cdp.navigation_count
        collector = LiepinReadOnlyCollector(driver, city_cache_path=root / "fixture-city-cache.json")
        result = collector.collect(QUERY, pages=2, page_delay=0, wait_seconds=1)
        assert result.ok, result.to_payload(include_snapshot=True)
        assert result.snapshot.get("terminationReason") == expected_reason, result.snapshot
        assert result.snapshot.get("paginationExhausted") is True, result.snapshot
        assert len(result.jobs) == expected_jobs, result.snapshot
        assert fixtures.search_pages == [1], fixtures.search_pages
        assert cdp.navigation_count - before == 1
        checks[f"{mode}_result_ends_query_without_page_two"] = True
    fixtures.mode = "modern"
    _controller_boot(cdp)
    fixtures.search_pages.clear()
    before = cdp.navigation_count
    collector = LiepinReadOnlyCollector(driver, city_cache_path=root / "fixture-city-cache.json")
    result = collector.collect(QUERY, limit=30, pages=2, page_delay=0, wait_seconds=1)
    assert result.ok and len(result.jobs) == 30, result.to_payload(include_snapshot=True)
    assert len({job.raw_data["jobId"] for job in result.jobs}) == 30
    assert all(job.company.startswith("Fixture Company ") and job.city == "深圳"
               and job.area == "南山区" for job in result.jobs), result.snapshot
    assert any("/a/" in job.url for job in result.jobs)
    assert fixtures.search_pages == [1] and cdp.navigation_count - before == 1
    checks["modern_cards_collect_both_routes_and_company_without_page_two"] = True
    fixtures.mode = "modern_city"
    _controller_boot(cdp)
    fixtures.search_pages.clear()
    before = cdp.navigation_count
    collector = LiepinReadOnlyCollector(driver, city_cache_path=root / "modern-city-cache.json")
    result = collector.collect(QUERY, city="深圳", limit=30, pages=2, page_delay=0, wait_seconds=1)
    assert result.ok and len(result.jobs) == 30, result.to_payload(include_snapshot=True)
    assert result.snapshot["cityVerification"]["verified"] is True
    assert collector.city_resolver.lookup("深圳") == ("050090", "verified_cache")
    assert fixtures.search_pages == [1] and cdp.navigation_count - before == 1
    checks["modern_city_filter_collects_once_without_directory_fallback"] = True

    fixtures.mode = "nationwide_city"
    _controller_boot(cdp)
    city_route = "https://www.liepin.com/city-sz/"
    opened = driver.open_url_in_new_tab(build_liepin_city_route_search_url(city_route, QUERY), wait_seconds=1)
    assert opened.get("ok"), opened
    evidence = collector._extract_snapshot()["cityEvidence"]
    assert evidence["visibleCity"] == "全国", evidence
    verified = collector.city_resolver.verify_route_evidence(evidence, city="深圳", query=QUERY,
        expected_route=city_route, previous_url=BOOT_URL)
    assert verified["verified"] is False and "city" in verified["conflicts"], verified
    checks["nationwide_filter_rejected_despite_city_route_title_and_metadata"] = True
    assert cdp.input_count == 0, "fixture gate unexpectedly used input actions"
    assert fixtures.rejected_count >= 1, "non-fixture denial probe was not intercepted"
    assert not fixtures.errors, fixtures.errors
    return {"ok": True, "headed": True, "isolated_profile": True,
            "external_requests_continued": 0, "input_actions": cdp.input_count,
            "fixture_requests_fulfilled": fixtures.fulfilled_count,
            "non_fixture_requests_rejected": fixtures.rejected_count, "checks": checks}


def main() -> None:
    chrome = (
        os.environ.get("LIEPIN_GATE_CHROME")
        or shutil.which("google-chrome") or shutil.which("chromium")
        or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    if not Path(chrome).is_file():
        raise RuntimeError("headed_chrome_unavailable")
    with tempfile.TemporaryDirectory(prefix="jobagent-liepin-gate-") as directory:
        root = Path(directory)
        profile = root / "chrome-profile"
        # The dead proxy and disabled name resolution protect background/new-target
        # traffic even before Fetch interception is enabled. No headless flag.
        process = subprocess.Popen([
            chrome, f"--user-data-dir={profile}", "--remote-debugging-port=0",
            "--remote-debugging-address=127.0.0.1", "--remote-allow-origins=*",
            "--proxy-server=http://127.0.0.1:9", "--proxy-bypass-list=<-loopback>",
            "--host-resolver-rules=MAP * ~NOTFOUND", "--disable-background-networking",
            "--disable-extensions", "--disable-component-extensions-with-background-pages",
            "--disable-component-update", "--disable-sync", "--disable-default-apps",
            "--no-first-run", "--no-default-browser-check", "--metrics-recording-only",
            "--password-store=basic", "--disable-quic", "--window-size=1000,800", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        fixtures = None
        cdp = ObservedCDPClient()
        try:
            port, ws_url = _owned_target(profile, process)
            fixtures = FixtureInterceptor(ws_url)
            cdp.connect(ws_url)
            cdp.send("Network.enable")
            cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
            report = _run_cases(cdp, fixtures, root)
            report["dedicated_cdp_port"] = port
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        finally:
            cdp.disconnect()
            if fixtures is not None:
                fixtures.close()
            # Only the subprocess group created above is eligible for cleanup.
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)


if __name__ == "__main__":
    main()
