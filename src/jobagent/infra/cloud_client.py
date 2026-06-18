"""HTTP client for the Job Agent Cloud API.

Uses stdlib urllib only (no extra deps). Endpoints documented in
docs/m1-engineering-plan-20260509.md §2.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from jobagent.infra.credentials import api_base_url, load_license_key

# Per-endpoint timeouts (seconds) — generous to absorb DeepSeek tail latency.
TIMEOUTS = {
    "/v1/health": 10,
    "/v1/health/llm": 30,
    "/v1/me": 10,
    "/v1/resume/analyze": 180,
    "/v1/jobs/rank": 120,
    "/v1/greet/generate": 60,
}


class CloudError(Exception):
    """Wraps any failure (network, timeout, HTTP status, JSON decode)."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class NotConfiguredError(CloudError):
    """No AgentMesh360 API key found. User needs to run `jobagent init --key ...`."""


# Friendly Chinese hints for common error codes (GAP-17).
FRIENDLY_HINTS: dict[str, str] = {
    "missing_license": "未配置 AgentMesh360 API key。注册/登录 https://agentmesh360.com/app/ 后复制 API key，再运行 `jobagent init --key <your_api_key>`。",
    "missing_api_key": "未配置 AgentMesh360 API key。注册/登录 https://agentmesh360.com/app/ 后复制 API key，再运行 `jobagent init --key <your_api_key>`。",
    "invalid_license": "API key 无效或已过期。请从 AgentMesh360 账户面板重新复制。",
    "invalid_api_key": "API key 无效或已过期。请从 AgentMesh360 账户面板重新复制。",
    "license_revoked": "API key 已被撤销。请从 AgentMesh360 账户面板重新生成或联系支持。",
    "license_expired": "API key 已过期。请从 AgentMesh360 账户面板重新生成或联系支持。",
    "quota_exceeded": "当前 credit / 配额已用完。请前往 AgentMesh360 账户面板查看。",
    "insufficient_credits": "当前 credit 不足。请前往 AgentMesh360 账户面板查看。",
    "llm_parse_failed": "云端 LLM 输出解析失败（已自动重试 1 次）。换个简历/重试通常可恢复；持续失败请反馈。",
    "llm_timeout": "云端 LLM 调用超时。稍后重试；持续超时请反馈。",
    "llm_failed": "云端 LLM 调用失败。稍后重试。",
    "empty_message": "云端返回了空消息。重试一次通常可恢复。",
}


def hint_for(code: str | None) -> str | None:
    if not code:
        return None
    return FRIENDLY_HINTS.get(code)


def _request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    require_auth: bool = True,
) -> dict[str, Any]:
    url = api_base_url() + path
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if require_auth:
        key = load_license_key()
        if not key:
            raise NotConfiguredError(
                "No AgentMesh360 API key configured. Run `jobagent init --key <your_api_key>` after registering at https://agentmesh360.com/app/."
            )
        headers["Authorization"] = f"Bearer {key}"

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    timeout = TIMEOUTS.get(path, 60)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body_text)
            detail = payload.get("detail", {})
            code = detail.get("code") if isinstance(detail, dict) else None
            msg = detail.get("message") if isinstance(detail, dict) else body_text
        except json.JSONDecodeError:
            code, msg = None, body_text
        raise CloudError(
            f"HTTP {e.code} on {path}: {msg}", status=e.code, code=code
        ) from e
    except urllib.error.URLError as e:
        raise CloudError(f"Network error on {path}: {e.reason}") from e
    except TimeoutError as e:
        raise CloudError(f"Timeout on {path} (>{timeout}s)") from e

    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise CloudError(f"Invalid JSON from {path}: {e}") from e


# ── Public API ────────────────────────────────────────────────────


def health() -> dict[str, Any]:
    return _request("GET", "/v1/health", require_auth=False)


def me() -> dict[str, Any]:
    return _request("GET", "/v1/me")


def resume_analyze(
    resume_text: str,
    file_name: str | None = None,
    hints: dict | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"resume_text": resume_text}
    if file_name:
        body["file_name"] = file_name
    if hints:
        body["hints"] = hints
    return _request("POST", "/v1/resume/analyze", body)


def jobs_rank(profile: dict, jobs: list[dict]) -> dict[str, Any]:
    return _request("POST", "/v1/jobs/rank", {"profile": profile, "jobs": jobs})


def greet_generate(profile: dict, job: dict, style: str = "concise") -> dict[str, Any]:
    return _request(
        "POST",
        "/v1/greet/generate",
        {"profile": profile, "job": job, "style": style},
    )
