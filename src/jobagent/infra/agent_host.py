"""Which agent tool is running the CLI, as one coarse label.

Detection reads only well-known environment markers that agent hosts set for the
commands they run. Nothing else about the environment is sent. The value is
attached to authenticated API requests so the account owner's operator view can
show "Agent tool"; it respects the same opt-out as CLI analytics.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

AGENT_HOSTS = frozenset({
    "claude_code", "codex", "cursor", "gemini_cli", "openclaw", "opencode",
    "other_agent", "terminal",
})
_OPT_OUT = ("JOBAGENT_ANALYTICS_DISABLED", "JOBAGENT_ANALYTICS_KILL_SWITCH", "DO_NOT_TRACK")
_TRUE = frozenset({"1", "true", "yes", "on"})
# Variables a user may set globally for other reasons; they do not prove a host.
_CODEX_CONFIG_ONLY = frozenset({"CODEX_HOME"})


def _has_prefix(environ: Mapping[str, str], prefix: str, ignore: frozenset[str] = frozenset()) -> bool:
    return any(key.startswith(prefix) and key not in ignore and environ.get(key) for key in environ)


def detect_agent_host(environ: Mapping[str, str] | None = None, *, interactive: bool | None = None) -> str:
    env = os.environ if environ is None else environ
    explicit = str(env.get("JOBAGENT_AGENT_HOST") or "").strip().lower()
    if explicit in AGENT_HOSTS:
        return explicit
    if env.get("CLAUDECODE") or env.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude_code"
    if _has_prefix(env, "CODEX_", _CODEX_CONFIG_ONLY):
        return "codex"
    if env.get("GEMINI_CLI"):
        return "gemini_cli"
    if _has_prefix(env, "OPENCLAW"):
        return "openclaw"
    if env.get("OPENCODE") or _has_prefix(env, "OPENCODE_"):
        return "opencode"
    if env.get("CURSOR_AGENT") or env.get("CURSOR_TRACE_ID"):
        return "cursor"
    if interactive is None:
        try:
            interactive = sys.stdin.isatty() and sys.stdout.isatty()
        except (AttributeError, ValueError, OSError):
            interactive = False
    return "terminal" if interactive else "other_agent"


def agent_host_header(environ: Mapping[str, str] | None = None) -> str | None:
    """Header value, or None when the user opted out of CLI analytics."""
    env = os.environ if environ is None else environ
    if any(str(env.get(name) or "").strip().lower() in _TRUE for name in _OPT_OUT):
        return None
    try:
        return detect_agent_host(env)
    except Exception:  # never let detection affect a request
        return None
