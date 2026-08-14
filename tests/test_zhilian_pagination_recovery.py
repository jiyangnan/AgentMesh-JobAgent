"""Regression coverage for bounded Zhilian query pagination."""

from __future__ import annotations

from contextlib import nullcontext

import pytest

from jobagent.domain.models import Job
from jobagent.platforms import discovery
from jobagent.platforms.zhilian.collect import (
    ZhilianReadOnlyCollector,
    _query_page_exhausted,
)
from jobagent.platforms.zhilian.selectors import (
    ZHILIAN_SELECTOR_VERSION,
    build_zhilian_pagination_script,
    build_zhilian_snapshot_script,
)


def _login_verification() -> dict:
    return {
        "valid": True,
        "source": "recent_login_check",
        "platform": "zhilian",
        "round_id": "round-1",
        "browser_session_id": "local-cdp-19222",
        "age_seconds": 10,
    }


def _snapshot(
    query: str,
    *,
    job_id: str | None = None,
    no_results: bool = False,
    available_pages: list[int] | None = None,
    has_next_page: bool | None = None,
) -> dict:
    cards = []
    if job_id:
        cards.append(
            {
                "positionId": job_id,
                "jobTitle": query,
                "companyName": f"{job_id} Company",
                "cityName": "",
                "jobUrl": f"https://www.zhaopin.com/jobdetail/{job_id}.htm",
            }
        )
    payload = {
        "ok": True,
        "url": "https://www.zhaopin.com/jobs?jl=dynamic&kw=01300K004004338VHKHKTEG",
        "title": "热门职位招聘 2026年热门职位招聘信息-智联招聘",
        "readyState": "complete",
        "sessionState": "page_ready",
        "loginRequired": False,
        "searchPageEvidence": [
            "search_route",
            "search_input",
            "no_results" if no_results else "job_surface",
        ],
        "searchKeyword": query,
        "searchKeywordVisible": True,
        "candidateCount": len(cards),
        "jobSurfaceCount": len(cards),
        "jobActionCount": len(cards),
        "noResults": no_results,
        "cards": cards,
    }
    if available_pages is not None:
        payload.update(
            {
                "paginationDetected": True,
                "availablePages": available_pages,
                "currentPage": available_pages[0] if available_pages else 1,
                "hasNextPage": bool(has_next_page),
            }
        )
    return payload


class _PagingDriver:
    def __init__(self, query: str, snapshots: list[dict], *, page_two_available: bool = False):
        self.query = query
        self.snapshots = list(snapshots)
        self.page_two_available = page_two_available
        self.pagination_calls: list[int] = []
        self.open_calls = 0

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5):
        self.open_calls += 1
        return {"ok": True, "url": url}

    def _click_at(self, _x, _y):
        return None

    def dismiss_javascript_dialog(self):
        return {"ok": True, "dismissed": False}

    def _exec_js(self, script: str):
        if "zhilian_keyword_search" in script:
            return {
                "ok": True,
                "mode": "zhilian_keyword_search",
                "observedValue": self.query,
                "readyState": "complete",
                "sessionState": "page_ready",
                "clickPoint": {"x": 120, "y": 80},
            }
        if "zhilian_search_transition" in script:
            return {
                "ok": True,
                "mode": "zhilian_search_transition",
                "url": "https://www.zhaopin.com/jobs?jl=dynamic&kw=01300K004004338VHKHKTEG",
                "title": "热门职位招聘 2026年热门职位招聘信息-智联招聘",
                "readyState": "complete",
                "observedKeyword": self.query,
                "searchPageEvidence": ["search_route", "search_input"],
            }
        if "zhilian_pagination" in script:
            self.pagination_calls.append(2)
            if self.page_two_available:
                return {
                    "ok": True,
                    "mode": "zhilian_pagination",
                    "page": 2,
                    "action": "select_page",
                    "clickPoint": {"x": 500, "y": 900},
                }
            return {
                "ok": False,
                "mode": "zhilian_pagination",
                "error": "zhilian_page_option_not_found",
                "page": 2,
            }
        if not self.snapshots:
            raise AssertionError("unexpected extra Zhilian snapshot")
        return self.snapshots.pop(0)


def test_explicit_empty_first_page_does_not_attempt_second_page(tmp_path):
    driver = _PagingDriver("产品经理", [_snapshot("产品经理", no_results=True)])

    result = ZhilianReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "cities.json",
        login_verification=_login_verification(),
    ).collect(query="产品经理", pages=2, wait_seconds=0, page_delay=0)

    assert result.ok is True
    assert result.jobs == []
    assert driver.pagination_calls == []
    assert driver.open_calls == 1


def test_verified_single_page_with_jobs_does_not_attempt_second_page(tmp_path):
    driver = _PagingDriver(
        "产品经理",
        [
            _snapshot(
                "产品经理",
                job_id="JOB-1",
                available_pages=[1],
                has_next_page=False,
            )
        ],
    )

    result = ZhilianReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "cities.json",
        login_verification=_login_verification(),
    ).collect(query="产品经理", pages=2, wait_seconds=0, page_delay=0)

    assert result.ok is True
    assert [job.name for job in result.jobs] == ["产品经理"]
    assert driver.pagination_calls == []
    assert driver.open_calls == 1


def test_verified_second_page_is_collected_when_available(tmp_path, monkeypatch):
    driver = _PagingDriver(
        "产品经理",
        [
            _snapshot(
                "产品经理",
                job_id="JOB-1",
                available_pages=[1, 2],
                has_next_page=True,
            ),
            _snapshot(
                "产品经理",
                job_id="JOB-2",
                available_pages=[1, 2],
                has_next_page=False,
            ),
        ],
        page_two_available=True,
    )
    monkeypatch.setattr("jobagent.platforms.zhilian.collect.time.sleep", lambda _seconds: None)

    result = ZhilianReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "cities.json",
        login_verification=_login_verification(),
    ).collect(query="产品经理", pages=2, wait_seconds=0, page_delay=0)

    assert result.ok is True
    assert [job.url for job in result.jobs] == [
        "https://www.zhaopin.com/jobdetail/JOB-1.htm",
        "https://www.zhaopin.com/jobdetail/JOB-2.htm",
    ]
    assert driver.pagination_calls == [2]


def test_round_robin_continues_after_first_query_is_explicitly_empty(monkeypatch):
    calls: list[tuple[str, int]] = []

    def collect_page(
        platform,
        query,
        page,
        _limit,
        _driver,
        _wait_seconds,
        _login_verification,
    ):
        assert platform == "zhilian"
        calls.append((query["keyword"], page))
        if page > 1:
            pytest.fail("an exhausted query must not request page 2")
        if query["keyword"] == "产品经理":
            return []
        return [
            Job(
                name="数据产品经理",
                salary="20-30K",
                company="Example",
                city="深圳",
                url="https://www.zhaopin.com/jobdetail/JOB-2.htm",
                platform="zhilian",
                raw_data={"positionId": "JOB-2"},
            )
        ]

    monkeypatch.setattr(discovery, "_collect_web_platform", collect_page)
    plan = {
        "platform": "zhilian",
        "candidate_limit": 100,
        "queries": [
            {"keyword": "产品经理", "city": "深圳", "page_limit": 2},
            {"keyword": "数据产品经理", "city": "深圳", "page_limit": 1},
        ],
    }

    candidates = discovery.collect_from_search_plan(
        plan,
        driver=object(),
        page_delay=0,
        login_verification=_login_verification(),
    )

    assert calls == [("产品经理", 1), ("数据产品经理", 1)]
    assert [candidate["id"] for candidate in candidates] == ["JOB-2"]


def test_search_plan_does_not_request_page_two_after_verified_single_page(tmp_path):
    driver = _PagingDriver(
        "产品经理",
        [
            _snapshot(
                "产品经理",
                job_id="JOB-1",
                available_pages=[1],
                has_next_page=False,
            )
        ],
    )
    plan = {
        "platform": "zhilian",
        "candidate_limit": 100,
        "queries": [
            {"keyword": "产品经理", "city": "", "page_limit": 2},
        ],
    }

    candidates = discovery.collect_from_search_plan(
        plan,
        driver=driver,
        wait_seconds=0,
        page_delay=0,
        login_verification=_login_verification(),
    )

    assert [candidate["id"] for candidate in candidates] == ["JOB-1"]
    assert driver.pagination_calls == []
    assert driver.open_calls == 1


def test_search_plan_collects_page_two_only_when_verified_available(tmp_path):
    driver = _PagingDriver(
        "产品经理",
        [
            _snapshot(
                "产品经理",
                job_id="JOB-1",
                available_pages=[1, 2],
                has_next_page=True,
            ),
            _snapshot(
                "产品经理",
                job_id="JOB-2",
                available_pages=[1, 2],
                has_next_page=False,
            ),
        ],
        page_two_available=True,
    )
    plan = {
        "platform": "zhilian",
        "candidate_limit": 100,
        "queries": [
            {"keyword": "产品经理", "city": "", "page_limit": 2},
        ],
    }

    candidates = discovery.collect_from_search_plan(
        plan,
        driver=driver,
        wait_seconds=0,
        page_delay=0,
        login_verification=_login_verification(),
    )

    assert [candidate["id"] for candidate in candidates] == ["JOB-1", "JOB-2"]
    assert driver.pagination_calls == [2]
    assert driver.open_calls == 2


def test_readable_query_validation_ignores_opaque_route_keyword(tmp_path):
    driver = _PagingDriver(
        "数据运营经理",
        [_snapshot("数据运营经理", job_id="JOB-READABLE")],
    )

    result = ZhilianReadOnlyCollector(
        driver=driver,
        city_cache_path=tmp_path / "cities.json",
        login_verification=_login_verification(),
    ).collect(query="数据运营经理", pages=1, wait_seconds=0, page_delay=0)

    assert result.ok is True
    assert result.query == "数据运营经理"
    assert result.jobs[0].name == "数据运营经理"


def test_snapshot_script_emits_real_no_result_and_pagination_evidence():
    script = build_zhilian_snapshot_script(limit=5)
    pagination_script = build_zhilian_pagination_script(2)

    assert "暂无.{0,12}职位" in script
    assert ".(0, 12)职位" not in script
    assert "paginationDetected" in script
    assert "availablePages" in script
    assert "hasNextPage" in script
    assert "exhausted" in pagination_script
    assert ZHILIAN_SELECTOR_VERSION == "2026-08-14.3"


def test_unparsed_pagination_container_does_not_claim_query_exhausted():
    assert (
        _query_page_exhausted(
            {
                "paginationDetected": True,
                "hasNextPage": False,
                "availablePages": [],
                "currentPage": None,
            },
            1,
        )
        is False
    )


def test_all_explicitly_empty_queries_return_terminal_no_charge_skip(monkeypatch):
    calls: list[tuple[str, int]] = []

    def collect_page(
        _platform,
        query,
        page,
        _limit,
        _driver,
        _wait_seconds,
        _login_verification,
    ):
        calls.append((query["keyword"], page))
        return []

    monkeypatch.setattr(discovery, "_collect_web_platform", collect_page)
    plan = {
        "platform": "zhilian",
        "candidate_limit": 100,
        "queries": [
            {"keyword": "产品经理", "city": "深圳", "page_limit": 2},
            {"keyword": "数据产品经理", "city": "深圳", "page_limit": 2},
        ],
    }

    with pytest.raises(discovery.CollectionError) as error:
        discovery.collect_from_search_plan(
            plan,
            driver=object(),
            page_delay=0,
            login_verification=_login_verification(),
        )

    assert calls == [("产品经理", 1), ("数据产品经理", 1)]
    assert error.value.code == "no_candidates"
    assert "confirm whether to skip zhilian" in error.value.user_prompt
    assert error.value.details == {
        "retryable": False,
        "requires_user_action": True,
        "search_exhausted": True,
        "next_suggested": "jobagent round skip --platform zhilian --confirm-skip",
    }


def test_non_retryable_page_error_never_suggests_same_discover(tmp_path, monkeypatch):
    import jobagent.application.discover as application
    from jobagent.infra import discovery_state

    profile = {
        "schema_version": 1,
        "preferences": {"targetRoles": [{"title": "产品经理"}]},
    }
    plan = {
        "platform": "zhilian",
        "discover_id": "dis_page_failure",
        "queries": [{"keyword": "产品经理", "city": "深圳", "page_limit": 2}],
    }
    monkeypatch.setattr(application, "profile_path", lambda: tmp_path / "profile.json")
    monkeypatch.setattr(application, "load_json", lambda _path: profile)
    monkeypatch.setattr(application, "require_compatible_profile", lambda _profile: None)
    monkeypatch.setattr(application, "load_pending_decision", lambda _platform: None)
    monkeypatch.setattr(discovery_state, "discoveries_dir", lambda: tmp_path)
    monkeypatch.setattr(
        application.rounds,
        "ensure_current_round",
        lambda: {
            "round_id": "round-1",
            "intent": {"status": "confirmed", "target_roles": ["产品经理"]},
        },
    )
    monkeypatch.setattr(
        "jobagent.infra.account_state.current_account_ref",
        lambda: "acct_account_a",
    )
    monkeypatch.setattr(application, "verify_search_plan", lambda value, **_kwargs: value)
    monkeypatch.setattr(application, "active_command", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(
        application,
        "PlatformSessionLock",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(application.cloud_client, "discovery_start", lambda **_kwargs: plan)
    monkeypatch.setattr(
        application,
        "collect_from_search_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            application.CollectionError(
                "zhilian_page_option_not_found",
                "Zhilian page option could not be verified",
            )
        ),
    )

    with pytest.raises(application.CollectionError) as error:
        application.run_discover("zhilian", page_delay=0)

    details = error.value.details
    assert details["retryable"] is False
    assert details["requires_user_action"] is True
    assert details["next_suggested"] == "jobagent browser diagnose --platform zhilian"
    assert details["next_suggested"] != "jobagent zhilian discover"
    assert details["request_preserved"] is True
    assert details["no_charge"] is True
    assert details["billing"]["additional_charge_on_retry"] is False
