"""Crash-safe collection repair keeps the old task until durable acknowledgement."""
from contextlib import nullcontext
from copy import deepcopy

import pytest

from jobagent.application import collection_repair as repair, bulk_decision
from jobagent.infra import state, cloud_client
from jobagent.infra.protocol import candidate_digest, ProtocolError
from jobagent.platforms.discovery import CollectionError


@pytest.fixture
def setup_repair(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    plan = {"discover_id":"dis_repairtest", "discovery_contract_version":2, "platform":"zhilian",
            "account_ref":"acct_test", "round_id":"round_test", "candidate_limit":30,
            "request_id":"zhilian:test", "queries":[{"keyword":"数据产品经理","city":"深圳","page_limit":2}]}
    old = [{"id":"old", "title":"职位描述", "url":"https://www.zhaopin.com/jobdetail/CC1.htm"}]
    pending = {"discover_id":plan["discover_id"],"request_id":plan["request_id"],"plan":plan,"jobs":old}
    jobs = [{"id":f"CC{i}","title":f"数据产品经理{i}",
             "url":f"https://www.zhaopin.com/jobdetail/CC{i}.htm","jd":"完整职责与任职要求。" * 20} for i in range(30)]
    old_bulk = {"discover_id":plan["discover_id"],"uploads":{"batch-000000":candidate_digest(old)},
                "details":{},"detail_batches":[{"ids":["old"],"target_size":7}]}
    state.save_json(bulk_decision._checkpoint_path(plan["discover_id"]), old_bulk)
    monkeypatch.setattr(repair, "current_account_ref", lambda:"acct_test")
    monkeypatch.setattr(repair.rounds, "ensure_current_round", lambda:{"round_id":"round_test"})
    monkeypatch.setattr(repair.rounds, "recent_platform_login_verification", lambda p:{})
    monkeypatch.setattr("jobagent.application.hybrid_discovery.collection_reader", lambda p:nullcontext(object()))
    collected, saved_pending = [], []
    def collect(plan, **kwargs):
        collected.append(plan)
        kwargs["checkpoint_callback"]({"candidates":jobs,"completed_pages":[[0,1],[0,2]]})
        return jobs
    monkeypatch.setattr(repair, "collect_from_search_plan", collect)
    monkeypatch.setattr(repair, "save_pending_decision", lambda *args,**kwargs:saved_pending.append(kwargs))
    return plan,pending,jobs,old_bulk,collected,saved_pending


def receipt(plan, pending, jobs):
    return {"ok":True,"discover_id":plan["discover_id"],"candidate_digest":candidate_digest(jobs),
            "candidate_count":len(jobs),"batch_ids":["repair-fixture"],"additional_credits":0,
            "expected_candidate_digest":candidate_digest(pending["jobs"])}


def test_lost_response_resumes_collected_pool_without_recollection_or_old_state_edit(setup_repair, monkeypatch):
    plan,pending,jobs,old_bulk,collected,saved_pending=setup_repair
    posts=[]
    def request(method, discover_id, suffix, body=None):
        if method=="GET": return {"status":"enrichment_required"}
        posts.append(deepcopy(body))
        if len(posts)==1: raise cloud_client.CloudError("response lost",code="network_error")
        return receipt(plan,pending,jobs)
    monkeypatch.setattr(bulk_decision,"request",request)
    with pytest.raises(cloud_client.CloudError):
        repair.repair_pending_collection("zhilian",pending,plan)
    assert state.load_json(bulk_decision._checkpoint_path(plan["discover_id"])) == old_bulk
    assert not saved_pending
    journal=state.load_json(repair.journal_path(plan["discover_id"]))
    assert journal["original_pending"]==pending and journal["original_bulk"]==old_bulk
    assert repair.repair_pending_collection("zhilian",pending,plan)==jobs
    assert len(collected)==1 and posts[0]==posts[1]
    after=state.load_json(bulk_decision._checkpoint_path(plan["discover_id"]))
    assert {k:v for k,v in after.items() if k!="collection_repair_receipt"}==old_bulk
    assert saved_pending[-1]["jobs"]==jobs and saved_pending[-1]["request_id"]==pending["request_id"]
    monkeypatch.setattr(bulk_decision,"request",lambda *a,**kw:pytest.fail("Repair acknowledgement replaces ordinary uploads"))
    assert bulk_decision.upload_candidates(plan,jobs,final=True)==["repair-fixture"]
    with pytest.raises(ProtocolError):
        bulk_decision.upload_candidates(plan,jobs[:-1],final=True)


def test_account_mismatch_cannot_start_or_resume_repair(setup_repair,monkeypatch):
    plan,pending,*_=setup_repair
    monkeypatch.setattr(repair,"current_account_ref",lambda:"other")
    monkeypatch.setattr(bulk_decision,"request",lambda *a,**kw:pytest.fail("No network for another account"))
    with pytest.raises(ProtocolError):
        repair.repair_pending_collection("zhilian",pending,plan)


@pytest.mark.parametrize("fault",["incomplete","missing_jd","completed"])
def test_repair_requires_complete_evidence_and_unfinished_task(setup_repair,monkeypatch,fault):
    plan,pending,jobs,old_bulk,_,saved_pending=setup_repair
    if fault=="incomplete":jobs.pop()
    if fault=="missing_jd":jobs[0].pop("jd")
    def request(method,*args,**kwargs):
        assert method=="GET"
        return {"status":"completed" if fault=="completed" else "enrichment_required"}
    monkeypatch.setattr(bulk_decision,"request",request)
    with pytest.raises(CollectionError):
        repair.repair_pending_collection("zhilian",pending,plan)
    assert not saved_pending
    assert state.load_json(bulk_decision._checkpoint_path(plan["discover_id"]))==old_bulk
