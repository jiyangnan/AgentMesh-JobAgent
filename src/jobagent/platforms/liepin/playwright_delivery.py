"""DOM-based Liepin observations and individually leased recruiting actions."""
from __future__ import annotations

import re
import time

from jobagent.infra.browser_work import BrowserWorkError
from jobagent.application.native_liepin_identity import eligible, validate
from jobagent.platforms.liepin.playwright_history import read_history


def control_matches(job_id, control_id) -> bool:
    """Liepin's chat control carries an internal job id, not the URL id.

    The URL slug keeps a numeric job id whose suffix is the internal id used by
    the chat control (for example URL 1986207107 -> control 86207107). Only a
    numeric suffix of comparable length is accepted.
    """
    job_id, control_id = str(job_id or ""), str(control_id or "")
    if not (job_id.isdigit() and control_id.isdigit()):
        return False
    if control_id == job_id:
        return True
    return (len(control_id) >= 6 and len(job_id) - len(control_id) <= 3
            and job_id.endswith(control_id))


class LiepinDelivery:
    def __init__(self, runtime):
        self.runtime = runtime
        self.page = None
        self.identity = None

    def _chat_control(self, job_id):
        controls = self.page.locator('[data-tlg-elem-id="c_pc_job_detail_chat_btn"]')
        if controls.count() != 1:
            raise BrowserWorkError("liepin_job_control_unverified", "The job-bound communication control is unavailable.")
        if not control_matches(job_id, controls.first.get_attribute("data-jobid")):
            raise BrowserWorkError("liepin_job_control_unverified", "The job-bound communication control is unavailable.")
        return controls.first

    def _open_chat(self, job_id):
        control = self._chat_control(job_id)
        for attempt in range(2):
            try:
                # A slow page or transient overlay can defeat the default 12s
                # action timeout. Opening a conversation is idempotent and sends
                # no message, so one bounded retry is safe.
                control.click(timeout=30000)
                return
            except Exception:
                if attempt:
                    raise

    def _company_name(self):
        legacy = self.page.locator("main > content > .company-container .company-name")
        if legacy.count() == 1 and legacy.first.is_visible():
            return legacy.first.inner_text().strip()
        # The current detail layout keeps the employer in the aside company card.
        card = self.page.locator("main > aside .company-info-container .company-card .content .name")
        if card.count() == 1 and card.first.is_visible():
            return card.first.inner_text().strip()
        return ""

    def _text(self, selector):
        locator = self.page.locator(selector)
        if locator.count() != 1:
            raise BrowserWorkError("liepin_dom_identity_ambiguous", f"Expected one visible identity element: {selector}")
        return locator.inner_text().strip()

    def prepare(self, work):
        try:
            self._prepare(work)
        except BrowserWorkError:
            if work.get("state") == "ready":
                raise
            # Reconciliation cannot reissue the action here; execute settles it.

    def _prepare(self, work):
        if work["action"] not in {"inspect_session", "inspect_delivery", "inspect_resume_selection", "open_communication", "prepare_resume", "submit_resume", "send_greeting"}:
            raise BrowserWorkError("liepin_action_not_validated", "This adapter action still needs DOM validation; no external action intent was issued.")
        self.page = self.runtime.job_page(work)
        self.page.locator(".header-quick-menu-username").wait_for(state="visible")
        account = self._text(".header-quick-menu-username")
        expected = self.runtime.session.get("accounts", {}).get("liepin")
        if not account or (expected and account != expected):
            raise BrowserWorkError("playwright_account_changed", "The visible platform account changed; no action was issued.")
        if work["action"] == "inspect_session":
            self.identity = {**self.runtime.evidence(work), "account_label": account}
            return
        job = work["task"]["job"]
        self.page.locator(".job-apply-container .job-title").wait_for(state="visible")
        observed = {**self.runtime.evidence(work), "account_label": account, "login_state": "authenticated",
                    "job_id": str(job["id"]), "job_url": job["url"],
                    "title": self._text(".job-apply-container .job-title")}
        if not self.runtime.same_route(self.page.url, job["url"]) or observed["title"] != job["title"]:
            raise BrowserWorkError("liepin_job_changed", "The current detail differs from the approved job.")
        self._chat_control(job["id"])
        if eligible(work):
            # This scope excludes all recommendations and other jobs' companies.
            disclosed = self._company_name()
            if disclosed:
                observed["company"] = disclosed
            else:
                observed.update(company=None, company_visibility="not_disclosed",
                    recruiter_name=self._text("main > content > .recruiter-container .name-box .name"),
                    recruiter_company=self._text("main > content > .recruiter-container .title-box span:nth-child(2)").lstrip("· "),
                    detail_salary=self._text(".job-apply-container .salary"),
                    detail_area=self._text(".job-apply-container .job-properties span:first-child"))
        else:
            company = self._company_name()
            if not company:
                raise BrowserWorkError("liepin_identity_ambiguous", "One visible company identity is required.")
            observed["company"] = company
        # Initial detail validation is read-only. Later anonymous jobs also
        # require the independently observed conversation identity below.
        if work["action"] == "inspect_delivery" and work["task"].get("inspection_phase") != "after_communication":
            validate(work, observed)
        self.identity = observed
        self.prepared_choice = None
        if work["state"] == "ready" and work["action"] == "submit_resume":
            conversation = self._conversation(work)
            dialog, choice = self._resume_dialog(work, may_open=True)
            from jobagent.application import native_work as native
            from jobagent.infra import browser_work as store
            prepared = [w for w in store.list_work(native._binding())
                        if w["binding"] == work["binding"] and w["action"] == "prepare_resume"
                        and w["state"] == "closed" and (w.get("result") or {}).get("outcome") == "success"]
            old_options = prepared[0]["result"]["evidence"]["attachment_options"] if len(prepared) == 1 else []
            if ({o["reference"] for o in old_options} != {o["reference"] for o in choice["attachment_options"]}
                    or choice["resume_reference"] != work["task"]["resume_reference"]
                    or work["task"].get("submission_mode") != choice["submission_mode"]):
                raise BrowserWorkError("liepin_resume_options_changed", "Resume choices changed since confirmation; no submission intent was issued.")
            selected = work["task"]["attachment_reference"]
            indices = [i for i, o in enumerate(choice["attachment_options"]) if o["reference"] == selected]
            if len(indices) != 1:
                raise BrowserWorkError("liepin_attachment_changed", "The exact confirmed attachment is unavailable.")
            dialog.locator('input[type="radio"]').nth(indices[0]).check()
            _, choice = self._resume_dialog(work, may_open=False)
            if [o["reference"] for o in choice["attachment_options"] if o["selected"]] != [selected]:
                raise BrowserWorkError("liepin_attachment_selection_unknown", "The observed checked attachment differs from the confirmed selection.")
            self.prepared_choice = choice
        if work["state"] == "ready" and work["action"] == "send_greeting":
            conversation = self._conversation(work)
            editor = self.page.locator("textarea.im-ui-textarea:visible")
            if conversation["message_state"] == "unknown" or editor.input_value() not in {"", job["cloud_greeting"]}:
                raise BrowserWorkError("liepin_greeting_precondition_unknown", "Preserve the pending message or existing draft; no send intent was issued.")

    def _online_resume(self, work):
        cache = getattr(self.runtime, "liepin_resume_cache", None)
        if cache and cache["account"] == self.identity["account_label"]:
            return cache["reference"]
        navigation = self.page
        if self.page.locator("textarea.im-ui-textarea:visible").count():
            # The chat drawer covers the detail header. Read the account entry
            # in our separate history tab, preserving the open conversation.
            from jobagent.platforms.liepin.playwright_history import HISTORY_URL
            navigation, _ = self.runtime.auxiliary_page(work, HISTORY_URL, purpose="history")
        navigation.locator(".header-quick-menu-username").hover()
        link = navigation.get_by_role("link", name="我的简历", exact=True)
        link.wait_for(state="visible")
        target = link.get_attribute("href")
        resume, guard = self.runtime.auxiliary_page(work, target, purpose="resume")
        selected = resume.locator('[class*="headerSelect--"] span')
        selected.wait_for(state="visible")
        guard.check()
        if selected.count() != 1:
            raise BrowserWorkError("liepin_online_resume_ambiguous", "Read the selected online resume without changing it.")
        reference = selected.inner_text().strip()
        if not reference:
            raise BrowserWorkError("liepin_online_resume_unknown", "The selected online resume name is unavailable.")
        self.runtime.liepin_resume_cache = {"account": self.identity["account_label"], "reference": reference}
        self.page.bring_to_front()
        return reference

    def _conversation(self, work):
        editor = self.page.locator("textarea.im-ui-textarea:visible")
        if editor.count() != 1:
            return {"communication_state": "not_open", "conversation_job_verified": False,
                    "existing_outgoing_text": "", "message_state": "not_sent"}
        root = self.page.locator(".im-ui-chat-container:visible")
        if root.count() != 1:
            raise BrowserWorkError("liepin_conversation_identity_unknown", "Select one visible conversation before reading its receipt.")
        header = root.locator(":scope > div").first.inner_text().strip()
        key = (work["binding"]["job_id"], self.runtime.target_id(self.page), header)
        cache = getattr(self.runtime, "liepin_conversation_routes", {})
        url = cache.get(key)
        if not url:
            chooser = self.page.get_by_role("dialog").filter(has_text="选择附件简历")
            if chooser.count():
                if work["action"] not in {"prepare_resume", "submit_resume"} or chooser.count() != 1:
                    raise BrowserWorkError("liepin_chooser_blocks_observation", "Preserve the resume chooser while verifying its current task.")
                # Only dismiss our cancellable chooser to read its conversation.
                # A ready submission reopens and verifies all choices afterward.
                # Liepin nests a visible icon button inside a zero-size outer
                # Close button. Use the visible icon in that same modal.
                icon = chooser.locator('.ant-im-modal-close [aria-label="close"]:visible')
                close = icon if icon.count() == 1 else chooser.locator('button[aria-label="Close"]:visible')
                if close.count() != 1:
                    raise BrowserWorkError("liepin_chooser_close_unknown", "The chooser's visible cancel control is unavailable.")
                close.click()
                chooser.wait_for(state="hidden")
            # The visible job-card icon opens its actual official detail. Do
            # not infer the job ID from the page underneath the conversation.
            control = root.locator('[aria-label="work"]')
            if control.count() != 1:
                raise BrowserWorkError("liepin_conversation_job_control_unknown", "The conversation's job detail control is unavailable.")
            from jobagent.application.playwright_work import pace
            pace(self.runtime.session)
            with self.page.expect_popup(timeout=7000) as popup:
                control.click()
            opened = popup.value
            try:
                opened.wait_for_url("https://www.liepin.com/**", wait_until="commit", timeout=12000)
                url = opened.url
            finally:
                opened.close()
                self.page.bring_to_front()
            if root.locator(":scope > div").first.inner_text().strip() != header:
                raise BrowserWorkError("liepin_conversation_changed", "The conversation changed during its job-route check.")
            if self.runtime.same_route(url, work["task"]["job"]["url"]):
                cache[key] = url
                self.runtime.liepin_conversation_routes = cache
        snapshot = root.evaluate("""root => {
            const visible = e => !!e && !e.classList.contains('hide') &&
                getComputedStyle(e).display !== 'none' && getComputedStyle(e).visibility !== 'hidden';
            const messages = [...root.querySelectorAll('.im-ui-message-item-send')].map(el => ({
                text: (el.querySelector('.im-ui-txt.send .text')?.innerText || '').trim(),
                status: (el.querySelector('.im-ui-message-read-status')?.innerText || '').trim(),
                pending: [...el.querySelectorAll('.im-ui-message-item-loadingicon')].some(visible),
                failed: [...el.querySelectorAll('[class*="error"],[class*="fail"]')].some(visible),
                attachment: (el.querySelector('.im-ui-send-attachment-card')?.innerText || '').trim()
            }));
            return {text: root.innerText, messages};
        }""")
        matching = self.runtime.same_route(url, work["task"]["job"]["url"])
        recruiter = self.identity.get("recruiter_name")
        verified = matching and (not recruiter or recruiter in snapshot["text"])
        if not verified:
            raise BrowserWorkError("liepin_conversation_identity_unknown", "The open conversation has no verified link to this exact approved job.")
        greeting = work["task"]["job"].get("cloud_greeting")
        matches = [m for m in snapshot["messages"] if greeting and m["text"] == greeting]
        delivered = [m for m in matches if m["status"] in {"未读", "已读"} and not m["pending"] and not m["failed"]]
        return {"communication_state": "open", "conversation_job_verified": True,
                "existing_outgoing_text": greeting if matches else "",
                "message_state": "sent" if matches and len(delivered) == len(matches) else ("unknown" if matches else "not_sent"),
                "observed_outgoing": [m["text"] for m in snapshot["messages"] if m["text"]],
                "greeting_count": len(matches), "acknowledged_greeting_count": len(delivered),
                "resume_cards": [m["attachment"] for m in snapshot["messages"] if m["attachment"]],
                "resume_receipts": [{"text": m["attachment"], "acknowledged": m["status"] in {"未读", "已读"}
                    and not m["pending"] and not m["failed"]} for m in snapshot["messages"] if m["attachment"]],
                "acknowledged_resume_cards": [m["attachment"] for m in snapshot["messages"] if m["attachment"]
                    and m["status"] in {"未读", "已读"} and not m["pending"] and not m["failed"]]}

    def _resume_dialog(self, work, *, may_open):
        dialog = self.page.get_by_role("dialog").filter(has_text="选择附件简历")
        if dialog.count() == 0 and may_open:
            control = self.page.locator(".im-ui-chat-container:visible .action-resume")
            if control.count() != 1 or control.inner_text().strip() not in {"发简历", "发送简历"}:
                raise BrowserWorkError("liepin_resume_chooser_unknown", "The verified conversation's cancellable resume chooser is unavailable.")
            control.click()
        dialog.wait_for(state="visible")
        if dialog.count() != 1:
            raise BrowserWorkError("liepin_resume_chooser_ambiguous", "Exactly one resume-choice dialog is required.")
        text = dialog.inner_text()
        if not all(t in text for t in ("在线简历", "附件简历")):
            raise BrowserWorkError("liepin_resume_mode_unknown", "Read the actual online and attachment submission mode before proceeding.")
        submit = dialog.get_by_role("button", name="立即投递", exact=True)
        close = dialog.locator('[aria-label="Close"], [aria-label="close"]')
        if submit.count() != 1 or close.count() == 0:
            raise BrowserWorkError("liepin_resume_chooser_unknown", "The chooser must have a distinct final submit and cancel control.")
        radios = dialog.locator('input[type="radio"]')
        raw = radios.evaluate_all("""items => items.map(e => ({
            parts: [...(e.closest('label')?.querySelectorAll('p') || [])].map(p=>p.innerText.trim()), selected: e.checked
        }))""")
        options = []
        for item in raw:
            parts = item["parts"]
            match = re.fullmatch(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*上传", parts[1]) if len(parts) == 2 else None
            if not match or not parts[0]:
                raise BrowserWorkError("liepin_attachment_reference_unknown", "Every attachment needs its actual name and distinguishing upload time.")
            options.append({"reference": f"{parts[0]}（{match[1]}上传）", "selected": item["selected"]})
        if not options or len(options) > 20 or len({o["reference"] for o in options}) != len(options):
            raise BrowserWorkError("liepin_attachment_options_unknown", "The complete, distinct attachment list could not be verified.")
        return dialog, {"resume_reference": self._online_resume(work), "resume_state": "not_sent",
            "communication_state": "open", "conversation_job_verified": True, "submission_attempted": False,
            "dialog_cancellable": True, "options_complete": True, "submission_mode": "online_and_attachment",
            "attachment_options": options}

    def _submit_resume(self, work, *, may_act):
        from jobagent.infra import state, rounds
        from jobagent.application.playwright_work import receipt_path
        path = receipt_path(work["work_id"]).with_suffix(".preflight.json")
        if may_act:
            if not self.prepared_choice:
                raise BrowserWorkError("liepin_resume_preflight_missing", "No verified pre-submit selection is available.")
            dialog, current = self._resume_dialog(work, may_open=False)
            if current != self.prepared_choice:
                raise BrowserWorkError("liepin_resume_selection_changed", "The selection changed before submission; no final click was made.")
            checkpoint = {"binding": work["binding"], "nonce": work["nonce"], "choice": current,
                          "selected": work["task"]["attachment_reference"], "observed_at": rounds.utc_now(),
                          "prior_resume_cards": self._conversation(work)["resume_cards"]}
            state.save_json(path, checkpoint)
            # Persist intent and actual checked choice before this single click.
            dialog.get_by_role("button", name="立即投递", exact=True).click()
            dialog.wait_for(state="hidden")
        checkpoint = state.load_json(path) or {}
        if (checkpoint.get("binding") != work["binding"] or checkpoint.get("nonce") != work["nonce"]
                or checkpoint.get("selected") != work["task"].get("attachment_reference")
                or checkpoint.get("choice", {}).get("resume_reference") != work["task"]["resume_reference"]):
            raise BrowserWorkError("liepin_submission_checkpoint_missing", "Preserve the attempted submission; its checked pre-submit choice is unavailable.")
        deadline = time.monotonic() + (12 if may_act else 0)
        while True:
            conversation = self._conversation(work)
            prior = checkpoint["prior_resume_cards"]
            cards = conversation["resume_cards"]
            new_card = cards[-1] if len(cards) == len(prior) + 1 and cards[:-1] == prior else ""
            if (new_card and "在线简历" in new_card and "附件简历" in new_card
                    and conversation["resume_receipts"][-1]["acknowledged"]):
                return {"outcome": "success", "evidence": {**self.identity, **conversation,
                    "resume_state": "sent", "resume_reference": checkpoint["choice"]["resume_reference"],
                    "receipt_kind": "resume_card", "receipt_checked": True,
                    "submission_mode": checkpoint["choice"]["submission_mode"],
                    "attachment_reference": checkpoint["selected"], "attachment_selection_verified": True}}
            if time.monotonic() >= deadline:
                raise BrowserWorkError("liepin_resume_receipt_pending", "No acknowledged job-bound resume card is visible; never submit again.")
            self.page.wait_for_timeout(250)

    def _send_greeting(self, work, *, may_act):
        from jobagent.infra import state, rounds
        from jobagent.application.playwright_work import receipt_path
        greeting = work["task"]["job"]["cloud_greeting"]
        conversation = self._conversation(work)
        path = receipt_path(work["work_id"]).with_suffix(".preflight.json")
        if may_act:
            if conversation["message_state"] == "unknown":
                raise BrowserWorkError("liepin_greeting_receipt_pending", "Reconcile the existing pending outgoing message first.")
            editor = self.page.locator("textarea.im-ui-textarea:visible")
            if editor.input_value() not in {"", greeting}:
                raise BrowserWorkError("liepin_existing_draft", "Preserve the existing draft; do not overwrite it.")
            editor.fill(greeting)
            if editor.input_value() != greeting:
                raise BrowserWorkError("liepin_greeting_changed", "The editor differs from the exact confirmed greeting.")
            state.save_json(path, {"binding": work["binding"], "nonce": work["nonce"], "greeting": greeting,
                                  "prior_count": conversation["greeting_count"],
                                  "prior_acknowledged_count": conversation["acknowledged_greeting_count"],
                                  "observed_at": rounds.utc_now()})
            self.page.locator(".im-ui-chat-container:visible button.im-ui-basic-send-btn").click()
        checkpoint = state.load_json(path) or {}
        if (checkpoint.get("binding") != work["binding"] or checkpoint.get("nonce") != work["nonce"]
                or checkpoint.get("greeting") != greeting):
            raise BrowserWorkError("liepin_greeting_checkpoint_missing", "Preserve the attempted greeting; its pre-send checkpoint is unavailable.")
        deadline = time.monotonic() + (12 if may_act else 0)
        while True:
            conversation = self._conversation(work)
            if (conversation["greeting_count"] == checkpoint["prior_count"] + 1
                    and conversation["acknowledged_greeting_count"] == checkpoint["prior_acknowledged_count"] + 1):
                return {"outcome": "success", "evidence": {**self.identity, **conversation,
                    "outgoing_text": greeting, "receipt_checked": True}}
            if time.monotonic() >= deadline:
                raise BrowserWorkError("liepin_greeting_receipt_pending", "The exact outgoing greeting is not acknowledged; never send again.")
            self.page.wait_for_timeout(250)

    def execute(self, work, *, may_act):
        try:
            return self._execute(work, may_act=may_act)
        except Exception as exc:
            if may_act:
                raise
            # The action was attempted earlier and reconciliation cannot verify
            # it. Settle as terminal unresolved: never resend, never report a
            # delivery, and never leave the platform blocked forever.
            job = work["task"].get("job") or {}
            identity = self.identity or {}
            error = (exc.payload.get("error") if isinstance(exc, BrowserWorkError) else type(exc).__name__)
            return {"outcome": "unresolved", "evidence": {
                **identity,
                "job_id": str(work["binding"].get("job_id") or identity.get("job_id") or ""),
                "job_url": job.get("url") or identity.get("job_url", ""),
                "title": job.get("title") or identity.get("title", ""),
                "receipt_checked": True, "submission_attempted": False,
                "reconciliation_error": error or "unknown"}}

    def _execute(self, work, *, may_act):
        action = work["action"]
        if action == "inspect_session":
            resume = self._online_resume(work)
            return {"outcome": "success", "evidence": {**self.identity, "login_state": "authenticated",
                "account_navigation": True, "resume_or_activity": True, "resume_reference": resume}}
        if action in {"inspect_delivery", "inspect_resume_selection"}:
            after_communication = work["task"].get("inspection_phase") == "after_communication"
            history = read_history(self.runtime, work, self.identity["account_label"], refresh=after_communication)
            already_submitted = work["binding"]["job_id"] in history["entries"]
            if already_submitted and not after_communication:
                raise BrowserWorkError("liepin_previous_application", "This job is already in official application history; do not apply or send again.")
            conversation = self._conversation(work)
            # The same recruiter may have older messages for other jobs. The
            # current job card alone does not bind those historical messages.
            conversation.update(existing_outgoing_text="", message_state="not_sent")
            return {"outcome": "success", "evidence": {**self.identity, **conversation,
                "resume_reference": self._online_resume(work), "resume_state": "sent" if already_submitted else "not_sent",
                **({"receipt_kind": "application_history"} if already_submitted else {}),
                "history_checked": True, "history_total": history["total"], "receipt_checked": True,
                "resume_selection_verified": True, "submission_attempted": False}}
        if action == "open_communication":
            if may_act and self.page.locator("textarea.im-ui-textarea:visible").count() == 0:
                self._open_chat(work["binding"]["job_id"])
                self.page.locator("textarea.im-ui-textarea:visible").wait_for(state="visible", timeout=30000)
            if not may_act and self.page.locator("textarea.im-ui-textarea:visible").count() == 0:
                # The earlier click did not leave a verifiable conversation on
                # this page. Settle without another click instead of wedging
                # reconciliation forever; nothing is reported as delivered.
                return {"outcome": "unresolved", "evidence": {**self.identity,
                    "communication_state": "unknown", "conversation_job_verified": False,
                    "submission_attempted": False, "receipt_checked": True,
                    "reason": "conversation_unverified_after_action",
                    "reconciliation_error": "conversation_unverified_after_action"}}
            conversation = self._conversation(work)
            anchor = work["task"].get("anonymous_recruiter_identity")
            if anchor and not may_act and self.identity.get("company") in (None, "") and (
                    self.identity.get("recruiter_name") != anchor.get("recruiter_name")
                    or self.identity.get("recruiter_company") != anchor.get("recruiter_company")):
                # The attempted action cannot be tied back to the declared
                # recruiter anchor. Settle without another click.
                return {"outcome": "unresolved", "evidence": {**self.identity, **conversation,
                    "submission_attempted": False, "receipt_checked": True,
                    "reason": "anonymous_recruiter_identity_changed",
                    "reconciliation_error": "anonymous_recruiter_identity_changed"}}
            return {"outcome": "success", "evidence": {**self.identity, **conversation,
                "default_greeting_observed": bool(conversation.get("observed_outgoing"))}}
        if action == "prepare_resume":
            conversation = self._conversation(work)
            if not conversation["conversation_job_verified"]:
                raise BrowserWorkError("liepin_resume_preparation_unverified", "Verify this unsent job conversation before opening its chooser.")
            _, choice = self._resume_dialog(work, may_open=may_act)
            return {"outcome": "success", "evidence": {**self.identity, **conversation, **choice, "receipt_checked": True}}
        if action == "submit_resume":
            return self._submit_resume(work, may_act=may_act)
        if action == "send_greeting":
            return self._send_greeting(work, may_act=may_act)
        raise BrowserWorkError("liepin_action_not_validated", "This adapter action has not completed DOM validation; no external action was attempted.")
