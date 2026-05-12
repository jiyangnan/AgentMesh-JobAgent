"""Boss driver package — AppleScript (macOS) and CDP (cross-platform)."""

from __future__ import annotations

from .base import BossActionDriver


def create_driver(prefer: str = "auto") -> BossActionDriver:
    """Create the best available BossActionDriver.

    Args:
        prefer: 'cdp' | 'applescript' | 'auto'.
            'auto' tries CDP first, then AppleScript on macOS.

    Returns:
        A ready-to-use BossActionDriver instance.

    Raises:
        RuntimeError: If no driver can be initialized.
    """
    import sys

    if prefer == "applescript":
        if sys.platform != "darwin":
            raise RuntimeError("AppleScript driver is only available on macOS")
        from .applescript_driver import AppleScriptBossDriver
        return AppleScriptBossDriver()

    if prefer == "cdp":
        from .cdp_driver import CDPBossDriver
        return CDPBossDriver()

    # auto: CDP first (cross-platform), AppleScript fallback on macOS
    try:
        from .cdp_driver import CDPBossDriver
        return CDPBossDriver()
    except Exception:
        if sys.platform == "darwin":
            from .applescript_driver import AppleScriptBossDriver
            return AppleScriptBossDriver()
        raise RuntimeError(
            "无法启动浏览器驱动。请确保 Google Chrome 已安装。"
        )
