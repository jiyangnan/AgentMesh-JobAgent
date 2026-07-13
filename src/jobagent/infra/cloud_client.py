"""Current Job Agent 0.3 cloud protocol client."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from jobagent import __version__
from jobagent.infra.credentials import api_base_url, load_api_key

PROTOCOL_VERSION = 1


class CloudError(Exception):
    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


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
    request = urllib.request.Request(
        api_base_url() + path,
        data=(json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None),
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
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
        raise CloudError(message, status=exc.code, code=code) from exc
    except urllib.error.URLError as exc:
        raise CloudError(f"Network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise CloudError(f"Request timed out after {timeout}s") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise CloudError("Cloud returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise CloudError("Cloud returned an unexpected payload")
    return payload


def health() -> dict[str, Any]:
    return _request("GET", "/v1/health", require_auth=False, timeout=15)


def me(*, api_key: str | None = None) -> dict[str, Any]:
    return _request("GET", "/v1/me", api_key=api_key, timeout=20)


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
    )
