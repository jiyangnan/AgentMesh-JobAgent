from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlencode

import pytest

from jobagent.platforms.liepin.collect import (
    LiepinCollectResult,
    LiepinReadOnlyCollector,
    _query_termination_reason,
    _snapshot_failure,
)
from jobagent.platforms.liepin.selectors import build_liepin_snapshot_script


QUERY = "AI 产品经理"


def result_url(page: int = 1, query: str = QUERY) -> str:
    return "https://www.liepin.com/zhaopin/?" + urlencode(
        {"key": query, "city": "050090", "dq": "050090", "currentPage": page - 1}
    )


def snapshot(*, no_results: bool = False, page: int = 1) -> dict:
    return {
        "ok": True,
        "url": result_url(page),
        "requestedUrl": result_url(page),
        "cityVerification": {"verified": True},
        "cityEvidence": {
            "controlCity": "深圳", "controlCode": "050090",
            "metaCity": "深圳", "titleCity": "深圳",
            "inputQuery": QUERY, "urlQuery": QUERY,
            "jobCardCount": 0 if no_results else 1,
            "noResults": no_results, "resultSurface": True,
        },
        "cards": [] if no_results else [{
            "jobId": f"example-{page}", "jobTitle": QUERY, "companyName": "Example",
            "cityName": "深圳", "jobUrl": f"https://www.liepin.com/job/example-{page}.shtml",
        }],
    }


class FakeCDP:
    def __init__(self):
        self.sent = []

    def send(self, method, params):
        self.sent.append((method, params))


class Driver:
    def __init__(self, *, pages=None, state=None, open_result=None):
        self.cdp = FakeCDP()
        self.clicks = []
        self.opened = []
        self.scripts = []
        self.pages = pages or [snapshot()]
        self.state = state or {"href": result_url(), "body": "非常抱歉，暂时没有合适的职位"}
        self.open_result = open_result

    def _click_at(self, x, y):
        self.clicks.append((x, y))

    def open_url_in_new_tab(self, url, wait_seconds=8):
        self.opened.append(url)
        return self.open_result if self.open_result is not None else {"ok": True, "url": url}

    def _exec_js(self, script):
        self.scripts.append(script)
        if "deleteContentBackward" in script:
            return {"ok": True}
        if "selectorVersion" in script:
            return copy.deepcopy(self.pages[min(len(self.opened) - 1, len(self.pages) - 1)])
        return copy.deepcopy(self.state)


@pytest.mark.parametrize("body", ["暂无相关职位", "非常抱歉，暂时没有合适的职位", ""])
def test_matching_exact_query_does_not_resubmit_no_results(body):
    driver = Driver(state={
        "href": result_url(), "body": body,
        "input": {"x": 1, "y": 2}, "button": {"x": 3, "y": 4},
    })
    LiepinReadOnlyCollector(driver=driver)._submit_search_if_query_missing(QUERY)
    assert driver.clicks == []
    assert driver.cdp.sent == []
    assert len(driver.scripts) == 1


def test_query_substring_in_url_or_body_does_not_suppress_needed_search(monkeypatch):
    monkeypatch.setattr("jobagent.platforms.liepin.collect.time.sleep", lambda _seconds: None)
    driver = Driver(state={
        "href": result_url(query=QUERY + "助理"), "body": QUERY,
        "input": {"x": 1, "y": 2}, "button": {"x": 3, "y": 4},
    })
    LiepinReadOnlyCollector(driver=driver)._submit_search_if_query_missing(QUERY)
    assert driver.clicks == [(1, 2), (3, 4)]
    assert ("Input.insertText", {"text": QUERY}) in driver.cdp.sent


def search_state(url="https://www.liepin.com/zhaopin/", *, x=3, y=4):
    return {"ok": True, "href": url, "input": {"x": 1, "y": 2},
            "button": {"x": x, "y": y}}


def test_challenge_after_typing_stops_before_search_click(monkeypatch, tmp_path):
    monkeypatch.setattr("jobagent.platforms.liepin.collect.time.sleep", lambda _seconds: None)
    driver = Driver()
    collector = LiepinReadOnlyCollector(driver=driver, city_cache_path=tmp_path / "cities.json")
    states = iter([search_state(), search_state("https://safe.liepin.com/verify")])
    monkeypatch.setattr(collector, "_extract_search_state", lambda: next(states))
    result = collector.collect(QUERY, city="深圳", pages=3, page_delay=0)
    assert result.error == "liepin_verification_required"
    assert result.to_payload()["requires_user_action"] is True
    assert result.to_payload()["user_prompt"]
    assert driver.clicks == [(1, 2)]  # Input click before the challenge only.
    assert len(driver.opened) == 1
    assert len(driver.scripts) == 1  # Clear input; no later snapshot or recovery action.


@pytest.mark.parametrize("after_typing", [
    {}, {"ok": False, "error": "read_failed"},
    search_state("https://example.com/zhaopin/"),
    search_state("https://www.liepin.com/login/"),
    search_state("https://www.liepin.com/city-beijing/zhaopin/"),
    search_state("https://www.liepin.com:invalid/zhaopin/"),
    {**search_state(), "button": None},
    {**search_state(), "button": {"x": "not-a-coordinate", "y": 4}},
])
def test_unknown_or_changed_page_after_typing_does_not_click(monkeypatch, after_typing):
    monkeypatch.setattr("jobagent.platforms.liepin.collect.time.sleep", lambda _seconds: None)
    driver = Driver()
    collector = LiepinReadOnlyCollector(driver=driver)
    states = iter([search_state(), after_typing])
    monkeypatch.setattr(collector, "_extract_search_state", lambda: next(states))
    result = collector._submit_search_if_query_missing(QUERY)
    assert result["error"] == "liepin_search_state_unknown"
    assert driver.clicks == [(1, 2)]


def test_search_uses_freshly_observed_control_coordinates(monkeypatch):
    monkeypatch.setattr("jobagent.platforms.liepin.collect.time.sleep", lambda _seconds: None)
    driver = Driver()
    collector = LiepinReadOnlyCollector(driver=driver)
    states = iter([search_state(), search_state(x=30, y=40)])
    monkeypatch.setattr(collector, "_extract_search_state", lambda: next(states))
    assert collector._submit_search_if_query_missing(QUERY) is None
    assert driver.clicks == [(1, 2), (30, 40)]


def test_query_committed_while_typing_does_not_submit_again(monkeypatch):
    monkeypatch.setattr("jobagent.platforms.liepin.collect.time.sleep", lambda _seconds: None)
    driver = Driver()
    collector = LiepinReadOnlyCollector(driver=driver)
    states = iter([search_state(), search_state(result_url())])
    monkeypatch.setattr(collector, "_extract_search_state", lambda: next(states))
    assert collector._submit_search_if_query_missing(QUERY) is None
    assert driver.clicks == [(1, 2)]


def test_verified_no_results_ends_current_query_after_one_page(tmp_path: Path):
    driver = Driver(pages=[snapshot(no_results=True)])
    result = LiepinReadOnlyCollector(driver=driver, city_cache_path=tmp_path / "cities.json").collect(
        QUERY, city="深圳", pages=4, page_delay=0,
    )
    assert result.ok is True
    assert result.jobs == []
    assert result.snapshot["cityVerification"]["verified"] is True
    assert result.snapshot["terminationReason"] == "no_results"
    assert result.snapshot["paginationExhausted"] is True
    assert len(driver.opened) == 1
    assert driver.clicks == []


def test_empty_parsed_cards_without_no_result_evidence_do_not_end_query(tmp_path: Path):
    first = snapshot()
    first["cards"] = []  # A selector/parser mismatch, not an empty search.
    driver = Driver(pages=[first, snapshot(page=2)])
    result = LiepinReadOnlyCollector(driver=driver, city_cache_path=tmp_path / "cities.json").collect(
        QUERY, city="深圳", pages=2, page_delay=0,
    )
    assert result.ok is True
    assert len(driver.opened) == 2
    assert len(result.jobs) == 1
    assert "paginationExhausted" not in result.snapshot


def test_empty_dom_without_no_result_evidence_is_not_terminal():
    empty = snapshot(no_results=True)
    empty["cityEvidence"].update(noResults=False, resultSurface=False)
    assert _query_termination_reason(empty, query=QUERY, city="深圳", page=1) == ""


def last_page_snapshot(page=1):
    last = snapshot(page=page)
    last["paginationEvidence"] = {
        "source": "visible_pagination", "currentPage": page,
        "nextControlPresent": True, "nextDisabled": True, "nextEnabled": False,
    }
    return last


def test_verified_last_page_stops_and_combines_terminal_metadata(tmp_path: Path):
    driver = Driver(pages=[snapshot(), last_page_snapshot(page=2)])
    result = LiepinReadOnlyCollector(driver=driver, city_cache_path=tmp_path / "cities.json").collect(
        QUERY, city="深圳", pages=4, page_delay=0,
    )
    assert result.ok is True
    assert len(driver.opened) == 2
    assert len(result.jobs) == 2
    assert result.snapshot["paginationExhausted"] is True
    assert result.snapshot["terminationReason"] == "last_page"
    assert result.snapshot["pages"][-1]["terminationReason"] == "last_page"


@pytest.mark.parametrize("changed", [
    {"currentPage": 2}, {"nextControlPresent": False}, {"nextDisabled": False},
    {"nextEnabled": True}, {"source": "url_only"},
])
def test_unverified_or_conflicting_pagination_is_not_terminal(changed):
    last = last_page_snapshot()
    last["paginationEvidence"].update(changed)
    assert _query_termination_reason(last, query=QUERY, city="深圳", page=1) == ""


@pytest.mark.parametrize("url", [
    "https://www.liepin.com/city-shenzhen/",
    "https://example.com/zhaopin/",
    "https://www.liepin.com.evil.test/zhaopin/",
    "https://www.liepin.com/city-beijing/zhaopin/",
    "https://www.liepin.com:invalid/zhaopin/",
    result_url(query="另一个查询"),
    result_url().replace("dq=050090", "dq=010"),
])
@pytest.mark.parametrize("terminal", ["no_results", "last_page"])
def test_untrusted_or_stale_result_route_cannot_end_query(url, terminal):
    data = snapshot(no_results=True) if terminal == "no_results" else last_page_snapshot()
    data["url"] = url
    assert _query_termination_reason(data, query=QUERY, city="深圳", page=1) == ""


@pytest.mark.parametrize("terminal", ["no_results", "last_page"])
def test_wrong_readable_query_or_unverified_city_cannot_end_query(terminal):
    data = snapshot(no_results=True) if terminal == "no_results" else last_page_snapshot()
    data["cityEvidence"]["inputQuery"] = "另一个查询"
    assert _query_termination_reason(data, query=QUERY, city="深圳", page=1) == ""
    data["cityEvidence"]["inputQuery"] = QUERY
    data["cityVerification"]["verified"] = False
    assert _query_termination_reason(data, query=QUERY, city="深圳", page=1) == ""


def test_stale_url_page_does_not_prove_last_page():
    data = last_page_snapshot(page=2)
    data["url"] = result_url(page=1)
    assert _query_termination_reason(data, query=QUERY, city="深圳", page=2) == ""


def test_driver_verification_stop_preserves_prompt_without_more_browser_actions(tmp_path):
    driver = Driver(open_result={
        "ok": False, "error": "liepin_verification_required",
        "url": "https://safe.liepin.com/verify", "requires_user_action": True,
        "user_prompt": "请在当前猎聘标签页完成验证。",
    })
    result = LiepinReadOnlyCollector(driver=driver, city_cache_path=tmp_path / "cities.json").collect(
        QUERY, city="深圳", pages=3, page_delay=0,
    )
    payload = result.to_payload()
    assert payload["error"] == "liepin_verification_required"
    assert payload["requires_user_action"] is True
    assert payload["user_prompt"] == driver.open_result["user_prompt"]
    assert driver.scripts == []
    assert driver.clicks == []
    assert len(driver.opened) == 1


def test_verification_page_seen_before_search_is_not_clicked(tmp_path):
    driver = Driver(state={
        "href": "https://safe.liepin.com/verify", "body": "请完成验证码",
        "input": {"x": 1, "y": 2}, "button": {"x": 3, "y": 4},
    })
    result = LiepinReadOnlyCollector(driver=driver, city_cache_path=tmp_path / "cities.json").collect(
        QUERY, city="深圳", pages=3, page_delay=0,
    )
    assert result.error == "liepin_verification_required"
    assert result.to_payload()["user_prompt"]
    assert len(driver.scripts) == 1
    assert driver.clicks == []
    assert driver.cdp.sent == []


def test_verification_snapshot_is_not_misclassified_as_login():
    data = {"ok": True, "url": "https://safe.liepin.com/verify", "loginPromptPresent": True}
    assert _snapshot_failure(data) == "liepin_verification_required"
    result = LiepinCollectResult(QUERY, "深圳", data["url"], [], snapshot=data,
                               ok=False, error=_snapshot_failure(data))
    assert result.to_payload()["requires_user_action"] is True
    assert result.to_payload()["user_prompt"]
    assert result.to_payload().get("next_suggested") != "jobagent liepin login"


@pytest.mark.parametrize("url", [
    "https://safe.liepin.com.evil.test/verify", "https://example.com/?next=https://safe.liepin.com/verify",
    "https://safe.liepin.com@evil.test/verify", "http://safe.liepin.com/verify",
])
def test_lookalike_urls_are_not_trusted_verification_pages(url):
    assert _snapshot_failure({"ok": True, "url": url}) != "liepin_verification_required"


def test_city_search_fallback_stops_at_driver_verification(tmp_path, monkeypatch):
    driver = Driver(open_result={"ok": False, "error": "liepin_verification_required",
                                "user_prompt": "请完成当前页面的验证。"})
    collector = LiepinReadOnlyCollector(driver=driver, city_cache_path=tmp_path / "cities.json")
    monkeypatch.setattr(collector, "_extract_city_search_evidence", lambda: {})
    monkeypatch.setattr(collector, "_extract_city_route", lambda _city: {})
    result = collector.collect(QUERY, city="郑州", pages=3, page_delay=0)
    assert result.error == "liepin_verification_required"
    assert result.to_payload()["user_prompt"] == "请完成当前页面的验证。"
    assert len(driver.opened) == 1
    assert not any("citylist" in url for url in driver.opened)


def unknown_page_payload(error="liepin_page_state_unknown"):
    return {
        "ok": False, "error": error, "retryable": False, "requires_user_action": True,
        "user_prompt": "猎聘当前页面状态暂时无法确认，已暂停操作并保留当前页面。"
                       "请先检查浏览器诊断结果，再继续本轮搜索。",
        "next_suggested": "jobagent browser diagnose --platform liepin",
    }


def assert_interruption_preserved(result, original):
    payload = result.to_payload(include_snapshot=True)
    assert result.ok is False
    for key in ("error", "retryable", "requires_user_action", "user_prompt", "next_suggested"):
        assert payload[key] == original[key]


@pytest.mark.parametrize("error", ["liepin_page_state_unknown", "liepin_search_state_unknown",
                                  "liepin_manual_check_required"])
def test_driver_intervention_payload_is_preserved_without_second_open(tmp_path, error):
    original = unknown_page_payload(error)
    driver = Driver(open_result=original)
    result = LiepinReadOnlyCollector(driver=driver, city_cache_path=tmp_path / "cities.json").collect(
        QUERY, city="深圳", pages=3, page_delay=0,
    )
    assert_interruption_preserved(result, original)
    assert result.snapshot["open_result"] == original
    assert len(driver.opened) == 1
    assert driver.scripts == []
    assert driver.clicks == []


def test_actual_cdp_unknown_payload_survives_collector_handoff(tmp_path, monkeypatch):
    from jobagent.drivers.boss.cdp_driver import CDPBossDriver

    class UnreadableCDP:
        def evaluate(self, *_args, **_kwargs):
            raise RuntimeError("offline test: page URL unavailable")

        def send(self, *_args, **_kwargs):
            raise AssertionError("Unknown page must not receive navigation or input")

    boundary_driver = object.__new__(CDPBossDriver)
    boundary_driver.cdp = UnreadableCDP()
    monkeypatch.setattr(boundary_driver, "_ensure_connected_for_url", lambda _url: "")
    original = boundary_driver.open_url_in_new_tab(result_url())
    assert original["error"] == "liepin_page_state_unknown"
    driver = Driver(open_result=original)
    result = LiepinReadOnlyCollector(driver=driver, city_cache_path=tmp_path / "cities.json").collect(
        QUERY, city="深圳", pages=3, page_delay=0,
    )
    assert_interruption_preserved(result, original)
    assert result.snapshot["open_result"] == original
    assert len(driver.opened) == 1
    assert driver.scripts == []


@pytest.mark.parametrize("error", ["liepin_page_state_unknown", "liepin_manual_check_required"])
def test_city_search_unknown_intervention_does_not_fall_back_to_directory(tmp_path, monkeypatch, error):
    original = unknown_page_payload(error)
    driver = Driver(open_result=original)
    collector = LiepinReadOnlyCollector(driver=driver, city_cache_path=tmp_path / "cities.json")
    monkeypatch.setattr(collector, "_extract_city_search_evidence", lambda: {})
    monkeypatch.setattr(collector, "_extract_city_route", lambda _city: {})
    result = collector.collect(QUERY, city="郑州", pages=3, page_delay=0)
    assert_interruption_preserved(result, original)
    assert len(driver.opened) == 1
    assert not any("citylist" in url for url in driver.opened)
    assert driver.scripts == []


@pytest.mark.parametrize("boundary", ["city_evidence", "city_route"])
def test_city_read_intervention_stops_before_any_navigation(tmp_path, monkeypatch, boundary):
    original = unknown_page_payload()
    driver = Driver()
    collector = LiepinReadOnlyCollector(driver=driver, city_cache_path=tmp_path / "cities.json")
    monkeypatch.setattr(collector, "_extract_city_search_evidence",
                        lambda: original if boundary == "city_evidence" else {})
    monkeypatch.setattr(collector, "_extract_city_route", lambda _city: original)
    result = collector.collect(QUERY, city="郑州", pages=3, page_delay=0)
    assert_interruption_preserved(result, original)
    assert driver.opened == []
    assert driver.clicks == []


def test_replacement_city_open_preserves_unknown_intervention(tmp_path, monkeypatch):
    original = unknown_page_payload()
    stale = snapshot()
    stale["cityEvidence"]["controlCity"] = "北京"
    driver = Driver(pages=[stale])

    def open_page(url, wait_seconds=8):
        driver.opened.append(url)
        return {"ok": True, "url": url} if len(driver.opened) == 1 else original

    driver.open_url_in_new_tab = open_page
    collector = LiepinReadOnlyCollector(driver=driver, city_cache_path=tmp_path / "cities.json")
    monkeypatch.setattr(collector, "_resolve_city", lambda *args, **kwargs: {
        "ok": True, "city": "深圳", "code": "050090", "source": "verified_test_route",
    })
    result = collector.collect(QUERY, city="深圳", pages=3, page_delay=0)
    assert_interruption_preserved(result, original)
    assert len(driver.opened) == 2  # Initial page plus interrupted replacement only.
    assert driver.clicks == []


def test_verified_city_route_open_preserves_unknown_intervention(tmp_path, monkeypatch):
    original = unknown_page_payload()
    driver = Driver(open_result=original)
    collector = LiepinReadOnlyCollector(driver=driver, city_cache_path=tmp_path / "cities.json")
    monkeypatch.setattr(collector, "_extract_city_search_evidence", lambda: {
        "url": "https://www.liepin.com/zhaopin/",
    })
    monkeypatch.setattr(collector, "_extract_city_route", lambda _city: {
        "ok": True, "route": "https://www.liepin.com/city-zhengzhou/",
    })
    result = collector.collect(QUERY, city="郑州", pages=3, page_delay=0)
    assert_interruption_preserved(result, original)
    assert len(driver.opened) == 1
    assert driver.scripts == []


@pytest.mark.parametrize(("body", "no_results"), [
    ("暂无相关职位", True), ("非常抱歉，暂时没有合适的职位", True),
    ("非常抱歉，页面加载失败", False), ("", False),
])
def test_snapshot_javascript_requires_explicit_empty_result_text(body, no_results):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable for offline DOM-script test")
    harness = """
      global.location = {href: 'https://www.liepin.com/zhaopin/', origin: 'https://www.liepin.com'};
      global.document = {title: '', body: {innerText: BODY},
        querySelector: () => null, querySelectorAll: () => []};
      global.window = {getComputedStyle: () => ({display: 'block', visibility: 'visible', opacity: 1})};
    """.replace("BODY", json.dumps(body, ensure_ascii=False))
    completed = subprocess.run(
        [node, "-e", harness + "console.log(" + build_liepin_snapshot_script() + ");"],
        text=True, capture_output=True, check=True,
    )
    data = json.loads(completed.stdout)
    assert data["cityEvidence"]["noResults"] is no_results
    assert data["paginationEvidence"]["nextControlPresent"] is False


@pytest.mark.parametrize(("disabled", "hidden", "expected_present", "expected_disabled"), [
    (True, False, True, True), (False, False, True, False),
    (True, True, False, False),
])
def test_snapshot_javascript_extracts_visible_next_page_state(
    disabled, hidden, expected_present, expected_disabled,
):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable for offline DOM-script test")
    harness = """
      const node = (text, attrs = {}) => ({
        innerText: text, textContent: text, className: '',
        getAttribute: name => attrs[name] || null,
        getBoundingClientRect: () => ({width: 20, height: 20}),
        querySelectorAll: () => [],
      });
      const next = node('下一页', {'aria-disabled': DISABLED ? 'true' : 'false'});
      next.hidden = HIDDEN;
      const active = node('2');
      const root = node('1 2 下一页');
      root.querySelectorAll = selector => selector.includes('pagination-next') ? [next] : [active];
      global.location = {href: 'https://www.liepin.com/zhaopin/?currentPage=1', origin: 'https://www.liepin.com'};
      global.document = {title: '', body: {innerText: ''}, querySelector: () => null,
        querySelectorAll: selector => selector.startsWith('.ant-pagination,') ? [root] : []};
      global.window = {getComputedStyle: el => ({display: el.hidden ? 'none' : 'block',
                                               visibility: 'visible', opacity: 1})};
    """.replace("DISABLED", json.dumps(disabled)).replace("HIDDEN", json.dumps(hidden))
    completed = subprocess.run(
        [node, "-e", harness + "console.log(" + build_liepin_snapshot_script() + ");"],
        text=True, capture_output=True, check=True,
    )
    evidence = json.loads(completed.stdout)["paginationEvidence"]
    assert evidence["source"] == "visible_pagination"
    assert evidence["currentPage"] == 2
    assert evidence["nextControlPresent"] is expected_present
    assert evidence["nextDisabled"] is expected_disabled
    assert evidence["nextEnabled"] is (not disabled and not hidden)
