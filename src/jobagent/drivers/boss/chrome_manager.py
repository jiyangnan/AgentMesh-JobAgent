"""Chrome instance manager — cross-platform Chrome launch, detection, and reconnect."""

from __future__ import annotations

import http.client
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

def _is_port_open(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _wait_for_cdp_port(port: int, max_wait: float = 10.0) -> bool:
    """Poll until CDP HTTP endpoint responds."""
    start = time.time()
    while time.time() - start < max_wait:
        if _is_port_open(port):
            # Also verify it's actually a CDP endpoint
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/json")
                resp = conn.getresponse()
                if resp.status == 200:
                    conn.close()
                    return True
                conn.close()
            except Exception:
                pass
        time.sleep(0.5)
    return False


def _get_first_page_ws_url(port: int) -> str | None:
    """GET /json and return the first 'page' tab's webSocketDebuggerUrl."""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/json")
        resp = conn.getresponse()
        data = resp.read().decode("utf-8")
        conn.close()
        import json
        targets = json.loads(data)
        for t in targets:
            if t.get("type") == "page":
                return t.get("webSocketDebuggerUrl")
        return None
    except Exception:
        return None


# ── Chrome instance manager ───────────────────────────────

class ChromeInstanceManager:
    """Manages a dedicated Chrome instance for Job Agent.

    - Launches Chrome with a fixed --user-data-dir (login state persists)
    - Uses a fixed remote-debugging-port (predictable, reconnectable)
    - Detects and reconnects to an already-running instance
    - Handles process lifecycle (start, stop, crash recovery)
    """

    DEFAULT_PORT = 19222
    DEFAULT_WINDOW_SIZE = (1200, 800)

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

        # If port is open but we couldn't get a WS URL (old instance without
        # --remote-allow-origins), kill it before restarting.
        if _is_port_open(self.port):
            self._kill_port_process()
            time.sleep(1.0)

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
            "--remote-allow-origins=*",  # Allow CDP connections from any origin
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

        # Wait for CDP port
        if not _wait_for_cdp_port(self.port, max_wait=15.0):
            self.stop()
            raise RuntimeError("Chrome 启动超时，请重试")

        # Get WebSocket URL
        ws_url = _get_first_page_ws_url(self.port)
        if not ws_url:
            self.stop()
            raise RuntimeError("无法获取 Chrome CDP 连接")

        return ws_url

    def _kill_port_process(self) -> None:
        """Kill whatever process is holding our CDP port."""
        try:
            # macOS/Linux: lsof
            result = subprocess.run(
                ["lsof", "-ti", f":{self.port}"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                for pid in result.stdout.strip().splitlines():
                    try:
                        subprocess.run(["kill", "-9", pid], capture_output=True)
                    except Exception:
                        pass
        except Exception:
            pass

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
        if self._process is None:
            # Might have been started externally or before our process tracking
            return _is_port_open(self.port)
        return self._process.poll() is None
