"""Connect the packaged Playwright-compatible runtime to dedicated Chrome.

No extra agent plugin, bundled browser download, credential export, or private
platform API is used. Stopping this driver disconnects without closing Chrome.
Patchright supplies the Playwright API for the visible-browser runtime.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from jobagent.drivers.boss.chrome_manager import ChromeInstanceManager
from jobagent.infra.browser_work import BrowserWorkError


class PlaywrightRuntime:
    DRIVER_MODULE = "patchright.sync_api"

    def __init__(self, session):
        self.session = session
        self.driver = None
        self.browser = None
        self.context = None
        self.page = None
        self.profile_verified = False
        self.guard = None
        self._navigation_observers = {}

    def __enter__(self):
        try:
            manager = ChromeInstanceManager(port=self.session["port"],
                                            user_data_dir=Path(self.session["profile_directory"]))
            connected, _ = manager._try_connect_existing()
            if not connected:
                raise BrowserWorkError("playwright_browser_unavailable",
                    "The original JobAgent Chrome is unavailable. Diagnose that browser before resuming; delivery never starts a replacement profile.",
                    automatic_retry_allowed=False)
            from importlib import import_module
            self.driver = import_module(self.DRIVER_MODULE).sync_playwright().start()
            self.browser = self.driver.chromium.connect_over_cdp(
                f"http://127.0.0.1:{manager.port}", timeout=15000, no_defaults=True)
            if len(self.browser.contexts) != 1:
                raise BrowserWorkError("playwright_context_ambiguous", "The dedicated browser must have one verified profile context.")
            self.context = self.browser.contexts[0]
            self.context.set_default_timeout(12000)
            check = self.context.new_page()
            try:
                check.goto("chrome://version", wait_until="domcontentloaded", timeout=12000)
                profile = check.locator("#profile_path").inner_text().strip()
                # Chrome reports .../<user-data-dir>/Default (or Profile N).
                if Path(profile).resolve().parent != Path(self.session["profile_directory"]).resolve():
                    raise BrowserWorkError("playwright_profile_mismatch", "The debugging port belongs to another Chrome profile; no platform action was performed.")
                self.profile_verified = True
            finally:
                check.close()
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        for page, handler in self._navigation_observers.values():
            page.remove_listener("framenavigated", handler)
        self._navigation_observers.clear()
        if self.driver:
            self.driver.stop()
            self.driver = None

    def _watch_navigation(self, page, guard):
        previous = self._navigation_observers.pop(id(page), None)
        if previous:
            previous[0].remove_listener("framenavigated", previous[1])

        def observe(frame):
            # A CDP response can deliver a navigation event while job_page is
            # still finding its next target. Never consult mutable self.page.
            if frame == page.main_frame:
                guard.observe(frame.url)

        page.on("framenavigated", observe)
        self._navigation_observers[id(page)] = (page, observe)

    @staticmethod
    def same_route(actual, expected):
        left, right = urlsplit(actual), urlsplit(expected)
        if (left.scheme, left.hostname, left.path.rstrip("/")) != (right.scheme, right.hostname, right.path.rstrip("/")):
            return False
        if right.path.rstrip("/") == "/pc/jobdetail":
            expected_id = parse_qs(right.query).get("jobId", [])
            return len(expected_id) == 1 and parse_qs(left.query).get("jobId", []) == expected_id
        return True

    def job_page(self, work):
        from jobagent.application.native_work import _official
        platform = work["binding"]["platform"]
        url = work["task"].get("job", {}).get("url") or work["task"].get("url")
        if not _official(platform, url):
            raise BrowserWorkError("playwright_url_untrusted", "Only the exact approved official HTTPS job route can be opened.")
        from jobagent.drivers.playwright_guard import NavigationGuard
        from jobagent.infra import state
        self.guard = NavigationGuard(self.session["profile_directory"], platform, url)
        self.guard.check()
        path = state.STATE_DIR / "playwright_tabs.json"
        tabs = state.load_json(path) or {}
        pages = [p for p in self.context.pages if not p.is_closed()]
        # A login inspection may visit the same URL as the owned resume tab.
        # Never claim that auxiliary target as the job tab: the next history
        # navigation would otherwise replace the very job being inspected.
        auxiliary_targets = {target for key, target in tabs.get(self.session["id"], {}).items()
                             if key.endswith(":account")}
        pages = [p for p in pages if self.target_id(p) not in auxiliary_targets]
        self.page = next((p for p in pages if self.same_route(p.url, url)), None)
        if self.page is None:
            target = tabs.get(self.session["id"], {}).get(platform)
            self.page = next((p for p in pages if self.target_id(p) == target), None) if target else None
            if self.page is None:
                self.page = self.context.new_page()
        self._watch_navigation(self.page, self.guard)
        self.page.bring_to_front()
        if not self.same_route(self.page.url, url):
            from jobagent.application.playwright_work import pace
            pace(self.session)
            self.page.goto(url, wait_until="domcontentloaded", timeout=25000)
        self.guard.observe(self.page.url)
        self.guard.check()
        tabs.setdefault(self.session["id"], {})[platform] = self.target_id(self.page)
        state.save_json(path, tabs)
        if not _official(platform, self.page.url):
            raise BrowserWorkError("playwright_redirect_untrusted", "The approved job redirected away from its official HTTPS origin.")
        return self.page

    def target_id(self, page=None):
        connection = self.context.new_cdp_session(page or self.page)
        try:
            return connection.send("Target.getTargetInfo")["targetInfo"]["targetId"]
        finally:
            connection.detach()

    def auxiliary_page(self, work, url, *, purpose, refresh=False):
        """Reuse one owned read-only account page without losing the job dialog."""
        from jobagent.application.native_work import _official
        from jobagent.drivers.playwright_guard import NavigationGuard
        from jobagent.infra import state
        platform = work["binding"]["platform"]
        if purpose not in {"history", "resume"} or not _official(platform, url):
            raise BrowserWorkError("playwright_auxiliary_untrusted", "Use an observed official account history/resume route.")
        guard = NavigationGuard(self.session["profile_directory"], platform, url)
        guard.check()
        path = state.STATE_DIR / "playwright_tabs.json"
        tabs = state.load_json(path) or {}
        # Both read-only purposes share the second owned tab.
        key = platform + ":account"
        target = tabs.get(self.session["id"], {}).get(key)
        page = next((p for p in self.context.pages if not p.is_closed()
                     and target and self.target_id(p) == target), None)
        if page is self.page:
            # Repair an older aliased tab mapping without moving the active
            # job page or changing any work/receipt state.
            page = None
        if page is None:
            page = self.context.new_page()
            tabs.setdefault(self.session["id"], {})[key] = self.target_id(page)
            state.save_json(path, tabs)
        self._watch_navigation(page, guard)
        page.bring_to_front()
        if refresh or not self.same_route(page.url, url):
            from jobagent.application.playwright_work import pace
            pace(self.session)
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
        guard.observe(page.url)
        guard.check()
        return page, guard

    def evidence(self, work):
        return {"source": "playwright_dom_observation", "profile_directory": self.session["profile_directory"],
                "profile_verified": self.profile_verified, "page_url": self.page.url if self.page else "",
                "page_target_id": self.target_id() if self.page and not self.page.is_closed() else None,
                "observation": "JobAgent observed the dedicated Chrome page through its built-in Playwright runtime."}

    def adapter(self, work):
        if work["binding"]["platform"] == "boss":
            from jobagent.platforms.boss.playwright_delivery import BossDelivery
            return BossDelivery(self)
        if work["binding"]["platform"] == "51job":
            from jobagent.platforms.job51.playwright_delivery import Job51Delivery
            return Job51Delivery(self)
        if work["binding"]["platform"] == "zhilian":
            from jobagent.platforms.zhilian.playwright_delivery import ZhilianDelivery
            return ZhilianDelivery(self)
        if work["binding"]["platform"] == "liepin":
            from jobagent.platforms.liepin.playwright_delivery import LiepinDelivery
            return LiepinDelivery(self)
        raise BrowserWorkError("playwright_platform_not_validated",
            "This platform's packaged executor has not passed validation; preserve the authorized task without issuing an action.")
