"""Full preparation/confirmation/durable-send flow in an isolated browser.

All browser traffic and file GETs are intercepted; no real account or employer.
The separate Server HTTP tests exercise the real policy/authorization routes.
"""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobagent.application import (
    delivery_confirmation,
    linkedin_application,
    native_work,
    playwright_work,
    review,
)
from jobagent.drivers.playwright_runtime import PlaywrightRuntime
from jobagent.infra import browser_work, cloud_client, protocol, rounds, state
from tests import test_linkedin_protocol, test_playwright_runtime

signed_plan = test_linkedin_protocol.signed_plan
chrome = test_playwright_runtime.chrome

HTML = r"""<!doctype html><html lang=en><body>
<button aria-expanded=false aria-label="Fixture (you)" onclick="this.setAttribute('aria-expanded',this.getAttribute('aria-expanded')==='true'?'false':'true');document.querySelector('[role=menu]').hidden=!document.querySelector('[role=menu]').hidden">Me</button>
<div role=menu hidden><a href="https://www.linkedin.com/in/fixture/">Fixture profile</a></div>
<main></main><div id=overlay></div>
<script>
const id=location.pathname.match(/view\/(\d+)/)?.[1];
let data=JSON.parse(localStorage.getItem('jobs')||'{}');
function save(){data[id]=job;localStorage.setItem('jobs',JSON.stringify(data));}
let job=data[id]||{step:0,name:'',email:'',phone:'5550101',resume:'',permit:'',policy:false,follow:true,sent:false,clicks:0};
function page(){
 if(!id){document.querySelector('main').innerHTML='Job tracker '+Object.entries(data).filter(([k,v])=>v.sent).map(([k,v])=>`<a href="https://www.linkedin.com/jobs/view/${k}/"><span>Engineer ${k}</span><span>Fixture Company · Tokyo</span><span>Just applied</span></a>`).join('');return;}
 document.querySelector('main').innerHTML=`<div>Fixture Company</div><h1>Engineer ${id}</h1><p>Tokyo, Japan</p>${job.sent?'<p>Application submitted</p>':'<button onclick="openForm()">Easy Apply</button>'}<h2>About the job</h2><p>Offline synthetic job description.</p>`;
}
function set(key,value){job[key]=value;save();}
function next(){job.step++;save();form();}
function openForm(){document.querySelector('#overlay').innerHTML='<div role=dialog><h2>Application</h2></div>';setTimeout(form,80);}
function form(){
 let body='';
 if(job.step===0)body=`<label>Full name<input required value="${job.name}" oninput="set('name',this.value)"></label><label>Email address<input type=email required value="${job.email}" oninput="set('email',this.value)"></label><label>Phone number<input type=tel required value="${job.phone}" oninput="set('phone',this.value)"></label><button onclick="next()">Next</button>`;
 if(job.step===1)body=`<fieldset><legend>Resume*</legend>${['English.pdf','Japanese.pdf'].map((f,i)=>`<div><input style="display:none" type=radio name=resume id=r${i} ${job.resume===f?'checked':''} onchange="set('resume','${f}')"><label for=r${i}>${f}</label><a href="/ambry/${f}" target=_blank>Download</a></div>`).join('')}</fieldset><button onclick="next()">Next</button>`;
 if(job.step===2)body=`<fieldset><legend>Authorized to work here?*</legend>${['Yes','No'].map(v=>`<label>${v}<input type=radio name=permit required ${job.permit===v?'checked':''} onchange="set('permit','${v}')"></label>`).join('')}</fieldset><label><input type=checkbox required ${job.policy?'checked':''} onchange="set('policy',this.checked)">I read and accept the employer policy</label><a href=https://example.test/privacy>Employer privacy policy</a><button onclick="next()">Review</button>`;
 if(job.step===3)body=`<h3>Review</h3><p>${job.name} | ${job.email} | ${job.phone} | ${job.resume} | Authorized: ${job.permit} | Policy: ${job.policy}</p><a href="/ambry/${job.resume}">Download selected resume</a><label><input type=checkbox ${job.follow?'checked':''} onchange="set('follow',this.checked)">Follow company</label><button onclick="job.clicks++;job.sent=true;save();document.querySelector('#overlay').innerHTML='';page()">Submit application</button>`;
 const back=job.step>0?`<button onclick="job.step--;save();window.form()">Back</button>`:'';
 document.querySelector('#overlay').innerHTML='<div role=dialog><h2>Application</h2>'+body+back+'</div>';
}
page();
</script></body></html>"""


@pytest.fixture
def flow(chrome, tmp_path, monkeypatch, signed_plan, request):
    _, _, sign, profile, intent = signed_plan
    account = getattr(request, "param", None) or "acct_linkedin_fixture"
    for name, value in {
        "APP_DIR": tmp_path,
        "STATE_DIR": tmp_path / "state",
        "ROUNDS_DIR": tmp_path / "state" / "rounds",
    }.items():
        monkeypatch.setattr(state, name, value)
    for module in (
        "infra.account_state",
        "application.native_work",
        "application.playwright_work",
        "application.linkedin_application",
        "application.review",
        "application.delivery_confirmation",
        "application.workflow_delivery",
        "application.delivery_receipts",
    ):
        monkeypatch.setattr(
            "jobagent." + module + ".current_account_ref", lambda: account
        )
    monkeypatch.setattr(playwright_work, "pace", lambda _: None)
    monkeypatch.setattr(native_work, "MIN_ACTION_INTERVAL_SECONDS", 0)
    session = {
        **chrome,
        "account_ref": account,
        "round_id": "round-fixture",
        "accounts": {"linkedin": "https://www.linkedin.com/in/fixture/"},
    }
    active = {
        "schema_version": rounds.ROUND_SCHEMA_VERSION,
        "round_id": "round-fixture",
        "status": "active",
        "platform_order": ["linkedin"],
        "intent": intent,
        "browser_executor": "playwright",
        "delivery_executor": "playwright",
        "playwright_session": session,
        "browser_session_id": session["id"],
        "platforms": {"linkedin": {"status": "reviewed"}},
    }
    rounds.save_round(active)
    jobs = [
        {
            "id": i,
            "title": "Engineer " + i,
            "company": "Fixture Company",
            "area": "Tokyo, Japan",
            "salary": None,
            "url": f"https://www.linkedin.com/jobs/view/{i}/",
            "score": 90,
            "reason": "Synthetic fit",
            "risk": None,
        }
        for i in ("123", "456")
    ]
    manifest = sign(
        {
            "manifest_type": "decision_manifest",
            "protocol_version": protocol.PROTOCOL_VERSION,
            "manifest_id": "matched-fixture",
            "discover_id": "discover-fixture",
            "platform": "linkedin",
            "account_ref": account,
            "round_id": active["round_id"],
            "profile_digest": protocol.digest_payload(profile),
            "intent_digest": protocol.digest_payload(intent),
            "expires_at": "2099-01-01T00:00:00Z",
            "deduplicated_count": 2,
            "selected": jobs,
            "review": [],
            "rejected": [],
        }
    )
    source = tmp_path / "decision.json"
    state.save_json(
        source,
        {
            "platform": "linkedin",
            "discover_id": "discover-fixture",
            "manifest": manifest,
        },
    )
    policy_calls = []

    def plan(body):
        policy_calls.append(copy.deepcopy(body))
        decisions = []
        for field in body["fields"]:
            contact = field["question"] in {
                "Full name",
                "Email address",
                "Phone number",
            }
            key = protocol.digest_payload(
                [
                    body["context_digest"],
                    "contact" if contact else body["job_id"],
                    field["field_id"],
                ]
            )
            item = {
                "field_id": field["field_id"],
                "reuse_key": key,
                "behavior": "confirm",
            }
            if field["question"] == "Full name":
                item.update(
                    behavior="profile_fact",
                    value="Synthetic Applicant",
                    source={"kind": "resume_fact", "reference": "basic.name"},
                )
            decisions.append(item)
        return {
            "plan": sign(
                {
                    k: v
                    for k, v in body.items()
                    if k not in {"fields", "confirm_promote"}
                }
                | {
                    "manifest_type": "linkedin_application_plan",
                    "protocol_version": 1,
                    "account_ref": account,
                    "fields_digest": protocol.digest_payload(body["fields"]),
                    "decisions": decisions,
                    "delivery_authorized": False,
                }
            )
        }

    monkeypatch.setattr(cloud_client, "linkedin_application_plan", plan)
    server = {}

    def register(body):
        assert all("application" not in item for item in body["candidates"])
        identifier = protocol.digest_payload(body)
        value = {
            k: body[k]
            for k in (
                "round_id",
                "discover_id",
                "manifest_id",
                "client_preview_id",
                "criteria_revision",
            )
        }
        value.update(
            protocol_version=2,
            account_ref=account,
            preview_id=identifier,
            candidate_digest=protocol.digest_payload(body["candidates"]),
        )
        server[identifier] = value
        return {
            "preview": sign(
                {
                    **value,
                    "manifest_type": "workflow_delivery_preview",
                    "delivery_authorized": False,
                }
            )
        }

    def approve(identifier, choice):
        assert choice == "confirm_all"
        server[identifier]["authorization"] = sign(
            {
                **server[identifier],
                "manifest_type": "workflow_delivery_authorization",
                "choice": choice,
                "authorization_id": "authorized-fixture",
                "authorized_at": rounds.utc_now(),
            }
        )
        return {"authorization": server[identifier]["authorization"]}

    monkeypatch.setattr(cloud_client, "workflow_delivery_preview", register)
    monkeypatch.setattr(cloud_client, "workflow_delivery_answer", approve)
    monkeypatch.setattr(
        cloud_client,
        "workflow_delivery_status",
        lambda key: {"authorization": server[key]["authorization"]},
    )
    # No workflow/API traffic in this local runtime test. Actual signed HTTP
    # transport contracts are checked in the Server repository.
    monkeypatch.setattr(
        cloud_client,
        "_request",
        lambda *a, **k: pytest.fail("Unexpected network request"),
    )
    files = {
        "English.pdf": b"%PDF-1.7\nSynthetic English resume",
        "Japanese.pdf": b"%PDF-1.7\nSynthetic Japanese resume",
    }
    from patchright.sync_api import APIRequestContext

    def download(_request, url, **kwargs):
        name = url.rsplit("/", 1)[-1]
        assert url == "https://www.linkedin.com/ambry/" + name and name in files
        content = files[name]
        return SimpleNamespace(
            status=200,
            headers={"content-length": str(len(content))},
            body=lambda: content,
            dispose=lambda: None,
        )

    monkeypatch.setattr(APIRequestContext, "get", download)
    world = {"account": "fixture"}
    original_enter = PlaywrightRuntime.__enter__

    def enter(runtime):
        original_enter(runtime)
        runtime.context.route(
            "**/*",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body=world.get("html", HTML).replace(
                    "/in/fixture/", "/in/" + world["account"] + "/"
                ),
            ),
        )
        return runtime

    monkeypatch.setattr(PlaywrightRuntime, "__enter__", enter)
    return SimpleNamespace(
        source=source,
        session=session,
        policy_calls=policy_calls,
        files=files,
        world=world,
    )


def stored_jobs(flow):
    with PlaywrightRuntime(flow.session) as runtime:
        page = next(
            p
            for p in runtime.context.pages
            if p.url.startswith("https://www.linkedin.com/")
        )
        return page.evaluate("JSON.parse(localStorage.getItem('jobs')||'{}')")


def answer(flow, response):
    checkpoint = rounds.ensure_current_round()["linkedin_preparation"]
    card = checkpoint["card"]
    assert (
        response["interaction"]["interaction_id"]
        == card["interaction"]["interaction_id"]
    )
    values = {}
    for key, item in card["questions"].items():
        f = item["field"]
        values[key] = {
            "email": "fixture@example.test",
            "tel": "5550101",
            "attachment": "English.pdf",
            "radio": "No",
            "checkbox": f["question"] != "Follow company",
        }[f["type"]]
    args = SimpleNamespace(
        interaction_id=response["interaction"]["interaction_id"],
        application_answers=values,
    )
    linkedin_application.respond(args)
    # Identical crash replay is safe and cannot change an answer.
    assert linkedin_application.respond(args)["idempotent_replay"]
    return native_work.next_work()


def preview(flow):
    response = review.review_decision("linkedin", input_path=str(flow.source))
    cards = []
    for _ in range(16):
        if response.get("event") == "linkedin_application_facts":
            cards.append(copy.deepcopy(response["interaction"]))
            response = answer(flow, response)
        elif response.get("work"):
            response = playwright_work.run(response["work"]["work_id"], max_steps=30)
        else:
            break
    assert response.get("event") == "delivery_preview", response
    assert all(j["clicks"] == 0 for j in stored_jobs(flow).values())
    return response, cards


def authorize(flow, response):
    from jobagent.infra.interaction_state import load_pending_interaction

    result = delivery_confirmation.respond_delivery_confirmation(
        load_pending_interaction(), choice="confirm_all", exclude_indices=[]
    )
    assert result.get("event") == "delivery_authorized", result
    return native_work.start_delivery(
        "linkedin",
        input_path=response["review_file"],
        preview_id=result["preview_id"],
        authorization_id=result["authorization_id"],
    )


def test_batch_preparation_full_preview_then_exactly_one_submit_per_job(flow):
    response, cards = preview(flow)
    # Email and phone are asked together once for the two-job batch. Employer
    # declarations and resume choices remain individually job-bound.
    contacts = [
        f
        for c in cards
        for f in c["fields"]
        if f["label"] in {"Email address", "Phone number"}
    ]
    assert len(contacts) == 2
    assert "fixture@example.test" in response["delivery_preview"]["fallback_text"]
    assert "English.pdf" in response["delivery_preview"]["fallback_text"]
    assert "Follow company: No" in response["delivery_preview"]["fallback_text"]
    for call in flow.policy_calls:
        assert all("value" not in f for f in call["fields"])
        assert "English.pdf" not in json.dumps(call)
    following = authorize(flow, response)
    completed = playwright_work.run(following["work"]["work_id"], max_steps=10)
    assert completed["summary"]["resume_submitted"] == 2, completed
    assert [j["clicks"] for j in stored_jobs(flow).values()] == [1, 1]
    authorized = state.load_json(Path(response["review_file"]))
    assert authorized["delivery_authorization"]["server_authorization"]
    resumed = native_work.next_work()
    assert resumed["next_suggested"] == "jobagent linkedin audit"
    assert state.load_json(Path(response["review_file"])) == authorized
    assert [j["clicks"] for j in stored_jobs(flow).values()] == [1, 1]


@pytest.mark.parametrize("change", ["answer", "file", "account"])
def test_changes_after_confirmation_block_before_submit_intent(flow, change):
    response, _ = preview(flow)
    work = authorize(flow, response)["work"]
    if change == "file":
        flow.files["English.pdf"] += b"changed"
    elif change == "account":
        flow.world["account"] = "other"
    else:
        with PlaywrightRuntime(flow.session) as runtime:
            page = next(p for p in runtime.context.pages if "/jobs/view/" in p.url)
            page.evaluate(
                "let d=JSON.parse(localStorage.jobs);d['123'].phone='999';localStorage.jobs=JSON.stringify(d)"
            )
    if change == "account":
        with pytest.raises(browser_work.BrowserWorkError):
            playwright_work.run(work["work_id"], max_steps=10)
    else:
        recovered = playwright_work.run(work["work_id"], max_steps=10)
        assert recovered["old_authorization_invalidated"]
    assert all(j["clicks"] == 0 for j in stored_jobs(flow).values())
    assert not any(
        w["side_effect"] and w["state"] != "ready"
        for w in browser_work.list_work(native_work._binding())
    )


@pytest.mark.parametrize("late", [False, True])
def test_lost_response_and_late_receipt_never_repeat_submit(flow, monkeypatch, late):
    from jobagent.platforms.linkedin.playwright_delivery import LinkedInDelivery

    response, _ = preview(flow)
    first = authorize(flow, response)["work"]
    execute = LinkedInDelivery.execute
    interrupted = []

    def lose_response(adapter, work, *, may_act):
        result = execute(adapter, work, may_act=may_act)
        if work["action"] == "submit_resume" and may_act and not interrupted:
            interrupted.append(work["work_id"])
            if late:
                adapter.page.evaluate(
                    "let d=JSON.parse(localStorage.jobs);d['123'].sent=false;localStorage.jobs=JSON.stringify(d)"
                )
            raise TimeoutError("Synthetic lost reply after the single submit")
        return result

    monkeypatch.setattr(LinkedInDelivery, "execute", lose_response)
    stopped = playwright_work.run(first["work_id"], max_steps=10)
    assert stopped["requires_technical_recovery"]
    assert stored_jobs(flow)["123"]["clicks"] == 1
    if late:
        waiting = playwright_work.run(interrupted[0])
        assert waiting["reconciliation_only"]
        assert stored_jobs(flow)["123"]["clicks"] == 1
        with PlaywrightRuntime(flow.session) as runtime:
            page = next(p for p in runtime.context.pages if "/jobs/view/" in p.url)
            page.evaluate(
                "let d=JSON.parse(localStorage.jobs);d['123'].sent=true;localStorage.jobs=JSON.stringify(d)"
            )
    complete = playwright_work.run(interrupted[0], max_steps=10)
    assert complete["summary"]["resume_submitted"] == 2
    assert [j["clicks"] for j in stored_jobs(flow).values()] == [1, 1]


@pytest.mark.parametrize("external", [False, True])
def test_existing_draft_uses_only_the_observed_same_job_native_link(flow, external):
    from jobagent.platforms.linkedin.playwright_delivery import LinkedInDelivery

    url = (
        "https://employer.test/apply"
        if external
        else "https://www.linkedin.com/jobs/view/123/?draft=true"
    )
    flow.world["html"] = (
        '''<html lang=zh><main><p>Fixture Company</p><h1>Engineer 123</h1>
        <p>您上一次修改此申请资料的时间: 13 小时前</p>
        <a href="'''
        + url
        + """" onclick="document.querySelector('#form').hidden=false;return false">继续</a>
        <h2>关于职位</h2></main><div id=form role=dialog hidden><label>姓名<input></label><button>下一步</button></div></html>"""
    )
    with PlaywrightRuntime(flow.session) as runtime:
        page = runtime.context.new_page()
        page.goto("https://www.linkedin.com/jobs/view/123/")
        adapter = LinkedInDelivery(runtime)
        adapter.page = page
        work = {
            "task": {
                "job": {
                    "id": "123",
                    "title": "Engineer 123",
                    "company": "Fixture Company",
                }
            }
        }
        if external:
            with pytest.raises(browser_work.BrowserWorkError):
                adapter._open_form(work)
            assert not page.locator("#form").is_visible()
        else:
            assert adapter._open_form(work)["action"] == "next"


def test_changed_round_intent_blocks_queued_preparation_before_browser_connect(
    flow, monkeypatch
):
    work = review.review_decision("linkedin", input_path=str(flow.source))["work"]
    active = rounds.ensure_current_round()
    active["intent"]["target_roles"] = ["Changed direction"]
    rounds.save_round(active)
    monkeypatch.setattr(
        PlaywrightRuntime,
        "__enter__",
        lambda _: pytest.fail("Stale work opened a browser"),
    )
    with pytest.raises(browser_work.BrowserWorkError, match="intention or resume"):
        playwright_work.run(work["work_id"])


@pytest.mark.parametrize("crash", [False, True])
def test_changed_attachment_reprepares_same_batch_and_requires_new_confirmation(flow, monkeypatch, crash):
    from jobagent.application.delivery import _load_reviewed
    from jobagent.infra.delivery_preview import DeliveryPreviewError

    original, _ = preview(flow)
    work = authorize(flow, original)["work"]
    old_source = copy.deepcopy(rounds.ensure_current_round()["platforms"]["linkedin"]["native_delivery"])
    flow.files["English.pdf"] += b"replacement bytes"
    if crash:
        cancel = browser_work.cancel_unexecuted

        def crash_after_cancel(*args, **kwargs):
            cancel(*args, **kwargs)
            raise RuntimeError("Synthetic crash after ledger cancellation")

        monkeypatch.setattr(browser_work, "cancel_unexecuted", crash_after_cancel)
        with pytest.raises(RuntimeError, match="Synthetic crash"):
            playwright_work.run(work["work_id"])
        monkeypatch.setattr(browser_work, "cancel_unexecuted", cancel)
        response = native_work.next_work()
    else:
        response = playwright_work.run(work["work_id"])
    with pytest.raises(DeliveryPreviewError):
        _load_reviewed("linkedin", old_source["input_path"], preview_id=old_source["preview_id"],
                       authorization_id=old_source["authorization_id"])
    cards = []
    for _ in range(20):
        assert not response.get("requires_technical_recovery"), response
        if response.get("event") == "linkedin_application_facts":
            cards.extend(response["interaction"]["fields"])
            response = answer(flow, response)
        elif response.get("work"):
            response = playwright_work.run(response["work"]["work_id"], max_steps=30)
        else:
            break
    assert response["event"] == "delivery_preview", response
    assert response["delivery_preview"]["preview_id"] != original["delivery_preview"]["preview_id"]
    assert any("Resume" in f["label"] for f in cards)
    assert not any(f["label"] in {"Email address", "Phone number"} for f in cards)
    assert all(job["clicks"] == 0 for job in stored_jobs(flow).values())
    next_work = authorize(flow, response)["work"]
    completed = playwright_work.run(next_work["work_id"], max_steps=12)
    # The replacement affects both jobs: the second also needs its own new
    # preview rather than inheriting the first job's refreshed attachment.
    for _ in range(20):
        assert not completed.get("requires_technical_recovery"), completed
        if completed.get("event") == "linkedin_application_facts":
            completed = answer(flow, completed)
        elif completed.get("work"):
            completed = playwright_work.run(completed["work"]["work_id"], max_steps=30)
        else:
            break
    assert completed["event"] == "delivery_preview"
    last = authorize(flow, completed)["work"]
    result = playwright_work.run(last["work_id"], max_steps=12)
    assert [job["clicks"] for job in stored_jobs(flow).values()] == [1, 1]
    assert result["summary"]["resume_submitted"] == 2
