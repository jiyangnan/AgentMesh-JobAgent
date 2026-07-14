"""Focused tests for 51Job SPA collection readiness."""

import json

from jobagent.platforms.job51.collect import Job51ReadOnlyCollector


class FakeDriver:
    def __init__(self, states):
        self.states = list(states)
        self.last = self.states[-1]
        self.snapshot_calls = 0

    def open_url_in_new_tab(self, url, wait_seconds=0):
        return {"ok": True, "url": url, "title": "前程无忧"}

    def _exec_js(self, _script):
        self.snapshot_calls += 1
        state = self.states.pop(0) if self.states else self.last
        self.last = state
        return {"raw": json.dumps(state, ensure_ascii=False)}


def test_job51_collect_waits_past_javascript_placeholder(monkeypatch):
    monkeypatch.setattr("jobagent.platforms.job51.collect.time.sleep", lambda _seconds: None)
    driver = FakeDriver(
        [
            {
                "ok": True,
                "pageReady": False,
                "placeholder": True,
                "loginRequired": False,
                "cards": [],
                "bodySnippet": "We're sorry but 51job doesn't work properly without JavaScript enabled.",
            },
            {
                "ok": True,
                "pageReady": True,
                "placeholder": False,
                "loginRequired": False,
                "url": "https://we.51job.com/pc/search",
                "cards": [
                    {
                        "jobId": "J123",
                        "jobTitle": "数据产品负责人",
                        "salary": "3-5万",
                        "cityName": "北京",
                        "companyName": "示例科技有限公司",
                    }
                ],
            },
        ]
    )

    result = Job51ReadOnlyCollector(driver=driver).collect(
        query="数据产品负责人",
        city="北京",
        limit=20,
        wait_seconds=1,
        pages=1,
    )

    assert result.ok is True
    assert len(result.jobs) == 1
    assert driver.snapshot_calls == 2


def test_job51_collect_does_not_treat_placeholder_as_empty_success(monkeypatch):
    monkeypatch.setattr("jobagent.platforms.job51.collect.time.sleep", lambda _seconds: None)
    placeholder = {
        "ok": True,
        "pageReady": False,
        "placeholder": True,
        "loginRequired": False,
        "cards": [],
        "bodySnippet": "We're sorry but 51job doesn't work properly without JavaScript enabled.",
    }
    driver = FakeDriver([placeholder])

    result = Job51ReadOnlyCollector(driver=driver).collect(
        query="数据产品负责人",
        city="北京",
        limit=20,
        wait_seconds=1,
        pages=1,
    )

    assert result.ok is False
    assert result.error == "job51_page_not_ready"
    assert result.jobs == []


def test_job51_collect_waits_for_delayed_cards_after_filter_shell_mounts(monkeypatch):
    monkeypatch.setattr("jobagent.platforms.job51.collect.time.sleep", lambda _seconds: None)
    empty_shell = {
        "ok": True,
        "pageReady": False,
        "appMounted": True,
        "placeholder": False,
        "loginRequired": False,
        "loginPromptPresent": False,
        "candidateCount": 0,
        "url": "https://we.51job.com/pc/search?keyword=AI",
        "bodySnippet": "工作地点 综合排序",
        "cards": [],
    }
    driver = FakeDriver(
        [
            empty_shell,
            empty_shell,
            {
                **empty_shell,
                "pageReady": True,
                "candidateCount": 1,
                "cards": [
                    {
                        "jobId": "J456",
                        "jobTitle": "AI产品负责人",
                        "salary": "4-6万",
                        "cityName": "上海",
                        "companyName": "人工智能有限公司",
                    }
                ],
            },
        ]
    )

    result = Job51ReadOnlyCollector(driver=driver).collect(
        query="AI产品负责人",
        city="上海",
        limit=20,
        wait_seconds=1,
        pages=1,
    )

    assert result.ok is True
    assert [job.name for job in result.jobs] == ["AI产品负责人"]
    assert driver.snapshot_calls == 3
