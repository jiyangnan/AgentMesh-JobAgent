"""AgentMesh360 API key storage at ~/.jobagent/credentials (mode 600).

Single-key model — one user, one key. M4 may add multi-profile support."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from jobagent.infra.state import APP_DIR, ensure_dirs

CREDENTIALS_PATH = APP_DIR / "credentials"

DEFAULT_API_BASE = "https://api.jobagent.agentmesh360.com"


def credentials_path() -> Path:
    return CREDENTIALS_PATH


def load_api_key() -> str | None:
    """Return the saved API key, or None if not configured.

    Order: env var JOBAGENT_API_KEY > ~/.jobagent/credentials.
    """
    env = os.environ.get("JOBAGENT_API_KEY")
    if env:
        return env.strip()
    if not CREDENTIALS_PATH.exists():
        return None
    text = CREDENTIALS_PATH.read_text(encoding="utf-8").strip()
    return text or None


def save_api_key(key: str) -> Path:
    ensure_dirs()
    CREDENTIALS_PATH.write_text(key.strip() + "\n", encoding="utf-8")
    # Tighten permissions (rw for owner only)
    try:
        os.chmod(CREDENTIALS_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # Best-effort on non-POSIX filesystems
    return CREDENTIALS_PATH


def api_base_url() -> str:
    """Override default with JOBAGENT_API_BASE env (useful for staging/tests)."""
    return os.environ.get("JOBAGENT_API_BASE", DEFAULT_API_BASE).rstrip("/")
