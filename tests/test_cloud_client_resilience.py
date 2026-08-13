from __future__ import annotations

import io
import json
import ssl
import urllib.error

import pytest

from jobagent.infra import cloud_client, discovery_state


class _Response:
    def __init__(self, payload: dict):
        self.raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.raw


def _http_error(status: int, detail: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.example.test/v1/discovery/decide",
        status,
        "temporary failure",
        {},
        io.BytesIO(json.dumps({"detail": detail}).encode("utf-8")),
    )


def test_discovery_decide_retries_tls_eof_with_same_request(monkeypatch):
    calls: list[tuple[bytes | None, int]] = []
    sleeps: list[float] = []

    def flaky(request, *, timeout):
        calls.append((request.data, timeout))
        if len(calls) < 3:
            raise urllib.error.URLError(
                ssl.SSLEOFError(8, "UNEXPECTED_EOF_WHILE_READING")
            )
        return _Response({"manifest_type": "decision_manifest"})

    monkeypatch.setattr(cloud_client.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(cloud_client.time, "sleep", sleeps.append)
    monkeypatch.setattr(cloud_client, "load_api_key", lambda: "agentmesh_test_key")
    monkeypatch.setattr(
        cloud_client, "api_base_url", lambda: "https://api.example.test"
    )

    result = cloud_client.discovery_decide(
        discover_id="dis_test",
        jobs=[{"id": "job-1", "title": "AI Product Manager"}],
    )

    assert result == {"manifest_type": "decision_manifest"}
    assert len(calls) == 3
    assert calls[0][0] == calls[1][0] == calls[2][0]
    assert sleeps == [1.0, 3.0]


def test_discovery_decide_retries_gateway_503(monkeypatch):
    attempts = 0

    def flaky(_request, *, timeout):
        nonlocal attempts
        attempts += 1
        assert timeout == 600
        if attempts < 3:
            raise _http_error(503, {"message": "Service Unavailable"})
        return _Response({"ok": True})

    monkeypatch.setattr(cloud_client.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(cloud_client.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(cloud_client, "load_api_key", lambda: "agentmesh_test_key")
    monkeypatch.setattr(
        cloud_client, "api_base_url", lambda: "https://api.example.test"
    )

    assert cloud_client.discovery_decide(discover_id="dis_test", jobs=[]) == {
        "ok": True
    }
    assert attempts == 3


def test_search_plan_renewal_retries_with_same_bound_request(monkeypatch):
    calls: list[dict] = []

    def flaky(request, *, timeout):
        calls.append(json.loads(request.data))
        assert timeout == 60
        if len(calls) < 3:
            raise _http_error(503, {"message": "temporary gateway failure"})
        return _Response({"manifest_type": "search_plan", "discover_id": "dis_stable"})

    monkeypatch.setattr(cloud_client.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(cloud_client.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(cloud_client, "load_api_key", lambda: "agentmesh_test_key")
    monkeypatch.setattr(
        cloud_client, "api_base_url", lambda: "https://api.example.test"
    )

    result = cloud_client.discovery_renew(
        discover_id="dis_stable",
        platform="zhilian",
        profile_digest="sha256:" + "1" * 64,
        intent_digest="sha256:" + "2" * 64,
    )

    assert result["discover_id"] == "dis_stable"
    assert len(calls) == 3
    assert calls[0] == calls[1] == calls[2]


def test_semantic_decision_failure_is_not_retried(monkeypatch):
    attempts = 0

    def fail(_request, *, timeout):
        nonlocal attempts
        attempts += 1
        assert timeout == 600
        raise _http_error(502, {"code": "decision_failed_refunded"})

    monkeypatch.setattr(cloud_client.urllib.request, "urlopen", fail)
    monkeypatch.setattr(
        cloud_client.time, "sleep", lambda _delay: pytest.fail("unexpected retry")
    )
    monkeypatch.setattr(cloud_client, "load_api_key", lambda: "agentmesh_test_key")
    monkeypatch.setattr(
        cloud_client, "api_base_url", lambda: "https://api.example.test"
    )

    with pytest.raises(cloud_client.CloudError) as error:
        cloud_client.discovery_decide(discover_id="dis_test", jobs=[])

    assert attempts == 1
    assert error.value.code == "decision_failed_refunded"
    assert error.value.retryable is False


def test_resume_analyze_does_not_retry_without_request_idempotency(monkeypatch):
    attempts = 0

    def fail(_request, *, timeout):
        nonlocal attempts
        attempts += 1
        assert timeout == 180
        raise urllib.error.URLError(ssl.SSLEOFError(8, "UNEXPECTED_EOF_WHILE_READING"))

    monkeypatch.setattr(cloud_client.urllib.request, "urlopen", fail)
    monkeypatch.setattr(cloud_client, "load_api_key", lambda: "agentmesh_test_key")
    monkeypatch.setattr(
        cloud_client, "api_base_url", lambda: "https://api.example.test"
    )

    with pytest.raises(cloud_client.CloudError) as error:
        cloud_client.resume_analyze("A sufficiently long resume body for this test")

    assert attempts == 1
    assert error.value.retryable is True
    assert error.value.attempts == 1


def test_tls_eof_has_stable_code_and_safe_discovery_diagnostic(monkeypatch):
    api_key = "agentmesh_live_super_secret"

    def fail(_request, *, timeout):
        assert timeout == 60
        raise urllib.error.URLError(ssl.SSLEOFError(8, "UNEXPECTED_EOF_WHILE_READING"))

    monkeypatch.setattr(cloud_client.urllib.request, "urlopen", fail)
    monkeypatch.setattr(cloud_client.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(cloud_client, "load_api_key", lambda: api_key)
    monkeypatch.setattr(
        cloud_client, "api_base_url", lambda: "https://api.example.test"
    )

    with pytest.raises(cloud_client.CloudError) as error:
        cloud_client.discovery_start(
            platform="zhilian",
            profile={"schema_version": 1},
            request_id="zhilian:req-stable",
        )

    assert error.value.code == "tls_connection_eof"
    assert error.value.retryable is True
    assert error.value.attempts == 3
    diagnostic = error.value.details["network_diagnostic"]
    assert diagnostic["operation"] == "discovery_start"
    assert diagnostic["request_id"] == "zhilian:req-stable"
    assert diagnostic["failure_type"] == "tls_eof"
    assert api_key not in json.dumps(error.value.details)


def test_timeout_has_stable_code(monkeypatch):
    def fail(_request, *, timeout):
        assert timeout == 20
        raise TimeoutError("timed out")

    monkeypatch.setattr(cloud_client.urllib.request, "urlopen", fail)
    monkeypatch.setattr(cloud_client.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(cloud_client, "load_api_key", lambda: "agentmesh_test_key")
    monkeypatch.setattr(
        cloud_client, "api_base_url", lambda: "https://api.example.test"
    )

    with pytest.raises(cloud_client.CloudError) as error:
        cloud_client.me()

    assert error.value.code == "network_timeout"
    assert error.value.retryable is True
    assert (
        error.value.details["network_diagnostic"]["operation"] == "account_verification"
    )


@pytest.mark.parametrize("status", [502, 503, 504])
def test_gateway_failures_have_stable_code(status, monkeypatch):
    def fail(_request, *, timeout):
        assert timeout == 60
        raise _http_error(status, {"message": "temporary gateway failure"})

    monkeypatch.setattr(cloud_client.urllib.request, "urlopen", fail)
    monkeypatch.setattr(cloud_client.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(cloud_client, "load_api_key", lambda: "agentmesh_test_key")
    monkeypatch.setattr(
        cloud_client, "api_base_url", lambda: "https://api.example.test"
    )

    with pytest.raises(cloud_client.CloudError) as error:
        cloud_client.discovery_start(
            platform="zhilian",
            profile={"schema_version": 1},
            request_id=f"zhilian:req-{status}",
        )

    assert error.value.status == status
    assert error.value.code == "cloud_gateway_unavailable"
    assert error.value.retryable is True
    assert error.value.attempts == 3
    assert error.value.details["network_diagnostic"]["failure_type"] == "gateway"


def test_gateway_status_replaces_generic_server_code(monkeypatch):
    def fail(_request, *, timeout):
        assert timeout == 20
        raise _http_error(503, {"code": "cloud_error", "message": "temporary"})

    monkeypatch.setattr(cloud_client.urllib.request, "urlopen", fail)
    monkeypatch.setattr(cloud_client.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(cloud_client, "load_api_key", lambda: "agentmesh_test_key")
    monkeypatch.setattr(
        cloud_client, "api_base_url", lambda: "https://api.example.test"
    )

    with pytest.raises(cloud_client.CloudError) as error:
        cloud_client.me()

    assert error.value.code == "cloud_gateway_unavailable"
    assert error.value.retryable is True


def test_certificate_verification_failure_is_not_retried(monkeypatch):
    attempts = 0

    def fail(_request, *, timeout):
        nonlocal attempts
        attempts += 1
        assert timeout == 20
        raise urllib.error.URLError(
            ssl.SSLCertVerificationError(1, "certificate verify failed")
        )

    monkeypatch.setattr(cloud_client.urllib.request, "urlopen", fail)
    monkeypatch.setattr(
        cloud_client.time,
        "sleep",
        lambda _delay: pytest.fail("certificate failures must not retry"),
    )
    monkeypatch.setattr(cloud_client, "load_api_key", lambda: "agentmesh_test_key")
    monkeypatch.setattr(
        cloud_client, "api_base_url", lambda: "https://api.example.test"
    )

    with pytest.raises(cloud_client.CloudError) as error:
        cloud_client.me()

    assert attempts == 1
    assert error.value.code == "tls_certificate_verification_failed"
    assert error.value.retryable is False


def test_certificate_verification_string_reason_is_not_retried(monkeypatch):
    monkeypatch.setattr(
        cloud_client.urllib.request,
        "urlopen",
        lambda _request, *, timeout: (_ for _ in ()).throw(
            urllib.error.URLError("CERTIFICATE_VERIFY_FAILED")
        ),
    )
    monkeypatch.setattr(
        cloud_client.time,
        "sleep",
        lambda _delay: pytest.fail("certificate failures must not retry"),
    )
    monkeypatch.setattr(cloud_client, "load_api_key", lambda: "agentmesh_test_key")
    monkeypatch.setattr(
        cloud_client, "api_base_url", lambda: "https://api.example.test"
    )

    with pytest.raises(cloud_client.CloudError) as error:
        cloud_client.me()

    assert error.value.code == "tls_certificate_verification_failed"
    assert error.value.retryable is False


def test_pending_decision_is_atomic_and_discover_id_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery_state, "discoveries_dir", lambda: tmp_path)
    plan = {"discover_id": "dis_pending", "signature": "signed"}
    jobs = [{"id": "job-1", "title": "AI Product Manager"}]

    path = discovery_state.save_pending_decision(
        "boss",
        plan=plan,
        jobs=jobs,
    )
    loaded = discovery_state.load_pending_decision("boss")

    assert path.name == "pending-decision.json"
    assert not path.with_suffix(".tmp").exists()
    assert loaded is not None
    assert loaded["discover_id"] == "dis_pending"
    assert loaded["plan"] == plan
    assert loaded["jobs"] == jobs

    discovery_state.clear_pending_decision("boss", discover_id="dis_other")
    assert path.exists()
    discovery_state.clear_pending_decision("boss", discover_id="dis_pending")
    assert not path.exists()


def test_pending_start_is_atomic_and_context_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery_state, "discoveries_dir", lambda: tmp_path)

    path = discovery_state.save_pending_start(
        "zhilian",
        request_id="zhilian:req-stable",
        round_id="round-1",
        profile_digest="profile-digest",
        intent_digest="intent-digest",
        account_ref="acct_account_a",
    )
    loaded = discovery_state.load_pending_start("zhilian")

    assert path.name == "pending-start.json"
    assert not path.with_suffix(".tmp").exists()
    assert loaded == {
        "schema_version": 1,
        "platform": "zhilian",
        "request_id": "zhilian:req-stable",
        "round_id": "round-1",
        "profile_digest": "profile-digest",
        "intent_digest": "intent-digest",
        "account_ref": "acct_account_a",
        "saved_at": loaded["saved_at"],
    }

    discovery_state.clear_pending_start("zhilian", request_id="zhilian:req-other")
    assert path.exists()
    discovery_state.clear_pending_start("zhilian", request_id="zhilian:req-stable")
    assert not path.exists()
