"""Liepin manual apply-open flow.

The beta implementation opens selected Liepin job pages for human review. It
does not click apply buttons, send messages, or upload cookies.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from jobagent.domain.models import SendAttempt
from jobagent.drivers.boss import create_driver

from .audit import LiepinAuditEvent, LiepinAuditLog


@dataclass
class LiepinApplyOpenResult:
    ok: bool
    opened: int
    planned: int
    failed: int
    total: int
    events: list[dict[str, Any]] = field(default_factory=list)
    handoff: list[dict[str, Any]] = field(default_factory=list)
    mode: str = "manual_apply_open"
    platform: str = "liepin"
    next_suggested: str = "Review opened Liepin pages manually, then run `jobagent liepin audit`."
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


class LiepinApplyOpener:
    def __init__(
        self,
        driver: Any | None = None,
        audit_log: LiepinAuditLog | None = None,
    ):
        self.driver = driver
        self.audit_log = audit_log or LiepinAuditLog()

    def open_jobs(
        self,
        jobs: list[dict[str, Any]],
        limit: int = 5,
        start: int = 0,
        wait_seconds: int = 3,
        dry_run: bool = False,
    ) -> LiepinApplyOpenResult:
        selected = jobs[max(0, start): max(0, start) + max(1, limit)]
        events: list[dict[str, Any]] = []
        handoff_items: list[dict[str, Any]] = []
        opened = 0
        planned = 0
        failed = 0

        for index, job in enumerate(selected, start=max(0, start)):
            url = str(job.get("url") or "")
            handoff = _handoff_evidence(job)
            event = self._event_from_job(
                job,
                status="planned" if dry_run else "opened",
                message="dry_run" if dry_run else "Opened for manual review.",
                evidence={"index": index, **handoff},
            )
            if not url:
                event = self._event_from_job(
                    job,
                    status="failed",
                    error="missing_job_url",
                    message="Cannot open Liepin job without url.",
                    evidence={"index": index, **handoff},
                )
                failed += 1
            elif dry_run:
                planned += 1
            else:
                driver = self.driver or create_driver(platform="liepin")
                self.driver = driver
                result = driver.open_url_in_new_tab(url, wait_seconds=wait_seconds)
                if result.get("ok"):
                    opened += 1
                    event = self._event_from_job(
                        job,
                        status="opened",
                        message="Opened for manual review.",
                        evidence={"index": index, **handoff, "open_result": result},
                    )
                else:
                    failed += 1
                    event = self._event_from_job(
                        job,
                        status="failed",
                        error=str(result.get("error") or "open_failed"),
                        message="Failed to open Liepin job page.",
                        evidence={"index": index, **handoff, "open_result": result},
                    )

            self.audit_log.append(event)
            events.append(event.to_dict())
            handoff_items.append(_handoff_item(job, index=index, status=event.status, error=event.error))

        if dry_run:
            next_suggested = (
                "Review handoff, then rerun without `--dry-run` to open Liepin pages for manual review."
            )
            user_prompt = ""
            requires_user_action = False
        else:
            next_suggested = (
                "Review opened Liepin pages manually, copy each handoff greeting, "
                "then run `jobagent liepin audit`."
            )
            user_prompt = (
                "请在已打开的猎聘页面中人工确认岗位，并复制 handoff 列表里对应的 greeting。"
                "完成后可运行 `jobagent liepin audit` 查看记录。"
            )
            requires_user_action = opened > 0

        return LiepinApplyOpenResult(
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
    ) -> LiepinAuditEvent:
        return LiepinAuditEvent(
            action="apply_open",
            status=status,
            job_url=str(job.get("url") or ""),
            job_name=str(job.get("name") or job.get("title") or ""),
            company=str(job.get("company") or ""),
            error=error,
            message=message,
            evidence=evidence or {},
        )


class LiepinApplySender:
    """Submit the account resume and require resume-specific evidence.

    Some Liepin jobs expose only a chat entry. Opening it may send a
    platform-owned default message, which is never counted as resume
    delivery. The sender continues to the explicit ``发简历`` action and
    records success only after resume-specific delivery evidence appears.
    """

    def __init__(
        self,
        driver: Any | None = None,
        audit_log: LiepinAuditLog | None = None,
    ):
        self.driver = driver
        self.audit_log = audit_log or LiepinAuditLog()

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
            if skip_delivered and _normalize_liepin_url(url) in delivered_urls:
                attempt = SendAttempt(job_url=url, message=message, delivered=False, error="already_delivered")
                attempt.steps = [
                    {
                        "step": "skip_liepin_apply_send",
                        "ok": True,
                        "reason": "already_delivered",
                        "url": url,
                    }
                ]
                status = "skipped"
                audit_message = "Skipped because this Liepin job URL was already delivered."
            else:
                attempt = self._send_one(job, message, wait_seconds=wait_seconds, dry_run=dry_run)
                status = "planned" if dry_run else ("delivered" if attempt.delivered else "failed")
                audit_message = "dry_run" if dry_run else ("Delivered." if attempt.delivered else "Failed.")
                if attempt.delivered:
                    delivered_urls.add(_normalize_liepin_url(attempt.job_url))

            attempts.append(attempt)
            self.audit_log.append(
                LiepinAuditEvent(
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
                        "greeting": message,
                        "score": job.get("score"),
                        "match_level": job.get("match_level") or job.get("recommendation") or job.get("cloud_recommendation"),
                        "steps": attempt.steps,
                    },
                )
            )
            if stop_on_failure and not dry_run and status == "failed":
                break
        return attempts

    def _send_one(
        self,
        job: dict[str, Any],
        message: str,
        wait_seconds: int = 3,
        dry_run: bool = False,
    ) -> SendAttempt:
        url = str(job.get("url") or "")
        attempt = SendAttempt(job_url=url, message=message, delivered=False)
        steps: list[dict[str, Any]] = []
        if not url:
            attempt.error = "missing_job_url"
            attempt.steps = steps
            return attempt
        if dry_run:
            attempt.error = "dry_run"
            attempt.steps = [{"step": "plan_liepin_apply_send", "ok": True, "url": url}]
            return attempt

        driver = self.driver or create_driver(platform="liepin")
        self.driver = driver

        open_result = driver.open_url_in_new_tab(url, wait_seconds=wait_seconds)
        steps.append({"step": "open_job_url", **open_result})
        if not open_result.get("ok"):
            attempt.error = str(open_result.get("error") or "open_job_url_failed")
            attempt.steps = steps
            return attempt

        inspect_before = _exec_liepin_js(driver, _liepin_apply_inspect_script())
        steps.append({"step": "inspect_before_apply", **inspect_before})
        if _liepin_page_requires_login(inspect_before):
            attempt.error = "login_required"
            attempt.steps = steps
            return attempt
        if _liepin_delivery_detected(inspect_before):
            attempt.delivered = True
            attempt.steps = steps
            return attempt

        if not inspect_before.get("canSendResume"):
            click_entry = _exec_liepin_js(driver, _liepin_apply_click_entry_script())
            steps.append({"step": "click_apply_or_contact_entry", **click_entry})
            if not click_entry.get("ok"):
                attempt.error = str(click_entry.get("error") or "apply_entry_not_found")
                attempt.steps = steps
                return attempt

        terminal = self._drive_dialog(driver, message=message, steps=steps)
        if terminal.get("delivered"):
            attempt.delivered = True
        else:
            attempt.error = str(terminal.get("error") or "delivery_not_verified")
        attempt.steps = steps
        return attempt

    def _drive_dialog(
        self,
        driver: Any,
        message: str,
        steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        for _ in range(6):
            time.sleep(1)
            state = _exec_liepin_js(driver, _liepin_apply_inspect_script())
            steps.append({"step": "inspect_apply_state", **state})
            if _liepin_page_requires_login(state):
                return {"delivered": False, "error": "login_required"}
            if _liepin_delivery_detected(state):
                return {"delivered": True}
            if state.get("requires_user_action"):
                return {"delivered": False, "error": state.get("user_action") or "user_action_required"}

            if state.get("canSendResume"):
                resume_action = _exec_liepin_js(driver, _liepin_apply_click_resume_script())
                steps.append({"step": "click_liepin_resume_action", **resume_action})
                if not resume_action.get("ok"):
                    continue
                time.sleep(1.5)
                after_resume = _exec_liepin_js(driver, _liepin_apply_inspect_script())
                steps.append({"step": "inspect_after_resume_action", **after_resume})
                if _liepin_delivery_detected(after_resume):
                    return {"delivered": True}
                if _liepin_page_requires_login(after_resume):
                    return {"delivered": False, "error": "login_required"}
                if after_resume.get("requires_user_action"):
                    return {
                        "delivered": False,
                        "error": after_resume.get("user_action") or "user_action_required",
                    }
                continue

            confirm = _exec_liepin_js(driver, _liepin_apply_click_confirm_script())
            steps.append({"step": "click_liepin_confirm", **confirm})
            if not confirm.get("ok"):
                continue

            time.sleep(1.5)
            after = _exec_liepin_js(driver, _liepin_apply_inspect_script())
            steps.append({"step": "inspect_after_confirm", **after})
            if _liepin_delivery_detected(after):
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


def _normalize_liepin_url(url: str) -> str:
    return str(url or "").strip().rstrip("/")


def _handoff_item(
    job: dict[str, Any],
    index: int,
    status: str,
    error: str = "",
) -> dict[str, Any]:
    evidence = _handoff_evidence(job)
    action = (
        "copy_greeting_to_liepin_page"
        if evidence["has_greeting"]
        else "run_liepin_greet_preview_before_contact"
    )
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


def _exec_liepin_js(driver: Any, script: str) -> dict[str, Any]:
    if not hasattr(driver, "_exec_js"):
        return {"ok": False, "error": "driver_js_not_supported"}
    result = driver._exec_js(script)
    if isinstance(result, dict) and isinstance(result.get("raw"), str):
        import json

        try:
            parsed = json.loads(result["raw"])
            return parsed if isinstance(parsed, dict) else {"ok": False, "error": "unexpected_js_result"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return result if isinstance(result, dict) else {"ok": False, "error": "unexpected_js_result"}


def _liepin_page_requires_login(state: dict[str, Any]) -> bool:
    if state.get("loginRequired") is True:
        return True
    text = f"{state.get('title') or ''}\n{state.get('bodySnippet') or ''}"
    return any(
        token in text
        for token in ("登录/注册", "扫码登录", "验证码登录", "手机验证码", "安全验证", "滑块")
    )


def _liepin_delivery_detected(state: dict[str, Any]) -> bool:
    if state.get("delivered") is True:
        return True
    text = f"{state.get('title') or ''}\n{state.get('bodySnippet') or ''}"
    return any(
        token in text
        for token in ("投递成功", "简历发送成功", "简历已发送", "已投递")
    )


def _liepin_apply_inspect_script() -> str:
    return r"""
    (function(){
      const text = (document.body && (document.body.innerText || document.body.textContent) || '').trim();
      const title = document.title || '';
      const href = location.href || '';
      const loginRequired = /\/login|passport|account/.test(href) || /登录\/注册|扫码登录|验证码登录|手机验证码|安全验证|滑块/.test(title + '\n' + text.slice(0, 800));
      const delivered = /\/job\/apply\/success/.test(href)
        || /投递成功/.test(title)
        || /投递成功|简历发送成功|简历已发送|已投递/.test(text);
      const visibleButtons = Array.from(document.querySelectorAll('button,a')).filter(el => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 1 && rect.height > 1;
      }).map(el => (el.innerText || el.textContent || '').trim());
      const canConfirmResume = visibleButtons.some(t => /立即投递|确认投递|投递/.test(t));
      const canSendResume = visibleButtons.some(t => /^(发简历|发送简历|投递简历)$/.test(t));
      const requiresResume = /请选择简历|上传简历|完善简历|创建简历|附件简历/.test(text) && !canConfirmResume;
      const requiresCaptcha = /验证码登录|手机验证码|安全验证|滑块/.test(text);
      return JSON.stringify({
        ok: true,
        href,
        title,
        loginRequired,
        delivered,
        canSendResume,
        requires_user_action: requiresResume || requiresCaptcha,
        user_action: requiresCaptcha ? 'captcha_required' : (requiresResume ? 'resume_selection_required' : ''),
        bodySnippet: text.slice(0, 1200)
      });
    })()
    """


def _liepin_apply_click_entry_script() -> str:
    """Return JS that clicks the primary apply/contact entry button.

    Label order matters: 立即投递 variants come FIRST because Liepin only
    supports resume submission (no Boss-style greeting chat). 聊一聊 /
    立即沟通 are listed only as last-resort fallbacks for edge cases
    where a job page shows only a chat entry (rare); they are NOT used
    for sending greetings automatically.
    """
    return r"""
    (function(){
      const labels = [
        '投简历', '投递简历', '立即投递', '申请职位', '应聘职位',
        '我要应聘', '立即沟通', '继续沟通', '继续聊', '聊一聊', '沟通'
      ];
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
      return JSON.stringify({ok:false, error:'apply_entry_not_found'});
    })()
    """


def _liepin_apply_fill_message_script(message: str) -> str:
    import json

    msg = json.dumps(message)
    return f"""
    (function(){{
      const message = {msg};
      const selectors = [
        'textarea',
        '[contenteditable="true"]',
        '.chat-input',
        '.im-input',
        '.message-input'
      ];
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


def _liepin_apply_click_resume_script() -> str:
    return r"""
    (function(){
      const labels = ['发简历', '发送简历', '投递简历'];
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
      return JSON.stringify({ok:false, error:'resume_action_not_found'});
    })()
    """


def _liepin_apply_click_confirm_script() -> str:
    return r"""
    (function(){
      const labels = [
        '确认投递', '立即投递', '投递', '确认'
      ];
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
        return visible(node) && /确认投递|立即投递|投递/.test(t) && !/取消|关闭|返回/.test(t);
      });
      if (fuzzy) {
        const t = (fuzzy.innerText || fuzzy.textContent || '').trim();
        fuzzy.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
        return JSON.stringify({ok:true, clicked:t.slice(0,40), fuzzy:true});
      }
      return JSON.stringify({ok:false, error:'confirm_button_not_found'});
    })()
    """
