"""Read-only installation handoff, shared by installers and the public CLI.

Credential presence is not account verification or completed onboarding. This
entry point deliberately avoids updates, migrations, browser work and billing.
"""
from __future__ import annotations

import json
import sys
from typing import Any

ACCOUNT_URL = "https://agentmesh360.com/app/"
WORKBENCH_URL = "https://agentmesh360.com/workbench/"
INIT_COMMAND = "jobagent init --key <your_api_key>"


def key_handoff() -> dict[str, Any]:
    return {
        "onboarding": {
            "stage": "api_key_required",
            "account_url": ACCOUNT_URL,
            "return_to": "current_agent_conversation",
            "return_message": "我已配置 API Key，请继续 Job Agent 设置。",
        },
        "requires_user_action": True,
        "user_prompt": (
            "Job Agent 客户端已安装，下一步是连接你的 AgentMesh360 账户。\n"
            f"1. 打开 {ACCOUNT_URL}，免费注册或登录，在个人中心生成 API Key。\n"
            "2. 拿到 Key 后，请回到当前这段 Agent 对话，让 Agent 继续配置。"
            "如果这是你信任的私密对话，可以把 Key 提供给 Agent；"
            "也可以在自己的终端运行 jobagent init --key <你的API_Key> 完成配置。\n"
            "3. 如果已在终端配置，回到这里回复："
            "“我已配置 API Key，请继续 Job Agent 设置。”\n"
            "接下来 Agent 会检查环境和账户，再引导你准备简历与确认目标城市。"
            "申请 Key 后仍需回来继续；注册和生成 Key 免费，云端能力按账户可用额度使用。"
            "请勿把 Key 发到公开群聊或 Issue。"
        ),
        "agent_instructions": (
            "Show user_prompt in the user's language before ending the installation turn. "
            "Wait for the user's real Key or confirmation of local configuration. "
            "Never execute a placeholder Key. With a supplied Key, run init without echoing "
            "the secret, then immediately run jobagent doctor env. If already configured, "
            "run doctor env directly. Continue setup until a concrete user handoff; "
            "installation or --version success alone does not complete onboarding."
        ),
        "next_suggested": INIT_COMMAND,
    }


def installation_handoff() -> dict[str, Any]:
    from jobagent import __version__
    from jobagent.infra.credentials import load_api_key

    base = {
        "ok": True,
        "event": "onboarding_handoff",
        "client_version": __version__,
        "source": "local_configuration",
        "account_verified": False,
        "cli_command": [sys.executable, "-m", "jobagent"],
    }
    try:
        key_present = bool(load_api_key())
    except (OSError, UnicodeError):
        # Never print credential bytes or exception text from this read.
        return {
            **base,
            "onboarding": {"stage": "credentials_unreadable"},
            "requires_user_action": True,
            "user_prompt": (
                "客户端已安装，但无法读取本机 API Key 配置。请检查配置文件的读取权限；"
                "完成后回到当前对话回复“配置读取问题已处理，请继续”，"
                "Agent 会重新运行 jobagent onboarding。现有配置已保留。"
            ),
            "next_suggested": "jobagent onboarding",
        }
    if not key_present:
        return {**base, **key_handoff(), "api_key_configured": False}
    return {
        **base,
        "api_key_configured": True,
        "onboarding": {"stage": "environment_check_required"},
        "requires_user_action": False,
        "user_prompt": (
            "Job Agent 客户端已安装，检测到本机已有 API Key 配置。"
            "下一步由 Agent 运行 jobagent doctor env，检查环境和账户，"
            "再根据结果继续设置或说明已有任务的进度。"
            "已有配置尚未经过本次在线验证，无需先重新申请 Key。"
        ),
        "agent_instructions": (
            "Run jobagent doctor env now and relay its current setup or recovery handoff. "
            "Do not stop after installation or infer readiness from credential presence. "
            "An install request authorizes setup checks, not a new round, paid analysis, "
            "browser actions or delivery. Preserve any existing work and authorization. "
            "If this installer was invoked by a CLI-directed repair, resume that original "
            "recovery continuation instead of starting a separate setup flow."
        ),
        "next_suggested": "jobagent doctor env",
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", action="store_true")
    args = parser.parse_args()
    from jobagent.infra.workflow_protocol import with_contract
    handoff = with_contract(installation_handoff())
    if args.installer:
        print(handoff["user_prompt"])
        print("\nAgent handoff (follow before ending setup):")
    print(json.dumps(handoff, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
