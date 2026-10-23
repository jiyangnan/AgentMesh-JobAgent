"""A staged 51Job repair cannot rewrite uploads, re-search on replay or rebill."""
from contextlib import nullcontext
from copy import deepcopy
import pytest
from jobagent.application import collection_repair as repair, bulk_decision
from jobagent.infra import state, cloud_client
from jobagent.infra.protocol import candidate_digest, ProtocolError
from jobagent.platforms.discovery import CollectionError


@pytest.fixture
def staged(tmp_path,monkeypatch):
    monkeypatch.setattr(state,'STATE_DIR',tmp_path)
    plan={'discover_id':'dis_staged','discovery_contract_version':2,'platform':'51job','account_ref':'acct_test',
          'round_id':'round_test','candidate_limit':30,'request_id':'51job:original',
          'queries':[{'keyword':'数据产品经理','city':'深圳','page_limit':2}]}
    old=[{'id':str(i),'title':'产品经理'+str(i),'company':'示例公司','area':'深圳','salary':'3-5万',
          'url':'https://we.51job.com/pc/search?keyword=data#jobId='+str(i)} for i in range(20)]
    collection={'plan':plan,'progress':{'candidates':old,'completed_pages':[[0,1]]}}
    jobs=[{**(old[i] if i<20 else {'id':str(i),'title':'产品经理'+str(i),'company':'示例公司','area':'深圳','salary':'3-5万'}),
           'url':'https://jobs.51job.com/shenzhen/'+str(i)+'.html','jd':'完整职责和任职条件。'*20} for i in range(30)]
    bulk={'discover_id':plan['discover_id'],'uploads':{'batch-000000':candidate_digest(old[:15])},'details':{}}
    state.save_json(bulk_decision._checkpoint_path(plan['discover_id']),bulk)
    monkeypatch.setattr(repair,'current_account_ref',lambda:'acct_test')
    monkeypatch.setattr(repair.rounds,'ensure_current_round',lambda:{'round_id':'round_test'})
    monkeypatch.setattr('jobagent.application.hybrid_discovery.collection_reader',lambda p:nullcontext(object()))
    reads=[]
    def collect(plan,**kw):
        reads.append(plan)
        kw['checkpoint_callback']({'candidates':jobs,'completed_pages':[[0,1],[0,2]]})
        return jobs
    monkeypatch.setattr(repair,'collect_from_search_plan',collect)
    return plan,collection,jobs,bulk,reads


def test_lost_ack_reuses_same_body_with_original_state_until_acceptance(staged,monkeypatch):
    plan,collection,jobs,bulk,reads=staged
    posts=[]
    def request(method,did,suffix,body=None):
        if method=='GET':return {'status':'collecting'}
        posts.append(deepcopy(body))
        if len(posts)==1:raise cloud_client.CloudError('lost',code='network_error')
        return {'ok':True,'discover_id':did,'candidate_digest':candidate_digest(jobs),'candidate_count':30,
                'batch_ids':['repair-test'],'additional_credits':0,'billing_status':'not_charged',
                'expected_candidate_digest':candidate_digest(collection['progress']['candidates'][:15])}
    monkeypatch.setattr(bulk_decision,'request',request)
    assert repair.needs_job51_staged_repair(collection)
    with pytest.raises(cloud_client.CloudError):repair.repair_staged_job51(collection,plan)
    assert state.load_json(bulk_decision._checkpoint_path(plan['discover_id']))==bulk
    journal=state.load_json(repair.journal_path(plan['discover_id']))
    assert journal['original_collection']==collection and journal['original_bulk']==bulk
    assert repair.repair_staged_job51(collection,plan)==jobs
    assert len(reads)==1 and posts[0]==posts[1]
    assert not repair.pending_repair({'plan':plan,'discover_id':plan['discover_id']})
    assert bulk_decision.upload_candidates(plan,jobs,final=True)==['repair-test']
    with pytest.raises(ProtocolError):bulk_decision.upload_candidates(plan,jobs[:-1],final=True)


@pytest.mark.parametrize('fault',['missing_old','changed_title','missing_jd','frozen','account'])
def test_invalid_evidence_stays_unmodified(staged,monkeypatch,fault):
    plan,collection,jobs,bulk,reads=staged
    if fault=='missing_old':jobs[0]['id']='999'
    if fault=='changed_title':jobs[0]['title']='other'
    if fault=='missing_jd':jobs[0].pop('jd')
    if fault=='account':monkeypatch.setattr(repair,'current_account_ref',lambda:'wrong')
    def request(method,*args,**kwargs):
        assert method=='GET'
        return {'status':'queued' if fault=='frozen' else 'collecting'}
    monkeypatch.setattr(bulk_decision,'request',request)
    with pytest.raises((ProtocolError,CollectionError)):repair.repair_staged_job51(collection,plan)
    assert state.load_json(bulk_decision._checkpoint_path(plan['discover_id']))==bulk
