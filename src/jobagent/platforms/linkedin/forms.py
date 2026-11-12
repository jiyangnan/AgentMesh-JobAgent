"""Local, bounded Easy Apply form observations and preparation operations.

Applicant values belong in the account-bound local task, never ordinary logs.
This module neither chooses factual answers nor exposes a submit operation.
The application layer must bind facts/attachments and obtain full-preview
authorization separately before the delivery ledger can issue a submit intent.
"""
from __future__ import annotations

import json
import tempfile
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

from jobagent.infra.protocol import digest_payload
from jobagent.platforms.linkedin.collect import detail_id, fail

UI = {
    "en": {"next": "Next", "review": "Review", "submit": "Submit application"},
    "zh": {"next": "下一步", "review": "查看", "submit": "提交申请"},
    "ja": {"next": "次へ", "review": "確認", "submit": "応募を送信"},
    "ko": {"next": "다음", "review": "검토", "submit": "신청서 전송"},
}
DIALOG = "dialog:visible,[role=dialog]:visible"
CONTROLS = "input,select,textarea"
_READ = r"""root => {
 const visible = e => e.checkVisibility() && !e.closest('[hidden],[aria-hidden="true"]');
 const editable = e => visible(e) || (e.type==='radio' && !e.closest('[hidden],[aria-hidden="true"]')
    && [...(e.labels || [])].some(visible));
 const text = e => e && visible(e) ? e.innerText.trim() : '';
 const label = e => {
   const labelled = (e.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
      .map(id => e.getRootNode().querySelector('#' + CSS.escape(id))).filter(Boolean);
   return [...new Set([e.getAttribute('aria-label'), ...labelled.map(text),
     ...[...(e.labels || [])].map(text)].filter(Boolean))].join(' ').trim();
 };
 const controls = [...root.querySelectorAll('input,select,textarea')];
 const fields = [], handled = new Set(), unsupported = [];
 for (let index=0; index<controls.length; index++) {
   const e = controls[index];
   if (visible(e) && e.type==='file' && e.required) unsupported.push('upload_required');
   if (handled.has(e) || !editable(e) || ['hidden','submit','button','reset','file'].includes(e.type)) continue;
   const group = e.type === 'radio' ? e.closest('fieldset,[role=radiogroup]') : null;
   if (e.type === 'radio' && !group) {unsupported.push('ungrouped_radio');continue;}
   const members = group ? [...group.querySelectorAll('input[type=radio]')].filter(editable) : [e];
   members.forEach(c => handled.add(c));
   const legends = group ? [...group.querySelectorAll('legend')].map(text).filter(Boolean) : [];
   const question = group ? legends.join(' ') || label(group) || text(group.previousElementSibling) : label(e);
   let choiceLabels = members.map(label);
   // Some native radios repeat the question in aria-label, leave <label>
   // empty, and render the choice in a sibling paragraph. Only use text from
   // the smallest visible container holding exactly this one control.
   if (group && (choiceLabels.some(v=>!v) || new Set(choiceLabels).size!==members.length)) {
     choiceLabels = members.map(c => {
       for(let a=c.parentElement; a && a!==group; a=a.parentElement) {
         if(a.querySelectorAll('input,select,textarea').length!==1)break;
         const value=text(a);
         if(value && value!==question && value!==label(c))return value;
       }
       return label(c);
     });
   }
   const options = group ? members.map((c,i) => ({label:choiceLabels[i], value:choiceLabels[i], disabled:c.disabled, index:controls.indexOf(c)}))
       : e.tagName==='SELECT' ? [...e.options].map(o => ({label:o.textContent.trim(), value:o.value, disabled:o.disabled})) : [];
   const attachment = group && options.length && options.every(o => /\.(pdf|docx?)\b/i.test(o.label));
   const type = attachment ? 'attachment' : group ? 'radio' : e.tagName==='TEXTAREA' ? 'textarea' : e.type;
   const evidence = [];
   if (members.some(c => c.required || c.getAttribute('aria-required')==='true') || group?.getAttribute('aria-required')==='true') evidence.push('native_required');
   if (/[*＊]\s*$/.test(question)) evidence.push('question_asterisk');
   if (!question && !attachment) unsupported.push('unlabelled_field');
   if (!['text','email','tel','url','number','textarea','select-one','radio','attachment','checkbox'].includes(type)) unsupported.push('field_type:'+type);
   const actionableOptions = options.filter(o => o.value !== '');
   if (actionableOptions.some(o => !o.label) || new Set(actionableOptions.map(o=>o.value)).size!==actionableOptions.length) unsupported.push('ambiguous_options');
   fields.push({index,type,question,options,required:evidence.length>0,required_evidence:evidence,
     disabled:members.every(c=>c.disabled),
     value:group ? members.flatMap((c,i)=>c.checked?[choiceLabels[i]]:[]) : e.type==='checkbox' ? e.checked : e.value,
     invalid:members.some(c=>c.getAttribute('aria-invalid')==='true' || (c.validity && !c.validity.valid)),
     constraints:group ? {} : {min:e.min||null,max:e.max||null,step:e.step||null,maxlength:e.maxLength>=0?e.maxLength:null}});
 }
 for (const e of root.querySelectorAll('[contenteditable=true],[role=combobox],[role=listbox]')) {
   if (visible(e) && e.tagName!=='SELECT') unsupported.push('custom_input');
 }
 const errors=[...root.querySelectorAll('[role=alert], [aria-invalid=true]')].filter(visible).map(e=>text(e)||label(e)).filter(Boolean);
 const requiredErrors=[...root.querySelectorAll('p,span,div')].filter(e=>visible(e)&&!e.children.length&&text(e)==='此字段必填').map(text);
 // File URLs can be short-lived. Bind their bytes separately, never mistake
 // an attachment download for an employer policy link.
 const links=[...root.querySelectorAll('a[href]')].filter(e=>visible(e)&&!new URL(e.href).pathname.startsWith('/ambry/'))
   .map(e=>({text:text(e),url:e.href}));
 const context_text=[...root.querySelectorAll('p,legend,h1,h2,h3,h4')].filter(visible).map(text).filter(Boolean);
 return {fields,links,context_text,unsupported:[...new Set(unsupported)],errors:[...new Set([...errors,...requiredErrors])],
   headings:[...root.querySelectorAll('h1,h2,h3,h4')].map(text).filter(Boolean),
   buttons:[...root.querySelectorAll('button')].filter(visible).map(e=>({text:text(e),label:label(e),disabled:e.disabled})),
   text:root.innerText.trim()};
}"""


def _schema(snapshot):
    return [{key: value for key, value in field.items() if key not in {"value", "invalid"}}
            for field in snapshot["fields"]]


class ApplicationForm:
    def __init__(self, page, job_id):
        self.page, self.job_id = page, job_id

    def observe(self):
        if detail_id(self.page.url) != self.job_id:
            fail("linkedin_application_identity_changed", "The active application is not on the exact job route")
        lang = (self.page.locator("html").get_attribute("lang") or "").split("-")[0]
        if lang not in UI:
            fail("linkedin_ui_language_unverified", "The application interface language has not been verified")
        dialog = self.page.locator(DIALOG)
        if dialog.count() != 1:
            fail("linkedin_application_dialog_ambiguous", "Exactly one active native application dialog is required")
        snapshot = dialog.evaluate(_READ)
        snapshot.update(job_id=self.job_id, language=lang)
        seen = set()
        for field in snapshot["fields"]:
            semantic = {key: field[key] for key in ("type", "question", "options")}
            # Browser indices are ephemeral, and not semantic option identity.
            semantic["options"] = [{k: v for k, v in o.items() if k != "index"} for o in semantic["options"]]
            field["field_id"] = digest_payload(semantic)
            if field["field_id"] in seen:
                snapshot["unsupported"].append("duplicate_question")
            seen.add(field["field_id"])
        actions = [action for action, label in UI[lang].items()
                   if any(button["text"] == label for button in snapshot["buttons"])]
        snapshot["action"] = actions[0] if len(actions) == 1 else "unknown"
        if snapshot["action"] == "unknown":
            snapshot["unsupported"].append("navigation_ambiguous")
        snapshot["schema_digest"] = digest_payload(_schema(snapshot))
        snapshot["policy_digest"] = digest_payload({"context_text": snapshot["context_text"], "links": snapshot["links"]})
        snapshot["snapshot_digest"] = digest_payload(snapshot)
        return snapshot

    def _current(self, expected):
        current = self.observe()
        if current["snapshot_digest"] != expected.get("snapshot_digest"):
            fail("linkedin_application_changed", "The form changed after observation; read it again before acting")
        return current

    def attachment(self, snapshot):
        """Read one exact native selection through its visible download link.

        This is a bounded GET of an observed member-page link, not an upload
        or a guessed API. An exact private local copy makes the attachment
        reviewable; neither bytes nor the download token enter cloud requests.
        """
        self._current(snapshot)
        selected = [f for f in snapshot["fields"] if f["type"] == "attachment"]
        root = self.page.locator(DIALOG)
        if selected:
            if len(selected) != 1 or len(selected[0]["value"]) != 1:
                fail("linkedin_attachment_unverified", "Select exactly one existing resume")
            field = selected[0]
            index = next(o["index"] for o in field["options"] if o["value"] == field["value"][0])
            # Native cards associate the radio and its download below the
            # smallest common ancestor. Do not reach a group of two resumes.
            refs = root.locator(CONTROLS).nth(index).evaluate("""e=>{
              for(let a=e.parentElement;a&&a!==e.closest('dialog,[role=dialog]');a=a.parentElement){
                if(a.querySelectorAll('input[type=radio]').length>1)break;
                const links=[...a.querySelectorAll('a[href]')].filter(x=>x.checkVisibility()&&new URL(x.href).pathname.startsWith('/ambry/'));
                if(links.length)return links.map(x=>x.href);
              }return [];
            }""")
        elif snapshot["action"] == "submit":
            refs = root.locator('a[href*="/ambry/"]:visible').evaluate_all("es=>es.map(e=>e.href)")
        else:
            return None
        if len(refs) != 1:
            fail("linkedin_attachment_unverified", "The selected resume must have one native download link")
        parsed = urlsplit(refs[0])
        if parsed.scheme != "https" or parsed.netloc != "www.linkedin.com" or not parsed.path.startswith("/ambry/"):
            fail("linkedin_attachment_unverified", "Use the exact native resume download origin")
        response = self.page.context.request.get(refs[0], timeout=15000, max_redirects=0)
        try:
            size = int(response.headers.get("content-length") or 0)
            if response.status != 200 or size > 2 * 1024 * 1024:
                fail("linkedin_attachment_unverified", "The selected resume download was not a bounded file response")
            content = response.body()
            if not 0 < len(content) <= 2 * 1024 * 1024 or not content.startswith((b"%PDF-", b"PK\x03\x04", b"\xd0\xcf\x11\xe0")):
                fail("linkedin_attachment_unverified", "The native attachment was not a supported resume file")
            from jobagent.infra import state
            digest = sha256(content).hexdigest()
            extension = ".pdf" if content.startswith(b"%PDF-") else ".docx" if content.startswith(b"PK") else ".doc"
            directory = state.STATE_DIR / "linkedin-attachments"
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = directory / (digest + extension)
            if not path.exists():
                with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
                    handle.write(content)
                    temporary = Path(handle.name)
                temporary.replace(path)
            if sha256(path.read_bytes()).hexdigest() != digest:
                fail("linkedin_attachment_unverified", "The local attachment copy no longer matches the native file")
            return {"sha256": digest, "size_bytes": len(content), "file_path": str(path.resolve())}
        finally:
            response.dispose()

    def fill(self, snapshot, answers):
        """Apply only explicit, sourced values to this exact observed page.

        Consent/checkbox values require an explicit user source. A changed
        conditional schema returns the new observation before another field
        can be touched. The caller then resolves that newly observed page.
        """
        current = self._current(snapshot)
        if current["unsupported"]:
            fail("linkedin_application_manual_required", "Only supported editable preparation pages may be filled")
        fields = {f["field_id"]: f for f in current["fields"]}
        if not isinstance(answers, dict) or set(answers) - set(fields):
            fail("linkedin_answer_scope_invalid", "Answers must name fields on this exact page")
        if current["action"] == "submit" and any(fields[key]["type"] != "checkbox" for key in answers):
            fail("linkedin_application_review_edit_required", "Return to the editable page before changing application answers")
        # Validate the complete set before the first draft mutation.
        for key, answer in answers.items():
            field = fields[key]
            if (not isinstance(answer, dict) or set(answer) != {"value", "source"}
                    or not isinstance(answer["source"], dict)
                    or answer["source"].get("kind") not in {"user_confirmed", "resume_fact"}
                    or not isinstance(answer["source"].get("reference"), str)
                    or not 0 < len(answer["source"]["reference"].strip()) <= 300 or field["disabled"]):
                fail("linkedin_answer_source_required", "Every answer needs its confirmed fact reference")
            value = answer["value"]
            if field["type"] in {"attachment", "checkbox"} and answer["source"]["kind"] != "user_confirmed":
                fail("linkedin_explicit_choice_required", "Attachment and checkbox choices require the user's explicit answer")
            if field["type"] == "checkbox":
                valid = type(value) is bool
            else:
                valid = isinstance(value, str) and len(value) <= 10000
                if field["options"]:
                    valid = valid and len([o for o in field["options"] if o["value"] == value and not o["disabled"]]) == 1
                maximum = field["constraints"].get("maxlength")
                if maximum is not None:
                    valid = valid and len(value) <= maximum
            if not valid:
                fail("linkedin_answer_value_invalid", "The sourced answer is not one supported native value")
        for key, answer in answers.items():
            self._current(current)
            field, value = fields[key], answer["value"]
            dialog = self.page.locator(DIALOG)
            inputs = dialog.locator(CONTROLS)
            control = inputs.nth(field["index"])
            if field["type"] in {"radio", "attachment"}:
                index = next(o["index"] for o in field["options"] if o["value"] == value)
                radio = inputs.nth(index)
                if not radio.is_checked():
                    if radio.is_visible():
                        radio.check(timeout=5000)
                    else:
                        identifier = radio.get_attribute("id")
                        label = dialog.locator("label[for=" + json.dumps(identifier) + "]") if identifier else None
                        if label is None or label.count() != 1 or not label.is_visible():
                            fail("linkedin_application_manual_required", "The native choice has no unique visible control")
                        label.click(timeout=5000)
            elif field["type"] == "checkbox":
                control.set_checked(value, timeout=5000)
            elif field["type"] == "select-one":
                control.select_option(value=value, timeout=5000)
            else:
                control.fill(value, timeout=5000)
            after = self.observe()
            if after["schema_digest"] != current["schema_digest"]:
                return {"snapshot": after, "requires_replan": True}
            old_values = {f["field_id"]: f["value"] for f in current["fields"]}
            if any(f["field_id"] != key and f["value"] != old_values[f["field_id"]] for f in after["fields"]):
                return {"snapshot": after, "requires_replan": True}
            observed = next(f for f in after["fields"] if f["field_id"] == key)["value"]
            if observed != ([value] if field["type"] in {"radio", "attachment"} else value):
                fail("linkedin_answer_readback_failed", "The native form did not retain the sourced value")
            current = after
        return {"snapshot": deepcopy(current), "requires_replan": False}

    def advance(self, snapshot):
        current = self._current(snapshot)
        if current["action"] not in {"next", "review"}:
            fail("linkedin_application_submit_forbidden", "Preparation never submits an application")
        if (current["unsupported"] or current["errors"] or any(f["invalid"] or
                (f["required"] and not f["disabled"] and f["value"] in ("", [], False)) for f in current["fields"])):
            fail("linkedin_application_input_required", "Resolve missing fields and visible errors before advancing")
        button = self.page.locator(DIALOG).get_by_role("button", name=UI[current["language"]][current["action"]], exact=True)
        if button.count() != 1 or not button.is_enabled():
            fail("linkedin_application_navigation_changed", "The observed next action is not uniquely available")
        button.click(timeout=5000)
        # No fixed page count and no retry of a next/review click. The caller
        # waits for and observes the actual next page or validation feedback.
        return {"advanced": True, "previous_snapshot_digest": current["snapshot_digest"]}
