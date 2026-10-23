"""51Job resume delivery: exact approved detail, one intent and named history receipt."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from jobagent.infra import rounds, state
from jobagent.infra.browser_work import BrowserWorkError

HISTORY_URL = 'https://i.51job.com/userset/my_apply.php'
RESUME_URL = 'https://www.51job.com/resume/center'


def job_identity(url):
    try:
        parsed=urlsplit(url)
        match=re.fullmatch(r'/(?:[^/]+/)?([0-9]+)\.html',parsed.path)
        if (parsed.scheme=='https' and parsed.hostname=='jobs.51job.com' and match
                and not parsed.username and not parsed.password and parsed.port in {None,443}):
            return match[1]
    except ValueError:
        pass
    raise BrowserWorkError('job51_job_identity_unknown','The official detail must identify the exact approved job.')


def unique_text(page, selector):
    node=page.locator(selector)
    node.wait_for(state='visible')
    if node.count()!=1 or not node.inner_text().strip():
        raise BrowserWorkError('job51_identity_ambiguous','The visible job, account or resume identity is ambiguous.')
    return node.inner_text().strip()


def read_history(runtime, work, account_label, *, refresh=False):
    from jobagent.application.playwright_work import pace
    path=state.STATE_DIR/'playwright_history'/f"{runtime.session['id']}-51job.json"
    binding={k:work['binding'][k] for k in ('account_ref','round_id')}
    cached=state.load_json(path) or {}
    if (not refresh and cached.get('complete') and cached.get('binding')==binding
            and cached.get('account_label')==account_label
            and 0<=time.time()-cached.get('observed_epoch',0)<300):
        return cached
    page,guard=runtime.auxiliary_page(work,HISTORY_URL,purpose='history',refresh=True)
    if urlsplit(page.url).hostname!='i.51job.com':
        raise BrowserWorkError('job51_history_authentication_required',
            'The separate account-history page needs authentication; the search-page login has not been changed.',requires_user_action=True)
    if unique_text(page,'#pc_uname')!=account_label or unique_text(page,'.myStates a.on')!='全部':
        raise BrowserWorkError('job51_history_account_changed','The authenticated account and all-applications category must match.')
    entries={};pages=[]
    for index in range(1,101):
        guard.check()
        rows=page.locator('.apox > .e').evaluate_all('''els=>els.map(e=>({
          url:e.querySelector('a.zhn')?.href || '',title:e.querySelector('a.zhn')?.innerText.trim() || '',
          company:e.querySelector('a.gs')?.innerText.trim() || '',
          recorded_at:e.querySelector('.rq span')?.innerText.trim() || '',
          resume_reference:e.querySelector('.jl span')?.innerText.trim() || ''}))''')
        current=page.locator('.dw_page .p_in li.on')
        totals=page.locator('.dw_page .p_in > .td').all_text_contents()
        match=re.search(r'共\s*(\d+)\s*页',' '.join(totals))
        if current.count()!=1 or current.inner_text().strip()!=str(index) or not match:
            raise BrowserWorkError('job51_history_pagination_unknown','History does not expose a complete page boundary; no absence was inferred.')
        total_pages=int(match[1])
        if not 1<=total_pages<=100:
            raise BrowserWorkError('job51_history_limit','History exceeds the bounded read budget.')
        for row in rows:
            identifier=job_identity(row['url'])
            if (identifier in entries or not all(row.values())
                    or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',row['recorded_at'])):
                raise BrowserWorkError('job51_history_incomplete','History contains an ambiguous or incomplete job receipt.')
            entries[identifier]={**row,'id':identifier}
        pages.append({'page':index,'count':len(rows)})
        if index==total_pages:
            break
        next_page=page.locator('.dw_page .p_in a').filter(has_text=re.compile(r'^下一页$'))
        if next_page.count()!=1:
            raise BrowserWorkError('job51_history_pagination_unknown','The next history page is unavailable.')
        pace(runtime.session)
        next_page.click()
        page.locator('.dw_page .p_in li.on').filter(has_text=re.compile('^'+str(index+1)+'$')).wait_for()
    result={'binding':binding,'account_label':account_label,'complete':True,'coverage':'platform_recent_60_days',
            'entries':entries,'pages':pages,'observed_at':rounds.utc_now(),'observed_epoch':time.time()}
    state.save_json(path,result)
    if runtime.page:runtime.page.bring_to_front()
    return result


def single_resume(runtime,work,account_label):
    if runtime.page and runtime.same_route(runtime.page.url,RESUME_URL):
        page,guard=runtime.page,runtime.guard
    else:
        page,guard=runtime.auxiliary_page(work,RESUME_URL,purpose='resume')
    guard.check()
    if urlsplit(page.url).hostname!='www.51job.com':
        raise BrowserWorkError('job51_resume_authentication_required','Authenticate the official resume page before choosing a resume.',requires_user_action=True)
    # The account header hydrates separately from the resume panel. Wait for
    # the bound label on this page; an initial placeholder is not an account
    # switch and does not justify a second navigation or another login.
    from patchright.sync_api import TimeoutError as BrowserTimeoutError
    expected=page.locator('.name-span').filter(has_text=re.compile(r'^\s*'+re.escape(account_label)+r'\s*$'))
    try:
        expected.wait_for(state='visible')
        page.locator('.resumeOptions > li').first.wait_for(state='visible')
    except BrowserTimeoutError as exc:
        raise BrowserWorkError('job51_resume_account_changed','The bound account and its resume list did not finish loading; no resume was selected.') from exc
    if unique_text(page,'.name-span')!=account_label:
        raise BrowserWorkError('job51_resume_account_changed','The resume page belongs to another account.')
    options=page.locator('.resumeOptions > li')
    if options.count()!=1 or 'active' not in (options.get_attribute('class') or '').split():
        raise BrowserWorkError('job51_resume_choice_required','Multiple or unknown account resumes require explicit selection before submission.',requires_user_action=True)
    name=unique_text(page,'.resumeOptions > li.active')
    if options.get_attribute('title')!=name:
        raise BrowserWorkError('job51_resume_name_unknown','The complete existing resume name is unavailable.')
    if runtime.page:runtime.page.bring_to_front()
    return name


class Job51Delivery:
    def __init__(self,runtime):
        self.runtime=runtime
        self.page=None
        self.identity=None
        self.preflight=None

    def prepare(self,work):
        if work['action'] not in {'inspect_session','inspect_delivery','inspect_resume_selection','submit_resume'}:
            raise BrowserWorkError('job51_action_not_validated','This action is not supported by the packaged 51Job executor.')
        target=work
        if work['action']=='inspect_session':
            # The marketing homepage renders two account headers. Verify the
            # actual account's resume surface, which also proves access.
            target={**work,'task':{**work['task'],'url':RESUME_URL}}
        self.page=self.runtime.job_page(target)
        if work['action']=='inspect_session':
            self.page.locator('.resumeOptions > li').first.wait_for(state='visible')
        account=unique_text(self.page,'.name-span')
        expected=self.runtime.session.get('accounts',{}).get('51job')
        if expected and account!=expected:
            raise BrowserWorkError('playwright_account_changed','The visible platform account changed; no action was issued.')
        self.identity={**self.runtime.evidence(work),'account_label':account,'login_state':'authenticated'}
        if work['action']=='inspect_session':
            reference=single_resume(self.runtime,work,account)
            self.identity.update(account_navigation=True,resume_or_activity=True,resume_reference=reference)
            return
        job=work['task']['job']
        identifier=job_identity(self.page.url)
        title=unique_text(self.page,'.tHjob h1')
        company=unique_text(self.page,'a.com_name')
        if identifier!=str(job['id']) or title!=job['title'] or company!=job['company']:
            raise BrowserWorkError('job51_job_identity_changed','The visible job, title or company differs from the approved item; no action was issued.')
        self.identity.update(job_id=identifier,job_url=job['url'],title=title,company=company)
        if work['action']=='submit_resume' and work['state']=='ready':
            self.preflight=self._inspect(work)
            if (self.preflight['resume_state']!='not_sent'
                    or self.preflight['resume_reference']!=work['task']['resume_reference']):
                raise BrowserWorkError('job51_submission_precondition_changed','A prior application or changed resume requires reconciliation before any new click.')
            self._submit_button()

    def _submit_button(self):
        button=self.page.locator('.jobapply-wrapper .apply-btn-new').filter(has_text=re.compile(r'^\s*立即投递\s*$'))
        if (button.count()!=1 or not button.is_visible() or not button.is_enabled()
                or button.get_attribute('id')!=self.identity['job_id']
                or any(x in (button.get_attribute('class') or '').split() for x in ('disabled','is-disabled'))):
            raise BrowserWorkError('job51_submit_control_unknown','The exact unsent job apply control is unavailable.')
        return button

    def _inspect(self,work):
        history=read_history(self.runtime,work,self.identity['account_label'])
        row=history['entries'].get(self.identity['job_id'])
        observed={**self.identity,'history_checked':True,'history_complete':True,'history_coverage':history['coverage'],
                  'communication_state':'not_applicable','receipt_checked':True}
        if row:
            if row['title']!=self.identity['title'] or row['company']!=self.identity['company']:
                raise BrowserWorkError('job51_history_job_changed','History metadata differs from the exact approved job.')
            return {**observed,'resume_state':'sent','resume_reference':row['resume_reference'],
                    'receipt_kind':'application_history','history_record':row,'prior_application':True}
        # Recent site history is supplemented by JobAgent's durable account
        # ledger before this task exists. A visible already-applied control is
        # never treated as a new send opportunity just because a row is older.
        self._submit_button()
        reference=single_resume(self.runtime,work,self.identity['account_label'])
        return {**observed,'resume_state':'not_sent','resume_reference':reference,
                'resume_selection_verified':True,'submission_attempted':False}

    def execute(self,work,*,may_act):
        if work['action']=='inspect_session':return {'outcome':'success','evidence':self.identity}
        if work['action'] in {'inspect_delivery','inspect_resume_selection'}:
            return {'outcome':'success','evidence':self._inspect(work)}
        from jobagent.application.playwright_work import receipt_path
        path=receipt_path(work['work_id']).with_suffix('.preflight.json')
        if may_act:
            if not self.preflight or self.preflight['resume_state']!='not_sent':
                raise BrowserWorkError('job51_preflight_missing','No verified unsent job and selected resume are available.')
            checkpoint={'binding':work['binding'],'nonce':work['nonce'],'observed_at':rounds.utc_now(),
                        'resume_reference':self.preflight['resume_reference'],'prior_application_absent':True}
            state.save_json(path,checkpoint)
            self._submit_button().click()
            self.page.wait_for_timeout(800)
        checkpoint=state.load_json(path) or {}
        if (checkpoint.get('binding')!=work['binding'] or checkpoint.get('nonce')!=work['nonce']
                or checkpoint.get('resume_reference')!=work['task']['resume_reference']
                or checkpoint.get('prior_application_absent') is not True):
            raise BrowserWorkError('job51_checkpoint_missing','Preserve the attempted application; its original preflight is unavailable.')
        history=read_history(self.runtime,work,self.identity['account_label'],refresh=True)
        row=history['entries'].get(self.identity['job_id'])
        if (not row or row['title']!=self.identity['title'] or row['company']!=self.identity['company']
                or row['resume_reference']!=checkpoint['resume_reference']):
            raise BrowserWorkError('job51_receipt_pending','No exact named-resume application receipt is available; no second click is permitted.')
        started=datetime.fromisoformat(checkpoint['observed_at'].replace('Z','+00:00')).astimezone(timezone(timedelta(hours=8))).date()
        recorded=datetime.strptime(row['recorded_at'],'%Y-%m-%d').date()
        if not started<=recorded<=datetime.now(timezone(timedelta(hours=8))).date():
            raise BrowserWorkError('job51_receipt_date_mismatch','The receipt predates the original verified absence and send intent.')
        return {'outcome':'success','evidence':{**self.identity,'resume_state':'sent',
                'resume_reference':row['resume_reference'],'communication_state':'not_applicable',
                'receipt_kind':'application_history','receipt_checked':True,'history_record':row}}


def verified_prior_application(work,evidence):
    if (work['binding']['platform']!='51job' or work['action'] not in {'inspect_delivery','inspect_resume_selection'}
            or evidence.get('source')!='playwright_dom_observation' or evidence.get('prior_application') is not True
            or evidence.get('history_complete') is not True or evidence.get('receipt_checked') is not True
            or evidence.get('receipt_kind')!='application_history'):
        return False
    row=evidence.get('history_record') or {}
    try:
        identifier=job_identity(row.get('url',''))
    except BrowserWorkError:
        return False
    job=work['task']['job']
    return bool(identifier==str(job['id']) and row.get('title')==job['title'] and row.get('company')==job['company']
                and row.get('resume_reference') and row['resume_reference']==evidence.get('resume_reference')
                and re.fullmatch(r'\d{4}-\d{2}-\d{2}',row.get('recorded_at','')))
