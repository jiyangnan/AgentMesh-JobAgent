"""Undisclosed clients keep exact signed job and observed recruiter bindings."""
import copy

import pytest

from jobagent.application import native_work as native
from jobagent.infra import browser_work as store
from tests.test_native_work import env, observation, submit


def start(env):
    env.choose("liepin")
    env.jobs[0].update(id="12345678", url="https://www.liepin.com/a/12345678.shtml",
                       company="某互联网公司", salary="30-40k", area="深圳")
    return native.begin(env.start("liepin")["work"]["work_id"])["work"]


def evidence(work, **changes):
    return observation(work, **{"company": None, "company_visibility": "not_disclosed",
        "recruiter_name": "测试顾问", "recruiter_company": "测试人才服务有限公司",
        "detail_salary": "30-40k", "detail_area": "深圳", **changes})


def test_anonymous_full_chain_preserves_authorization_and_binds_recruiter(env):
    work = start(env)
    original_binding = copy.deepcopy(work["binding"])
    stored_task = copy.deepcopy(store.get_work(work["work_id"], native._binding())["task"])
    assert "anonymous_company_identity" in work["task"]
    assert store.get_work(work["work_id"], native._binding())["task"] == stored_task
    while True:
        response = submit(env, work, evidence(work))
        if not response.get("work"):
            break
        assert response["work"]["binding"] == original_binding
        assert response["work"]["task"]["job"]["company"] == "某互联网公司"
        assert response["work"]["task"]["anonymous_recruiter_identity"]["recruiter_name"] == "测试顾问"
        assert response["work"]["task"]["result_example"]["evidence"]["company"] is None
        assert response["work"]["task"]["unresolved_result_example"]["evidence"]["company"] is None
        work = native.begin(response["work"]["work_id"])["work"]
    assert response["summary"]["resume_submitted"] == 1
    assert response["summary"]["greeting_sent"] == 1


@pytest.mark.parametrize("changes", [
    {"company": "Other disclosed company"}, {"company_visibility": "unknown"},
    {"recruiter_name": ""}, {"recruiter_company": ""},
    {"detail_salary": "20-30k"}, {"detail_area": "北京"},
    {"page_url": "https://www.liepin.com/a/99999999.shtml"},
    {"title": "Other job"}, {"job_id": "99999999"},
])
def test_incomplete_or_conflicting_initial_evidence_cannot_issue_action(env, changes):
    work = start(env)
    result = evidence(work)
    result["evidence"].update(changes)
    with pytest.raises(store.BrowserWorkError):
        submit(env, work, result)
    assert not any(w["side_effect"] for w in store.list_account_work("account-test"))


@pytest.mark.parametrize("change", [
    {"recruiter_name": "Different recruiter"}, {"recruiter_company": "Different agency"},
    {"conversation_job_verified": False}, {"company": "某互联网公司"},
])
def test_conversation_cannot_switch_recruiter_or_bypass_anchor(env, change):
    work = start(env)
    response = submit(env, work, evidence(work))
    work = native.begin(response["work"]["work_id"])["work"]
    result = evidence(work)
    result["evidence"].update(change)
    with pytest.raises(store.BrowserWorkError):
        submit(env, work, result)
    assert store.get_work(work["work_id"], native._binding())["state"] == "intent_recorded"


@pytest.mark.parametrize("change", [
    {"company": "Named client company"}, {"url": "https://www.liepin.com/job/12345678.shtml"},
])
def test_exception_does_not_apply_to_disclosed_or_direct_employer_jobs(env, change):
    env.choose("liepin")
    env.jobs[0].update(id="12345678", url="https://www.liepin.com/a/12345678.shtml",
                       company="某互联网公司", salary="30-40k", area="深圳")
    env.jobs[0].update(change)
    work = native.begin(env.start("liepin")["work"]["work_id"])["work"]
    with pytest.raises(store.BrowserWorkError):
        submit(env, work, evidence(work))


def test_preexisting_action_cannot_gain_anonymous_identity_without_preflight(env):
    work = start(env)
    response = submit(env, work, observation(work))
    work = native.begin(response["work"]["work_id"])["work"]
    assert "anonymous_recruiter_identity" not in work["task"]
    with pytest.raises(store.BrowserWorkError):
        submit(env, work, evidence(work))
