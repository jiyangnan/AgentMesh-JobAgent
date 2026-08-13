"""Current Job Agent 0.3 cloud protocol client."""

from __future__ import annotations

import http.client
import json
import ssl
import time
import urllib.error
import urllib.request
from typing import Any

from jobagent import __version__
from jobagent.infra.credentials import api_base_url, load_api_key

PROTOCOL_VERSION = 1
_TRANSIENT_HTTP_STATUSES = frozenset({502, 503, 504})
_NON_RETRYABLE_502_CODES = frozenset(
    {
        "decision_failed_no_charge",
        "decision_failed_refunded",
        "llm_parse_failed",
    }
)
_RETRY_DELAYS_SECONDS = (1.0, 3.0)
_TRANSIENT_TRANSPORT_CODES = frozenset(
    {
        "cloud_gateway_unavailable",
        "network_connection_error",
        "network_connection_interrupted",
        "network_timeout",
        "tls_connection_eof",
        "tls_connection_error",
    }
)


class CloudError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        retryable: bool = False,
        attempts: int = 1,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.retryable = retryable
        self.attempts = attempts
        self.details = details or {}


class NotConfiguredError(CloudError):
    pass


def is_transient_transport_error(error: CloudError) -> bool:
    """Return whether a local control command may use verified cached state."""

    return bool(error.retryable and error.code in _TRANSIENT_TRANSPORT_CODES)


def _network_diagnostic(
    *,
    operation: str,
    failure_type: str,
    attempts: int,
    started_at: float,
    request_id: str | None = None,
    status: int | None = None,
) -> dict[str, Any]:
    diagnostic: dict[str, Any] = {
        "service": "agentmesh_cloud",
        "operation": operation,
        "failure_type": failure_type,
        "attempts": attempts,
        "elapsed_ms": max(0, round((time.monotonic() - started_at) * 1000)),
    }
    if request_id:
        diagnostic["request_id"] = request_id
    if status is not None:
        diagnostic["http_status"] = status
    return diagnostic


def _transport_failure(error: BaseException) -> tuple[str, str, bool, str]:
    """Map transport exceptions to stable, non-secret client error contracts."""

    normalized = str(error).upper()
    if (
        isinstance(error, ssl.SSLCertVerificationError)
        or "CERTIFICATE_VERIFY_FAILED" in normalized
    ):
        return (
            "tls_certificate_verification_failed",
            "tls_certificate",
            False,
            "TLS certificate verification failed.",
        )
    if (
        isinstance(error, ssl.SSLEOFError)
        or "UNEXPECTED_EOF_WHILE_READING" in normalized
    ):
        return (
            "tls_connection_eof",
            "tls_eof",
            True,
            "TLS connection ended unexpectedly while reading the cloud response.",
        )
    if isinstance(error, TimeoutError) or "TIMED OUT" in normalized:
        return (
            "network_timeout",
            "timeout",
            True,
            "The cloud request timed out.",
        )
    if isinstance(error, ssl.SSLError):
        return (
            "tls_connection_error",
            "tls",
            True,
            "The TLS connection to AgentMesh cloud failed.",
        )
    if isinstance(error, (ConnectionError, http.client.HTTPException)):
        return (
            "network_connection_interrupted",
            "connection_interrupted",
            True,
            "The cloud connection was interrupted.",
        )
    return (
        "network_connection_error",
        "connection",
        True,
        "The AgentMesh cloud service could not be reached.",
    )


def _request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    require_auth: bool = True,
    api_key: str | None = None,
    timeout: int = 180,
    max_attempts: int = 1,
    operation: str = "cloud_request",
    request_id: str | None = None,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-JobAgent-Client-Version": __version__,
        "X-JobAgent-Protocol-Version": str(PROTOCOL_VERSION),
    }
    if require_auth:
        key = api_key or load_api_key()
        if not key:
            raise NotConfiguredError(
                "AgentMesh API Key is required. Run `jobagent init --key <your_api_key>`."
            )
        headers["Authorization"] = f"Bearer {key}"
    encoded_body = (
        json.dumps(body, ensure_ascii=False).encode("utf-8")
        if body is not None
        else None
    )
    max_attempts = max(1, max_attempts)
    started_at = time.monotonic()
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            api_base_url() + path,
            data=encoded_body,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
            break
        except urllib.error.HTTPError as exc:
            raw_error = exc.read().decode("utf-8", errors="replace")
            code = None
            message = raw_error
            try:
                payload = json.loads(raw_error)
                detail = payload.get("detail", payload)
                if isinstance(detail, dict):
                    code = detail.get("code") or detail.get("reason")
                    message = detail.get("message") or json.dumps(
                        detail, ensure_ascii=False
                    )
            except json.JSONDecodeError:
                pass
            retryable = exc.code in _TRANSIENT_HTTP_STATUSES and not (
                exc.code == 502 and code in _NON_RETRYABLE_502_CODES
            )
            if retryable and attempt < max_attempts:
                time.sleep(
                    _RETRY_DELAYS_SECONDS[
                        min(attempt - 1, len(_RETRY_DELAYS_SECONDS) - 1)
                    ]
                )
                continue
            semantic_failure = bool(
                exc.code == 502 and code in _NON_RETRYABLE_502_CODES
            )
            stable_code = (
                str(code)
                if semantic_failure
                else "cloud_gateway_unavailable"
                if exc.code in _TRANSIENT_HTTP_STATUSES
                else code
            )
            details = {}
            if exc.code in _TRANSIENT_HTTP_STATUSES:
                details["network_diagnostic"] = _network_diagnostic(
                    operation=operation,
                    failure_type="gateway",
                    attempts=attempt,
                    started_at=started_at,
                    request_id=request_id,
                    status=exc.code,
                )
            raise CloudError(
                message,
                status=exc.code,
                code=stable_code,
                retryable=retryable,
                attempts=attempt,
                details=details,
            ) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            code, failure_type, retryable, message = _transport_failure(reason)
            if retryable and attempt < max_attempts:
                time.sleep(
                    _RETRY_DELAYS_SECONDS[
                        min(attempt - 1, len(_RETRY_DELAYS_SECONDS) - 1)
                    ]
                )
                continue
            raise CloudError(
                message,
                code=code,
                retryable=retryable,
                attempts=attempt,
                details={
                    "network_diagnostic": _network_diagnostic(
                        operation=operation,
                        failure_type=failure_type,
                        attempts=attempt,
                        started_at=started_at,
                        request_id=request_id,
                    )
                },
            ) from exc
        except (
            TimeoutError,
            ssl.SSLError,
            ConnectionError,
            http.client.HTTPException,
        ) as exc:
            code, failure_type, retryable, message = _transport_failure(exc)
            if retryable and attempt < max_attempts:
                time.sleep(
                    _RETRY_DELAYS_SECONDS[
                        min(attempt - 1, len(_RETRY_DELAYS_SECONDS) - 1)
                    ]
                )
                continue
            raise CloudError(
                message,
                code=code,
                retryable=retryable,
                attempts=attempt,
                details={
                    "network_diagnostic": _network_diagnostic(
                        operation=operation,
                        failure_type=failure_type,
                        attempts=attempt,
                        started_at=started_at,
                        request_id=request_id,
                    )
                },
            ) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise CloudError("Cloud returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise CloudError("Cloud returned an unexpected payload")
    return payload


def health() -> dict[str, Any]:
    return _request(
        "GET",
        "/v1/health",
        require_auth=False,
        timeout=15,
        max_attempts=2,
        operation="cloud_health",
    )


def me(*, api_key: str | None = None) -> dict[str, Any]:
    return _request(
        "GET",
        "/v1/me",
        api_key=api_key,
        timeout=20,
        max_attempts=2,
        operation="account_verification",
    )


def resume_analyze(
    resume_text: str,
    file_name: str | None = None,
    hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"resume_text": resume_text}
    if file_name:
        body["file_name"] = file_name
    if hints:
        body["hints"] = hints
    return _request(
        "POST",
        "/v1/resume/analyze",
        body,
        timeout=180,
        operation="resume_analyze",
    )


def discovery_start(
    *,
    platform: str,
    profile: dict[str, Any],
    request_id: str,
    round_intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from jobagent.infra.protocol import digest_payload

    body = {
        "platform": platform,
        "profile": profile,
        "profile_digest": digest_payload(profile),
        "client_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
    }
    if round_intent and round_intent.get("status") == "confirmed":
        body["round_intent"] = round_intent
        body["intent_digest"] = digest_payload(round_intent)
    return _request(
        "POST",
        "/v1/discovery/start",
        body,
        timeout=60,
        max_attempts=3,
        operation="discovery_start",
        request_id=request_id,
    )


def discovery_renew(
    *,
    discover_id: str,
    platform: str,
    profile_digest: str,
    intent_digest: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "platform": platform,
        "profile_digest": profile_digest,
        "client_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
    }
    if intent_digest is not None:
        body["intent_digest"] = intent_digest
    return _request(
        "POST",
        f"/v1/discovery/{discover_id}/renew",
        body,
        timeout=60,
        max_attempts=3,
        operation="search_plan_renewal",
        request_id=discover_id,
    )


def discovery_decide(
    *,
    discover_id: str,
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    return _request(
        "POST",
        "/v1/discovery/decide",
        {
            "discover_id": discover_id,
            "client_version": __version__,
            "protocol_version": PROTOCOL_VERSION,
            "jobs": jobs,
        },
        timeout=600,
        max_attempts=3,
        operation="discovery_decide",
        request_id=discover_id,
    )
