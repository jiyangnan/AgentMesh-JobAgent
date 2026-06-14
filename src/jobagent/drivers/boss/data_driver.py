"""Compatibility import for the Boss collection flow.

Boss-specific collection now lives under ``jobagent.platforms.boss``. Keep this
module so existing CLI code and external scripts importing the old path continue
to work during the platformization milestone.
"""

from __future__ import annotations

from jobagent.platforms.boss.collect import BossDataDriver

__all__ = ["BossDataDriver"]
