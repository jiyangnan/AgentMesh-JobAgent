"""Zhilian apply/open and controlled send flows."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from jobagent.domain.models import SendAttempt
from jobagent.drivers.boss import create_driver

from .audit import ZhilianAuditEvent, ZhilianAuditLog


@dataclass
class ZhilianApplyOpenResult:
    ok: bool
    opened: int
    planned: int
    failed: int
    total: int
    events: list[dict[str, Any]] = field(default_factory=list)
    handoff: list[dict[str, Any]] = field(default_factory=list)
    mode: str = "manual_apply_open"
    platform: str = "zhilian"
    next_suggested: str = "Review opened Zhilian pages manually, then run `jobagent zhilian audit`."
    requires_user_action: bool = False
    user_prompt: str = ""

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "ok": self.ok,
            "platform": self.platform,
            "mode": self.mode,
            "total": self.total,
            "planned": self.planned,
            "opened": self.opened,
            "failed": self.failed,
            "requires_user_action": self.requires_user_action,
            "handoff": self.handoff,
            "events": self.events,
            "next_suggested": self.next_suggested,
        }
        if self.user_prompt:
            payload["user_prompt"] = self.user_prompt
        return payload


class ZhilianApplyOpener:
    def __init__(self, driver: Any | None = None, audit_log: ZhilianAuditLog | None = None):
        self.driver = driver
        self.audit_log = audit_log or ZhilianAuditLog()

    def open_jobs(
        self,
        jobs: list[dict[str, Any]],
        limit: int = 5,
        start: int = 0,
        wait_seconds: int = 3,
        dry_run: bool = False,
    ) -> ZhilianApplyOpenResult:
        selected = jobs[max(0, start): max(0, start) + max(1, limit)]
        events: list[dict[str, Any]] = []
        handoff_items: list[dict[str, Any]] = []
        opened = 0
        planned = 0
        failed = 0

        for index, job in enumerate(selected, start=max(0, start)):
            url = str(job.get("url") or "")
            handoff = _handoff_evidence(job)
            status = "planned" if dry_run else "opened"
            message = "dry_run" if dry_run else "Opened for manual review."
            if not url:
                status = "failed"
                message = "Cannot open Zhilian job without url."
                failed += 1
                event = self._event_from_job(job, status=status, error="missing_job_url", message=message, evidence={"index": index, **handoff})
            elif dry_run:
                planned += 1
                event = self._event_from_job(job, status=status, message=message, evidence={"index": index, **handoff})
            else:
                driver = self.driver or create_driver(platform="zhilian")
                self.driver = driver
                result = driver.open_url_in_new_tab(url, wait_seconds=wait_seconds)
                if result.get("ok"):
                    opened += 1
                    event = self._event_from_job(job, status="opened", message=message, evidence={"index": index, **handoff, "open_result": result})
                else:
                    failed += 1
                    event = self._event_from_job(
                        job,
                        status="failed",
                        error=str(result.get("error") or "open_failed"),
                        message="Failed to open Zhilian job page.",
                        evidence={"index": index, **handoff, "open_result": result},
                    )

            self.audit_log.append(event)
            events.append(event.to_dict())
            handoff_items.append(_handoff_item(job, index=index, status=event.status, error=event.error))

        if dry_run:
            next_suggested = "Review handoff, then rerun without `--dry-run` to open Zhilian pages for manual review."
            user_prompt = ""
            requires_user_action = False
        else:
            next_suggested = "Review opened Zhilian pages manually, then run `jobagent zhilian audit`."
            user_prompt = "请在已打开的智联页面中人工确认岗位。智联只支持立即投递附件简历，handoff 里的 greeting 仅作为匹配审阅备注，不会发送到智联页面。完成后可运行 `jobagent zhilian audit` 查看记录。"
            requires_user_action = opened > 0

        return ZhilianApplyOpenResult(
            ok=failed == 0,
            opened=opened,
            planned=planned,
            failed=failed,
            total=len(selected),
            events=events,
            handoff=handoff_items,
            next_suggested=next_suggested,
            requires_user_action=requires_user_action,
            user_prompt=user_prompt,
        )

    def _event_from_job(
        self,
        job: dict[str, Any],
        status: str,
        message: str = "",
        error: str = "",
        evidence: dict[str, Any] | None = None,
    ) -> ZhilianAuditEvent:
        return ZhilianAuditEvent(
            action="apply_open",
            status=status,
            job_url=str(job.get("url") or ""),
            job_name=str(job.get("name") or job.get("title") or ""),
            company=str(job.get("company") or ""),
            error=error,
            message=message,
            evidence=evidence or {},
        )


class ZhilianApplySender:
    """Submit resumes to Zhilian jobs via "立即投递".

    Zhilian UX differs fundamentally from Boss直聘:
      - **No greeting-based chat flow.** Zhilian's contact mechanism on a job
        page is "立即投递" (submit the resume bound to the user's Zhilian
        account). The "立即沟通" button exists but opens a separate IM
        channel that doesn't accept an outbound greeting on first contact.
      - **Greeting is handoff-only.** ``zhilian greet preview`` generates a
        personalized greeting, but it is NOT auto-sent by this sender. The
        greeting is emitted into the JSON ``handoff`` field for the
        ``zhilian apply open`` flow, where the human user manually copies
        it into whatever channel they choose.
      - **This sender does ONE thing: click 立即投递 to submit the resume.**
        The ``message`` parameter is accepted for API symmetry with Boss
        but is not delivered to the recruiter.

    The ``zhilian_greeting_not_supported`` step in ``_drive_dialog`` will
    report ``zhilian_resume_submit_has_no_message_editor`` on every healthy
    Zhilian job page — that is expected behavior, not a bug.
    """

    def __init__(self, driver: Any | None = None, audit_log: ZhilianAuditLog | None = None):
        self.driver = driver
        self.audit_log = audit_log or ZhilianAuditLog()

    def send_batch(
        self,
        jobs: list[dict[str, Any]],
        limit: int = 5,
        start: int = 0,
        wait_seconds: int = 3,
        dry_run: bool = False,
        skip_delivered: bool = True,
        stop_on_failure: bool = True,
    ) -> list[SendAttempt]:
        selected = jobs[max(0, start): max(0, start) + max(1, limit)]
        attempts: list[SendAttempt] = []
        delivered_urls = self.audit_log.delivered_apply_send_urls() if skip_delivered else set()
        for index, job in enumerate(selected, start=max(0, start)):
            message = str(job.get("cloud_greeting") or job.get("greeting") or "")
            url = str(job.get("url") or "").strip()
            if skip_delivered and _normalize_zhilian_url(url) in delivered_urls:
                attempt = SendAttempt(job_url=url, message=message, delivered=False, error="already_delivered")
                attempt.steps = [{"step": "skip_zhilian_apply_send", "ok": True, "reason": "already_delivered", "url": url}]
                status = "skipped"
                audit_message = "Skipped because this Zhilian job URL was already delivered."
            else:
                attempt = self._send_one(job, message, wait_seconds=wait_seconds, dry_run=dry_run)
                status = "planned" if dry_run else ("delivered" if attempt.delivered else "failed")
                audit_message = "dry_run" if dry_run else ("Delivered." if attempt.delivered else "Failed.")
                if attempt.delivered:
                    delivered_urls.add(_normalize_zhilian_url(attempt.job_url))

            attempts.append(attempt)
            self.audit_log.append(
                ZhilianAuditEvent(
                    action="apply_send",
                    status=status,
                    job_url=attempt.job_url,
                    job_name=str(job.get("name") or job.get("title") or ""),
                    company=str(job.get("company") or ""),
                    error=attempt.error,
                    message=audit_message,
                    evidence={
                        "index": index,
                        "has_greeting": bool(message.strip()),
                        "greeting_generated": bool(message.strip()),
                        "greeting": message,
                        "greeting_role": "review_note",
                        "submit_action": "resume_submit",
                        "greeting_delivery": _zhilian_greeting_delivery(attempt.steps, message),
                        "score": job.get("score"),
                        "match_level": job.get("match_level") or job.get("recommendation") or job.get("cloud_recommendation"),
                        "steps": attempt.steps,
                    },
                )
            )
            if stop_on_failure and not dry_run and status == "failed":
                break
        return attempts

    def _send_one(self, job: dict[str, Any], message: str, wait_seconds: int = 3, dry_run: bool = False) -> SendAttempt:
        url = str(job.get("url") or "")
        attempt = SendAttempt(job_url=url, message=message, delivered=False)
        steps: list[dict[str, Any]] = []
        if not url:
            attempt.error = "missing_job_url"
            attempt.steps = steps
            return attempt
        if dry_run:
            attempt.error = "dry_run"
            attempt.steps = [{"step": "plan_zhilian_apply_send", "ok": True, "url": url}]
            return attempt

        driver = self.driver or create_driver(platform="zhilian")
        self.driver = driver
        open_result = driver.open_url_in_new_tab(url, wait_seconds=wait_seconds)
        steps.append({"step": "open_job_url", **open_result})
        if not open_result.get("ok"):
            attempt.error = str(open_result.get("error") or "open_job_url_failed")
            attempt.steps = steps
            return attempt

        inspect_before = _exec_zhilian_js(driver, _zhilian_apply_inspect_script())
        steps.append({"step": "inspect_before_apply", **inspect_before})
        if _zhilian_page_requires_login(inspect_before):
            attempt.error = "login_required"
            attempt.steps = steps
            return attempt
        if inspect_before.get("requires_user_action"):
            attempt.error = str(inspect_before.get("user_action") or "user_action_required")
            attempt.steps = steps
            return attempt
        if _zhilian_delivery_detected(inspect_before):
            attempt.delivered = True
            attempt.steps = steps
            return attempt

        click_entry = _exec_zhilian_js(driver, _zhilian_apply_click_entry_script())
        steps.append({"step": "click_apply_or_contact_entry", **click_entry})
        if not click_entry.get("ok"):
            attempt.error = str(click_entry.get("error") or "zhilian_apply_entry_not_found")
            attempt.steps = steps
            return attempt

        terminal = self._drive_dialog(driver, message=message, steps=steps)
        if terminal.get("delivered"):
            attempt.delivered = True
        else:
            attempt.error = str(terminal.get("error") or "delivery_not_verified")
        attempt.steps = steps
        return attempt

    def _drive_dialog(self, driver: Any, message: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
        for _ in range(6):
            time.sleep(1)
            state = _exec_zhilian_js(driver, _zhilian_apply_inspect_script())
            steps.append({"step": "inspect_apply_state", **state})
            if _zhilian_page_requires_login(state):
                return {"delivered": False, "error": "login_required"}
            if _zhilian_delivery_detected(state):
                return {"delivered": True}
            if state.get("requires_user_action"):
                return {"delivered": False, "error": state.get("user_action") or "user_action_required"}
            if message.strip():
                steps.append({
                    "step": "zhilian_greeting_not_supported",
                    "ok": True,
                    "reason": "zhilian_resume_submit_has_no_message_editor",
                })
            confirm = _exec_zhilian_js(driver, _zhilian_apply_click_confirm_script())
            steps.append({"step": "click_zhilian_confirm", **confirm})
            if not confirm.get("ok"):
                continue
            time.sleep(1.5)
            after = _exec_zhilian_js(driver, _zhilian_apply_inspect_script())
            steps.append({"step": "inspect_after_confirm", **after})
            if _zhilian_delivery_detected(after):
                return {"delivered": True}
            if after.get("requires_user_action"):
                return {"delivered": False, "error": after.get("user_action") or "user_action_required"}
        return {"delivered": False, "error": "delivery_not_verified"}


def _handoff_evidence(job: dict[str, Any]) -> dict[str, Any]:
    greeting = str(job.get("cloud_greeting") or job.get("greeting") or "")
    return {
        "has_greeting": bool(greeting),
        "greeting": greeting,
        "score": job.get("score"),
        "match_level": job.get("match_level") or job.get("recommendation") or job.get("cloud_recommendation"),
    }


def _handoff_item(job: dict[str, Any], index: int, status: str, error: str = "") -> dict[str, Any]:
    evidence = _handoff_evidence(job)
    action = "review_zhilian_fit_before_resume_submit" if evidence["has_greeting"] else "review_zhilian_job_before_resume_submit"
    return {
        "index": index,
        "status": status,
        "action": action,
        "job_name": str(job.get("name") or job.get("title") or ""),
        "company": str(job.get("company") or ""),
        "url": str(job.get("url") or ""),
        "error": error,
        **evidence,
    }


def _zhilian_greeting_delivery(steps: list[dict[str, Any]], message: str) -> dict[str, Any]:
    if not message.strip():
        return {"status": "empty", "filled": False, "reason": "empty_greeting"}

    unsupported_steps = [step for step in steps if step.get("step") == "zhilian_greeting_not_supported"]
    if unsupported_steps:
        return {
            "status": "not_supported",
            "filled": False,
            "reason": "zhilian_resume_submit_has_no_message_editor",
        }

    fill_steps = [step for step in steps if step.get("step") == "fill_zhilian_message"]
    if not fill_steps:
        return {"status": "not_attempted", "filled": False, "reason": "fill_step_not_reached"}

    last = fill_steps[-1]
    if last.get("ok") and last.get("filled"):
        return {
            "status": "filled",
            "filled": True,
            "tag": last.get("tag") or "",
            "length": last.get("len"),
        }

    reason = str(last.get("reason") or last.get("error") or "not_filled")
    return {
        "status": reason,
        "filled": False,
        "reason": reason,
    }


def _normalize_zhilian_url(url: str) -> str:
    return str(url or "").strip().rstrip("/")


def _exec_zhilian_js(driver: Any, script: str) -> dict[str, Any]:
    if not hasattr(driver, "_exec_js"):
        return {"ok": False, "error": "driver_js_not_supported"}
    result = driver._exec_js(script)
    if isinstance(result, dict) and isinstance(result.get("raw"), str):
        try:
            parsed = json.loads(result["raw"])
            return parsed if isinstance(parsed, dict) else {"ok": False, "error": "unexpected_js_result"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return result if isinstance(result, dict) else {"ok": False, "error": "unexpected_js_result"}


def _zhilian_page_requires_login(state: dict[str, Any]) -> bool:
    if state.get("loginRequired") is True:
        return True
    text = f"{state.get('title') or ''}\n{state.get('bodySnippet') or ''}"
    return any(token in text for token in ("登录/注册", "请登录", "扫码登录", "验证码登录", "手机验证码", "安全验证", "滑块"))


def _zhilian_delivery_detected(state: dict[str, Any]) -> bool:
    if state.get("delivered") is True:
        return True
    text = f"{state.get('title') or ''}\n{state.get('bodySnippet') or ''}"
    return any(token in text for token in ("投递成功", "发送成功", "已发送", "已投递", "已沟通", "继续沟通", "继续聊", "沟通中"))


def _zhilian_apply_inspect_script() -> str:
    return r"""
    (function(){
      const text = (document.body && (document.body.innerText || document.body.textContent) || '').trim();
      const title = document.title || '';
      const href = location.href || '';
      const loginRequired = /passport|login|account/.test(href) || /登录\/注册|请登录|扫码登录|验证码登录|手机验证码|安全验证|滑块/.test(title + '\n' + text.slice(0, 800));
      const delivered = /投递成功|发送成功|已发送|已投递|已沟通|继续沟通|继续聊|沟通中/.test(text);
      const requiresResume = /请选择简历|上传简历|完善简历|创建简历|附件简历/.test(text) && !/确认投递|立即投递|投递/.test(text);
      const requiresCaptcha = /验证码登录|手机验证码|安全验证|滑块|Security Verification|Verifying the safety of the connection|Tencent Cloud EdgeOne|check the box below/.test(title + '\n' + text);
      return JSON.stringify({
        ok: true,
        href,
        title,
        loginRequired,
        delivered,
        requires_user_action: requiresResume || requiresCaptcha,
        user_action: requiresCaptcha ? 'captcha_required' : (requiresResume ? 'resume_selection_required' : ''),
        bodySnippet: text.slice(0, 1200)
      });
    })()
    """


def _zhilian_apply_click_entry_script() -> str:
    return r"""
    (function(){
      const labels = ['投简历', '投递简历', '立即投递', '申请职位', '应聘职位', '我要应聘', '立即沟通', '继续沟通', '继续聊', '聊一聊', '沟通'];
      function visible(el){
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 1 && rect.height > 1;
      }
      const all = Array.from(document.querySelectorAll('button,a'));
      for (const label of labels) {
        const el = all.find(node => visible(node) && (node.innerText || node.textContent || '').trim() === label);
        if (el) {
          el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
          return JSON.stringify({ok:true, clicked:label});
        }
      }
      const fuzzy = all.find(node => {
        const t = (node.innerText || node.textContent || '').trim();
        return visible(node) && /立即沟通|投递|应聘|沟通|继续聊|聊一聊/.test(t) && !/已投递|已沟通|取消|关闭/.test(t);
      });
      if (fuzzy) {
        const t = (fuzzy.innerText || fuzzy.textContent || '').trim();
        fuzzy.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
        return JSON.stringify({ok:true, clicked:t.slice(0,40), fuzzy:true});
      }
      return JSON.stringify({ok:false, error:'zhilian_apply_entry_not_found'});
    })()
    """


def _zhilian_apply_fill_message_script(message: str) -> str:
    msg = json.dumps(message)
    return f"""
    (function(){{
      const message = {msg};
      const selectors = ['textarea', '[contenteditable="true"]', '.chat-input', '.im-input', '.message-input'];
      function visible(el){{
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 1 && rect.height > 1;
      }}
      let editor = null;
      for (const selector of selectors) {{
        editor = Array.from(document.querySelectorAll(selector)).find(visible);
        if (editor) break;
      }}
      if (!editor) return JSON.stringify({{ok:true, filled:false, reason:'editor_not_found'}});
      editor.focus();
      if (editor.tagName === 'TEXTAREA' || editor.tagName === 'INPUT') {{
        editor.value = message;
      }} else {{
        editor.innerText = message;
        editor.textContent = message;
      }}
      editor.dispatchEvent(new InputEvent('input', {{bubbles:true, inputType:'insertText', data:message}}));
      editor.dispatchEvent(new Event('change', {{bubbles:true}}));
      return JSON.stringify({{ok:true, filled:true, tag:editor.tagName, len:message.length}});
    }})()
    """


def _zhilian_apply_click_confirm_script() -> str:
    return r"""
    (function(){
      const labels = ['发送', '确认发送', '确认投递', '立即投递', '投递', '确认', '继续沟通', '继续聊', '完成', '确定'];
      function visible(el){
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 1 && rect.height > 1;
      }
      const all = Array.from(document.querySelectorAll('button,a'));
      for (const label of labels) {
        const el = all.find(node => visible(node) && (node.innerText || node.textContent || '').trim() === label);
        if (el) {
          el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
          return JSON.stringify({ok:true, clicked:label});
        }
      }
      const fuzzy = all.find(node => {
        const t = (node.innerText || node.textContent || '').trim();
        return visible(node) && /发送|确认|投递|继续沟通|继续聊|确定/.test(t) && !/取消|关闭|返回/.test(t);
      });
      if (fuzzy) {
        const t = (fuzzy.innerText || fuzzy.textContent || '').trim();
        fuzzy.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
        return JSON.stringify({ok:true, clicked:t.slice(0,40), fuzzy:true});
      }
      return JSON.stringify({ok:false, error:'zhilian_confirm_button_not_found'});
    })()
    """
