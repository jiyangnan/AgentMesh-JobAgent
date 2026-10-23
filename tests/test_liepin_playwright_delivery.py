"""Offline DOM checks for job-bound conversation receipts, never real accounts."""
import pytest

from tests.test_playwright_runtime import chrome
from jobagent.drivers.playwright_runtime import PlaywrightRuntime
from jobagent.infra.browser_work import BrowserWorkError
from jobagent.platforms.liepin.playwright_delivery import LiepinDelivery


def fixture_adapter(runtime, monkeypatch, *, route="123", status="未读", pending=False):
    monkeypatch.setattr("jobagent.application.playwright_work.pace", lambda session: None)
    runtime.context.route("https://www.liepin.com/**", lambda route: route.fulfill(body="<html>official fixture detail</html>"))
    page = runtime.context.pages[0]
    page.set_content(f'''<div class="im-ui-chat-container"><div>招聘人甲 · 数据产品经理</div>
      <button aria-label="work" onclick="window.open('https://www.liepin.com/a/{route}.shtml')">岗位</button>
      <textarea class="im-ui-textarea"></textarea>
      <div class="im-ui-message-item-send"><div class="im-ui-txt send"><div class="text">已确认招呼</div></div>
      <span class="im-ui-message-read-status">{status}</span>
      <span class="im-ui-message-item-loadingicon {'hide' if not pending else ''}">等待</span></div></div>''')
    runtime.page = page
    adapter = LiepinDelivery(runtime)
    adapter.page = page
    adapter.identity = {"recruiter_name": "招聘人甲"}
    return adapter, {"binding": {"job_id": "123"},
                     "task": {"job": {"url": "https://www.liepin.com/a/123.shtml", "cloud_greeting": "已确认招呼"}}}


def test_conversation_job_popup_is_verified_and_only_owned_popup_closed(chrome, monkeypatch):
    with PlaywrightRuntime(chrome) as runtime:
        adapter, work = fixture_adapter(runtime, monkeypatch)
        count = len(runtime.context.pages)
        result = adapter._conversation(work)
        assert result["conversation_job_verified"] is True
        assert result["message_state"] == "sent"
        assert len(runtime.context.pages) == count
        assert adapter.page.locator("textarea").is_visible()
        # Same live conversation does not repeatedly navigate to its job.
        adapter.page.locator('[aria-label="work"]').evaluate("e => e.onclick = () => {throw Error('repeated navigation')}")
        assert adapter._conversation(work)["message_state"] == "sent"


def test_other_job_conversation_never_becomes_delivery_evidence(chrome, monkeypatch):
    with PlaywrightRuntime(chrome) as runtime:
        adapter, work = fixture_adapter(runtime, monkeypatch, route="456")
        with pytest.raises(BrowserWorkError, match="exact approved job"):
            adapter._conversation(work)


def test_resume_chooser_uses_visible_nested_cancel_before_route_check(chrome, monkeypatch):
    with PlaywrightRuntime(chrome) as runtime:
        adapter, work = fixture_adapter(runtime, monkeypatch)
        work["action"] = "prepare_resume"
        adapter.page.evaluate('''() => {document.body.insertAdjacentHTML('beforeend', `
          <div role="dialog">选择附件简历<button aria-label="Close" class="ant-im-modal-close" style="display:contents">
          <span aria-label="close" onclick="this.closest('[role=dialog]').remove()">×</span></button></div>`); }''')
        assert adapter._conversation(work)["conversation_job_verified"] is True
        assert adapter.page.get_by_role("dialog").count() == 0


@pytest.mark.parametrize("status,pending", [("", False), ("未读", True)])
def test_outgoing_text_without_acknowledgement_remains_unknown(chrome, monkeypatch, status, pending):
    with PlaywrightRuntime(chrome) as runtime:
        adapter, work = fixture_adapter(runtime, monkeypatch, status=status, pending=pending)
        result = adapter._conversation(work)
        assert result["existing_outgoing_text"] == "已确认招呼"
        assert result["message_state"] == "unknown"


def test_prepare_reports_every_attachment_without_selecting_or_submitting(chrome, monkeypatch):
    with PlaywrightRuntime(chrome) as runtime:
        adapter, work = fixture_adapter(runtime, monkeypatch)
        monkeypatch.setattr(adapter, "_online_resume", lambda work: "中文简历")
        adapter.page.evaluate('''() => {window.submits=0;document.body.insertAdjacentHTML('beforeend', `
          <div role="dialog"><h2>选择附件简历</h2><p>同时发送在线简历和附件简历</p>
          <button aria-label="Close">关闭</button>
          <label><input type="radio" name="resume" checked><p>项目经理</p><p>2026-09-14 14:10上传</p><span>预览</span></label>
          <label><input type="radio" name="resume"><p>产品经理</p><p>2026-05-21 18:07上传</p><span>预览</span></label>
          <button onclick="window.submits++">立即投递</button></div>`)}''')
        _, evidence = adapter._resume_dialog(work, may_open=False)
        assert evidence["attachment_options"] == [
            {"reference": "项目经理（2026-09-14 14:10上传）", "selected": True},
            {"reference": "产品经理（2026-05-21 18:07上传）", "selected": False}]
        assert evidence["submission_attempted"] is False
        assert adapter.page.evaluate("window.submits") == 0


def test_resume_submit_persists_checked_choice_and_reconciliation_never_resends(chrome, monkeypatch, tmp_path):
    from jobagent.infra import state
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    with PlaywrightRuntime(chrome) as runtime:
        adapter, work = fixture_adapter(runtime, monkeypatch)
        work.update(action="submit_resume", work_id="fixture-resume", nonce="fixture-nonce")
        work["task"].update(resume_reference="中文简历", attachment_reference="产品经理（2026-05-21 18:07上传）")
        monkeypatch.setattr(adapter, "_online_resume", lambda work: "中文简历")
        adapter._conversation(work)  # Verify before showing the modal.
        adapter.page.evaluate('''() => {window.submits=0;document.body.insertAdjacentHTML('beforeend', `
          <div role="dialog"><h2>选择附件简历</h2><p>同时发送在线简历和附件简历</p>
          <button aria-label="Close">关闭</button>
          <label><input type="radio" checked><p>产品经理</p><p>2026-05-21 18:07上传</p></label>
          <button id="submit">立即投递</button></div>`);
          document.querySelector('#submit').onclick = () => { window.submits++;
            document.querySelector('[role=dialog]').remove();
            document.querySelector('.im-ui-chat-container').insertAdjacentHTML('beforeend', `
              <div class="im-ui-message-item-send"><div class="im-ui-send-attachment-card">在线简历 附件简历</div>
              <span class="im-ui-message-read-status">未读</span></div>`);
          }; }''')
        _, adapter.prepared_choice = adapter._resume_dialog(work, may_open=False)
        assert adapter._submit_resume(work, may_act=True)["evidence"]["attachment_reference"] == work["task"]["attachment_reference"]
        assert (tmp_path / "playwright_receipts/fixture-resume.preflight.json").exists()
        assert adapter._submit_resume(work, may_act=False)["evidence"]["resume_state"] == "sent"
        assert adapter.page.evaluate("window.submits") == 1


@pytest.mark.parametrize("prior_identical", [False, True])
def test_exact_greeting_acknowledgement_and_reconciliation_never_resends(chrome, monkeypatch, tmp_path, prior_identical):
    from jobagent.infra import state
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    with PlaywrightRuntime(chrome) as runtime:
        adapter, work = fixture_adapter(runtime, monkeypatch)
        work.update(action="send_greeting", work_id="fixture-greeting", nonce="only-once")
        if not prior_identical:
            adapter.page.locator('.im-ui-message-item-send').evaluate('e=>e.remove()')
        adapter.page.evaluate('''() => {window.sends=0;
          const button=document.createElement('button');button.className='im-ui-basic-send-btn';button.textContent='发送';
          button.onclick=()=>{window.sends++;const text=document.querySelector('textarea').value;
            document.querySelector('.im-ui-chat-container').insertAdjacentHTML('beforeend', `
              <div class="im-ui-message-item-send"><div class="im-ui-txt send"><div class="text">${text}</div></div>
              <span class="im-ui-message-read-status">未读</span></div>`);};
          document.querySelector('.im-ui-chat-container').append(button); }''')
        assert adapter._send_greeting(work, may_act=True)["evidence"]["outgoing_text"] == "已确认招呼"
        assert adapter._send_greeting(work, may_act=False)["outcome"] == "success"
        assert adapter.page.evaluate("window.sends") == 1


def test_missing_resume_checkpoint_cannot_turn_a_card_into_success(chrome, monkeypatch, tmp_path):
    from jobagent.infra import state
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    with PlaywrightRuntime(chrome) as runtime:
        adapter, work = fixture_adapter(runtime, monkeypatch)
        work.update(action="submit_resume", work_id="no-checkpoint", nonce="once")
        work["task"].update(resume_reference="中文简历", attachment_reference="已选附件")
        with pytest.raises(BrowserWorkError, match="checked pre-submit choice"):
            adapter._submit_resume(work, may_act=False)
