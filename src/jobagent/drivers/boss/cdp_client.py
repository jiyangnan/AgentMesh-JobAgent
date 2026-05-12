"""CDP WebSocket client — thin wrapper over Chrome DevTools Protocol."""

from __future__ import annotations

import json
from typing import Any

import websocket


class CDPClient:
    """WebSocket client for Chrome DevTools Protocol.

    Maps CDP method calls to request/response pairs with auto-incrementing IDs.
    """

    def __init__(self):
        self.ws: websocket.WebSocket | None = None
        self._id_counter = 0
        self._pending: dict[int, Any] = {}  # id -> (resolve, reject)  — simplified inline

    def connect(self, ws_url: str, timeout: float = 10.0) -> None:
        """Open WebSocket connection to a CDP endpoint."""
        self.disconnect()
        self.ws = websocket.create_connection(ws_url, timeout=timeout)

    def disconnect(self) -> None:
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None
        self._pending.clear()

    @property
    def connected(self) -> bool:
        return self.ws is not None and self.ws.connected

    def send(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
        """Send a CDP method call and wait for the response.

        Args:
            method: CDP method name, e.g. 'Runtime.evaluate'.
            params: Method parameters dict.
            timeout: Seconds to wait for response.

        Returns:
            The parsed JSON result from CDP.

        Raises:
            RuntimeError: If not connected or request times out / errors.
        """
        if not self.connected:
            raise RuntimeError("CDP 未连接")

        self._id_counter += 1
        msg_id = self._id_counter
        payload = {"id": msg_id, "method": method, "params": params or {}}

        self.ws.send(json.dumps(payload))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = self.ws.recv()
                data = json.loads(raw)
                # Only handle responses with matching id
                if data.get("id") == msg_id:
                    if "error" in data:
                        raise RuntimeError(f"CDP error: {data['error']}")
                    return data.get("result")
                # Ignore events (no id or different id)
            except websocket.WebSocketTimeoutException:
                continue
            except Exception as e:
                raise RuntimeError(f"CDP 通信错误: {e}")
        raise RuntimeError(f"CDP 请求超时: {method}")

    def evaluate(self, expression: str, await_promise: bool = False, return_by_value: bool = True, timeout: float = 30.0) -> Any:
        """Convenience: Runtime.evaluate with common defaults."""
        result = self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": return_by_value,
            },
            timeout=timeout,
        )
        # result is the CDP result dict; caller usually wants result.result.value
        return result


import time  # noqa: E402 — imported at end to avoid circular issues with type hints
