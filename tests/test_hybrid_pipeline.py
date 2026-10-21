import copy
from types import SimpleNamespace

import pytest

from jobagent.application import bulk_decision
from jobagent.domain.models import Job
from jobagent.drivers.collection_reader import CollectionReader
from jobagent.infra import state
from jobagent.platforms import discovery, detail_reader


def plan(platform="boss", count=100):
    return {"platform": platform, "discover_id": "dis_test", "discovery_contract_version": 2,
            "candidate_limit": count, "page_budget": 200, "collection_time_budget_seconds": 1800,
            "queries": [{"keyword": "产品经理", "city": "上海", "page_limit": 100}]}


def job(i, platform="boss"):
    return Job(name="产品经理", company="Fixture", city="上海", salary="20-30K", platform=platform,
               url=f"https://www.zhipin.com/job_detail/{i}.html", raw_data={"encryptJobId": str(i), "jobId": str(i)})


@pytest.mark.parametrize("platform", ["boss", "liepin", "zhilian", "51job"])
@pytest.mark.parametrize("count", [100, 200, 500, 1000])
def test_collection_full_scale_without_native_per_job_reads(monkeypatch, platform, count):
    calls = []
    def collect(query, page, remaining, *args):
        calls.append(page)
        return [job(i, platform) for i in range((page - 1) * 15, min(page * 15, count))]
    monkeypatch.setattr(discovery, "_collect_boss", collect)
    monkeypatch.setattr(discovery, "_collect_web_platform", lambda p, *args: discovery.CollectedPage(collect(*args)))
    result = discovery.collect_from_search_plan(plan(platform, count), driver=object(), page_delay=0)
    assert len(result) == count
    assert len(calls) == (count + 14) // 15


def test_repeated_page_preserves_last_safe_page_and_does_not_skip_it(monkeypatch):
    snapshots = []
    monkeypatch.setattr(discovery, "_collect_boss", lambda *a: [job(1), job(2)])
    with pytest.raises(discovery.CollectionError) as error:
        discovery.collect_from_search_plan(plan(), driver=object(), page_delay=0,
            checkpoint_callback=lambda p: snapshots.append(copy.deepcopy(p)))
    assert error.value.code == "collection_page_not_advancing"
    assert snapshots[-1]["completed_pages"] == [[0, 1]]
    assert len(snapshots[-1]["candidates"]) == 2
    with pytest.raises(discovery.CollectionError, match="earlier page"):
        discovery.collect_from_search_plan(plan(), driver=object(), page_delay=0, resume_progress=snapshots[-1])


def test_budget_does_not_silently_finalize_partial_pool(monkeypatch):
    p = plan()
    p["page_budget"] = 1
    snapshots = []
    monkeypatch.setattr(discovery, "_collect_boss", lambda *a: [job(1)])
    with pytest.raises(discovery.CollectionError) as error:
        discovery.collect_from_search_plan(p, driver=object(), page_delay=0, checkpoint_callback=snapshots.append)
    assert error.value.code == "collection_budget_reached"
    assert error.value.details["partial_result_command"].endswith("--accept-partial")
    assert len(snapshots[-1]["candidates"]) == 1


def test_upload_ack_loss_retries_identical_block_and_preserves_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    saved, calls = {}, []
    lose = True
    def request(method, identifier, suffix, body):
        nonlocal lose
        calls.append(copy.deepcopy(body))
        key = suffix.rsplit("/", 1)[1]
        if key in saved:
            assert saved[key] == body
        saved[key] = body
        if lose:
            lose = False
            raise ConnectionError("response lost after server persisted")
        return {"batch_id": key, "digest": body["digest"]}
    monkeypatch.setattr(bulk_decision, "request", request)
    candidates = [{"id": str(i), "title": "Fixture"} for i in range(31)]
    with pytest.raises(ConnectionError):
        bulk_decision.upload_candidates(plan(), candidates)
    bulk_decision.upload_candidates(plan(), candidates)
    ids = bulk_decision.upload_candidates(plan(), candidates, final=True)
    assert len(ids) == 3 and len(saved) == 3
    assert calls[0] == calls[1]
    assert len(calls) == 4
    changed = copy.deepcopy(candidates)
    changed[0]["title"] = "changed"
    with pytest.raises(ValueError, match="prefix changed"):
        bulk_decision.upload_candidates(plan(), changed, final=True)


def test_collection_reader_has_no_delivery_methods(tmp_path):
    reader = CollectionReader(driver=SimpleNamespace(manager=SimpleNamespace(user_data_dir=tmp_path / 'chrome')))
    for name in ("send_message", "submit_resume", "greet", "apply", "click_communicate"):
        with pytest.raises(AttributeError):
            getattr(reader, name)
    for url in ("http://www.zhipin.com/wapi/zpgeek/search/joblist.json", "https://www.zhipin.com/wapi/zpgeek/friend/add.json"):
        with pytest.raises(ValueError):
            reader.api_fetch(url)
    with pytest.raises(ValueError):
        reader.api_fetch("https://www.zhipin.com/wapi/zpgeek/search/joblist.json", method="POST")


def test_port_conflict_never_kills_or_relaunches_chrome(monkeypatch):
    from jobagent.drivers.boss import chrome_manager as manager
    instance = manager.ChromeInstanceManager(port=19222)
    monkeypatch.setattr(instance, "_try_connect_existing", lambda: (False, None))
    monkeypatch.setattr(manager, "_is_port_open", lambda _: True)
    monkeypatch.setattr(manager.subprocess, "Popen", lambda *a, **k: pytest.fail("restarted Chrome"))
    monkeypatch.setattr(manager.subprocess, "run", lambda *a, **k: pytest.fail("terminated a process"))
    with pytest.raises(RuntimeError, match="cdp_port_unavailable"):
        instance.ensure_running()


@pytest.mark.parametrize("outcome", ["restricted", "login"])
def test_details_pause_without_converting_challenge_to_missing_jd(outcome):
    reader = SimpleNamespace(open_url_in_new_tab=lambda *a, **k: {},
        _exec_js=lambda _: {"url": "https://www.zhipin.com/job_detail/1.html", outcome: True})
    with pytest.raises(discovery.CollectionError) as error:
        detail_reader.read_detail(reader, "boss", {"id": "1", "url": "https://www.zhipin.com/job_detail/1.html"})
    assert error.value.code == "detail_user_action_required"


def test_details_never_follow_redirect_to_other_job():
    reader = SimpleNamespace(open_url_in_new_tab=lambda *a, **k: {},
        _exec_js=lambda _: {"url": "https://www.zhipin.com/job_detail/2.html", "jd": "actual content"})
    with pytest.raises(discovery.CollectionError) as error:
        detail_reader.read_detail(reader, "boss", {"id": "1", "url": "https://www.zhipin.com/job_detail/1.html"})
    assert error.value.code == "detail_identity_mismatch"


def test_boss_uses_stable_upstream_page_size_near_target(monkeypatch):
    from jobagent.platforms.boss.collect import BossDataDriver
    sizes = []
    monkeypatch.setattr(BossDataDriver, 'fetch_jobs', lambda self, *a, **kw: sizes.append(kw['page_size']) or [])
    discovery._collect_boss({'keyword': '产品经理', 'city': '深圳'}, 7, 1, SimpleNamespace(bulk_readonly=True), 0)
    assert sizes == [15]


def test_query_page_limit_preserves_partial_instead_of_charging(monkeypatch):
    p = plan(); p['queries'][0]['page_limit'] = 1
    monkeypatch.setattr(discovery, '_collect_boss', lambda *a: [job(1)])
    with pytest.raises(discovery.CollectionError) as error:
        discovery.collect_from_search_plan(p, driver=object(), page_delay=0)
    assert error.value.code == 'collection_query_budget_reached'


@pytest.mark.parametrize('platform,url', [
    ('zhilian', 'https://jobs.zhaopin.com/CC123.htm'),
    ('51job', 'https://we.51job.com/pc/jobdetail?jobId=123'),
    ('51job', 'https://jobs.51job.com/shenzhen/123.html'),
])
def test_official_detail_url_variants(platform, url):
    assert detail_reader.detail_url_allowed(platform, url)
    assert not detail_reader.detail_url_allowed(platform, 'https://example.test/redirect?next=' + url)


def test_query_encoded_job_identity_must_not_change():
    reader = SimpleNamespace(open_url_in_new_tab=lambda *a, **kw: {'ok': True},
        _exec_js=lambda _: {'url': 'https://we.51job.com/pc/jobdetail?jobId=wrong', 'jd': 'Some detail'})
    with pytest.raises(discovery.CollectionError) as err:
        detail_reader.read_detail(reader, '51job', {'id': '123', 'url': 'https://we.51job.com/pc/jobdetail?jobId=123'})
    assert err.value.code == 'detail_identity_mismatch'


def test_background_boss_detail_never_navigates_or_renders(tmp_path):
    calls = []
    def evaluate(expression, **kwargs):
        calls.append((expression, kwargs))
        return {'result': {'value': {'code':0, 'zpData': {'jobInfo': {'encryptId':'1', 'postDescription':'负责AI产品，要求5年经验'}}}}}
    driver = SimpleNamespace(manager=SimpleNamespace(user_data_dir=tmp_path / 'chrome'), cdp=SimpleNamespace(evaluate=evaluate),
        open_url_in_new_tab=lambda *a, **k: pytest.fail('navigated'),
        _ensure_connected_for_url=lambda *a: pytest.fail('navigation helper'),
        _exec_js=lambda *a: pytest.fail('rendered DOM'))
    result = detail_reader.read_detail(CollectionReader(driver=driver), 'boss',
        {'id':'1','url':'https://www.zhipin.com/job_detail/1.html','security_id':'fixture'})
    assert result['status'] == 'available' and '要求5年经验' in result['jd']
    assert len(calls) == 1 and calls[0][1]['await_promise'] is True
    assert 'Page.navigate' not in calls[0][0] and 'document.cookie' not in calls[0][0]
    assert '49657' not in calls[0][0] and '86344' not in calls[0][0]


def test_background_html_is_parsed_without_running_scripts_or_loading_images():
    reader=SimpleNamespace(read_detail_document=lambda url:{'url':url,'html':'<body><div class="job-description">负责AI产品<br>要求5年经验<script>do_not_execute()</script></div><img src="/large-image"></body>'})
    result=detail_reader.read_detail(reader,'liepin',{'id':'1','url':'https://www.liepin.com/job/1.shtml'})
    assert '负责AI产品' in result['jd'] and '要求5年经验' in result['jd']
    assert 'do_not_execute' not in result['jd'] and 'large-image' not in result['jd']


@pytest.mark.parametrize('response,code', [
    ({'error': 'detail_user_action_required'}, 'detail_user_action_required'),
    ({'error': 'detail_transport_failed'}, 'detail_transport_failed'),
    ({'error': 'detail_origin_unavailable'}, 'detail_origin_unavailable'),
    ({'url': 'https://www.zhipin.com/job_detail/1.html', 'html': '<body>请完成验证</body>'}, 'detail_user_action_required'),
    ({'url': 'https://www.zhipin.com/job_detail/2.html', 'html': '<div class="job-sec-text">wrong job</div>'}, 'detail_identity_mismatch'),
])
def test_background_detail_failures_never_fall_back_to_navigation(response, code):
    reader = SimpleNamespace(read_detail_document=lambda _: response,
        open_url_in_new_tab=lambda *a, **k: pytest.fail('fell back to visible navigation'))
    with pytest.raises(discovery.CollectionError) as error:
        detail_reader.read_detail(reader, 'boss', {'id': '1', 'url': 'https://www.zhipin.com/job_detail/1.html'})
    assert error.value.code == code


def test_background_js_only_detail_is_explicitly_unavailable():
    reader = SimpleNamespace(read_detail_document=lambda url: {'url': url, 'html': '<body><script>loadEverything()</script></body>'},
        open_url_in_new_tab=lambda *a, **k: pytest.fail('fell back to visible navigation'))
    result = detail_reader.read_detail(reader, 'boss', {'id': '1', 'url': 'https://www.zhipin.com/job_detail/1.html'})
    assert result['status'] == 'unavailable' and result['jd'] == ''


def test_receipt_sync_ack_loss_replays_same_receipt_without_delivery(tmp_path, monkeypatch):
    from jobagent.application import delivery_receipts as outbox
    from jobagent.infra.protocol import digest_payload
    from jobagent.infra.cloud_client import CloudError
    monkeypatch.setattr(state, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(outbox, 'current_account_ref', lambda: 'acct_fixture')
    path = tmp_path / 'delivery-receipts' / (digest_payload('acct_fixture')[7:] + '.json')
    state.save_json(path, {'account_ref':'acct_fixture', 'receipts': {'receipt_fixture': {'payload': {'job_id':'1'}, 'synced':False}}})
    calls = []
    def request(method, url, body, **kwargs):
        calls.append((method, url, body))
        if len(calls) == 1:
            raise CloudError('lost ack', code='cloud_network_error')
        return {'receipt_id':'receipt_fixture'}
    monkeypatch.setattr(outbox.cloud_client, '_request', request)
    assert outbox.flush_pending() == {'synced':0, 'pending':1}
    assert outbox.flush_pending() == {'synced':1, 'pending':0}
    assert outbox.flush_pending() == {'synced':1, 'pending':0}
    assert len(calls) == 2 and calls[0] == calls[1]


@pytest.mark.parametrize('code', [36, 37, 7])
def test_boss_detail_rejection_preserves_request_without_visible_fallback(code, tmp_path):
    driver=SimpleNamespace(manager=SimpleNamespace(user_data_dir=tmp_path / 'chrome'),cdp=SimpleNamespace(evaluate=lambda *a,**k:{'result':{'value':{'code':code}}}))
    reader=CollectionReader(driver=driver)
    with pytest.raises(discovery.CollectionError) as error:
        detail_reader.read_detail(reader,'boss',{'id':'1','url':'https://www.zhipin.com/job_detail/1.html','security_id':'fixture'})
    assert error.value.code=='boss_detail_request_rejected'
    assert error.value.details['request_preserved'] and not error.value.details['automatic_retry_allowed']


def test_required_jd_pause_keeps_successful_reads_and_retries_only_missing(tmp_path,monkeypatch):
    from contextlib import nullcontext
    from jobagent.application import hybrid_discovery
    from jobagent.infra.protocol import candidate_digest
    monkeypatch.setattr(state,'STATE_DIR',tmp_path)
    jobs=[{'id':str(i),'url':f'https://www.zhipin.com/job_detail/{i}.html','security_id':'fixture'} for i in range(3)]
    task={'discover_id':'dis_test','platform':'boss','account_ref':'fixture','candidate_digest':candidate_digest(jobs),'collection_executor':'cdp_readonly','items':[{'id':j['id'],'url':j['url']} for j in jobs]}
    p={**plan(),'account_ref':'fixture'}
    monkeypatch.setattr(bulk_decision,'verify_signed_payload',lambda value,**kw:value)
    uploaded=[]
    monkeypatch.setattr(bulk_decision,'request',lambda method,*args:task if method=='GET' else uploaded.append(method))
    monkeypatch.setattr(hybrid_discovery,'collection_reader',lambda *a:nullcontext(object()))
    state.save_json(bulk_decision._checkpoint_path('dis_test'),{'uploads':{},'details':{}})
    reads=[];missing=True
    def read(reader,platform,item):
        reads.append(item['id'])
        ok=not (missing and item['id']=='1')
        return {**{k:item[k] for k in ('id','url')},'status':'available' if ok else 'unavailable','jd':'full JD' if ok else ''}
    monkeypatch.setattr(detail_reader,'read_detail',read)
    with pytest.raises(discovery.CollectionError) as error:bulk_decision._enrich('boss',p,jobs)
    assert error.value.code=='detail_evidence_required' and not error.value.details['retryable']
    assert reads==['0','1'] and 'POST' not in uploaded
    missing=False
    bulk_decision._enrich('boss',p,jobs)
    assert reads==['0','1','1','2'] and uploaded.count('POST')==1


def test_bulk_listing_failure_never_uses_visible_page_fallback(monkeypatch):
    from jobagent.platforms.boss.collect import BossDataDriver
    driver=SimpleNamespace(bulk_readonly=True,api_fetch=lambda *a:(_ for _ in ()).throw(RuntimeError('fixture')))
    collector=BossDataDriver(driver=driver)
    monkeypatch.setattr(collector,'_fetch_visible_page',lambda *a:pytest.fail('foreground fallback'))
    with pytest.raises(RuntimeError,match='fixture'):collector._fetch_page('AI','101280600')


@pytest.mark.parametrize('count', [100, 200, 500, 1000])
def test_detail_batches_cover_entire_signed_task_and_survive_resume(tmp_path, monkeypatch, count):
    from itertools import cycle
    choices = cycle([7, 11, 13])
    monkeypatch.setattr(bulk_decision.random, 'choice', lambda _: next(choices))
    items = [{'id': str(i)} for i in range(count)]
    path = tmp_path / 'details.json'
    saved = {'details': {}}
    batches = bulk_decision._detail_batches(path, saved, items)
    assert [i for batch in batches for i in batch['ids']] == [str(i) for i in range(count)]
    assert {batch['target_size'] for batch in batches} == {7, 11, 13}
    assert all(len(batch['ids']) == batch['target_size'] for batch in batches[:-1])
    monkeypatch.setattr(bulk_decision.random, 'choice', lambda _: pytest.fail('redrew preserved batches'))
    assert bulk_decision._detail_batches(path, state.load_json(path), items) == batches
    with pytest.raises(ValueError, match='signed task'):
        bulk_decision._detail_batches(path, state.load_json(path), list(reversed(items)))


def test_detail_batch_upload_lost_ack_reuses_reads_and_exact_batch(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from jobagent.application import hybrid_discovery
    from jobagent.infra.protocol import candidate_digest
    monkeypatch.setattr(state, 'STATE_DIR', tmp_path)
    choices = iter([7, 11, 13])
    monkeypatch.setattr(bulk_decision.random, 'choice', lambda _: next(choices))
    jobs = [{'id': str(i), 'url': f'https://www.zhipin.com/job_detail/{i}.html'} for i in range(31)]
    task = {'discover_id': 'dis_test', 'platform': 'boss', 'account_ref': 'fixture',
            'candidate_digest': candidate_digest(jobs), 'collection_executor': 'cdp_readonly', 'items': jobs}
    monkeypatch.setattr(bulk_decision, 'verify_signed_payload', lambda value, **kw: value)
    monkeypatch.setattr(hybrid_discovery, 'collection_reader', lambda _: nullcontext(object()))
    reads, uploads = [], []
    def read(reader, platform, item):
        reads.append(item['id'])
        return {**item, 'status': 'available', 'jd': 'Full description ' + item['id']}
    monkeypatch.setattr(detail_reader, 'read_detail', read)
    def request(method, discover_id, suffix, body=None):
        if method == 'GET': return task
        if method == 'PUT':
            uploads.append((suffix, copy.deepcopy(body)))
            if len(uploads) == 1: raise ConnectionError('server saved detail batch; response lost')
        return {'ok': True}
    monkeypatch.setattr(bulk_decision, 'request', request)
    path = bulk_decision._checkpoint_path('dis_test')
    state.save_json(path, {'details': {}, 'uploads': {}})
    with pytest.raises(ConnectionError):
        bulk_decision._enrich('boss', {**plan(), 'account_ref': 'fixture'}, jobs)
    assert reads == [str(i) for i in range(7)]
    assert len(state.load_json(path)['details']) == 7
    monkeypatch.setattr(bulk_decision.random, 'choice', lambda _: pytest.fail('redrew batch after lost ack'))
    bulk_decision._enrich('boss', {**plan(), 'account_ref': 'fixture'}, jobs)
    assert reads == [str(i) for i in range(31)]
    assert uploads[0] == uploads[1]
    assert [len(body['results']) for _, body in uploads] == [7, 7, 11, 13]
    assert len(state.load_json(path)['detail_uploads']) == 3
    monkeypatch.setattr(hybrid_discovery, 'collection_reader', lambda _: pytest.fail('opened browser for completed reads'))
    bulk_decision._enrich('boss', {**plan(), 'account_ref': 'fixture'}, jobs)
    assert len(uploads) == 4 and len(reads) == 31
