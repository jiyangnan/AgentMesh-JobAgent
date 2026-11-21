"""Chrome instance manager — cross-platform Chrome launch, detection, and reconnect."""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any


# ── Cross-platform Chrome paths ───────────────────────────

CHROME_PATHS: dict[str, list[str]] = {
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ],
    "win32": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(os.path.expanduser("~"), r"AppData\Local\Google\Chrome\Application\chrome.exe"),
    ],
    "linux": [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/snap/bin/chromium",
    ],
}


def find_chrome() -> str | None:
    """Detect Chrome executable path for the current platform."""
    platform = os.name  # 'posix' or 'nt'
    # Map os.name to our platform keys
    if platform == "posix":
        if os.uname().sysname == "Darwin":
            key = "darwin"
        else:
            key = "linux"
    else:
        key = "win32"

    for p in CHROME_PATHS.get(key, []):
        if Path(p).exists():
            return p
    return None


def is_chrome_available() -> bool:
    return find_chrome() is not None


# ── CDP port utilities ────────────────────────────────────

# Chromium's PROFILE_IN_USE and PROCESS_NOTIFIED result codes.
_PROFILE_HANDOFF_EXIT_CODES = (21, 24)
# A clean launcher exit, DOWNGRADE_AND_RELAUNCH, or NORMAL_EXIT_AUTO_DE_ELEVATED.
_RELAUNCH_EXIT_CODES = (0, 33, 38)
_PROFILE_HANDOFF_GRACE_SECONDS = 3


def _is_port_open(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _get_first_page_ws_url(port: int, *, timeout: float = 5.0) -> str | None:
    """GET /json and return the first 'page' tab's webSocketDebuggerUrl."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", "/json")
        resp = conn.getresponse()
        if resp.status != 200:
            return None
        targets = json.loads(resp.read())
        if not isinstance(targets, list):
            return None
        for t in targets:
            if isinstance(t, dict) and t.get("type") == "page":
                ws_url = t.get("webSocketDebuggerUrl")
                if isinstance(ws_url, str) and ws_url:
                    return ws_url
    except (OSError, http.client.HTTPException, ValueError):
        pass
    finally:
        conn.close()
    return None


def _wait_for_cdp_page(port: int, process: subprocess.Popen, *, max_wait: float) -> str | None:
    """Wait for a usable page or a failed launch, using a monotonic deadline."""
    deadline = time.monotonic() + max_wait
    profile_handoff_observed = False
    while (remaining := deadline - time.monotonic()) > 0:
        ws_url = _get_first_page_ws_url(port, timeout=min(remaining, 2.0))
        if ws_url:
            return ws_url
        exit_code = process.poll()
        if exit_code in _PROFILE_HANDOFF_EXIT_CODES:
            if not profile_handoff_observed:
                # An existing Chrome for this profile may still be starting.
                deadline = min(deadline, time.monotonic() + _PROFILE_HANDOFF_GRACE_SECONDS)
                profile_handoff_observed = True
        elif exit_code is not None and exit_code not in _RELAUNCH_EXIT_CODES:
            break
        time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
    return None


# ── Chrome instance manager ───────────────────────────────

class ChromeStartupError(RuntimeError):
    def __init__(self, code: str, message: str, *, port: int, exit_code: int | None):
        super().__init__(message)
        self.payload = {
            "ok": False,
            "error": code,
            "message": message,
            "cdp_port": port,
            "chrome_exit_code": exit_code,
        }


class ChromeInstanceManager:
    """Manages a dedicated Chrome instance for Job Agent.

    - Launches Chrome with a fixed --user-data-dir (login state persists)
    - Uses a fixed remote-debugging-port (predictable, reconnectable)
    - Detects and reconnects to an already-running instance
    - Handles process lifecycle (start, stop, crash recovery)
    """

    DEFAULT_PORT = 19222
    DEFAULT_WINDOW_SIZE = (1200, 800)
    STARTUP_TIMEOUT_SECONDS = 30

    def __init__(
        self,
        port: int | None = None,
        user_data_dir: Path | str | None = None,
    ):
        self.port = port or self.DEFAULT_PORT
        self.user_data_dir = Path(user_data_dir) if user_data_dir else self._default_user_data_dir()
        self._process: subprocess.Popen | None = None
        self._ensure_running_promise: Any = None  # type: ignore

    def _default_user_data_dir(self) -> Path:
        """Platform-appropriate default profile directory."""
        if os.name == "posix":
            if os.uname().sysname == "Darwin":
                base = Path.home() / "Library" / "Application Support"
            else:
                base = Path.home() / ".config"
        else:
            base = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")))
        return base / "jobagent" / "chrome-profile"

    def _try_connect_existing(self) -> tuple[bool, str | None]:
        """Try to connect to an already-running Chrome on our port."""
        if not _is_port_open(self.port):
            return False, None
        ws_url = _get_first_page_ws_url(self.port)
        return (ws_url is not None), ws_url

    def ensure_running(self) -> str:
        """Ensure Chrome is running and return the CDP WebSocket URL.

        Returns:
            webSocketDebuggerUrl for the first page tab.

        Raises:
            RuntimeError: If Chrome is not available or fails to start.
        """
        # Check if already running and CDP responds
        connected, ws_url = self._try_connect_existing()
        if connected and ws_url:
            return ws_url

        # Never terminate an unknown process or reset a user's live profile.
        if _is_port_open(self.port):
            raise RuntimeError(f"cdp_port_unavailable: port {self.port} is occupied or has no readable Chrome page; preserve Chrome and run browser diagnose")

        # Find Chrome binary
        chrome_path = find_chrome()
        if not chrome_path:
            raise RuntimeError(
                "未找到 Chrome 浏览器。请安装 Google Chrome：\n"
                "  macOS: https://www.google.com/chrome/\n"
                "  Windows: https://www.google.com/chrome/"
            )

        # Ensure user data directory exists
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

        # Launch Chrome
        width, height = self.DEFAULT_WINDOW_SIZE
        args = [
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.user_data_dir}",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-sync",
            "--metrics-recording-only",
            f"--window-size={width},{height}",
            "about:blank",
        ]

        popen_kwargs: dict[str, Any] = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        else:
            popen_kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0)

        try:
            self._process = subprocess.Popen(
                [chrome_path, *args],
                **popen_kwargs,
            )
        except Exception as e:
            raise RuntimeError(f"启动 Chrome 失败: {e}")

        ws_url = _wait_for_cdp_page(self.port, self._process, max_wait=self.STARTUP_TIMEOUT_SECONDS)
        if ws_url:
            if self._process.poll() is not None:
                self._process = None
            return ws_url

        exit_code = self._process.poll()
        self.stop()
        if exit_code in _PROFILE_HANDOFF_EXIT_CODES:
            code = "chrome_profile_in_use"
            message = (
                "Chrome 无法使用 Job Agent 的专用资料目录。"
                "如果该专用 Chrome 已打开，请正常退出这个实例后重试。请保留现有资料目录。"
            )
        elif exit_code is not None:
            code = "chrome_exited_before_cdp"
            message = f"Chrome 在本地调试页面就绪前已退出（退出码 {exit_code}）。"
        else:
            code = "chrome_cdp_startup_timeout"
            message = f"Chrome 在 {self.STARTUP_TIMEOUT_SECONDS} 秒内未提供可连接的本地调试页面。"
        raise ChromeStartupError(code, message, port=self.port, exit_code=exit_code)

    def stop(self) -> None:
        """Terminate the Chrome process."""
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            except Exception:
                pass
        self._process = None

    def is_running(self) -> bool:
        """Check if our Chrome instance is still alive."""
        if self._process is not None and self._process.poll() is None:
            return True
        # The browser may have been started externally or by a launcher.
        return _is_port_open(self.port)
