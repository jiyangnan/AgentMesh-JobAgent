from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BossActionDriver(ABC):
    @abstractmethod
    def chrome_running(self) -> bool: ...

    @abstractmethod
    def applescript_js_enabled(self) -> tuple[bool, str]: ...

    @abstractmethod
    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5) -> dict[str, Any]: ...

    @abstractmethod
    def inspect_page(self) -> dict[str, Any]: ...

    @abstractmethod
    def click_chat_entry(self) -> dict[str, Any]: ...

    @abstractmethod
    def inspect_chat_editor(self) -> dict[str, Any]: ...

    @abstractmethod
    def fill_chat_message(self, message: str) -> dict[str, Any]: ...

    @abstractmethod
    def click_send(self) -> dict[str, Any]: ...

    @abstractmethod
    def verify_delivery(self, message: str) -> dict[str, Any]: ...
