"""Official history completeness and incremental refresh using offline DOM."""
from types import SimpleNamespace

import pytest

from tests.test_playwright_runtime import chrome
from jobagent.drivers.playwright_runtime import PlaywrightRuntime
from jobagent.infra import state
from jobagent.infra.browser_work import BrowserWorkError
from jobagent.platforms.liepin.playwright_history import read_history


def render(page, ids, total=None):
    rows = ''.join(f'''<div class="jobCardPcContainer"><a data-nick="job-detail-job-info"
        href="https://www.liepin.com/a/{i}.shtml"><span class="jobTitleBox"><span class="ellipsis-1">岗位 {i}</span></span></a>
        <span class="companyName">测试公司</span></div>''' for i in ids)
    page.set_content(f'''<span class="header-quick-menu-username">测试账号</span>
        <p>我的投递 {len(ids) if total is None else total}</p><div class="record-list-box">{rows}</div>
        <li class="ant-c-pagination-next" aria-disabled="true"></li>''')


def test_history_refresh_merges_only_when_official_total_is_complete(chrome, monkeypatch, tmp_path):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr("jobagent.application.playwright_work.pace", lambda session: None)
    work = {"binding": {"account_ref": "test-account", "round_id": "test-round"}}
    with PlaywrightRuntime(chrome) as runtime:
        page = runtime.context.pages[0]
        runtime.page = page
        refreshes = []
        def auxiliary(*args, **kwargs):
            refreshes.append(kwargs.get("refresh"))
            return page, SimpleNamespace(check=lambda: None)
        monkeypatch.setattr(runtime, "auxiliary_page", auxiliary)
        render(page, [100, 101])
        initial = read_history(runtime, work, "测试账号")
        assert initial["complete"] and initial["total"] == 2
        assert refreshes == [True]
        assert read_history(runtime, work, "测试账号") == initial
        assert refreshes == [True]
        render(page, [102, 100], total=3)
        updated = read_history(runtime, work, "测试账号", refresh=True)
        assert set(updated["entries"]) == {"100", "101", "102"}
        assert updated["total"] == 3
        # A removal changes the count. Full current rows replace, not merge,
        # the earlier index so deleted entries cannot prove current delivery.
        render(page, [102, 100])
        replaced = read_history(runtime, work, "测试账号", refresh=True)
        assert set(replaced["entries"]) == {"100", "102"}


def test_incomplete_history_cannot_prove_no_prior_application(chrome, monkeypatch, tmp_path):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr("jobagent.application.playwright_work.pace", lambda session: None)
    work = {"binding": {"account_ref": "test-account", "round_id": "test-round"}}
    with PlaywrightRuntime(chrome) as runtime:
        page = runtime.context.pages[0]
        runtime.page = page
        monkeypatch.setattr(runtime, "auxiliary_page", lambda *args, **kwargs: (page, SimpleNamespace(check=lambda: None)))
        render(page, [100], total=2)
        with pytest.raises(BrowserWorkError, match="official total"):
            read_history(runtime, work, "测试账号")
        assert not list((tmp_path / "playwright_history").glob("*.json"))
