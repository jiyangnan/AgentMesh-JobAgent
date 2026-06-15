from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from jobagent.infra.state import load_json, save_json, support_state_path

PUBLIC_REPO_URL = "https://github.com/jiyangnan/AgentMesh-JobAgent"
FIRST_DELIVERY_PROMPT_KEY = "first_delivery_star_prompted_at"


def star_prompt_message() -> str:
    return (
        "\n"
        + "-" * 60
        + "\n"
        + "你已经完成了 Job Agent 的首次真实投递。谢谢你把工具跑到了真正有价值的一步。\n"
        + "如果它对你有帮助，欢迎给公开 CLI 仓库点一个 star：\n"
        + f"{PUBLIC_REPO_URL}\n"
        + "这只是自愿支持，不影响 license 或后续使用。这个提示只会出现一次。\n"
        + "-" * 60
        + "\n"
    )


def support_star_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "action": "open_github_repo_and_star_if_you_want",
        "url": PUBLIC_REPO_URL,
        "message": "如果 Job Agent 对你有帮助，欢迎自愿给公开 CLI 仓库点一个 star。",
        "note": "Star is optional. It is never required for license, download, or usage.",
    }


def record_first_successful_delivery(
    *,
    platform: str,
    command: str,
    delivered: int,
    dry_run: bool = False,
) -> dict[str, Any] | None:
    if dry_run or delivered <= 0:
        return None

    path = support_state_path()
    state = load_json(path) or {}
    if state.get(FIRST_DELIVERY_PROMPT_KEY):
        return None

    now = datetime.now(timezone.utc).isoformat()
    state.update({
        FIRST_DELIVERY_PROMPT_KEY: now,
        "first_delivery_platform": platform,
        "first_delivery_command": command,
        "first_delivery_delivered": delivered,
        "public_repo_url": PUBLIC_REPO_URL,
    })
    save_json(path, state)
    return {
        "prompted": True,
        "prompted_at": now,
        "platform": platform,
        "command": command,
        "delivered": delivered,
        "url": PUBLIC_REPO_URL,
        "message": star_prompt_message(),
    }


def print_first_delivery_star_prompt_once(
    *,
    platform: str,
    command: str,
    delivered: int,
    dry_run: bool = False,
) -> bool:
    event = record_first_successful_delivery(
        platform=platform,
        command=command,
        delivered=delivered,
        dry_run=dry_run,
    )
    if not event:
        return False
    print(event["message"], file=sys.stderr)
    return True
