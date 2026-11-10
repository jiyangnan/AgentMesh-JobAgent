"""Native Easy Apply preparation and one-intent submission in dedicated Chrome."""

import re
import time
from urllib.parse import urlsplit, urlunsplit

from jobagent.infra.browser_work import BrowserWorkError
from jobagent.infra.linkedin_application import validate_application
from jobagent.platforms.linkedin.collect import _UI, detail_id
from jobagent.platforms.linkedin.forms import DIALOG, UI, ApplicationForm

HISTORY_URL = "https://www.linkedin.com/jobs-tracker/?stage=applied"
ACCOUNT = re.compile(r"本人|\(you\)|\bMe\b|さんのプロフィール$|님\(나\)$")
SUBMITTED = {
    "申请已提交",
    "已申请",
    "Applied",
    "Application submitted",
    "Your application was sent",
    "応募が送信されました",
    "지원서를 전송함",
}
REWIND = {
    "en": ("Edit", "Back"), "zh": ("编辑", "返回"),
    "ja": ("編集", "戻る"), "ko": ("수정", "뒤로"),
}


def fail(code, message):
    raise BrowserWorkError(code, message)


def account_identity(page):
    button = page.get_by_role("button", name=ACCOUNT)
    button.wait_for(state="visible", timeout=10000)
    if button.count() != 1:
        fail("linkedin_account_ambiguous", "The active member control is not unique")
    opened = button.get_attribute("aria-expanded") != "true"
    if opened:
        button.click(timeout=5000)
    try:
        links = page.locator('[role="menu"]:visible a[href*="/in/"]:visible')
        links.first.wait_for(state="visible", timeout=5000)
        refs = set()
        for href in links.evaluate_all("es=>es.map(e=>e.href)"):
            parsed = urlsplit(href)
            if (
                parsed.scheme == "https"
                and parsed.netloc == "www.linkedin.com"
                and re.fullmatch(r"/in/[^/]+/?", parsed.path)
            ):
                refs.add(
                    urlunsplit(
                        (
                            parsed.scheme,
                            parsed.netloc,
                            parsed.path.rstrip("/") + "/",
                            "",
                            "",
                        )
                    )
                )
        if len(refs) != 1:
            fail(
                "linkedin_account_ambiguous",
                "The member menu must identify one actual profile",
            )
        return refs.pop()
    finally:
        if opened and button.get_attribute("aria-expanded") == "true":
            button.click(timeout=5000)


class LinkedInDelivery:
    def __init__(self, runtime):
        self.runtime = runtime
        self.page = self.history = self.form = None
        self.identity = None
        self.before = None

    def _account(self, work):
        page, guard = self.runtime.auxiliary_page(
            work, HISTORY_URL, purpose="history", refresh=True
        )
        guard.check()
        page.locator("main").wait_for(state="visible", timeout=10000)
        if urlsplit(page.url).path.rstrip("/") != "/jobs-tracker":
            fail(
                "linkedin_login_required",
                "The original account must be signed in to its native job tracker",
            )
        account = account_identity(page)
        expected = self.runtime.session.get("accounts", {}).get("linkedin")
        if expected and expected != account:
            fail("playwright_account_changed", "The LinkedIn member profile changed")
        self.history = page
        return account

    def _header(self, work):
        page, job = self.page, work["task"]["job"]
        if detail_id(page.url) != job["id"]:
            fail(
                "linkedin_application_identity_changed",
                "The current route is not the exact approved job",
            )
        language = (page.locator("html").get_attribute("lang") or "").split("-")[0]
        if language not in _UI:
            fail(
                "linkedin_ui_language_unverified",
                "The current interface language is not supported",
            )
        about = _UI[language]["about"]
        page.get_by_role("heading", name=about, exact=True).wait_for(
            state="visible", timeout=10000
        )
        header = page.locator("main").inner_text().split(about, 1)[0]
        lines = [s.strip() for s in header.splitlines() if s.strip()]
        if len(lines) < 2 or lines[:2] != [job["company"], job["title"]]:
            fail(
                "linkedin_application_identity_changed",
                "The visible title and company differ from the matched job",
            )
        return language, header, lines

    def prepare(self, work):
        if work["action"] not in {
            "inspect_session",
            "prepare_application",
            "inspect_delivery",
            "submit_resume",
        }:
            fail(
                "linkedin_action_unsupported", "This work is outside native Easy Apply"
            )
        account = self._account(work)
        if work["action"] == "inspect_session":
            self.runtime.page = self.history
            self.identity = {
                **self.runtime.evidence(work),
                "account_label": account,
                "login_state": "authenticated",
                "account_navigation": True,
                "resume_or_activity": True,
            }
            return
        self.page = self.runtime.job_page(work)
        _, _, lines = self._header(work)
        job = work["task"]["job"]
        self.identity = {
            **self.runtime.evidence(work),
            "account_label": account,
            "login_state": "authenticated",
            "job_id": job["id"],
            "job_url": job["url"],
            "title": job["title"],
            "company": job["company"],
        }
        self.before = any(line in SUBMITTED for line in lines)
        if work["action"] in {"inspect_delivery", "submit_resume"}:
            application = validate_application(job)
            if (
                application["context"]["session"] != self.runtime.session
                or application["account_label"] != account
            ):
                fail(
                    "linkedin_application_context_changed",
                    "The prepared application belongs to another browser account",
                )
            # An issued submit intent permits receipt inspection only, including
            # after reconnect. Never open/fill a draft in this branch.
            if work["action"] == "submit_resume" and work["state"] != "ready":
                return
            if not self.before and not work["task"].get(
                "historical_action_requires_reconciliation"
            ):
                self._restore(work, application)

    def _open_form(self, work):
        if self.page.locator(DIALOG).count() == 0:
            language, header, lines = self._header(work)
            if any(line in SUBMITTED for line in lines):
                fail(
                    "linkedin_already_applied",
                    "This job already has a native application",
                )
            exact = _UI[language]["easy"]
            # Only the observed Chinese existing-draft entry is enabled here;
            # unknown translated draft controls require fresh native evidence.
            draft = language == "zh" and "您上一次修改此申请资料" in header
            if draft:
                exact = "继续"
            button = (
                self.page.locator("main")
                .get_by_role("link" if draft else "button")
                .filter(has_text=re.compile(r"^\s*" + re.escape(exact) + r"\s*$"))
            )
            if button.count() != 1:
                fail(
                    "linkedin_native_application_unavailable",
                    "No unique native Easy Apply or verified draft entry is available",
                )
            if draft and (
                detail_id(button.evaluate("e=>e.href")) != work["task"]["job"]["id"]
                or button.get_attribute("target") not in {None, "", "_self"}
            ):
                fail(
                    "linkedin_native_application_unavailable",
                    "The draft link must stay on the same native job",
                )
            button.click(timeout=5000)
            self.page.locator(DIALOG).wait_for(state="visible", timeout=10000)
        self.form = ApplicationForm(self.page, work["task"]["job"]["id"])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            snapshot = self.form.observe()
            # The dialog shell appears before the asynchronously loaded form.
            if (
                snapshot["action"] != "unknown"
                or snapshot["fields"]
                or snapshot["errors"]
            ):
                return snapshot
            self.page.wait_for_timeout(150)
        return snapshot

    def _next_snapshot(self, old):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            current = self.form.observe()
            if current["errors"] or (
                current["snapshot_digest"] != old["snapshot_digest"]
                and current["action"] != "unknown"
            ):
                return current
            self.page.wait_for_timeout(150)
        fail(
            "linkedin_application_transition_unknown",
            "No verified page transition followed the single navigation action",
        )

    def _restore(self, work, application):
        current = self._open_form(work)
        for _ in range(len(application["pages"]) + 1):
            expected = next(
                (
                    p
                    for p in application["pages"]
                    if p["schema_digest"] == current["schema_digest"]
                ),
                None,
            )
            actual_values = {
                f["field_id"]: f["value"]
                for f in current["fields"]
                if not f["disabled"]
            }
            if (
                expected is None
                or expected["policy_digest"] != current["policy_digest"]
                or actual_values
                != {f["field_id"]: f["value"] for f in expected["fields"]}
            ):
                fail(
                    "linkedin_application_preview_changed",
                    "Application answers, attachment or policy changed; obtain a new full preview",
                )
            if current["action"] == "submit":
                if current["snapshot_digest"] != application["review_digest"]:
                    fail(
                        "linkedin_application_preview_changed",
                        "The final native preview changed after authorization",
                    )
                if self.form.attachment(current) != application["attachment"]:
                    fail(
                        "linkedin_application_preview_changed",
                        "The resume bytes changed after authorization",
                    )
                return current
            self.form.advance(current)
            current = self._next_snapshot(current)
        fail(
            "linkedin_application_page_limit",
            "The original preview could not be recovered within its page budget",
        )

    def _prepare_page(self, work, may_act):
        if self.before:
            return {
                "outcome": "success",
                "evidence": {
                    **self.identity,
                    "submission_attempted": False,
                    "preparation_state": "unavailable",
                },
            }
        current = self._open_form(work)
        restarted = False
        if work["task"].get("restart_from_first_page"):
            # Re-observe every page, including the attachment selection. A
            # final preview alone cannot stand in for the original answers.
            edit, back = REWIND[current["language"]]
            for _ in range(40):
                dialog = self.page.locator(DIALOG)
                button = dialog.get_by_role("button", name=back, exact=True)
                if current["action"] == "submit":
                    edits = dialog.get_by_role("button", name=edit, exact=True)
                    if edits.count():
                        button = edits.first
                if not button.count() or not button.is_enabled():
                    restarted = current["action"] in {"next", "review"}
                    break
                button.click(timeout=5000)
                current = self._next_snapshot(current)
            if not restarted:
                return {"outcome": "success", "evidence": {
                    **self.identity, "submission_attempted": False,
                    "preparation_state": "form", "snapshot": current,
                    "requires_manual": True,
                }}
        evidence = {
            **self.identity,
            "submission_attempted": False,
            "preparation_state": "form",
            "snapshot": current,
            "restarted_from_first_page": restarted,
        }
        if (
            not may_act
            or work["task"]["operation"] != "fill"
            or current["snapshot_digest"] != work["task"]["snapshot_digest"]
        ):
            return {"outcome": "success", "evidence": evidence}
        answers = work["task"]["answers"]
        result = self.form.fill(current, answers)
        current = result["snapshot"]
        evidence["snapshot"] = current
        if result["requires_replan"]:
            return {"outcome": "success", "evidence": evidence}
        if current["errors"] or any(
            f["invalid"] for f in current["fields"] if not f["disabled"]
        ):
            evidence["requires_manual"] = True
            return {"outcome": "success", "evidence": evidence}
        completed = {
            "schema_digest": current["schema_digest"],
            "policy_digest": current["policy_digest"],
            "fields": [
                {k: f[k] for k in ("field_id", "question", "type", "value")}
                | {
                    "source": answers[f["field_id"]]["source"]
                    if f["field_id"] in answers
                    else {
                        "kind": "optional_blank",
                        "reference": "observed_empty_optional",
                    }
                }
                for f in current["fields"]
                if not f["disabled"]
            ],
        }
        evidence["completed_page"] = completed
        if any(f["type"] == "attachment" for f in current["fields"]):
            evidence["attachment"] = self.form.attachment(current)
        if current["action"] == "submit":
            if current["unsupported"] or any(f["invalid"] for f in current["fields"]):
                fail(
                    "linkedin_application_input_required",
                    "The final preview still has invalid input",
                )
            evidence["preparation_state"] = "ready"
            evidence["attachment"] = self.form.attachment(current)
        else:
            self.form.advance(current)
            evidence["snapshot"] = self._next_snapshot(current)
        return {"outcome": "success", "evidence": evidence}

    def _receipt(self, work, *, prior=False):
        # A visible native status must survive a fresh detail load and agree
        # with this exact job in the account tracker. Absence is never a retry.
        self.page.reload(wait_until="domcontentloaded", timeout=20000)
        _, _, lines = self._header(work)
        durable = any(line in SUBMITTED for line in lines)
        account = self._account(work)
        if account != self.identity["account_label"]:
            fail("playwright_account_changed", "The receipt account changed")
        job = work["task"]["job"]
        # Tracker links wrap title, company and dates in one anchor. Match
        # the exact visible title node, not the concatenated whole card.
        records = self.history.locator('a[href*="/jobs/view/"]:visible')
        deadline = time.monotonic() + 5
        matches = []
        while time.monotonic() < deadline:
            rows = records.evaluate_all("""es=>es.map(e=>({url:e.href,
                texts:[e,...e.querySelectorAll('*')].filter(n=>n.checkVisibility())
                    .map(n=>n.innerText?.trim()).filter(Boolean)}))""")
            matches = [
                {
                    "url": r["url"],
                    "title": next(t for t in r["texts"] if t == job["title"]),
                }
                for r in rows
                if detail_id(r["url"]) == job["id"] and job["title"] in r["texts"]
            ]
            if matches:
                break
            self.history.wait_for_timeout(150)
        self.page.bring_to_front()
        evidence = {
            **self.identity,
            "receipt_checked": True,
            "history_checked": True,
            "application_digest": job["application_digest"],
            "communication_state": "not_applicable",
            "native_status_persisted": durable,
            "history_record": matches[0] if matches else None,
        }
        if not durable or not matches:
            return {"outcome": "uncertain", "evidence": evidence}
        evidence.update(
            resume_state="sent",
            receipt_kind="native_application_persistent",
            prior_application=prior,
            resume_reference=work["task"].get("resume_reference", "")
            if not prior
            else "",
            attachment_receipt_available=False,
        )
        return {"outcome": "success", "evidence": evidence}

    def execute(self, work, *, may_act):
        action = work["action"]
        if action == "inspect_session":
            return {"outcome": "success", "evidence": self.identity}
        if action == "prepare_application":
            return self._prepare_page(work, may_act)
        if action == "inspect_delivery":
            if self.before:
                return self._receipt(work, prior=True)
            if work["task"].get("historical_action_requires_reconciliation"):
                return self._receipt(work)
            return {
                "outcome": "success",
                "evidence": {
                    **self.identity,
                    "history_checked": True,
                    "resume_state": "not_sent",
                    "communication_state": "not_applicable",
                    "native_preview_verified": True,
                    "application_digest": work["task"]["job"]["application_digest"],
                    "submission_attempted": False,
                    "resume_reference": work["task"]["resume_reference"],
                },
            }
        if not may_act:
            return self._receipt(work)
        if self.before:
            return self._receipt(work, prior=True)
        current = self.form.observe()
        application = validate_application(work["task"]["job"])
        if current["snapshot_digest"] != application["review_digest"]:
            fail(
                "linkedin_application_preview_changed",
                "The application changed before the permitted submit",
            )
        if self.form.attachment(current) != application["attachment"]:
            fail(
                "linkedin_application_preview_changed",
                "The selected resume changed before submission",
            )
        button = self.page.locator(DIALOG).get_by_role(
            "button", name=UI[current["language"]]["submit"], exact=True
        )
        if button.count() != 1 or not button.is_enabled():
            fail(
                "linkedin_submit_unavailable",
                "The native submit action is not uniquely enabled",
            )
        button.click(timeout=5000)
        self.page.get_by_text(
            re.compile("^(?:" + "|".join(re.escape(s) for s in SUBMITTED) + ")$")
        ).first.wait_for(state="visible", timeout=10000)
        return self._receipt(work)
