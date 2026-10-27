"""Offline Chrome acceptance for exact-job, named-resume, at-most-once delivery."""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
import pytest
from tests.test_playwright_runtime import chrome  # noqa: F401
from jobagent.drivers.playwright_runtime import PlaywrightRuntime
from jobagent.infra import state
from jobagent.infra.browser_work import BrowserWorkError
from jobagent.platforms.job51.playwright_delivery import Job51Delivery, read_history, single_resume, job_identity


@pytest.mark.parametrize('url',['https://evil.test/1000.html','https://evil@jobs.51job.com/a/1000.html','https://jobs.51job.com/a/1000.html/extra'])
def test_official_exact_history_identity_only(url):
    with pytest.raises(BrowserWorkError):job_identity(url)


def fixture(runtime,monkeypatch,tmp_path):
    monkeypatch.setattr(state,'STATE_DIR',tmp_path)
    monkeypatch.setattr('jobagent.application.playwright_work.pace',lambda _:None)
    facts={'submits':0,'missing_receipt':False,'wrong_resume':False,'multiple_resumes':False,'account':'测试账号','history_pages':[],'delayed_resume_account':False}
    today=datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
    def row(identifier,title,reference):
        return f'<div class="e"><a class="zhn" href="https://jobs.51job.com/all/{identifier}.html">{title}</a><a class="gs">测试公司</a><div class="rq"><span>{today}</span></div><div class="jl"><span>{reference}</span></div></div>'
    def route(request):
        u=urlsplit(request.request.url)
        if u.hostname=='jobs.51job.com' and u.path=='/fixture-submit':
            facts['submits']+=1;request.fulfill(json={'ok':True});return
        if u.hostname=='jobs.51job.com':
            body=f'<span class="name-span">{facts["account"]}</span><div class="tHjob"><h1>数据产品经理</h1></div><a class="com_name">测试公司</a><div class="jobapply-wrapper"><div class="apply-btn-new" id="1000" onclick="fetch(\'/fixture-submit\')">立即投递</div></div>'
        elif u.hostname=='www.51job.com':
            body='<span class="name-span">测试账号</span><ul class="resumeOptions"><li class="active" title="我的简历1">我的简历1</li>'
            if facts['multiple_resumes']:body+='<li title="另一简历">另一简历</li>'
            body+='</ul>'
            if facts['delayed_resume_account']:
                body=body.replace('class="name-span">测试账号','class="name-span">正在加载')
                body+='<script>setTimeout(()=>document.querySelector(".name-span").textContent="测试账号",250)</script>'
            if u.path=='/':body='<span class="name-span">创建简历</span><span class="name-span">创建简历</span>'
        elif u.hostname=='i.51job.com':
            page=2 if 'page=2' in u.query else 1
            facts['history_pages'].append(page)
            reference='另一简历' if facts['wrong_resume'] else '我的简历1'
            rows=row('999','已有岗位','旧简历') if page==1 else ''
            if page==2 and facts['submits'] and not facts['missing_receipt']:
                rows=row('1000','数据产品经理',reference)
            body=f'<a id="pc_uname">测试账号</a><div class="myStates"><a class="on">全部</a></div><div class="apox">{rows}</div><div class="dw_page"><div class="p_in"><ul><li class="on">{page}</li></ul><span class="td">共2页，到第</span>'
            if page==1:body+='<a href="?page=2">下一页</a>'
            body+='</div></div>'
        else:request.abort();return
        request.fulfill(body='<meta charset="utf-8">'+body,content_type='text/html')
    runtime.context.route('**/*',route)
    work={'work_id':'fixture-51job-send','nonce':'once','action':'submit_resume','state':'ready',
          'binding':{'platform':'51job','job_id':'1000','account_ref':'fixture-account','round_id':'fixture-round','session_id':runtime.session['id']},
          'task':{'executor':'playwright','resume_reference':'我的简历1',
                  'job':{'id':'1000','title':'数据产品经理','company':'测试公司','url':'https://jobs.51job.com/shenzhen/1000.html'}}}
    return facts,work


def test_visible_submit_and_complete_named_receipt_replay_never_reclicks(chrome,monkeypatch,tmp_path):
    with PlaywrightRuntime(chrome) as runtime:
        facts,work=fixture(runtime,monkeypatch,tmp_path)
        adapter=Job51Delivery(runtime);adapter.prepare(work)
        assert adapter.preflight['resume_state']=='not_sent'
        assert facts['history_pages']==[1,2]
        result=adapter.execute(work,may_act=True)
        assert result['evidence']['resume_reference']=='我的简历1' and facts['submits']==1
        assert adapter.execute(work,may_act=False)['outcome']=='success' and facts['submits']==1
        from jobagent.application import native_work,playwright_work
        monkeypatch.setattr(playwright_work,'session',lambda:chrome)
        result['evidence'].update(observed_at=datetime.now(timezone.utc).isoformat())
        native_work._common_evidence(work,result)
        native_work._validate_delivery(work,result,result['evidence'])
        inspect={**work,'action':'inspect_delivery'}
        adapter=Job51Delivery(runtime);adapter.prepare(inspect)
        observed=adapter.execute(inspect,may_act=False)
        assert observed['evidence']['resume_state']=='sent'
        assert observed['evidence']['prior_application'] is True
        native_work._validate_delivery(inspect,observed,observed['evidence'])
        assert observed['evidence']['history_record']['resume_reference']=='我的简历1'
        assert facts['submits']==1


@pytest.mark.parametrize('fault',['missing_receipt','wrong_resume'])
def test_lost_or_wrong_receipt_is_not_retried(chrome,monkeypatch,tmp_path,fault):
    with PlaywrightRuntime(chrome) as runtime:
        facts,work=fixture(runtime,monkeypatch,tmp_path)
        adapter=Job51Delivery(runtime);adapter.prepare(work);facts[fault]=True
        with pytest.raises(BrowserWorkError,match='no second click'):adapter.execute(work,may_act=True)
        settled=adapter.execute(work,may_act=False)
        assert settled['outcome']=='unresolved'
        assert settled['evidence']['submission_attempted'] is False
        assert settled['evidence']['receipt_checked'] is True
        assert facts['submits']==1


def test_multiple_resumes_and_changed_account_stop_before_intent(chrome,monkeypatch,tmp_path):
    with PlaywrightRuntime(chrome) as runtime:
        facts,work=fixture(runtime,monkeypatch,tmp_path);facts['multiple_resumes']=True
        with pytest.raises(BrowserWorkError,match='explicit selection'):Job51Delivery(runtime).prepare(work)
        assert facts['submits']==0
        facts['multiple_resumes']=False;facts['account']='另一账号'
        runtime.page.reload()
        with pytest.raises(BrowserWorkError):Job51Delivery(runtime).prepare(work)
        assert facts['submits']==0


def test_missing_checkpoint_cannot_create_or_claim_delivery(chrome,monkeypatch,tmp_path):
    monkeypatch.setattr(state,'STATE_DIR',tmp_path)
    with PlaywrightRuntime(chrome) as runtime:
        facts,work=fixture(runtime,monkeypatch,tmp_path)
        adapter=Job51Delivery(runtime);adapter.prepare(work)
        settled=adapter.execute(work,may_act=False)
        assert settled['outcome']=='unresolved'
        assert settled['evidence']['reconciliation_error']=='job51_checkpoint_missing'
        assert facts['submits']==0


def test_resume_header_hydration_waits_without_reloading_or_submitting(chrome,monkeypatch,tmp_path):
    with PlaywrightRuntime(chrome) as runtime:
        facts,work=fixture(runtime,monkeypatch,tmp_path)
        facts['delayed_resume_account']=True
        adapter=Job51Delivery(runtime);adapter.prepare(work)
        assert adapter.preflight['resume_reference']=='我的简历1'
        assert facts['submits']==0


def test_changed_bound_account_stops_before_history_or_action(chrome,monkeypatch,tmp_path):
    with PlaywrightRuntime({**chrome,'accounts':{'51job':'原账户'}}) as runtime:
        facts,work=fixture(runtime,monkeypatch,tmp_path)
        with pytest.raises(BrowserWorkError,match='account changed'):
            Job51Delivery(runtime).prepare(work)
        assert facts['history_pages']==[] and facts['submits']==0


def test_session_checks_actual_resume_surface_instead_of_duplicate_home_headers(chrome,monkeypatch,tmp_path):
    with PlaywrightRuntime(chrome) as runtime:
        facts,work=fixture(runtime,monkeypatch,tmp_path)
        work={**work,'action':'inspect_session','task':{'url':'https://www.51job.com/'}}
        adapter=Job51Delivery(runtime);adapter.prepare(work)
        result=adapter.execute(work,may_act=False)
        assert result['evidence']['page_url']=='https://www.51job.com/resume/center'
        assert result['evidence']['resume_reference']=='我的简历1'
        assert facts['submits']==0
