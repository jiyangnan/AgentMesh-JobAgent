import pytest
from jobagent.drivers import playwright_runtime as runtime
from jobagent.infra.browser_work import BrowserWorkError


def test_missing_delivery_browser_never_launches_or_resets_a_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime.ChromeInstanceManager, "_try_connect_existing", lambda _: (False, None))
    monkeypatch.setattr(runtime.ChromeInstanceManager, "ensure_running", lambda _: pytest.fail("Launched Chrome"))
    with pytest.raises(BrowserWorkError) as error:
        with runtime.PlaywrightRuntime({"port": 1234, "profile_directory": str(tmp_path)}):
            pytest.fail("Connected to a missing browser")
    assert error.value.payload["automatic_retry_allowed"] is False
    assert list(tmp_path.iterdir()) == []
