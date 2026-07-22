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
    monkeypatch.setattr(cloud_client, "api_base_url", lambda: "https://api.example.test")

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
    monkeypatch.setattr(cloud_client, "api_base_url", lambda: "https://api.example.test")

    assert cloud_client.discovery_decide(discover_id="dis_test", jobs=[]) == {"ok": True}
    assert attempts == 3


def test_semantic_decision_failure_is_not_retried(monkeypatch):
    attempts = 0

    def fail(_request, *, timeout):
        nonlocal attempts
        attempts += 1
        assert timeout == 600
        raise _http_error(502, {"code": "decision_failed_refunded"})

    monkeypatch.setattr(cloud_client.urllib.request, "urlopen", fail)
    monkeypatch.setattr(cloud_client.time, "sleep", lambda _delay: pytest.fail("unexpected retry"))
    monkeypatch.setattr(cloud_client, "load_api_key", lambda: "agentmesh_test_key")
    monkeypatch.setattr(cloud_client, "api_base_url", lambda: "https://api.example.test")

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
        raise urllib.error.URLError(
            ssl.SSLEOFError(8, "UNEXPECTED_EOF_WHILE_READING")
        )

    monkeypatch.setattr(cloud_client.urllib.request, "urlopen", fail)
    monkeypatch.setattr(cloud_client, "load_api_key", lambda: "agentmesh_test_key")
    monkeypatch.setattr(cloud_client, "api_base_url", lambda: "https://api.example.test")

    with pytest.raises(cloud_client.CloudError) as error:
        cloud_client.resume_analyze("A sufficiently long resume body for this test")

    assert attempts == 1
    assert error.value.retryable is True
    assert error.value.attempts == 1


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
