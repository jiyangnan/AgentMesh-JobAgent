"""Stop a lost platform navigation instead of reopening a page in a loop."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from jobagent.infra import state, rounds
from jobagent.infra.browser_work import BrowserWorkError


class NavigationGuard:
    def __init__(self, profile_directory, platform, expected_url):
        self.path = Path(profile_directory) / "jobagent-delivery-navigation.json"
        self.platform = platform
        self.expected_url = expected_url
        self.chain = []
        self.blocked = None

    def observe(self, url):
        if not self.chain or self.chain[-1] != url:
            self.chain.append(url)
        expected = urlsplit(self.expected_url)
        seen_platform = any(urlsplit(item).hostname == expected.hostname for item in self.chain[:-1])
        if url == "about:blank" and seen_platform:
            self.blocked = {"platform": self.platform, "expected_url": self.expected_url,
                            "navigation_chain": self.chain[-12:], "observed_at": rounds.utc_now(),
                            "reason": "unexpected_blank_navigation", "automatic_retry_allowed": False}
            saved = state.load_json(self.path) or {}
            saved[self.platform] = self.blocked
            state.save_json(self.path, saved)

    def check(self):
        saved = self.blocked or (state.load_json(self.path) or {}).get(self.platform)
        if saved:
            exc = BrowserWorkError("playwright_navigation_paused", "The platform redirected to a blank page. The original task is preserved; automatic navigation retries are disabled.")
            exc.payload.update(request_preserved=True, requires_technical_recovery=True,
                               automatic_retry_allowed=False, navigation=saved,
                               next_suggested="jobagent work status")
            raise exc
