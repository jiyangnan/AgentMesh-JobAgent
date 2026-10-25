"""Isolated Chrome fixtures for Boss identity and interrupted single-send work."""
import json

import pytest

from jobagent.drivers.playwright_runtime import PlaywrightRuntime
from jobagent.infra import state
from jobagent.infra.browser_work import BrowserWorkError
from jobagent.platforms.boss.playwright_delivery import CHAT_URL, BossDelivery
from tests.test_playwright_runtime import chrome as chrome_fixture

chrome = chrome_fixture

JOB_URL = "https://www.zhipin.com/job_detail/fixture-job.html"


def work(action="inspect_delivery", **extra):
    return {"work_id": "fixture-work", "nonce": "fixture-nonce", "state": "ready", "action": action,
            "binding": {"platform": "boss", "job_id": "fixture-job"},
            "task": {"job": {"id": "fixture-job", "url": JOB_URL, "title": "数据产品经理",
                            "company": "合成测试公司", "cloud_greeting": "已确认的完整招呼"},
                     "boss_recruiter_identity": {"recruiter_name": "招聘者甲", "recruiter_id": "fixture-recruiter"}},
            **extra}


def chat_html(*, existing="", status="送达", changed=None, send="enabled", headhunter=False):
    display_title = "数据产品经理（猎头职位）" if headhunter else "数据产品经理"
    bound = {"encryptJobId": "fixture-job", "jobName": display_title, "brandName": "合成测试公司",
             "name": "招聘者甲", "encryptBossId": "fixture-recruiter", **(changed or {})}
    bubble = (f'<li class="message-item item-myself" data-mid="old-message">'
              f'<div class="text-content">{existing}</div><span class="message-status">{status}</span></li>') if existing else ""
    send_button = {"enabled": '<button type="send" class="btn-send disabled">发送</button>',
                   "disabled": '<button type="send" class="btn-send disabled">发送</button>',
                   "missing": ""}[send]
    company_span = "" if headhunter else f'<span>{bound["brandName"]}</span>'
    base_title = "猎头顾问" if headhunter else "招聘"
    return f"""<meta charset="utf-8">
    <div class="nav-figure"><span class="label-text">合成账号</span></div><a ka="header-resume">简历</a>
    <div class="chat-user"><input class="boss-search-input"><li role="listitem">招聘者甲 {bound["brandName"]}</li></div>
    <div class="chat-conversation"><div class="base-info"><span class="name-text" style="display:none"></span>
      <div><span class="name-text">招聘者甲</span></div>{company_span}<span class="base-title">{base_title}</span></div>
      <div class="chat-position-content"><span class="position-name">{display_title}</span></div>
      <ul class="im-list">{bubble}</ul><div id="chat-input" contenteditable="true"></div>
      {send_button}</div>
    <script>
    document.querySelector('.base-info > .name-text').remove();
    document.querySelector('.chat-position-content').__vue__={{conversation$:{json.dumps(bound, ensure_ascii=False)}}};
    document.querySelector('[role=listitem]').__vue__={{$props:{{source:{{encryptJobId:'fixture-job',name:'招聘者甲',brandName:'合成测试公司'}}}}}};
    window.sends=0;
    const editor=document.querySelector('#chat-input');
    const sendControl=document.querySelector('button');
    if(sendControl && {str(send == "enabled").lower()}){{
      // Boss enables the real send control one tick after the editor gets text.
      editor.addEventListener('input',()=>{{ setTimeout(()=>{{
        sendControl.classList.toggle('disabled', !editor.innerText.trim()); }}, 0); }});
    }}
    if(sendControl) sendControl.onclick=()=>{{
      window.sends++;
      const e=document.querySelector('#chat-input'), n=document.createElement('li');
      n.className='message-item item-myself';n.setAttribute('data-mid','new-message-'+window.sends);
      const t=document.createElement('div');t.className='text-content';t.textContent=e.innerText;n.append(t);
      const s=document.createElement('span');s.className='message-status';s.textContent=window.fixtureStatus??'送达';n.append(s);
      document.querySelector('.im-list').append(n);e.innerText='';
      sendControl.classList.add('disabled');
    }};
    </script>"""


def detail_html(*, friend=False, job_id="fixture-job", status="招聘中", anonymized=False):
    company_block = ("" if anonymized else
                     '<div class="sider-company"><div class="company-info"><a ka="job-detail-company_custompage">合成测试公司</a></div></div>')
    client_line = '<span class="brand-name">客户公司：合成测试公司</span>' if anonymized else ""
    return f"""<meta charset="utf-8">
    <div class="nav-figure"><span class="label-text">合成账号</span></div>
    <div class="job-banner"><h1>数据产品经理</h1><div class="job-status">{status}</div>{client_line}
      <a class="btn-startchat" data-isfriend="{'true' if friend else 'false'}"
         redirect-url="/web/geek/chat?id=fixture-recruiter&amp;jobId={job_id}"
         onclick="localStorage.opens=Number(localStorage.opens||0)+1;location.href='{CHAT_URL}?opened=1'">
         {'继续沟通' if friend else '立即沟通'}</a></div>
    {company_block}
    <div class="job-boss-info"><h2 class="name">招聘者甲<span>刚刚活跃</span></h2></div>"""


def setup(runtime, monkeypatch, tmp_path, *, detail=None, chat=None):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("jobagent.application.playwright_work.pace", lambda *_: None)
    runtime.session["accounts"] = {"boss": "合成账号"}
    runtime.context.route("**/*", lambda route: route.fulfill(content_type="text/html", body=(
        (detail or detail_html()) if "/job_detail/" in route.request.url else (chat or chat_html()))))
    return BossDelivery(runtime)


def sends(page):
    return page.evaluate("window.sends", isolated_context=False)


def test_existing_exact_history_is_read_without_a_new_greeting(chrome, monkeypatch, tmp_path):
    with PlaywrightRuntime(chrome) as runtime:
        adapter = setup(runtime, monkeypatch, tmp_path, detail=detail_html(friend=True),
                        chat=chat_html(existing="已确认的完整招呼"))
        w = work()
        adapter.prepare(w)
        result = adapter.execute(w, may_act=True)
        from jobagent.application.native_work import _validate_delivery
        _validate_delivery(w, result, result["evidence"])
        assert result["evidence"]["message_state"] == "delivered"
        assert result["evidence"]["page_url"].startswith(CHAT_URL)
        assert sends(adapter.page) == 0


@pytest.mark.parametrize("change", [
    {"encryptJobId": "other-job"}, {"jobName": "另一职位"}, {"brandName": "另一公司"},
    {"name": "另一招聘者"}, {"encryptBossId": "another-recipient"},
])
def test_same_title_or_visible_card_never_overrides_wrong_bound_identity(chrome, monkeypatch, tmp_path, change):
    with PlaywrightRuntime(chrome) as runtime:
        adapter = setup(runtime, monkeypatch, tmp_path, chat=chat_html(changed=change))
        with pytest.raises(BrowserWorkError, match="exact bound conversation"):
            adapter.prepare(work("send_greeting"))
        assert sends(adapter.page) == 0


def test_one_send_has_durable_checkpoint_and_reconciliation_does_not_send(chrome, monkeypatch, tmp_path):
    with PlaywrightRuntime(chrome) as runtime:
        adapter = setup(runtime, monkeypatch, tmp_path)
        w = work("send_greeting")
        adapter.prepare(w)
        result = adapter.execute(w, may_act=True)
        assert result["evidence"]["outgoing_text"] == w["task"]["job"]["cloud_greeting"]
        assert result["evidence"]["message_id"] == "new-message-1"
        assert adapter.execute(w, may_act=False)["outcome"] == "success"
        assert sends(adapter.page) == 1
        assert (tmp_path / "state/playwright_receipts/fixture-work.preflight.json").exists()


def test_already_sent_text_cannot_be_sent_a_second_time(chrome, monkeypatch, tmp_path):
    with PlaywrightRuntime(chrome) as runtime:
        adapter = setup(runtime, monkeypatch, tmp_path, chat=chat_html(existing="已确认的完整招呼"))
        w = work("send_greeting")
        adapter.prepare(w)
        result = adapter.execute(w, may_act=True)
        assert result["evidence"]["already_present"] is True
        assert result["evidence"]["submission_attempted"] is False
        assert sends(adapter.page) == 0


@pytest.mark.parametrize("status", ["", "发送中"])
def test_pending_outgoing_text_blocks_new_send_intent(chrome, monkeypatch, tmp_path, status):
    with PlaywrightRuntime(chrome) as runtime:
        adapter = setup(runtime, monkeypatch, tmp_path, chat=chat_html(existing="已确认的完整招呼", status=status))
        with pytest.raises(BrowserWorkError, match="pending message"):
            adapter.prepare(work("send_greeting"))
        assert sends(adapter.page) == 0


def test_restart_after_uncertain_click_only_reads_the_new_receipt(chrome, monkeypatch, tmp_path):
    with PlaywrightRuntime(chrome) as runtime:
        adapter = setup(runtime, monkeypatch, tmp_path)
        w = work("send_greeting")
        adapter.prepare(w)
        adapter.page.evaluate("window.fixtureStatus='发送中'", isolated_context=False)
        # Bound the receipt wait while preserving the actual one-click path.
        import jobagent.platforms.boss.playwright_delivery as module
        with monkeypatch.context() as patch:
            patch.setattr(module, "RECEIPT_WAIT_SECONDS", 0)
            with pytest.raises(BrowserWorkError, match="never resend"):
                adapter.execute(w, may_act=True)
        assert sends(adapter.page) == 1
        adapter.page.locator(".message-status").evaluate("e=>e.textContent='送达'")
    with PlaywrightRuntime(chrome) as runtime:
        adapter = setup(runtime, monkeypatch, tmp_path)
        w["state"] = "leased"
        adapter.prepare(w)
        assert adapter.execute(w, may_act=False)["outcome"] == "success"
        assert sends(adapter.page) == 1


def test_missing_checkpoint_is_not_reconstructed_from_old_text(chrome, monkeypatch, tmp_path):
    with PlaywrightRuntime(chrome) as runtime:
        adapter = setup(runtime, monkeypatch, tmp_path, chat=chat_html(existing="已确认的完整招呼"))
        w = work("send_greeting", state="leased")
        adapter.prepare(w)
        with pytest.raises(BrowserWorkError, match="durable checkpoint"):
            adapter.execute(w, may_act=False)
        assert sends(adapter.page) == 0


@pytest.mark.parametrize("send", ["disabled", "missing"])
def test_unavailable_send_control_leaves_no_draft_and_no_checkpoint(chrome, monkeypatch, tmp_path, send):
    with PlaywrightRuntime(chrome) as runtime:
        adapter = setup(runtime, monkeypatch, tmp_path, chat=chat_html(send=send))
        import jobagent.platforms.boss.playwright_delivery as module
        monkeypatch.setattr(module, "SEND_ENABLE_WAIT_SECONDS", 0.2)
        w = work("send_greeting")
        adapter.prepare(w)
        with pytest.raises(BrowserWorkError, match="send control"):
            adapter.execute(w, may_act=True)
        assert sends(adapter.page) == 0
        assert adapter._text("#chat-input") == ""
        assert not (tmp_path / "state/playwright_receipts/fixture-work.preflight.json").exists()


@pytest.mark.parametrize("send", ["disabled", "missing"])
def test_presend_failure_settles_unresolved_instead_of_wedging_reconciliation(chrome, monkeypatch, tmp_path, send):
    with PlaywrightRuntime(chrome) as runtime:
        adapter = setup(runtime, monkeypatch, tmp_path, chat=chat_html(send=send))
        import jobagent.platforms.boss.playwright_delivery as module
        monkeypatch.setattr(module, "SEND_ENABLE_WAIT_SECONDS", 0.2)
        w = work("send_greeting")
        adapter.prepare(w)
        with pytest.raises(BrowserWorkError, match="send control"):
            adapter.execute(w, may_act=True)
        w["state"] = "leased"
        recovered = BossDelivery(runtime)
        recovered.prepare(w)
        result = recovered.execute(w, may_act=False)
        assert result["outcome"] == "unresolved"
        assert result["evidence"]["reason"] == "send_not_attempted"
        assert result["evidence"]["submission_attempted"] is False
        assert result["evidence"]["message_state"] == "not_sent"
        assert result["evidence"]["outgoing_text"] == ""
        from jobagent.application.native_work import _validate_delivery
        _validate_delivery(w, result, result["evidence"])
        assert sends(recovered.page) == 0


def test_open_communication_once_then_reconcile_existing_conversation(chrome, monkeypatch, tmp_path):
    with PlaywrightRuntime(chrome) as runtime:
        adapter = setup(runtime, monkeypatch, tmp_path)
        w = work("open_communication")
        adapter.prepare(w)
        result = adapter.execute(w, may_act=True)
        assert result["evidence"]["conversation_job_verified"]
        w["state"] = "leased"
        recovered = BossDelivery(runtime)
        recovered.prepare(w)
        assert recovered.execute(w, may_act=False)["outcome"] == "success"
        assert recovered.page.evaluate("localStorage.opens") == "1"
        assert sends(recovered.page) == 0


def test_changed_control_cannot_open_another_job(chrome, monkeypatch, tmp_path):
    with PlaywrightRuntime(chrome) as runtime:
        adapter = setup(runtime, monkeypatch, tmp_path, detail=detail_html(job_id="another-job"))
        with pytest.raises(BrowserWorkError, match="exact job and recruiter"):
            adapter.prepare(work("open_communication"))
        assert adapter.page.evaluate("localStorage.opens") is None


def test_closed_job_is_not_inferred_from_missing_button(chrome, monkeypatch, tmp_path):
    with PlaywrightRuntime(chrome) as runtime:
        adapter = setup(runtime, monkeypatch, tmp_path, detail=detail_html(status="职位已关闭"))
        w = work()
        adapter.prepare(w)
        result = adapter.execute(w, may_act=True)
        assert result["outcome"] == "unavailable"
        assert result["evidence"]["unavailable_text"] == "职位已关闭"
        assert adapter.page.evaluate("localStorage.opens") is None


def test_recency_badge_still_authorizes_communication(chrome, monkeypatch, tmp_path):
    with PlaywrightRuntime(chrome) as runtime:
        adapter = setup(runtime, monkeypatch, tmp_path, detail=detail_html(status="最新"))
        w = work("open_communication")
        adapter.prepare(w)
        result = adapter.execute(w, may_act=True)
        assert result["outcome"] == "success"
        assert result["evidence"]["conversation_job_verified"] is True
        assert sends(adapter.page) == 0


def test_anonymized_employer_company_comes_from_the_banner(chrome, monkeypatch, tmp_path):
    with PlaywrightRuntime(chrome) as runtime:
        adapter = setup(runtime, monkeypatch, tmp_path, detail=detail_html(anonymized=True))
        w = work("open_communication")
        adapter.prepare(w)
        assert adapter.identity["company"] == "合成测试公司"
        result = adapter.execute(w, may_act=True)
        assert result["outcome"] == "success"
        assert sends(adapter.page) == 0


def test_anonymized_job_binds_conversation_by_exact_job_and_recruiter(chrome, monkeypatch, tmp_path):
    with PlaywrightRuntime(chrome) as runtime:
        adapter = setup(runtime, monkeypatch, tmp_path, chat=chat_html(changed={"brandName": "上海洛全企业"}))
        w = work("send_greeting")
        w["task"]["job"]["company"] = "某中型大数据公司"
        adapter.prepare(w)
        result = adapter.execute(w, may_act=True)
        assert result["outcome"] == "success"
        assert result["evidence"]["message_id"] == "new-message-1"
        assert adapter.identity["company"] == "上海洛全企业"
        assert sends(adapter.page) == 1


def test_headhunter_conversation_without_company_line_is_still_bound(chrome, monkeypatch, tmp_path):
    with PlaywrightRuntime(chrome) as runtime:
        adapter = setup(runtime, monkeypatch, tmp_path,
                        chat=chat_html(headhunter=True, changed={"brandName": "某中型大数据公司"}))
        w = work("send_greeting")
        w["task"]["job"]["company"] = "某中型大数据公司"
        adapter.prepare(w)
        result = adapter.execute(w, may_act=True)
        assert result["outcome"] == "success"
        assert result["evidence"]["message_id"] == "new-message-1"
        from jobagent.application.native_work import _validate_delivery
        _validate_delivery(w, result, result["evidence"])
        assert sends(adapter.page) == 1
