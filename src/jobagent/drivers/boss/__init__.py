"""Boss driver package — AppleScript (macOS) and CDP (cross-platform)."""

from __future__ import annotations

from .base import BossActionDriver


def create_driver(prefer: str = "auto", platform: str = "boss") -> BossActionDriver:
    """Create the best available BossActionDriver.

    Args:
        prefer: 'cdp' | 'applescript' | 'auto'.
            'auto' uses the dedicated CDP browser. AppleScript is explicit only.
        platform: preferred CDP tab namespace: 'boss' | 'liepin' | 'zhilian'.

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
        return CDPBossDriver(platform=platform)

    from .cdp_driver import CDPBossDriver
    return CDPBossDriver(platform=platform)
