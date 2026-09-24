import base64
import copy
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jobagent.application import greeting_refresh as g
from jobagent.infra import discovery_state as ds, protocol, state


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(state,'STATE_DIR',tmp_path/'state')
    monkeypatch.setattr(ds,'discoveries_dir',lambda: tmp_path/'discoveries')
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    monkeypatch.setattr(protocol,'DECISION_SIGNING_PUBLIC_KEY',base64.urlsafe_b64encode(public).decode().rstrip('='))
    def sign(data):
        data=copy.deepcopy(data); data.pop('signature',None)
        data.update(key_id='test',signature_algorithm='Ed25519')
        data['signature']=base64.urlsafe_b64encode(key.sign(protocol.canonical_json_bytes(data))).decode().rstrip('=')
        return data
    intent={'status':'confirmed','target_roles':['数据产品经理'],'target_cities':['武汉']}
    old=sign({'manifest_type':'decision_manifest','protocol_version':1,'manifest_id':'dm-old-one',
        'discover_id':'dis-one','platform':'boss','account_ref':'account-one','round_id':'round-one',
        'intent_digest':protocol.digest_payload(intent),'candidate_digest':'sha256:'+'a'*64,
        'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),'deduplicated_count':3,
        'selected':[{'id':'j1','greeting':'旧消息1'},{'id':'j2','greeting':'旧消息2'}],
        'review':[{'id':'j3','greeting':'旧消息3'}],'rejected':[]})
    base=ds.save_manifest(old)
    reviewed={'platform':'boss','discover_id':'dis-one','manifest':old,
        'user_overrides':[{'job_id':'j3','from':'review','to':'selected'}],
        'user_delivery_exclusions':[{'id':'j2'}]}
    source=ds.save_review(reviewed)
    active={'round_id':'round-one','intent':intent,'platforms':{'boss':{'evidence':{'discover_id':'dis-one'}}}}
    monkeypatch.setattr(g,'current_account_ref',lambda:'account-one')
    monkeypatch.setattr(g.rounds,'ensure_current_round',lambda:active)
    monkeypatch.setattr(g.rounds,'assert_platform_turn',lambda _:None)
    monkeypatch.setattr(g,'assert_list_active',lambda *_:None)
    monkeypatch.setattr(g.browser_work,'has_open',lambda:False)
    works=[]; monkeypatch.setattr(g.browser_work,'list_work',lambda _:works)
    calls=[]; new=copy.deepcopy(old);new['manifest_id']='dm-new-one'
    new['greeting_refresh']={'original_manifest_id':old['manifest_id'],'additional_credits':0,'same_discover_id':True}
    for bucket in ('selected','review'):
        for item in new[bucket]: item['greeting']='新的有依据招呼'+item['id']
    response={'manifest':sign(new),'additional_credits':0,'replayed':False}
    def cloud(**kwargs): calls.append(kwargs); return response
    monkeypatch.setattr(g.cloud_client,'discovery_greetings_refresh',cloud)
    pending={'context':{'discover_id':'dis-one'}};cleared=[]
    monkeypatch.setattr(g,'load_pending_interaction',lambda:pending)
    monkeypatch.setattr(g,'clear_pending_interaction',lambda:cleared.append(True))
    def review(platform,**kwargs):
        value=ds.load_envelope(platform,kwargs['input_path'])
        built=ds.build_review(value,promoted_ids=kwargs['promoted_ids'],confirm_promote=kwargs['confirm_promote'])
        g.review._preserve_delivery_exclusions(value,built)
        return {'requires_user_action':True,'review':built}
    monkeypatch.setattr(g.review,'review_decision',review)
    return locals()


def test_refresh_keeps_exclusions_promotions_signed_history_and_requires_confirmation(env):
    result=g.refresh('boss',input_path=str(env['base']))
    assert result['requires_user_action'] is True
    assert [j['id'] for j in result['review']['send_candidates']]==['j1','j3']
    assert all(j['cloud_greeting'].startswith('新的') for j in result['review']['send_candidates'])
    assert not result['review'].get('delivery_authorization')
    assert len(env['calls'])==1 and env['cleared']==[True]
    archives=list((state.STATE_DIR/'archive'/'greeting-revisions').glob('*.json'))
    assert len(archives)==1 and state.load_json(archives[0])['manifest']==env['old']


@pytest.mark.parametrize('field', ['candidate_digest','round_id','intent_digest','billing'])
def test_signed_response_cannot_change_search_or_billing(env,field):
    new=copy.deepcopy(env['new']);new[field]='changed'
    env['response']['manifest']=env['sign'](new)
    before=env['base'].read_bytes()
    with pytest.raises(g.browser_work.BrowserWorkError,match='保留'):
        g.refresh('boss',input_path=str(env['base']))
    assert env['base'].read_bytes()==before and not env['cleared']


def test_closed_unresolved_or_authorized_work_is_never_reopened(env):
    env['works'].append({'state':'closed','side_effect':True,'binding':{'discover_id':'dis-one'}})
    with pytest.raises(g.browser_work.BrowserWorkError):g.refresh('boss')
    assert not env['calls']


def test_existing_authorization_blocks_even_raw_input(env):
    data=state.load_json(env['source']);data['delivery_authorization']={'authorization_id':'old'}
    state.save_json(env['source'],data)
    with pytest.raises(g.browser_work.BrowserWorkError):g.refresh('boss',input_path=str(env['base']))
    assert not env['calls']


def test_network_failure_preserves_preview_and_pending_card(env,monkeypatch):
    def fail(**kwargs):raise RuntimeError('transport')
    monkeypatch.setattr(g.cloud_client,'discovery_greetings_refresh',fail)
    before=env['source'].read_bytes()
    with pytest.raises(RuntimeError,match='transport'):g.refresh('boss')
    assert env['source'].read_bytes()==before and not env['cleared']


def test_refresh_flag_is_available_on_standard_review_commands():
    from jobagent.cli import build_parser
    for args in [['boss','greet','preview'],['liepin','apply','review']]:
        assert build_parser().parse_args(args+['--refresh-greetings']).refresh_greetings


def test_bound_manifest_account_lives_in_resume_binding(env):
    binding={"id":"binding-one","context_id":"context-one"}
    env['active']['resume_binding']=binding
    old=copy.deepcopy(env['old']);old.pop('account_ref');old['resume_binding']={**binding,'account_ref':'account-one'}
    old=env['sign'](old);ds.save_manifest(old)
    reviewed=state.load_json(env['source']);reviewed['manifest']=old;state.save_json(env['source'],reviewed)
    new=copy.deepcopy(env['new']);new.pop('account_ref');new['resume_binding']=old['resume_binding']
    env['response']['manifest']=env['sign'](new)
    assert g.refresh('boss')['requires_user_action']


def test_signed_resume_account_mismatch_is_refused(env):
    old=copy.deepcopy(env['old']);old.pop('account_ref');old['resume_binding']={'account_ref':'another-account'}
    ds.save_manifest(env['sign'](old))
    with pytest.raises(g.browser_work.BrowserWorkError):g.refresh('boss',input_path=str(env['base']))
    assert not env['calls']
