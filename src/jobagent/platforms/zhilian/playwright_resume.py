"""Observe and select an existing Zhilian resume without uploading a file."""
from jobagent.infra import rounds, state
from jobagent.infra.account_state import current_account_ref
from jobagent.infra.browser_work import BrowserWorkError

RESUME_URL = 'https://i.zhaopin.com/resume'


def inventory(runtime, work, *, account_label=None, refresh=False):
    page, guard = runtime.auxiliary_page(work, RESUME_URL, purpose='resume', refresh=refresh)
    page.locator('.online-resume__name').wait_for(state='visible')
    account = page.locator('.zp-welcome__username').inner_text().strip()
    if not account or (account_label and account != account_label):
        raise BrowserWorkError('zhilian_resume_account_changed', 'The existing resume belongs to another platform account.')
    data = page.evaluate("""() => ({
        online:[...document.querySelectorAll('.online-resume')].map(e=>({
          reference:e.querySelector('.online-resume__name')?.innerText.trim(),
          control:e.querySelector('.online-resume__right-text')?.innerText.trim()})),
        attachments:[...document.querySelectorAll('.parse-item')].map(e=>({
          reference:e.querySelector('.parse-item__left-title-text')?.innerText.trim(),
          control:e.querySelector('.parse-item__right-text')?.innerText.trim()}))
    })""")
    guard.check()
    options = [{**o,'kind':'online'} for o in data['online']] + [{**o,'kind':'attachment'} for o in data['attachments']]
    if (len(data['online']) != 1 or not options or len(options) > 20
            or any(not o['reference'] or o['control'] not in {'设为默认','默认投递'} for o in options)
            or len({o['reference'] for o in options}) != len(options)
            or sum(o['control'] == '默认投递' for o in options) != 1):
        raise BrowserWorkError('zhilian_resume_options_unknown', 'Verify the complete distinct resume options and one current default.')
    return page, {'account_label':account,'options':options,
                  'selected':next(o['reference'] for o in options if o['control']=='默认投递')}


def preference(work, account_label):
    value = (rounds.ensure_current_round().get('playwright_platform_resume_preferences') or {}).get('zhilian')
    if (not value or value.get('status') != 'confirmed'
            or value.get('account_ref') != work['binding']['account_ref']
            or value.get('round_id') != work['binding']['round_id']
            or value.get('account_label') != account_label):
        raise BrowserWorkError('zhilian_resume_choice_required', 'Confirm an observed platform resume before sending; the platform default is not a choice.',
                               requires_user_action=True)
    return value


def select_existing(reference):
    from jobagent.application.playwright_work import session, ensure_session
    from jobagent.drivers.playwright_runtime import PlaywrightRuntime
    active = rounds.ensure_current_round()
    rounds.assert_platform_turn('zhilian')
    ensure_session('zhilian')
    active = rounds.ensure_current_round()
    current = session(active)
    work = {'binding':{'platform':'zhilian','account_ref':current_account_ref(),'round_id':active['round_id']}}
    expected_account = current.get('accounts',{}).get('zhilian')
    with PlaywrightRuntime(current) as runtime:
        page, observed = inventory(runtime, work, account_label=expected_account, refresh=True)
        chosen = [o for o in observed['options'] if o['reference']==reference and o['kind']=='online']
        if len(chosen) != 1:
            raise BrowserWorkError('zhilian_online_resume_not_observed', 'Choose the exact existing online resume shown in this account.', options=observed['options'])
        consent = {**work['binding'],'account_label':observed['account_label'],
                   'reference':reference,'kind':'online','before':observed,
                   'confirmed_at':rounds.utc_now(),'status':'selection_pending'}
        active.setdefault('playwright_platform_resume_preferences',{})['zhilian'] = consent
        rounds.save_round(active)
        if observed['selected'] != reference:
            page.locator('.online-resume__right-text').click()
            page.wait_for_function("() => document.querySelector('.online-resume__right-text')?.innerText.trim() === '默认投递'", timeout=12000)
        _, after = inventory(runtime, work, account_label=observed['account_label'])
        if after['selected'] != reference:
            raise BrowserWorkError('zhilian_resume_selection_unknown', 'The chosen online resume was not verified; no recruiting action was made.')
        consent.update(status='confirmed',after=after,verified_at=rounds.utc_now())
        rounds.save_round(active)
    return {'ok':True,'event':'platform_resume_preference_saved','platform':'zhilian',
            'resume_reference':reference,'request_preserved':True,'submitted':False,
            'next_suggested':'jobagent work next'}
