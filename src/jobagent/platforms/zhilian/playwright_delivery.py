"""Zhilian delivery through visible, job-bound controls and official history."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urljoin, urlsplit

from jobagent.infra import rounds, state
from jobagent.infra.browser_work import BrowserWorkError
from jobagent.platforms.zhilian.playwright_history import history_identity, read_history
from jobagent.platforms.zhilian.playwright_resume import inventory, preference


class ZhilianDelivery:
    def __init__(self, runtime):
        self.runtime = runtime
        self.page = None
        self.identity = None
        self.preflight = None

    def _text(self, selector):
        value = self.page.locator(selector)
        value.wait_for(state='visible')
        if value.count() != 1 or not value.inner_text().strip():
            raise BrowserWorkError('zhilian_identity_ambiguous', 'The exact job/account identity is unavailable.')
        return value.inner_text().strip()

    def _account_link(self, locator, path):
        if locator.count() != 1 or not locator.is_visible():
            return False
        try:
            parsed = urlsplit(urljoin(self.page.url, locator.get_attribute('href') or ''))
            if (parsed.scheme != 'https' or parsed.hostname != 'i.zhaopin.com'
                    or parsed.port not in {None, 443} or parsed.username or parsed.password
                    or parsed.path != path or parsed.fragment):
                return False
        except ValueError:
            return False
        # The home page points feedback to the viewed tab; job pages use
        # the bare schedule route. Both were observed in the same account.
        return not parsed.query or (path == '/schedule' and parse_qs(parsed.query, keep_blank_values=True) == {'status': ['viewed']})

    def prepare(self, work):
        if work['action'] not in {'inspect_session','inspect_delivery','inspect_resume_selection','submit_resume'}:
            raise BrowserWorkError('zhilian_action_not_validated', 'This Zhilian action is not supported by the packaged executor.')
        self.page = self.runtime.job_page(work)
        account = self._text('.c-login__top__name')
        self.identity = {**self.runtime.evidence(work), 'account_label':account,'login_state':'authenticated'}
        if work['action'] == 'inspect_session':
            self.page.locator('.c-login__top').hover()
            resume = self.page.get_by_role('link', name='我的简历', exact=True)
            activity = self.page.get_by_role('link', name='求职反馈', exact=True)
            resume.wait_for(state='visible')
            if not self._account_link(resume, '/resume') or not self._account_link(activity, '/schedule'):
                raise BrowserWorkError('zhilian_account_navigation_unknown', 'Verify the official account resume and feedback entries.')
            self.identity.update(account_navigation=True,resume_or_activity=True)
            return
        job = work['task']['job']
        identifier, _ = history_identity(self.page.url)
        title = self._text('h1.summary-planes__title')
        company = self._text('.company-info__name')
        if identifier != str(job['id']) or title != job['title'] or company != job['company']:
            raise BrowserWorkError('zhilian_job_identity_changed', 'The visible job, title or company differs from the approved item; no action was issued.')
        self.identity.update(job_id=identifier,job_url=job['url'],title=title,company=company)
        if work['action'] == 'submit_resume' and work['state'] == 'ready':
            observed = self._inspect(work)
            if observed['resume_state'] != 'not_sent':
                raise BrowserWorkError('zhilian_previous_application_found', 'A prior application was found before issuing a new send; reconcile the inspection first.')
            if observed['resume_reference'] != work['task']['resume_reference']:
                raise BrowserWorkError('zhilian_resume_changed', 'The currently selected resume differs from the approved preflight.')
            self._submit_button()
            self.preflight = observed

    def _submit_button(self):
        button = self.page.locator('.summary-planes__action button').filter(has_text='立即投递')
        if button.count() != 1 or button.inner_text().strip() != '立即投递' or not button.is_enabled():
            raise BrowserWorkError('zhilian_submit_control_unknown', 'The exact job-bound apply control is not available.')
        return button

    def _inspect(self, work):
        history = read_history(self.runtime, work, self.identity['account_label'])
        row = history['entries'].get(work['binding']['job_id'])
        observed = {**self.identity,'history_checked':True,'history_complete':True,
                    'communication_state':'not_applicable','receipt_checked':True}
        if row:
            if row['title'] != self.identity['title'] or row['company'] != self.identity['company']:
                raise BrowserWorkError('zhilian_history_job_changed', 'The official history identity differs from the approved job.')
            # History proves a prior application, but does not identify which
            # resume was sent then. Never attribute the current default to it.
            return {**observed,'resume_state':'sent','resume_reference':None,
                    'resume_name_unavailable':True,'prior_application':True,
                    'receipt_kind':'application_history','history_record':row}
        chosen = preference(work, self.identity['account_label'])
        _, selected = inventory(self.runtime, work, account_label=self.identity['account_label'])
        if selected['selected'] != chosen['reference']:
            raise BrowserWorkError('zhilian_resume_default_changed', 'The visible default is not the user-confirmed online resume; no application was made.', requires_user_action=True)
        self.page.bring_to_front()
        return {**observed,'resume_state':'not_sent','resume_reference':selected['selected'],
                'resume_selection_verified':True,'submission_attempted':False}

    def _submit(self, work, *, may_act):
        from jobagent.application.playwright_work import receipt_path
        path = receipt_path(work['work_id']).with_suffix('.preflight.json')
        if may_act:
            if not self.preflight or self.preflight['resume_state'] != 'not_sent':
                raise BrowserWorkError('zhilian_submission_preflight_missing', 'No verified unsent job and selected resume are available.')
            checkpoint = {'binding':work['binding'],'nonce':work['nonce'],
                          'resume_reference':self.preflight['resume_reference'],
                          'observed_at':rounds.utc_now(),'prior_application_absent':True}
            state.save_json(path,checkpoint)
            self._submit_button().click()
            self.page.wait_for_timeout(800)
        checkpoint = state.load_json(path) or {}
        if (checkpoint.get('binding') != work['binding'] or checkpoint.get('nonce') != work['nonce']
                or checkpoint.get('resume_reference') != work['task']['resume_reference']
                or checkpoint.get('prior_application_absent') is not True):
            raise BrowserWorkError('zhilian_submission_checkpoint_missing', 'Preserve the attempted application; its checked pre-submit resume is unavailable.')
        history = read_history(self.runtime,work,self.identity['account_label'],refresh=True)
        row = history['entries'].get(work['binding']['job_id'])
        if not row or row['title'] != self.identity['title'] or row['company'] != self.identity['company']:
            raise BrowserWorkError('zhilian_submission_receipt_pending', 'The exact job has no verified official application history; no second click is permitted.')
        try:
            recorded = datetime.strptime(' '.join(row['recorded_at'].split()), '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone(timedelta(hours=8)))
            started = datetime.fromisoformat(checkpoint['observed_at'].replace('Z','+00:00'))
            if recorded < started - timedelta(seconds=5):
                raise ValueError('older history')
        except (KeyError, ValueError):
            raise BrowserWorkError('zhilian_submission_old_history', 'This history record does not establish a new application after the saved intent; preserve it without another click.') from None
        return {'outcome':'success','evidence':{**self.identity,'resume_state':'sent',
                'resume_reference':checkpoint['resume_reference'],'receipt_checked':True,
                'receipt_kind':'application_history','history_record':row,
                'communication_state':'not_applicable'}}

    def execute(self, work, *, may_act):
        if work['action'] == 'inspect_session':
            return {'outcome':'success','evidence':self.identity}
        if work['action'] in {'inspect_delivery','inspect_resume_selection'}:
            return {'outcome':'success','evidence':self._inspect(work)}
        return self._submit(work,may_act=may_act)


def verified_prior_application(work, evidence):
    """A named current default must never be fabricated for an older receipt."""
    if (work['binding']['platform'] != 'zhilian' or work['action'] != 'inspect_delivery'
            or evidence.get('source') != 'playwright_dom_observation'
            or evidence.get('prior_application') is not True
            or evidence.get('history_complete') is not True
            or evidence.get('receipt_checked') is not True
            or evidence.get('receipt_kind') != 'application_history'
            or evidence.get('resume_name_unavailable') is not True):
        return False
    row = evidence.get('history_record') or {}
    try:
        identifier, _ = history_identity(row.get('url',''))
    except (BrowserWorkError, ValueError):
        return False
    job = work['task']['job']
    return bool(identifier == str(job['id']) and row.get('title') == job['title']
                and row.get('company') == job['company'] and row.get('status') and row.get('recorded_at'))
