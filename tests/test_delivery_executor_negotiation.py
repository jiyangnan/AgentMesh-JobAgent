"""Freeze the requested transport while preserving old pending requests."""
import pytest

from jobagent.application import discover
from jobagent.infra import cloud_client, discovery_state, protocol
from tests.test_cli_v3 import _future, _key_pair, _sign
from tests.test_hybrid_pipeline import plan


@pytest.mark.parametrize("old", [False, True])
def test_pending_executor_is_frozen_before_network_and_old_files_are_unchanged(monkeypatch, tmp_path, old):
    monkeypatch.setattr(discovery_state, "discoveries_dir", lambda: tmp_path)
    context = {"platform": "boss", "round_id": "round-fixture", "profile_digest": "profile-fixture",
               "intent_digest": None, "account_ref": "account-fixture", "delivery_executor": "playwright"}
    if old:
        discovery_state.save_pending_start("boss", request_id="boss:old-request",
            **{k: v for k, v in context.items() if k not in {"platform", "delivery_executor"}})
    first = discover._preserved_request_id(context)
    path = discovery_state.pending_start_path("boss")
    before = path.read_bytes()
    assert discover._preserved_request_id({**context, "delivery_executor": "codex_native"}) == first
    assert path.read_bytes() == before
    saved = discovery_state.load_pending_start("boss")
    assert saved.get("delivery_executor") == (None if old else "playwright")


@pytest.mark.parametrize("executor", [None, "codex_native", "playwright"])
def test_cloud_start_only_sends_explicit_executor(monkeypatch, executor):
    bodies = []
    monkeypatch.setattr(cloud_client, "_workflow_price_stage", lambda *args: None)
    def request(method, path, body, **kwargs):
        bodies.append(body)
        return {"discovery_contract_version": 2, "delivery_executor": executor or "codex_native"}
    monkeypatch.setattr(cloud_client, "_request", request)
    cloud_client.discovery_start(platform="boss", profile={}, request_id="executor-fixture",
                                 discovery_contract_version=2, delivery_executor=executor)
    assert bodies[0].get("delivery_executor") == executor
    assert ("delivery_executor" in bodies[0]) is (executor is not None)


def test_server_executor_mismatch_stops_before_collection(monkeypatch):
    monkeypatch.setattr(cloud_client, "_workflow_price_stage", lambda *args: None)
    monkeypatch.setattr(cloud_client, "_request", lambda *a, **k: {
        "discovery_contract_version": 2, "delivery_executor": "codex_native"})
    with pytest.raises(cloud_client.CloudError) as caught:
        cloud_client.discovery_start(platform="boss", profile={}, request_id="executor-fixture",
            discovery_contract_version=2, delivery_executor="playwright")
    assert caught.value.code == "delivery_executor_unavailable"
    assert caught.value.details == {"request_preserved": True, "no_charge": True}


@pytest.mark.parametrize("executor", ["codex_native", "playwright", "unknown", None, {}, []])
def test_only_supported_signed_executors_are_accepted(monkeypatch, executor):
    private, public = _key_pair()
    monkeypatch.setattr(protocol, "DECISION_SIGNING_PUBLIC_KEY", public)
    profile = {"preferences": {"targetRoles": [{"title": "产品经理"}]}}
    signed = _sign(private, {**plan(count=30), "manifest_type": "search_plan",
        "protocol_version": 1, "profile_digest": protocol.digest_payload(profile),
        "expires_at": _future(), "account_ref": "acct-fixture",
        "collection_executor": "cdp_readonly", "delivery_executor": executor})
    if executor in ("codex_native", "playwright"):
        assert protocol.verify_search_plan(signed, platform="boss", profile=profile)["delivery_executor"] == executor
    else:
        with pytest.raises(protocol.ProtocolError, match="bulk controls invalid"):
            protocol.verify_search_plan(signed, platform="boss", profile=profile)
