"""Read the official application list once; never infer absence from one page."""
from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

from jobagent.infra import state, rounds
from jobagent.infra.browser_work import BrowserWorkError


HISTORY_URL = "https://c.liepin.com/job/record/apply"


def history_identity(url):
    parsed = urlsplit(url)
    match = re.fullmatch(r"/(?:a|job)/(\d+)\.shtml", parsed.path)
    if parsed.scheme != "https" or parsed.hostname != "www.liepin.com" or not match:
        raise BrowserWorkError("liepin_history_identity_unknown", "An official history row has no exact job identity.")
    return match[1], f"https://www.liepin.com{parsed.path}"


def _rows(page):
    return page.locator(".record-list-box .jobCardPcContainer").evaluate_all("""elements => elements.map(el => ({
        url: el.querySelector('[data-nick="job-detail-job-info"]')?.href || '',
        title: el.querySelector('.jobTitleBox .ellipsis-1')?.innerText.trim() || '',
        company: el.querySelector('.companyName')?.innerText.trim() || '',
        text: el.innerText.trim()
    }))""")


def read_history(runtime, work, account_label, *, refresh=False):
    from jobagent.application.playwright_work import pace
    path = state.STATE_DIR / "playwright_history" / f"{runtime.session['id']}-liepin.json"
    cached = state.load_json(path) or {}
    binding = {key: work["binding"][key] for key in ("account_ref", "round_id")}
    valid_cache = (cached.get("complete") and cached.get("binding") == binding
            and cached.get("account_label") == account_label
            and 0 <= time.time() - cached.get("observed_epoch", 0) < 300)
    if valid_cache and not refresh:
        return cached
    # A stale open tab may predate applications made since its last load. A
    # new complete scan must start from a fresh first page and current total.
    page, guard = runtime.auxiliary_page(work, HISTORY_URL, purpose="history", refresh=True)
    page.locator(".header-quick-menu-username").wait_for(state="visible")
    if page.locator(".header-quick-menu-username").inner_text().strip() != account_label:
        raise BrowserWorkError("liepin_history_account_changed", "History belongs to a different platform account.")
    first = page.locator('.ant-c-pagination-item[title="1"]')
    if first.count() and "item-active" not in (first.get_attribute("class") or ""):
        old = page.locator('.record-list-box [data-nick="job-detail-job-info"]').first
        old_url = old.evaluate('e=>e.href') if old.count() else None
        pace(runtime.session)
        first.click()
        page.locator('.ant-c-pagination-item-active[title="1"]').wait_for()
        if old_url:
            page.wait_for_function('previous => document.querySelector(\'.record-list-box [data-nick="job-detail-job-info"]\')?.href !== previous', arg=old_url, timeout=12000)
    body = page.locator("body").inner_text()
    count = re.search(r"我的投递\s+(\d+)", body)
    if not count:
        raise BrowserWorkError("liepin_history_count_unknown", "The official history total is unavailable; absence cannot be assumed.")
    expected_total = int(count[1])
    if expected_total:
        page.locator(".record-list-box .jobCardPcContainer").first.wait_for()
    if valid_cache and refresh:
        prefix = {}
        for row in _rows(page):
            identifier, url = history_identity(row["url"])
            if not row["title"] or identifier in prefix:
                raise BrowserWorkError("liepin_history_page_repeated", "The refreshed history page is not a distinct verified prefix.")
            prefix[identifier] = {**row, "url": url, "id": identifier, "page": 1}
        merged = {**cached["entries"], **prefix}
        # A complete recent index plus newly observed rows must exactly match
        # the current official count. Otherwise read the full list again.
        if prefix and len(merged) == expected_total:
            result = {**cached, "entries": merged, "total": expected_total,
                      "observed_at": rounds.utc_now(), "observed_epoch": time.time(),
                      "refreshed_first_page": len(prefix)}
            state.save_json(path, result)
            runtime.page.bring_to_front()
            return result
    entries = {}
    pages = []
    for index in range(1, 101):
        guard.check()
        if expected_total:
            page.locator(".record-list-box .jobCardPcContainer").first.wait_for()
        rows = _rows(page)
        if not rows and expected_total:
            raise BrowserWorkError("liepin_history_incomplete", "The history page is empty before the declared total was reached.")
        for row in rows:
            identifier, url = history_identity(row["url"])
            if not row["title"] or identifier in entries:
                raise BrowserWorkError("liepin_history_page_repeated", "The application history did not advance to a distinct page.")
            entries[identifier] = {**row, "url": url, "id": identifier, "page": index}
        pages.append({"page": index, "row_count": len(rows)})
        if len(entries) == expected_total:
            break
        next_page = page.locator(".ant-c-pagination-next")
        if (len(entries) > expected_total or next_page.count() != 1
                or next_page.get_attribute("aria-disabled") == "true"):
            raise BrowserWorkError("liepin_history_incomplete", "Observed history rows do not match the official total.")
        pace(runtime.session)
        next_page.click()
        page.locator(f'.ant-c-pagination-item-active[title="{index + 1}"]').wait_for()
        # The paginator may update before its network result. Wait for the old
        # first link to disappear, without clicking again or reloading.
        if rows:
            page.locator('.record-list-box [data-nick="job-detail-job-info"]').first.wait_for()
            deadline = time.monotonic() + 12
            while page.locator('.record-list-box [data-nick="job-detail-job-info"]').first.evaluate('e=>e.href') == rows[0]["url"]:
                if time.monotonic() >= deadline:
                    raise BrowserWorkError("liepin_history_page_stalled", "The history page did not load; no navigation retry was made.")
                page.wait_for_timeout(200)
    if len(entries) != expected_total:
        raise BrowserWorkError("liepin_history_incomplete", "History exceeds the bounded read budget; do not infer no prior application.")
    result = {"binding": binding, "account_label": account_label, "complete": True,
              "observed_at": rounds.utc_now(), "observed_epoch": time.time(),
              "page_url": page.url, "total": expected_total, "pages": pages, "entries": entries}
    state.save_json(path, result)
    if runtime.page:
        runtime.page.bring_to_front()
    return result
