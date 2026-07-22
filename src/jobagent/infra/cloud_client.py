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


def _request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    require_auth: bool = True,
    api_key: str | None = None,
    timeout: int = 180,
    max_attempts: int = 1,
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
    encoded_body = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    max_attempts = max(1, max_attempts)
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
                    message = detail.get("message") or json.dumps(detail, ensure_ascii=False)
            except json.JSONDecodeError:
                pass
            retryable = exc.code in _TRANSIENT_HTTP_STATUSES and not (
                exc.code == 502 and code in _NON_RETRYABLE_502_CODES
            )
            if retryable and attempt < max_attempts:
                time.sleep(_RETRY_DELAYS_SECONDS[min(attempt - 1, len(_RETRY_DELAYS_SECONDS) - 1)])
                continue
            raise CloudError(
                message,
                status=exc.code,
                code=code,
                retryable=retryable,
                attempts=attempt,
            ) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            retryable = not isinstance(reason, ssl.SSLCertVerificationError)
            if retryable and attempt < max_attempts:
                time.sleep(_RETRY_DELAYS_SECONDS[min(attempt - 1, len(_RETRY_DELAYS_SECONDS) - 1)])
                continue
            raise CloudError(
                f"Network error: {reason}",
                retryable=retryable,
                attempts=attempt,
            ) from exc
        except (TimeoutError, ssl.SSLError, ConnectionError, http.client.HTTPException) as exc:
            retryable = not isinstance(exc, ssl.SSLCertVerificationError)
            if retryable and attempt < max_attempts:
                time.sleep(_RETRY_DELAYS_SECONDS[min(attempt - 1, len(_RETRY_DELAYS_SECONDS) - 1)])
                continue
            message = (
                f"Request timed out after {timeout}s"
                if isinstance(exc, TimeoutError)
                else f"Network error: {exc}"
            )
            raise CloudError(
                message,
                retryable=retryable,
                attempts=attempt,
            ) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise CloudError("Cloud returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise CloudError("Cloud returned an unexpected payload")
    return payload


def health() -> dict[str, Any]:
    return _request("GET", "/v1/health", require_auth=False, timeout=15, max_attempts=2)


def me(*, api_key: str | None = None) -> dict[str, Any]:
    return _request("GET", "/v1/me", api_key=api_key, timeout=20, max_attempts=2)


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
    return _request("POST", "/v1/resume/analyze", body, timeout=180)


def discovery_start(
    *,
    platform: str,
    profile: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    from jobagent.infra.protocol import digest_payload

    return _request(
        "POST",
        "/v1/discovery/start",
        {
            "platform": platform,
            "profile": profile,
            "profile_digest": digest_payload(profile),
            "client_version": __version__,
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request_id,
        },
        timeout=60,
        max_attempts=3,
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
    )
