"""Verified Boss detail/conversation controls and durable single-send receipts."""
from __future__ import annotations

import time
from urllib.parse import parse_qs, urljoin, urlsplit

from jobagent.infra.browser_work import BrowserWorkError

CHAT_URL = "https://www.zhipin.com/web/geek/chat"
ACCOUNT = ".nav-figure .label-text"
ROOT = ".chat-conversation"
RECEIPT_WAIT_SECONDS = 12


class BossDelivery:
    def __init__(self, runtime):
        self.runtime = runtime
        self.page = None
        self.identity = {}
        self.friend = False
        self.unavailable = ""

    def _text(self, selector):
        node = self.page.locator(selector)
        if node.count() != 1 or not node.is_visible():
            raise BrowserWorkError("boss_identity_ambiguous", "One visible identity element is required.")
        return node.inner_text().strip()

    def _account(self):
        self.runtime.guard.check()
        body = self.page.locator("body").inner_text()[:1200]
        if any(text in body for text in ("安全验证", "拖动滑块", "访问异常", "环境存在异常")):
            raise BrowserWorkError("boss_verification_required", "Preserve the current page for user verification; do not retry.")
        self.page.locator(ACCOUNT).wait_for(state="visible")
        account = self._text(ACCOUNT)
        expected = self.runtime.session.get("accounts", {}).get("boss")
        if not account or (expected and account != expected):
            raise BrowserWorkError("playwright_account_changed", "The Boss account differs from the preserved session.")
        return account

    def _message_page(self, work):
        task = {**work["task"], "url": CHAT_URL}
        task.pop("job", None)
        self.page = self.runtime.job_page({**work, "task": task})
        account = self._account()
        if self.identity.get("account_label", account) != account:
            raise BrowserWorkError("playwright_account_changed", "The account changed while opening existing history.")
        self.identity.update(self.runtime.evidence(work), account_label=account, login_state="authenticated")
        self.page.locator(".chat-user").wait_for(state="visible")

    def _detail(self, work):
        self.page = self.runtime.job_page(work)
        account = self._account()
        job = work["task"]["job"]
        self.page.locator(".job-banner h1").wait_for(state="visible")
        title = self._text(".job-banner h1")
        company = self._text('.sider-company .company-info a[ka="job-detail-company_custompage"]')
        if (not self.runtime.same_route(self.page.url, job["url"])
                or title != job["title"] or company != job["company"]):
            raise BrowserWorkError("boss_job_changed", "The approved detail route, title and company must all match.")
        self.identity = {**self.runtime.evidence(work), "account_label": account, "login_state": "authenticated",
                         "job_id": str(job["id"]), "job_url": job["url"], "title": title, "company": company}
        status = self._text(".job-banner .job-status")
        if status in {"已关闭", "已下线", "职位已关闭", "职位已下线", "停止招聘", "招聘已结束"}:
            self.unavailable = status
            return
        if status != "招聘中":
            raise BrowserWorkError("boss_availability_unknown", "An unknown job status cannot authorize communication.")
        control = self.page.locator(".job-banner .btn-startchat:visible")
        if control.count() != 1:
            raise BrowserWorkError("boss_communication_control_unknown", "A unique job-bound communication control is required.")
        target = urlsplit(urljoin(self.page.url, control.get_attribute("redirect-url") or ""))
        query = parse_qs(target.query)
        if (target.scheme != "https" or target.hostname != "www.zhipin.com"
                or target.path != "/web/geek/chat" or query.get("jobId") != [str(job["id"])]
                or len(query.get("id", [])) != 1):
            raise BrowserWorkError("boss_recipient_unverified", "The visible communication control is not bound to this exact job and recruiter.")
        recruiter = self.page.locator(".job-boss-info h2.name")
        if recruiter.count() != 1:
            raise BrowserWorkError("boss_recipient_unverified", "Read the actual recruiter on the approved detail.")
        name = recruiter.evaluate("e => [...e.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent).join('').trim()")
        if not name:
            raise BrowserWorkError("boss_recipient_unverified", "The recruiter name is unavailable.")
        self.identity.update(recruiter_name=name, recruiter_id=query["id"][0])
        relation = control.get_attribute("data-isfriend")
        text = control.inner_text().strip()
        if (relation, text) not in {("true", "继续沟通"), ("false", "立即沟通")}:
            raise BrowserWorkError("boss_relationship_unknown", "The observed relationship and communication control disagree.")
        self.friend = relation == "true"

    def prepare(self, work):
        if work["action"] not in {"inspect_session", "inspect_delivery", "open_communication", "send_greeting"}:
            raise BrowserWorkError("boss_action_not_validated", "This Boss action is not supported.")
        if work["action"] == "inspect_session":
            self._message_page(work)
            return
        conversation_only = (work["action"] == "send_greeting"
                             or work["task"].get("inspection_phase") == "after_communication"
                             or (work["action"] == "open_communication" and work["state"] != "ready"))
        if conversation_only:
            self._message_page(work)
            self._conversation(work)
        else:
            self._detail(work)
            if self.friend and not self.unavailable:
                self._message_page(work)
                self._conversation(work)
        if work["action"] == "send_greeting" and work["state"] == "ready":
            observed = self._conversation(work)
            draft = self._text("#chat-input")
            if observed["message_state"] == "unknown" or draft not in {"", work["task"]["job"]["cloud_greeting"]}:
                raise BrowserWorkError("boss_greeting_precondition_unknown", "Preserve the pending message or existing draft; no send intent was issued.")

    def _select_conversation(self, work):
        job = work["task"]["job"]
        card = self.page.locator(".chat-position-content:visible")
        if card.count() == 1:
            current = card.evaluate("e => e.__vue__?.conversation$?.encryptJobId || ''", isolated_context=False)
            if current == str(job["id"]):
                return
        items = self.page.locator('.chat-user li[role="listitem"]')
        def matches():
            rows = items.evaluate_all("""nodes => nodes.map(n => {
                const c=n.__vue__?.$props?.source;
                return {id:c?.encryptJobId || '', name:c?.name || '', company:c?.brandName || '',
                    visible:n.innerText};
            })""", isolated_context=False)
            return [i for i, r in enumerate(rows) if r["id"] == str(job["id"])
                    and r["name"] and r["name"] in r["visible"]
                    and r["company"] == job["company"] and r["company"] in r["visible"]]
        indices = matches()
        if not indices:
            name = (work["task"].get("boss_recruiter_identity") or self.identity).get("recruiter_name")
            search = self.page.locator(".boss-search-input")
            if name and search.count() == 1 and search.input_value() in {"", name}:
                search.fill(name)
                deadline = time.monotonic() + 8
                while not indices and time.monotonic() < deadline:
                    self.page.wait_for_timeout(200)
                    indices = matches()
        if len(indices) != 1:
            raise BrowserWorkError("boss_conversation_not_found", "No unique existing conversation is bound to the exact approved job; do not communicate again.")
        items.nth(indices[0]).click()
        self.page.locator(ROOT + " #chat-input").wait_for(state="visible")

    def _conversation(self, work):
        self._account()
        if not self.runtime.same_route(self.page.url, CHAT_URL):
            raise BrowserWorkError("boss_message_center_required", "Read and send only in the official Boss message center.")
        self._select_conversation(work)
        root = self.page.locator(ROOT + ":visible")
        if root.count() != 1:
            raise BrowserWorkError("boss_conversation_ambiguous", "Exactly one active conversation is required.")
        # Read only the data bound to the displayed job card, never a store,
        # API, component method, credential or unrelated conversation.
        observed = root.locator(".chat-position-content").evaluate("""el => {
            const c=el.__vue__?.conversation$;
            if(!c)return null;
            return {id:c.encryptJobId, title:c.jobName, company:c.brandName,
                    recruiter_name:c.name, recruiter_id:c.encryptBossId};
        }""", isolated_context=False)
        job = work["task"]["job"]
        name = self._text(ROOT + " .base-info .name-text")
        company = self._text(ROOT + " .base-info > span:not(.base-title)")
        title = self._text(ROOT + " .position-name")
        anchor = work["task"].get("boss_recruiter_identity") or self.identity
        if (not observed or observed.get("id") != str(job["id"]) or observed.get("title") != title
                or title != job["title"] or observed.get("company") != company or company != job["company"]
                or observed.get("recruiter_name") != name or not observed.get("recruiter_id")
                or any(anchor.get(k) and anchor[k] != observed.get(k) for k in ("recruiter_name", "recruiter_id"))):
            raise BrowserWorkError("boss_conversation_identity_unknown", "The displayed job, company and recruiter must agree with the exact bound conversation.")
        self.identity.update(self.runtime.evidence(work), job_id=str(observed["id"]), job_url=job["url"],
                             title=title, company=company, recruiter_name=name, recruiter_id=observed["recruiter_id"])
        messages = root.locator(".im-list .message-item.item-myself").evaluate_all("""nodes => nodes.map(n => ({
            id:n.getAttribute('data-mid') || '',
            text:(n.querySelector('.text-content')?.innerText || '').trim(),
            status:(n.querySelector('.message-status')?.innerText || '').trim(),
            failed:!!n.querySelector('.status-error,.status-fail,.message-error'),
            pending:!!n.querySelector('.status-loading,.status-sending')
        }))""")
        greeting = job.get("cloud_greeting")
        matches = [m for m in messages if greeting and m["text"] == greeting]
        acknowledged = [m for m in matches if m["id"] and m["status"] in {"送达", "已读"}
                        and not m["failed"] and not m["pending"]]
        return {"communication_state": "open", "conversation_job_verified": True,
                "existing_outgoing_text": greeting if matches else "",
                "message_state": ("delivered" if len(acknowledged) == len(matches) else "unknown") if matches else "not_sent",
                "messages": messages, "acknowledged_ids": [m["id"] for m in acknowledged],
                "history_checked": True, "resume_state": "not_applicable", "receipt_checked": True}

    def _send(self, work, may_act):
        from jobagent.application.playwright_work import receipt_path
        from jobagent.infra import rounds, state
        greeting = work["task"]["job"]["cloud_greeting"]
        observed = self._conversation(work)
        path = receipt_path(work["work_id"]).with_suffix(".preflight.json")
        if may_act:
            if observed["message_state"] == "delivered":
                return {"outcome": "success", "evidence": {**self.identity, **observed,
                    "outgoing_text": greeting, "already_present": True, "submission_attempted": False}}
            if observed["message_state"] != "not_sent":
                raise BrowserWorkError("boss_greeting_pending", "The existing message needs reconciliation; do not send again.")
            editor = self.page.locator("#chat-input")
            if self._text("#chat-input") not in {"", greeting}:
                raise BrowserWorkError("boss_existing_draft", "Preserve the existing draft.")
            editor.fill(greeting)
            if self._text("#chat-input") != greeting:
                raise BrowserWorkError("boss_greeting_changed", "The editor differs from the exact approved text.")
            fresh = self._conversation(work)  # Recheck after typing.
            if fresh["message_state"] != "not_sent":
                raise BrowserWorkError("boss_message_changed_before_send", "The message history changed during preparation; preserve the draft without sending.")
            button = self.page.locator(ROOT + ' button[type="send"].btn-send:visible')
            if button.count() != 1 or not button.is_enabled() or "disabled" in (button.get_attribute("class") or ""):
                raise BrowserWorkError("boss_send_unavailable", "No enabled send control is available.")
            state.save_json(path, {"binding": work["binding"], "nonce": work["nonce"], "greeting": greeting,
                "recruiter_id": self.identity["recruiter_id"], "account_label": self.identity["account_label"],
                "prior_ids": [m["id"] for m in observed["messages"]], "observed_at": rounds.utc_now()})
            button.click()
        checkpoint = state.load_json(path) or {}
        if (checkpoint.get("binding") != work["binding"] or checkpoint.get("nonce") != work["nonce"]
                or checkpoint.get("greeting") != greeting or checkpoint.get("recruiter_id") != self.identity["recruiter_id"]
                or checkpoint.get("account_label") != self.identity["account_label"]):
            raise BrowserWorkError("boss_send_checkpoint_missing", "The attempted message has no matching durable checkpoint; never resend.")
        deadline = time.monotonic() + (RECEIPT_WAIT_SECONDS if may_act else 0)
        while True:
            observed = self._conversation(work)
            new_ids = [mid for mid in observed["acknowledged_ids"] if mid not in checkpoint["prior_ids"]]
            if len(new_ids) == 1 and observed["message_state"] == "delivered" and not self._text("#chat-input"):
                return {"outcome": "success", "evidence": {**self.identity, **observed,
                    "outgoing_text": greeting, "message_id": new_ids[0]}}
            if time.monotonic() >= deadline:
                raise BrowserWorkError("boss_greeting_receipt_pending", "No new exact acknowledged message is visible; never resend.")
            self.page.wait_for_timeout(250)

    def execute(self, work, *, may_act):
        action = work["action"]
        if action == "inspect_session":
            if self.page.locator('a[ka="header-resume"]').count() != 1:
                raise BrowserWorkError("boss_account_activity_unknown", "Verify the account navigation and existing message area.")
            return {"outcome": "success", "evidence": {**self.identity, "account_navigation": True, "resume_or_activity": True}}
        if self.unavailable:
            return {"outcome": "unavailable", "evidence": {**self.identity, "receipt_checked": True,
                "availability": "unavailable", "unavailable_text": self.unavailable}}
        if action == "inspect_delivery":
            observed = self._conversation(work) if self.runtime.same_route(self.page.url, CHAT_URL) else {
                "history_checked": True, "receipt_checked": True, "communication_state": "not_open",
                "resume_state": "not_applicable", "existing_outgoing_text": "", "message_state": "not_sent"}
            return {"outcome": "success", "evidence": {**self.identity, **observed}}
        if action == "send_greeting":
            return self._send(work, may_act)
        if action == "open_communication":
            if may_act and not self.friend:
                from jobagent.application.playwright_work import receipt_path
                from jobagent.infra import rounds, state
                self._account()
                state.save_json(receipt_path(work["work_id"]).with_suffix(".preflight.json"),
                    {"binding": work["binding"], "nonce": work["nonce"], "recruiter_id": self.identity["recruiter_id"],
                     "observed_at": rounds.utc_now()})
                self.page.locator(".job-banner .btn-startchat:visible").click()
            self._message_page(work)
            observed = self._conversation(work)
            return {"outcome": "success", "evidence": {**self.identity, **observed,
                "default_greeting_observed": bool(observed["messages"])}}
        raise BrowserWorkError("boss_action_not_validated", "Unsupported Boss action.")
