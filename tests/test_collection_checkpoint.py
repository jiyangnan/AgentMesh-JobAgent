from __future__ import annotations

import base64
import copy
import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jobagent.domain.models import Job
from jobagent.infra import discovery_state as storage
from jobagent.infra import protocol
from jobagent.platforms import discovery


def plan():
    return {
        "platform": "liepin", "discover_id": "dis-test", "candidate_limit": 100, "request_id": "liepin:request",
        "queries": [
            {"keyword": "产品经理", "city": "郑州", "page_limit": 2},
            {"keyword": "数据分析师", "city": "杭州", "page_limit": 2},
        ],
    }


def job(identifier):
    return Job(name="产品经理", company="示例公司", city="郑州", salary="10-20K", platform="liepin",
               url=f"https://www.liepin.com/job/{identifier}.shtml",
               raw_data={"jobId": identifier})


@pytest.mark.parametrize("failure", [discovery.CollectionError("liepin_verification_required", "Verify"), KeyboardInterrupt()])
def test_resume_skips_completed_pages_and_deduplicates(monkeypatch, failure):
    calls, checkpoints = [], []
    def collect(platform, query, page, *args):
        calls.append((query["keyword"], page))
        if len(calls) == 3:
            raise failure
        return discovery.CollectedPage([job("shared"), job(f"{query['keyword']}-{page}")])
    monkeypatch.setattr(discovery, "_collect_web_platform", collect)
    with pytest.raises(type(failure)):
        discovery.collect_from_search_plan(plan(), driver=object(), page_delay=0,
                                          checkpoint_callback=lambda value: checkpoints.append(copy.deepcopy(value)))
    assert checkpoints[-1]["completed_pages"] == [[0, 1], [1, 1]]
    result = discovery.collect_from_search_plan(plan(), driver=object(), page_delay=0,
                                               resume_progress=checkpoints[-1])
    assert calls == [("产品经理", 1), ("数据分析师", 1), ("产品经理", 2), ("产品经理", 2), ("数据分析师", 2)]
    assert len(result) == 5
    assert len({item["id"] for item in result}) == 5


def test_exhausted_query_stays_retired_across_resume(monkeypatch):
    calls, checkpoints = [], []
    def collect(platform, query, page, *args):
        calls.append((query["keyword"], page))
        if len(calls) == 2:
            raise discovery.CollectionError("liepin_verification_required", "Verify")
        return discovery.CollectedPage([] if query["keyword"] == "产品经理" else [job("one")], exhausted=True)
    monkeypatch.setattr(discovery, "_collect_web_platform", collect)
    with pytest.raises(discovery.CollectionError):
        discovery.collect_from_search_plan(plan(), driver=object(), page_delay=0,
                                          checkpoint_callback=checkpoints.append)
    result = discovery.collect_from_search_plan(plan(), driver=object(), page_delay=0,
                                               resume_progress=checkpoints[-1])
    assert calls == [("产品经理", 1), ("数据分析师", 1), ("数据分析师", 1)]
    assert len(result) == 1


def test_empty_completed_plan_does_not_search_again(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("completed empty query searched again")
    monkeypatch.setattr(discovery, "_collect_web_platform", unexpected)
    with pytest.raises(discovery.CollectionError) as error:
        discovery.collect_from_search_plan(plan(), driver=object(), page_delay=0,
            resume_progress={"candidates": [], "completed_pages": [[0, 1], [1, 1]], "exhausted_queries": [0, 1]})
    assert error.value.code == "no_candidates"
    assert error.value.details["search_exhausted"] is True


@pytest.mark.parametrize("progress", [
    {}, {"candidates": [], "completed_pages": [[0, 2]], "exhausted_queries": []},
    {"candidates": [], "completed_pages": [[0, 3]], "exhausted_queries": []},
    {"candidates": [], "completed_pages": [], "exhausted_queries": [0]},
    {"candidates": [{"id": "a"}, {"id": "a"}], "completed_pages": [], "exhausted_queries": []},
])
def test_corrupt_cursor_stops_before_browser_creation(monkeypatch, progress):
    monkeypatch.setattr("jobagent.drivers.boss.create_driver", lambda **kwargs: pytest.fail("browser created"))
    with pytest.raises(discovery.CollectionError, match="invalid"):
        discovery.collect_from_search_plan(plan(), resume_progress=progress)


def seed_start():
    return storage.save_pending_start("liepin", request_id="liepin:request", round_id="round-test",
                                      profile_digest="profile", intent_digest=None, account_ref="account-test")


def test_previous_version_request_migrates_lazily_and_idempotently(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "discoveries_dir", lambda: tmp_path)
    path = seed_start()
    before = path.read_bytes()
    assert storage.load_collection_checkpoint("liepin") is None
    assert path.read_bytes() == before
    progress = {"candidates": [{"id": "one"}], "completed_pages": [[0, 1]], "exhausted_queries": []}
    storage.save_collection_checkpoint("liepin", request_id="liepin:request", plan=plan(), progress=progress)
    updated = path.read_bytes()
    assert json.loads(updated)["schema_version"] == 2
    storage.save_collection_checkpoint("liepin", request_id="liepin:request", plan=plan(), progress=progress)
    assert path.read_bytes() == updated
    with pytest.raises(ValueError, match="mismatch"):
        storage.save_collection_checkpoint("liepin", request_id="liepin:other", plan=plan(), progress=progress)
    assert path.read_bytes() == updated
    saved = json.loads(updated)
    saved["collection"]["progress"]["candidates"][0]["id"] = "changed"
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="Invalid"):
        storage.load_collection_checkpoint("liepin")


@pytest.fixture
def app_context(tmp_path, monkeypatch):
    from jobagent.application import discover as app
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", base64.urlsafe_b64encode(public).decode().rstrip("="))
    profile = {"schema_version": 1}
    intent = {"status": "confirmed", "target_roles": ["产品经理"], "target_cities": ["郑州", "杭州"]}
    monkeypatch.setattr(storage, "discoveries_dir", lambda: tmp_path / "discoveries")
    monkeypatch.setattr(app, "load_json", lambda path: profile)
    monkeypatch.setattr(app, "profile_path", lambda: tmp_path / "profile.json")
    monkeypatch.setattr(app, "require_compatible_profile", lambda value: None)
    monkeypatch.setattr(app.rounds, "ensure_current_round", lambda: {"round_id": "round-test", "intent": intent})
    monkeypatch.setattr(app.rounds, "recent_platform_login_verification", lambda platform: None)
    monkeypatch.setattr("jobagent.infra.account_state.current_account_ref", lambda: "account-test")
    monkeypatch.setattr(app, "active_command", lambda *args: nullcontext())
    monkeypatch.setattr(app, "PlatformSessionLock", lambda **kwargs: nullcontext())
    monkeypatch.setattr("jobagent.drivers.boss.create_driver", lambda **kwargs: object())
    def sign(payload):
        payload = dict(payload, signature_algorithm="Ed25519", key_id="test")
        payload["signature"] = base64.urlsafe_b64encode(private.sign(protocol.canonical_json_bytes(payload))).decode().rstrip("=")
        return payload
    def signed_plan(request_id, expired=False):
        return sign(dict(plan(), request_id=request_id, protocol_version=1, manifest_type="search_plan",
            profile_digest=protocol.digest_payload(profile), intent_digest=protocol.digest_payload(intent), round_intent=intent,
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=-1 if expired else 1)).isoformat()))
    return app, signed_plan, sign


def test_orchestration_preserves_same_signed_request_and_partial_candidates(app_context, monkeypatch):
    app, signed_plan, _ = app_context
    starts, decisions, pages = [], [], []
    def start(**kwargs):
        starts.append(kwargs["request_id"])
        return signed_plan(kwargs["request_id"])
    def collect(platform, query, page, *args):
        pages.append((query["keyword"], page))
        if len(pages) == 2:
            raise discovery.CollectionError("liepin_verification_required", "Verify", user_prompt="请完成验证。")
        return discovery.CollectedPage([job(query["keyword"])], exhausted=True)
    monkeypatch.setattr(app.cloud_client, "discovery_start", start)
    monkeypatch.setattr(discovery, "_collect_web_platform", collect)
    monkeypatch.setattr(app, "_decision_result", lambda platform, **kwargs: decisions.append(kwargs) or {"ok": True})
    with pytest.raises(discovery.CollectionError) as error:
        app.run_discover("liepin", page_delay=0)
    assert error.value.details["retryable"] is False
    assert error.value.details["next_suggested"] == "jobagent liepin discover"
    assert error.value.details["collection_progress_preserved"] is True
    assert error.value.details["completed_page_count"] == 1
    assert error.value.details["billing_status"] == "not_charged"
    assert decisions == []
    assert app.run_discover("liepin", page_delay=0)["ok"] is True
    assert len(starts) == 1
    assert pages == [("产品经理", 1), ("数据分析师", 1), ("数据分析师", 1)]
    assert len(decisions[0]["candidates"]) == 2
    assert decisions[0]["request_id"] == starts[0]
    assert storage.load_pending_start("liepin") is None
    assert len(storage.load_pending_decision("liepin")["jobs"]) == 2


@pytest.mark.parametrize("change_scope", [False, True])
def test_expired_checkpoint_renews_same_plan_without_replaying_completed_pages(app_context, monkeypatch, change_scope):
    app, signed_plan, sign = app_context
    context = app._start_context("liepin", profile=app.load_json(None), active_round=app.rounds.ensure_current_round(),
                                 round_intent=app.rounds.ensure_current_round()["intent"])
    request_id = app._preserved_request_id(context)
    old = signed_plan(request_id, expired=True)
    storage.save_collection_checkpoint("liepin", request_id=request_id, plan=old,
        progress={"candidates": [{"id": "saved", "title": "产品经理"}], "completed_pages": [[0, 1]], "exhausted_queries": [0]})
    def renew(**kwargs):
        new = signed_plan(request_id)
        new.pop("signature")
        new["renewal"] = {"reason": "search_plan_expired", "request_id": request_id, "discover_id": "dis-test",
            "request_preserved": True, "same_request_id": True, "same_discover_id": True, "additional_charge_on_renewal": False}
        if change_scope:
            new["queries"][1]["keyword"] = "另一职位"
        return sign(new)
    monkeypatch.setattr(app.cloud_client, "discovery_renew", renew)
    monkeypatch.setattr(app.cloud_client, "discovery_start", lambda **kwargs: pytest.fail("new start"))
    def collect(platform, query, page, *args):
        assert query["keyword"] == "数据分析师" and page == 1
        return discovery.CollectedPage([job("new")], exhausted=True)
    monkeypatch.setattr(discovery, "_collect_web_platform", collect)
    monkeypatch.setattr(app, "_decision_result", lambda platform, **kwargs: {"ok": True, "candidates": kwargs["candidates"]})
    if change_scope:
        with pytest.raises(discovery.CollectionError) as error:
            app.run_discover("liepin", page_delay=0)
        assert error.value.code == "collection_checkpoint_plan_mismatch"
    else:
        result = app.run_discover("liepin", page_delay=0)
        assert [item["id"] for item in result["candidates"]][0] == "saved"
        assert len(result["candidates"]) == 2


@pytest.mark.parametrize("field", ["account_ref", "profile_digest", "intent_digest"])
def test_context_mismatch_cannot_replace_collected_request(tmp_path, monkeypatch, field):
    from jobagent.application import discover as app
    monkeypatch.setattr(storage, "discoveries_dir", lambda: tmp_path / "discoveries")
    seed_start()
    storage.save_collection_checkpoint("liepin", request_id="liepin:request", plan=plan(),
        progress={"candidates": [], "completed_pages": [[0, 1]], "exhausted_queries": [0]})
    context = {key: storage.load_pending_start("liepin").get(key) for key in
               ("platform", "round_id", "profile_digest", "intent_digest", "account_ref")}
    context[field] = "changed"
    before = storage.pending_start_path("liepin").read_bytes()
    with pytest.raises(discovery.CollectionError) as error:
        app._preserved_request_id(context)
    assert error.value.code == "collection_checkpoint_context_mismatch"
    assert storage.pending_start_path("liepin").read_bytes() == before


def test_valid_signature_without_request_binding_cannot_resume(app_context, monkeypatch):
    app, signed_plan, sign = app_context
    context = app._start_context("liepin", profile=app.load_json(None), active_round=app.rounds.ensure_current_round(),
                                 round_intent=app.rounds.ensure_current_round()["intent"])
    request_id = app._preserved_request_id(context)
    signed = signed_plan(request_id)
    progress = {"candidates": [], "completed_pages": [[0, 1]], "exhausted_queries": [0]}
    storage.save_collection_checkpoint("liepin", request_id=request_id, plan=signed, progress=progress)
    signed.pop("request_id")
    signed.pop("signature")
    signed = sign(signed)
    with pytest.raises(ValueError, match="mismatch"):
        storage.save_collection_checkpoint("liepin", request_id=request_id, plan=signed, progress=progress)
    # Simulate a previous writer that did not enforce signed request binding.
    path = storage.pending_start_path("liepin")
    payload = json.loads(path.read_text())
    payload["collection"]["plan"] = signed
    payload["collection"]["plan_digest"] = storage.collection_plan_digest(signed)
    path.write_text(json.dumps(payload))
    before = path.read_bytes()
    monkeypatch.setattr("jobagent.drivers.boss.create_driver", lambda **kwargs: pytest.fail("browser created"))
    with pytest.raises(protocol.ProtocolError, match="request binding is missing"):
        app.run_discover("liepin", page_delay=0)
    assert path.read_bytes() == before
