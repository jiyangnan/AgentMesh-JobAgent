"""Read all official feedback categories before proving no prior application."""
from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

from jobagent.infra import rounds, state
from jobagent.infra.browser_work import BrowserWorkError

HISTORY_URL = "https://i.zhaopin.com/schedule"
STATUSES = ("投递成功", "被查看", "有意向", "邀面试", "不合适")


def history_identity(url):
    parsed = urlsplit(url)
    pattern = r"/jobdetail/([A-Za-z0-9_-]+)\.htm" if parsed.hostname == "www.zhaopin.com" else r"/([A-Za-z0-9_-]+)\.htm"
    match = re.fullmatch(pattern, parsed.path)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"www.zhaopin.com", "jobs.zhaopin.com"}
            or parsed.username or parsed.password or parsed.port not in {None, 80, 443} or not match):
        raise BrowserWorkError("zhilian_history_identity_unknown", "An official history row has no exact job identity.")
    return match[1], f"https://www.zhaopin.com/jobdetail/{match[1]}.htm"


def rows(page):
    return page.locator(".ji-item").evaluate_all("""els => els.map(e => ({
        url:e.querySelector('.ji-item-info-jobName')?.href || '',
        title:e.querySelector('.ji-item-info-jobName')?.innerText.trim() || '',
        company:e.querySelector('.ji-item-info-companyName')?.innerText.trim() || '',
        status:e.querySelector('.ji-item-status p')?.innerText.trim() || '',
        recorded_at:e.querySelector('.ji-item-status-timer')?.innerText.trim() || ''
    }))""")


def _settled(page, status, previous=None):
    deadline = time.monotonic() + 12
    stable, last = 0, None
    while time.monotonic() < deadline:
        selected = page.locator('.st-nav > ul > li.on > a').inner_text().strip()
        current = rows(page)
        empty = page.get_by_text('没有记录，好可怕 ~~~', exact=True).is_visible()
        valid = selected == status and (bool(current) or empty)
        # Category selection can update before its data arrives. Require the
        # old list to change and a stable new snapshot; never assume an empty
        # loading surface proves absence.
        if valid and current != previous and current == last:
            stable += 1
            if stable >= 2:
                return current
        else:
            stable = 0
        last = current
        page.wait_for_timeout(250)
    raise BrowserWorkError("zhilian_history_loading_unknown", "The selected history category did not finish loading; no absence or send permission was inferred.")


def _click_and_wait(page, control):
    # Observe the request caused by the visible UI. Never call this endpoint
    # ourselves or replay authentication/session material.
    with page.expect_response(lambda response: (
            urlsplit(response.url).hostname == 'fe-api.zhaopin.com'
            and urlsplit(response.url).path == '/c/i/schedule/feedback'), timeout=12000) as event:
        control.click()
    response = event.value
    response.finished()
    if response.status != 200:
        raise BrowserWorkError('zhilian_history_request_failed', 'The visible history navigation failed; no send was attempted.')


def read_history(runtime, work, account_label, *, refresh=False):
    from jobagent.application.playwright_work import pace
    path = state.STATE_DIR / 'playwright_history' / f"{runtime.session['id']}-zhilian.json"
    binding = {k:work['binding'][k] for k in ('account_ref', 'round_id')}
    cached = state.load_json(path) or {}
    if (not refresh and cached.get('complete') and cached.get('binding') == binding
            and cached.get('account_label') == account_label
            and 0 <= time.time() - cached.get('observed_epoch', 0) < 300):
        return cached
    page, guard = runtime.auxiliary_page(work, HISTORY_URL, purpose='history', refresh=True)
    page.locator('.zp-welcome__username').wait_for(state='visible')
    if page.locator('.zp-welcome__username').inner_text().strip() != account_label:
        raise BrowserWorkError('zhilian_history_account_changed', 'History belongs to another platform account.')
    entries, pages = {}, []
    for label in STATUSES:
        link = page.locator('.st-nav > ul > li > a').filter(has_text=re.compile('^' + label + r'\s*$'))
        if link.count() != 1:
            raise BrowserWorkError('zhilian_history_categories_unknown', 'All five official application categories must be available.')
        selected = page.locator('.st-nav > ul > li.on > a').inner_text().strip()
        if selected != label:
            pace(runtime.session)
            _click_and_wait(page, link)
        if label == '邀面试':
            all_interviews = page.locator('.st-nav-subnav:visible > a').filter(has_text=re.compile('^全部$'))
            if all_interviews.count() == 1 and 'on' not in (all_interviews.get_attribute('class') or '').split():
                pace(runtime.session)
                _click_and_wait(page, all_interviews)
        current = _settled(page, label)
        seen = set()
        for index in range(1, 101):
            guard.check()
            identifiers = []
            for row in current:
                identifier, url = history_identity(row['url'])
                if not row['title'] or not row['company'] or not row['status'] or not row['recorded_at'] or identifier in seen:
                    raise BrowserWorkError('zhilian_history_incomplete', 'A history row is incomplete or a page repeated.')
                seen.add(identifier); identifiers.append(identifier)
                entries[identifier] = {**row, 'id':identifier, 'url':url, 'category':label}
            pages.append({'category':label,'page':index,'count':len(current)})
            next_page = page.locator('.ivu-page-next')
            if not current:
                if next_page.count():
                    raise BrowserWorkError('zhilian_history_incomplete', 'An empty history category still has pagination.')
                break
            active_page = page.locator('.ivu-page-item-active')
            if (next_page.count() != 1 or active_page.count() != 1
                    or active_page.inner_text().strip() != str(index)):
                raise BrowserWorkError('zhilian_history_pagination_unknown', 'The complete history page boundary is unavailable.')
            if 'ivu-page-disabled' in (next_page.get_attribute('class') or '').split():
                break
            pace(runtime.session)
            _click_and_wait(page, next_page)
            current = _settled(page, label, current)
        else:
            raise BrowserWorkError('zhilian_history_limit', 'History exceeds the bounded read budget; no absence was inferred.')
    result = {'binding':binding,'account_label':account_label,'complete':True,'entries':entries,
              'total':len(entries),'pages':pages,'page_url':page.url,
              'observed_at':rounds.utc_now(),'observed_epoch':time.time()}
    state.save_json(path,result)
    if runtime.page:
        runtime.page.bring_to_front()
    return result
