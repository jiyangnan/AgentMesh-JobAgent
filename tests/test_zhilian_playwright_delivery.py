"""Offline Chromium: complete history, explicit resume choice, one send only."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests.test_playwright_runtime import chrome
from jobagent.drivers.playwright_runtime import PlaywrightRuntime
from jobagent.infra import state
from jobagent.infra.browser_work import BrowserWorkError
from jobagent.platforms.zhilian.playwright_delivery import ZhilianDelivery, verified_prior_application
from jobagent.platforms.zhilian.playwright_history import STATUSES, history_identity, read_history
from jobagent.platforms.zhilian.playwright_resume import inventory


@pytest.mark.parametrize('url', ['https://example.test/CC1.htm', 'https://www.zhaopin.com/jobdetail/CC1.htm/extra',
                                'https://evil@jobs.zhaopin.com/CC1.htm'])
def test_foreign_or_ambiguous_history_identity_rejected(url):
    with pytest.raises(BrowserWorkError):
        history_identity(url)


def test_historical_resume_name_is_not_invented():
    work={'action':'inspect_delivery','binding':{'platform':'zhilian'},
          'task':{'job':{'id':'CC1','title':'数据产品经理','company':'测试公司'}}}
    e={'source':'playwright_dom_observation','prior_application':True,'history_complete':True,
       'receipt_checked':True,'receipt_kind':'application_history','resume_name_unavailable':True,
       'history_record':{'url':'http://jobs.zhaopin.com/CC1.htm?trace=1','title':'数据产品经理',
                         'company':'测试公司','status':'被HR查看','recorded_at':'2026-10-01 12:00:00'}}
    assert verified_prior_application(work,e)
    assert not verified_prior_application(work,{**e,'history_complete':False})
    assert not verified_prior_application({**work,'action':'submit_resume'},e)


def test_homepage_relative_account_links_and_unknown_destinations(chrome, monkeypatch):
    with PlaywrightRuntime(chrome) as runtime:
        runtime.context.route('**/*', lambda route: route.abort())
        runtime.context.route('https://www.zhaopin.com/', lambda route: route.fulfill(body='''<meta charset="utf-8">
          <div class="c-login__top"><span class="c-login__top__name">测试账号</span></div>
          <a href="//i.zhaopin.com/resume">我的简历</a>
          <a href="//i.zhaopin.com/schedule?status=viewed">求职反馈</a>''', content_type='text/html'))
        page = runtime.context.pages[0]
        runtime.page = page
        page.goto('https://www.zhaopin.com/')
        monkeypatch.setattr(runtime, 'job_page', lambda work: page)
        work = {'action': 'inspect_session', 'task': {'executor': 'playwright'},
                'binding': {'platform': 'zhilian', 'session_id': chrome['id']}}
        adapter = ZhilianDelivery(runtime)
        adapter.prepare(work)
        result = adapter.execute(work, may_act=False)
        assert result['evidence']['account_navigation']
        # Exercise the same receipt contract as `work run`; the transport
        # supplies a timestamp, but the adapter must retain observed context.
        from jobagent.application import native_work, playwright_work
        monkeypatch.setattr(playwright_work, 'session', lambda: chrome)
        result['evidence'].update(source=playwright_work.SOURCE, observed_at=datetime.now(timezone.utc).isoformat())
        assert native_work._common_evidence(work, result)['page_url'] == page.url
        feedback = page.get_by_role('link', name='求职反馈', exact=True)
        for href in ['http://i.zhaopin.com/schedule', '//i.zhaopin.com.evil.test/schedule',
                     '//evil@i.zhaopin.com/schedule', '//i.zhaopin.com/schedule?redirect=https://evil.test',
                     '//i.zhaopin.com/not-schedule']:
            feedback.evaluate('(e, href) => e.setAttribute("href", href)', href)
            with pytest.raises(BrowserWorkError, match='official account'):
                adapter.prepare(work)


def history_fixture(runtime, monkeypatch, tmp_path, *, malformed=False):
    monkeypatch.setattr(state,'STATE_DIR',tmp_path)
    monkeypatch.setattr('jobagent.application.playwright_work.pace',lambda _:None)
    runtime.context.route('https://fe-api.zhaopin.com/c/i/schedule/feedback',lambda route:route.fulfill(json={'ok':True}))
    page=runtime.context.pages[0];runtime.page=page
    monkeypatch.setattr(runtime,'auxiliary_page',lambda *args,**kwargs:(page,SimpleNamespace(check=lambda:None)))
    labels=json.dumps(STATUSES,ensure_ascii=False)
    page.set_content('<span class="zp-welcome__username">测试账号</span><div class="st-nav"><ul></ul></div><main></main>')
    page.evaluate('''labels => {
      window.tab=0;window.index=1;window.bad=false;
      window.render=()=>{
        document.querySelectorAll('.st-nav li').forEach((e,i)=>e.className=i===tab?'on':'');
        const ids=tab===3?[]:(tab===0?(index===1?['CC0']:['CC01']):['CC'+tab]);
        const data=ids.map(id=>`<div class="ji-item"><a class="ji-item-info-jobName" href="http://jobs.zhaopin.com/${id}.htm">岗位 ${id}</a>
           <a class="ji-item-info-companyName">测试公司</a><div class="ji-item-status"><p>成功投递</p>
           <div class="ji-item-status-timer">2026-10-01 12:00:00</div></div></div>`).join('');
        document.querySelector('main').innerHTML=data+(ids.length?`<ul class="ivu-page"><li class="ivu-page-item-active">${index}</li>
          <li class="ivu-page-next ${tab===0&&index===1?'':'ivu-page-disabled'}">下一页</li></ul>`:'<p>没有记录，好可怕 ~~~</p>');
        const next=document.querySelector('.ivu-page-next');if(next)next.onclick=()=>{window.index++;window.load();};
      };
      window.load=()=>{window.render();fetch('https://fe-api.zhaopin.com/c/i/schedule/feedback');};
      labels.forEach((label,i)=>{const li=document.createElement('li');const a=document.createElement('a');a.textContent=label;
        a.onclick=()=>{window.tab=i;window.index=1;window.load();};li.appendChild(a);document.querySelector('.st-nav ul').appendChild(li);});
      window.render();
    }''',STATUSES)
    if malformed:
        page.locator('.ji-item-info-jobName').evaluate("e=>e.href='https://example.test/CC0.htm'")
    return page,{'binding':{'account_ref':'fixture-account','round_id':'fixture-round'}}


def test_history_reads_all_categories_and_pages_including_viewed_and_rejected(chrome, monkeypatch,tmp_path):
    with PlaywrightRuntime(chrome) as runtime:
        page,work=history_fixture(runtime,monkeypatch,tmp_path)
        result=read_history(runtime,work,'测试账号')
        assert result['complete'] and set(result['entries'])=={'CC0','CC01','CC1','CC2','CC4'}
        assert len(result['pages'])==6 and result['entries']['CC1']['category']=='被查看'
        assert result['entries']['CC4']['category']=='不合适'
        assert read_history(runtime,work,'测试账号')==result


def test_incomplete_history_is_not_persisted_or_used_as_absence(chrome,monkeypatch,tmp_path):
    with PlaywrightRuntime(chrome) as runtime:
        page,work=history_fixture(runtime,monkeypatch,tmp_path,malformed=True)
        with pytest.raises(BrowserWorkError):
            read_history(runtime,work,'测试账号')
        assert not list((tmp_path/'playwright_history').glob('*.json'))


def test_existing_online_resume_is_observed_without_using_attachment_default(chrome,monkeypatch):
    with PlaywrightRuntime(chrome) as runtime:
        page=runtime.context.pages[0]
        monkeypatch.setattr(runtime,'auxiliary_page',lambda *args,**kwargs:(page,SimpleNamespace(check=lambda:None)))
        page.set_content('''<span class="zp-welcome__username">测试账号</span>
          <div class="online-resume"><span class="online-resume__name">我的在线简历</span><p class="online-resume__right-text">设为默认</p></div>
          <div class="parse-item"><span class="parse-item__left-title-text">项目经理.pdf</span><p class="parse-item__right-text">默认投递</p></div>''')
        _,value=inventory(runtime,{},account_label='测试账号')
        assert value['selected']=='项目经理.pdf'
        assert value['options'][0]['reference']=='我的在线简历'
        with pytest.raises(BrowserWorkError):
            inventory(runtime,{},account_label='另一账号')


def test_submission_uses_checkpoint_and_reconciliation_never_clicks_again(chrome,monkeypatch,tmp_path):
    monkeypatch.setattr(state,'STATE_DIR',tmp_path)
    with PlaywrightRuntime(chrome) as runtime:
        page=runtime.context.pages[0];runtime.page=page
        page.set_content('''<div class="summary-planes__action"><button onclick="window.submits=(window.submits||0)+1">立即投递</button></div>''')
        page.locator('button').evaluate('e=>e.onclick=()=>{window.submits=(window.submits||0)+1}')
        adapter=ZhilianDelivery(runtime);adapter.page=page
        adapter.identity={'account_label':'测试账号','title':'数据产品经理','company':'测试公司'}
        adapter.preflight={'resume_state':'not_sent','resume_reference':'我的在线简历'}
        work={'work_id':'fixture-send','nonce':'once','binding':{'job_id':'CC1'},'task':{'resume_reference':'我的在线简历'}}
        row={'title':'数据产品经理','company':'测试公司','id':'CC1',
             'recorded_at':datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')}
        monkeypatch.setattr('jobagent.platforms.zhilian.playwright_delivery.read_history',lambda *args,**kwargs:{'entries':{'CC1':row}})
        assert adapter._submit(work,may_act=True)['evidence']['resume_reference']=='我的在线简历'
        assert adapter._submit(work,may_act=False)['outcome']=='success'
        assert page.evaluate('window.submits')==1
        row['recorded_at']='2020-01-01 00:00:00'
        with pytest.raises(BrowserWorkError,match='does not establish a new application'):
            adapter._submit(work,may_act=False)
        monkeypatch.setattr('jobagent.platforms.zhilian.playwright_delivery.read_history',lambda *args,**kwargs:{'entries':{}})
        with pytest.raises(BrowserWorkError,match='no second click'):
            adapter._submit(work,may_act=False)
        assert page.evaluate('window.submits')==1


def test_missing_submission_checkpoint_cannot_claim_success(chrome,monkeypatch,tmp_path):
    monkeypatch.setattr(state,'STATE_DIR',tmp_path)
    with PlaywrightRuntime(chrome) as runtime:
        adapter=ZhilianDelivery(runtime)
        work={'work_id':'missing','nonce':'once','binding':{'job_id':'CC1'},'task':{'resume_reference':'我的在线简历'}}
        with pytest.raises(BrowserWorkError,match='pre-submit resume'):
            adapter._submit(work,may_act=False)


def test_explicit_online_selection_records_consent_and_never_submits(chrome,monkeypatch,tmp_path):
    from contextlib import nullcontext
    from jobagent.platforms.zhilian.playwright_resume import select_existing
    from jobagent.application import playwright_work
    active={'round_id':'fixture-round','playwright_session':chrome}
    saved=[]
    monkeypatch.setattr(state,'STATE_DIR',tmp_path)
    monkeypatch.setattr('jobagent.infra.rounds.ensure_current_round',lambda:active)
    monkeypatch.setattr('jobagent.infra.rounds.assert_platform_turn',lambda platform:None)
    monkeypatch.setattr('jobagent.infra.rounds.save_round',lambda value:saved.append(json.loads(json.dumps(value))))
    monkeypatch.setattr(playwright_work,'ensure_session',lambda platform:None)
    monkeypatch.setattr(playwright_work,'session',lambda active:chrome)
    monkeypatch.setattr('jobagent.platforms.zhilian.playwright_resume.current_account_ref',lambda:'fixture-account')
    with PlaywrightRuntime(chrome) as runtime:
        monkeypatch.setattr('jobagent.drivers.playwright_runtime.PlaywrightRuntime',lambda session:nullcontext(runtime))
        page=runtime.context.pages[0]
        monkeypatch.setattr(runtime,'auxiliary_page',lambda *args,**kwargs:(page,SimpleNamespace(check=lambda:None)))
        page.set_content('''<span class="zp-welcome__username">测试账号</span>
          <div class="online-resume"><span class="online-resume__name">我的在线简历</span><p class="online-resume__right-text">设为默认</p></div>
          <div class="parse-item"><span class="parse-item__left-title-text">项目经理.pdf</span><p class="parse-item__right-text">默认投递</p></div>''')
        page.locator('.online-resume__right-text').evaluate("e=>e.onclick=()=>{e.textContent='默认投递';document.querySelector('.parse-item__right-text').textContent='设为默认';}")
        result=select_existing('我的在线简历')
        assert result['submitted'] is False
        assert saved[0]['playwright_platform_resume_preferences']['zhilian']['status']=='selection_pending'
        assert saved[-1]['playwright_platform_resume_preferences']['zhilian']['status']=='confirmed'
        assert inventory(runtime,{},account_label='测试账号')[1]['selected']=='我的在线简历'
