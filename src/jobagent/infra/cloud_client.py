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
from jobagent.infra.tls_support import TLSConfigurationError, failure_details, verified_context

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
    if isinstance(error, TLSConfigurationError):
        return ('tls_trust_configuration_failed', 'tls_configuration', False,
                'The HTTPS trust configuration could not be loaded.')
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
            with urllib.request.urlopen(request, timeout=timeout, context=verified_context()) as response:
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
                    **failure_details(reason),
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
                    **failure_details(exc),
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


def _workflow_request(method: str, path: str, body: dict | None = None, **kwargs) -> dict:
    """Workflow writes have service idempotency; reads never grant execution.

    Mark only classified transport failures. Business rejections and unrelated
    billable requests must not acquire this retry guarantee.
    """
    try:
        return _request(method, path, body, **kwargs)
    except CloudError as exc:
        if exc.status == 404 and exc.code is None:
            raise CloudError("The server does not support workflow protocol 2; keep the current state and update the service before continuing.",
                             status=404, code="workflow_protocol_unavailable",
                             details={"request_preserved": True, "required_protocol_version": 2}) from exc
        if is_transient_transport_error(exc):
            exc.details = {**exc.details, "request_preserved": True,
                           "workflow_request_preserved": True}
        raise


def workflow_submit(payload: dict[str, Any]) -> dict[str, Any]:
    return _workflow_request("POST", "/v1/workflow/intents", payload, timeout=20,
                    operation="workflow_submit")


def workflow_status(session_id: str) -> dict[str, Any]:
    from urllib.parse import quote
    return _workflow_request("GET", f"/v1/workflow/sessions/{quote(session_id, safe='')}",
                    timeout=20, operation="workflow_status")


def round_criteria_update(round_id: str, body: dict) -> dict:
    from urllib.parse import quote
    return _workflow_request("POST", f"/v1/workflow/rounds/{quote(round_id, safe='')}/criteria", body,
                    timeout=30, operation="round_criteria_update", max_attempts=3)


def workflow_delivery_preview(body: dict) -> dict:
    return _workflow_request("POST", "/v1/workflow/delivery/previews", body, timeout=30,
                    operation="workflow_delivery_preview", max_attempts=3)


def workflow_delivery_answer(preview_id: str, choice: str) -> dict:
    from urllib.parse import quote
    return _workflow_request("POST", f"/v1/workflow/delivery/previews/{quote(preview_id, safe='')}/answer",
                    {"choice": choice}, timeout=30, operation="workflow_delivery_answer", max_attempts=3)


def workflow_delivery_status(preview_id: str) -> dict:
    from urllib.parse import quote
    return _workflow_request("GET", f"/v1/workflow/delivery/previews/{quote(preview_id, safe='')}/authorization",
                    timeout=20, operation="workflow_delivery_status")


def round_criteria_status(round_id: str) -> dict:
    from urllib.parse import quote
    return _workflow_request("GET", f"/v1/workflow/rounds/{quote(round_id, safe='')}/criteria",
                    timeout=20, operation="round_criteria_status")


def round_criteria_filter(round_id: str, discover_id: str) -> dict:
    from urllib.parse import quote
    return _workflow_request("GET", f"/v1/workflow/rounds/{quote(round_id, safe='')}/filter/{quote(discover_id, safe='')}",
                    timeout=20, operation="round_criteria_filter")


def credits_quote(action: str, request_id: str, scope_digest: str) -> dict[str, Any]:
    result = _workflow_request("POST", "/v1/workflow/credits/quote",
                    {"action": action, "request_id": request_id, "scope_digest": scope_digest},
                    timeout=20, operation="credits_quote")
    from jobagent.infra import protocol
    from jobagent.infra.account_state import current_account_ref
    from datetime import datetime, timezone
    quote = protocol.verify_signed_payload(result["quote"], public_key=protocol.DECISION_SIGNING_PUBLIC_KEY,
                                           expected_type="workflow_credit_quote")
    if (quote.get("account_ref") != current_account_ref() or quote.get("action") != action
            or quote.get("request_id") != request_id or quote.get("scope_digest") != scope_digest
            or quote.get("charged") is not False or quote.get("protocol_version") != 2
            or datetime.fromisoformat(quote["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc)):
        raise CloudError("Credit quote context mismatch", code="credit_quote_invalid")
    return result


def _workflow_price_stage(action: str, request_id: str, scope: dict) -> None:
    from jobagent.infra import state, protocol
    workflow = state.load_json(state.STATE_DIR / "workflow.json") or {}
    if not workflow.get("intent"):
        return
    response = credits_quote(action, request_id, protocol.digest_payload(scope))
    from jobagent.infra.diagnostics import emit_stage
    emit_stage("credit_quote", action=action, quote=response["quote"], charged=False,
               recheck_at_execution=True)


def resume_center_preparation() -> dict[str, Any]:
    """Read-only workbench fact: resume list, confirmation state and receipt.

    Never charges credits and never writes; 503 resume_center_unavailable
    means the server has not enabled the resume center yet.
    """
    return _request(
        "GET",
        "/v1/resume-center/preparation",
        timeout=20,
        max_attempts=2,
        operation="resume_center_preparation",
    )


def resume_center_resume(resume_id: str) -> dict[str, Any]:
    """Read one account-owned online resume; never charges or edits it."""
    from urllib.parse import quote

    return _request("GET", f"/v1/resume-center/resumes/{quote(resume_id, safe='')}",
                    timeout=20, max_attempts=2, operation="resume_status")


def resume_selection(context_id: str, expected_state_revision: int) -> dict[str, Any]:
    """Ask the user which confirmed resume this round delivers with."""
    return _request(
        "POST",
        "/v1/resume-center/selections",
        {
            "context_id": context_id,
            "expected_state_revision": expected_state_revision,
            "idempotency_key": f"cli-selection:{context_id}",
        },
        timeout=20,
        operation="resume_selection",
    )


def resume_selection_respond(
    selection_id: str, response_id: str, resume_id: str
) -> dict[str, Any]:
    """Submit the user's resume choice; returns the bound resume binding."""
    return _request(
        "POST",
        f"/v1/resume-center/selections/{selection_id}/respond",
        {"response_id": response_id, "resume_id": resume_id},
        timeout=20,
        operation="resume_selection_respond",
    )


def resume_binding_material(binding_id: str) -> dict[str, Any]:
    """Confirmed material snapshot for a binding: profile, digest, identity."""
    return _request(
        "GET",
        f"/v1/resume-center/bindings/{binding_id}/material",
        timeout=20,
        max_attempts=2,
        operation="resume_binding_material",
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


def analytics_events(
    events: list[dict[str, Any]],
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Relay a bounded batch of privacy-whitelisted client facts."""

    if (
        not events
        or len(events) > 25
        or any(not isinstance(event, dict) for event in events)
    ):
        raise ValueError("Analytics relay requires between 1 and 25 events.")
    return _request(
        "POST",
        "/v1/analytics/events",
        {"events": events},
        api_key=api_key,
        timeout=2,
        max_attempts=1,
        operation="analytics_relay",
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
    from jobagent.infra.protocol import digest_payload
    _workflow_price_stage("analysis", "analysis_" + digest_payload(body).split(":")[1][:32],
                          {"material_digest": digest_payload(body)})
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
    resume_binding_id: str | None = None,
    context_id: str | None = None,
    round_id: str | None = None,
    criteria_revision: int | None = None,
) -> dict[str, Any]:
    from jobagent.infra.protocol import digest_payload

    body: dict[str, Any] = {
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
    if round_id:
        body["round_id"] = round_id
    if criteria_revision is not None:
        body["criteria_revision"] = criteria_revision
    if resume_binding_id:
        body["resume_binding_id"] = resume_binding_id
        if context_id:
            body["context_id"] = context_id
        if round_id:
            body["round_id"] = round_id
    _workflow_price_stage("discover", request_id, body)
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


def discovery_repair(
    *,
    discover_id: str,
    expected_manifest_id: str,
    expected_candidate_digest: str,
    patches: list[dict[str, Any]],
    safe_exclusions: list[dict[str, Any]],
) -> dict[str, Any]:
    return _request(
        "POST",
        f"/v1/discovery/{discover_id}/repair",
        {
            "client_version": __version__,
            "protocol_version": PROTOCOL_VERSION,
            "expected_manifest_id": expected_manifest_id,
            "expected_candidate_digest": expected_candidate_digest,
            "reason": "zhilian_reviewability_v2",
            "patches": patches,
            "safe_exclusions": safe_exclusions,
        },
        timeout=600,
        max_attempts=3,
        operation="discovery_repair",
        request_id=discover_id,
    )
