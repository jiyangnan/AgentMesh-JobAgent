import pytest
from jobagent.drivers.playwright_guard import NavigationGuard
from jobagent.infra.browser_work import BrowserWorkError


def test_unexpected_blank_persists_stop_across_processes(tmp_path):
    url = "https://www.liepin.com/job/123.shtml"
    guard = NavigationGuard(tmp_path, "liepin", url)
    guard.observe("about:blank")  # Initial new tab is harmless.
    guard.check()
    guard.observe(url)
    guard.observe("https://wow.liepin.com/test/index.html")
    guard.observe("about:blank")
    restarted = NavigationGuard(tmp_path, "liepin", "https://www.liepin.com/job/456.shtml")
    with pytest.raises(BrowserWorkError) as error:
        restarted.check()
    assert error.value.payload["automatic_retry_allowed"] is False
    assert error.value.payload["request_preserved"] is True
    assert error.value.payload["navigation"]["expected_url"] == url
    NavigationGuard(tmp_path, "boss", "https://www.zhipin.com/").check()
