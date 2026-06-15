from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from jobagent.application.doctor_boss import DEFAULT_JOB_URL, run_boss_doctor
from jobagent.application.doctor_liepin import run_liepin_doctor
from jobagent.application.probe_send import run_probe_send
from jobagent.application.verify_last_send import run_verify_last_send
from jobagent.domain.models import CandidateProfile, Job, RankedJob
from jobagent.infra.config import GreeterConfig

DEFAULT_CONFIG_PATH = "config/config.yaml"


def _print_json(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _require_license_or_exit(command_label: str) -> None:
    """Hard gate for commands that depend on our cloud IP (rank / greet / pipeline).

    Local fallbacks were intentionally removed: degraded quality + zero funnel
    signal made the local paths a net loss. If no license configured, surface
    a literal user-facing prompt (the agent should relay it verbatim per the
    literal-prompt principle).
    """
    from jobagent.infra.credentials import load_license_key
    if load_license_key():
        return
    msg = (
        f"\n{'─' * 60}\n"
        f"⛔  `{command_label}` requires a license key.\n\n"
        "   This command depends on our Cloud AI service (招聘方视角分析 /\n"
        "   匹配评估 / 招呼语生成). M1 阶段免费申请，几小时回。\n\n"
        "   三选一申请：\n"
        "   1) 申请表单（推荐）→ https://jobagent.agentmesh360.com/#apply\n"
        "   2) GitHub Issue   → https://github.com/jiyangnan/job-agent/issues/new?template=license-request.yml\n"
        "   3) 邮件           → hello@agentmesh360.com\n\n"
        "   拿到 key 后跑：\n"
        "      jobagent init --key jba_live_xxx\n"
        f"{'─' * 60}\n"
    )
    print(msg, file=sys.stderr)
    _print_json({
        "ok": False,
        "stage": "license_check",
        "command": command_label,
        "error": "license_required",
        "hint": "Run `jobagent init --key jba_live_xxx`. Apply at https://jobagent.agentmesh360.com/#apply",
    })
    sys.exit(2)


def _print_cloud_upgrade_hint(command_name: str) -> None:
    """Tail-printed to stderr after any Path C (local) command.

    Read by the user's host agent and—per the literal-prompt principle—relayed
    to the human user. Drives license-funnel conversion from anonymous local
    usage to cloud usage. Skipped when JOBAGENT_QUIET=1 (CI / repeated runs).
    """
    import os
    if os.environ.get("JOBAGENT_QUIET"):
        return
    from jobagent.infra.credentials import load_license_key
    if load_license_key():
        return  # User has a license; they chose Path C deliberately.

    cloud_features = {
        "resume": "用云端招聘方视角模型校准 36 字段 profile",
        "jobs": "用招聘方权重表的二次匹配打分（本地模式会先用 36 维画像做规则匹配）",
        "greet": "含量化成果、避免套话的个性化招呼语（本地模式会先用 36 维画像生成）",
    }
    feature = cloud_features.get(command_name, "云端 AI 优化的全链路")

    msg = (
        "\n" + "─" * 60 + "\n"
        "💡 你正在使用 **本地模式**（不需要 license）。\n"
        "   云端模式（推荐）有这些优势：\n"
        f"     • {feature}\n"
        "     • 三大业务 endpoint 共享同一份招聘方视角 profile\n"
        "     • 云端算法持续迭代，客户端无需更新即可受益\n"
        "\n"
        "   申请 license（M1 阶段免费）三选一：\n"
        "   • 申请表单（推荐）: https://jobagent.agentmesh360.com/#apply\n"
        "   • GitHub Issue:    https://github.com/jiyangnan/job-agent/issues/new?template=license-request.yml\n"
        "   • 邮件:            hello@agentmesh360.com\n"
        + "─" * 60 + "\n"
    )
    print(msg, file=sys.stderr)


def _ensure_boss_login() -> None:
    """Passive login guide: auto-open Chrome → BOSS login page → poll.

    Called before any command that requires Boss authentication.
    Silent — no terminal UI. The agent (caller) is responsible for
    notifying the human user that Chrome needs attention.
    """
    from jobagent.drivers.boss import create_driver
    from jobagent.drivers.boss.cdp_driver import CDPBossDriver

    driver = create_driver()
    if isinstance(driver, CDPBossDriver):
        if not driver.ensure_logged_in():
            print(json.dumps({
                "ok": False,
                "error": "login_timeout",
                "message": "登录超时（5分钟），请重试",
            }), file=sys.stderr)
            sys.exit(2)
    # AppleScript fallback: cannot do passive login guide,
    # rely on the downstream LoginRequiredError path.


def _platform_config_path(args: argparse.Namespace) -> str:
    return str(getattr(args, "config", DEFAULT_CONFIG_PATH))


def _require_platform_enabled_or_exit(platform: str, args: argparse.Namespace) -> None:
    from jobagent.platforms import is_platform_enabled, normalize_platform_key

    config_path = _platform_config_path(args)
    overrides = _load_yaml_if_exists(config_path)
    if is_platform_enabled(platform, overrides):
        return

    key = normalize_platform_key(platform)
    print(json.dumps({
        "ok": False,
        "error": "platform_disabled",
        "platform": key,
        "config": config_path,
        "message": f"Platform `{key}` is disabled by local config.",
    }, ensure_ascii=False, indent=2), file=sys.stderr)
    sys.exit(2)


def _add_collect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Optional YAML config with platforms.<name>.enabled overrides")
    parser.add_argument("--city", required=True, help="City name (e.g. 深圳)")
    parser.add_argument("--query", required=True, help="Search query (e.g. AI产品经理)")
    parser.add_argument("--page", type=int, default=1, help="Starting page (default: 1)")
    parser.add_argument("--pages", type=int, default=1, help="How many pages to fetch starting from --page (default: 1; tip: use 3-5 to get 45-75 jobs)")
    parser.add_argument("--page-size", type=int, default=15, help="Results per page (default: 15)")
    parser.add_argument(
        "--page-delay", type=float, default=5.0,
        help="Seconds to sleep between pages (default: 5.0). Recommended ≥ 4 to be courteous to the upstream API. 0 disables (only safe for --pages 1).",
    )
    parser.add_argument(
        "--page-delay-jitter", type=float, default=2.0,
        help="Random extra delay added per page (default: 2.0). Actual sleep = page-delay + uniform(0, jitter).",
    )
    parser.add_argument("--output", "-o", help="Output JSON file path (default: stdout)")


def _add_liepin_collect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Optional YAML config with platforms.<name>.enabled overrides")
    parser.add_argument("--fixture", help="Saved Liepin JSON sample to parse in read-only M2 probe mode")
    parser.add_argument("--city", default="", help="Optional city name to inject when fixture rows omit city")
    parser.add_argument("--query", default="", help="Search query for live read-only collect, or optional fixture metadata")
    parser.add_argument("--page", type=int, default=1, help="Starting Liepin search page for live read-only collect")
    parser.add_argument("--pages", type=int, default=1, help="How many Liepin pages to fetch starting from --page")
    parser.add_argument("--page-delay", type=float, default=3.0, help="Seconds to sleep between Liepin pages in live read-only collect")
    parser.add_argument("--limit", type=int, default=20, help="Max visible cards to extract in live read-only mode")
    parser.add_argument("--wait-seconds", type=int, default=8, help="Seconds to wait after opening Liepin search page")
    parser.add_argument("--skip-login-check", action="store_true", help="Skip pre-collect login check and rely on collect-time login detection")
    parser.add_argument("--include-snapshot", action="store_true", help="Include raw browser extraction snapshot in stdout/file output")
    parser.add_argument("--snapshot-output", help="Write raw browser extraction snapshot to a separate JSON file")
    parser.add_argument("--output", "-o", help="Output JSON file path (default: stdout)")


def _add_liepin_login_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Optional YAML config with platforms.<name>.enabled overrides")
    parser.add_argument("--check", action="store_true", help="Only check current Liepin login state")
    parser.add_argument("--query", default="AI产品经理", help="Search query used for login-state check")
    parser.add_argument("--city", default="深圳", help="City used for login-state check")
    parser.add_argument("--timeout", type=int, default=300, help="Max seconds to wait for login")
    parser.add_argument("--poll-interval", type=int, default=3, help="Seconds between login-state polls")
    parser.add_argument("--wait-seconds", type=int, default=5, help="Seconds to wait after opening a Liepin page")


def _add_zhilian_collect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Optional YAML config with platforms.<name>.enabled overrides")
    parser.add_argument("--fixture", help="Saved Zhilian JSON sample to parse in read-only spike mode")
    parser.add_argument("--city", default="", help="Optional city name to inject when fixture rows omit city")
    parser.add_argument("--query", default="", help="Search query for live read-only collect, or optional fixture metadata")
    parser.add_argument("--page", type=int, default=1, help="Starting Zhilian search page for live read-only collect")
    parser.add_argument("--pages", type=int, default=1, help="How many Zhilian pages to fetch starting from --page")
    parser.add_argument("--page-delay", type=float, default=3.0, help="Seconds to sleep between Zhilian pages in live read-only collect")
    parser.add_argument("--limit", type=int, default=20, help="Max visible cards to extract in live read-only mode")
    parser.add_argument("--detail-limit", type=int, default=0, help="Open up to N Zhilian job detail pages read-only to fill missing fields (default: 0)")
    parser.add_argument("--wait-seconds", type=int, default=8, help="Seconds to wait after opening Zhilian search page")
    parser.add_argument("--include-snapshot", action="store_true", help="Include raw browser extraction snapshot in stdout/file output")
    parser.add_argument("--snapshot-output", help="Write raw browser extraction snapshot to a separate JSON file")
    parser.add_argument("--output", "-o", help="Output JSON file path (default: stdout)")


def _add_zhilian_login_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Optional YAML config with platforms.<name>.enabled overrides")
    parser.add_argument("--check", action="store_true", help="Only check current Zhilian login state")
    parser.add_argument("--query", default="AI产品经理", help="Search query used for login-state check")
    parser.add_argument("--city", default="深圳", help="City used for login-state check")
    parser.add_argument("--timeout", type=int, default=300, help="Max seconds to wait for login")
    parser.add_argument("--poll-interval", type=int, default=3, help="Seconds between login-state polls")
    parser.add_argument("--wait-seconds", type=int, default=5, help="Seconds to wait after opening a Zhilian page")


def _add_zhilian_greet_preview_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Optional YAML config with platforms.<name>.enabled overrides")
    parser.add_argument("--input", "-i", required=True, help="Ranked JSON from `jobagent zhilian rank`")
    parser.add_argument("--limit", "-n", type=int, default=10, help="Max jobs to preview")
    parser.add_argument("--local", action="store_true", help="Use local template greeting preview without Cloud API or license")
    parser.add_argument(
        "--output", "-o",
        help="Save ranked Zhilian jobs with cloud/local greetings injected; defaults to <input>.with_greetings.json.",
    )


def _add_zhilian_apply_open_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Optional YAML config with platforms.<name>.enabled overrides")
    parser.add_argument("--input", "-i", required=True, help="Zhilian ranked/ready JSON from `jobagent zhilian rank` or `jobagent zhilian greet preview`")
    parser.add_argument("--limit", "-n", type=int, default=5, help="Max Zhilian job pages to open")
    parser.add_argument("--start", type=int, default=0, help="Zero-based offset into the input jobs")
    parser.add_argument("--wait-seconds", type=int, default=3, help="Seconds to wait after opening each Zhilian page")
    parser.add_argument("--dry-run", action="store_true", help="Plan and audit open targets without opening browser pages")
    parser.add_argument("--require-greeting", action="store_true", help="Fail before opening if selected jobs do not include cloud_greeting/greeting")
    parser.add_argument("--skip-login-check", action="store_true", help="Skip pre-open login check and let opened pages handle login manually")


def _add_zhilian_apply_send_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Optional YAML config with platforms.<name>.enabled overrides")
    parser.add_argument("--input", "-i", required=True, help="Zhilian ready JSON from `jobagent zhilian greet preview`")
    parser.add_argument("--limit", "-n", type=int, default=5, help="Max Zhilian jobs to send/apply")
    parser.add_argument("--start", type=int, default=0, help="Zero-based offset into the input jobs")
    parser.add_argument("--wait-seconds", type=int, default=3, help="Seconds to wait after opening each Zhilian page")
    parser.add_argument("--dry-run", action="store_true", help="Plan and audit send targets without clicking apply/send")
    parser.add_argument("--confirm-submit", action="store_true", help="Optional explicit marker that this command performs real Zhilian send/apply actions")
    parser.add_argument("--require-greeting", action="store_true", help="Fail before sending if selected jobs do not include cloud_greeting/greeting")
    parser.add_argument("--skip-login-check", action="store_true", help="Skip pre-send login check and let opened pages handle login manually")
    parser.add_argument("--no-skip-delivered", action="store_true", help="Do not skip Zhilian URLs already marked delivered in the audit log")
    parser.add_argument("--continue-on-failure", action="store_true", help="Continue batch after a failed Zhilian send/apply attempt")


def _add_rank_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Optional YAML config with platforms.<name>.enabled overrides")
    parser.add_argument("--input", "-i", required=True, help="Input JSON file with job list (from a platform collect command)")
    parser.add_argument("--top", "-n", type=int, default=20, help="Keep only top N results (default: 20)")
    parser.add_argument("--output", "-o", help="Output JSON file path (default: stdout)")
    parser.add_argument("--local", action="store_true", help="Use local rule ranking without Cloud API or license")


def _add_greet_preview_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Optional YAML config with platforms.<name>.enabled overrides")
    parser.add_argument("--input", "-i", required=True, help="Ranked JSON from `jobagent boss rank`")
    parser.add_argument("--limit", "-n", type=int, default=10, help="Max jobs to preview")
    parser.add_argument("--local", action="store_true", help="Use local template greeting preview without Cloud API or license")
    parser.add_argument(
        "--output", "-o",
        help="Save ranked jobs with cloud greetings injected; defaults to <input>.with_greetings.json. `jobagent boss greet send --input <output>` will then use those.",
    )


def _add_liepin_greet_preview_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Optional YAML config with platforms.<name>.enabled overrides")
    parser.add_argument("--input", "-i", required=True, help="Ranked JSON from `jobagent liepin rank`")
    parser.add_argument("--limit", "-n", type=int, default=10, help="Max jobs to preview")
    parser.add_argument("--local", action="store_true", help="Use local template greeting preview without Cloud API or license")
    parser.add_argument(
        "--output", "-o",
        help="Save ranked Liepin jobs with cloud greetings injected; defaults to <input>.with_greetings.json. Next use `jobagent liepin apply open` for manual handoff.",
    )


def _add_liepin_greet_send_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Optional YAML config with platforms.<name>.enabled overrides")
    parser.add_argument("--input", "-i", required=True, help="Liepin ready JSON from `jobagent liepin greet preview`")
    parser.add_argument("--limit", "-n", type=int, default=5, help="Max Liepin jobs to hand off manually")
    parser.add_argument("--dry-run", action="store_true", help="Preview the manual handoff plan without opening pages")


def _add_greet_send_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", "-i", required=True, help="Input JSON file with ranked jobs")
    parser.add_argument("--limit", "-n", type=int, default=10, help="Max jobs to greet")
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Config YAML for greeter settings")


def _add_greet_audit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Optional YAML config with platforms.<name>.enabled overrides")
    parser.add_argument("--recent", "-n", type=int, default=20, help="Show N most recent records")


def _add_liepin_apply_open_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Optional YAML config with platforms.<name>.enabled overrides")
    parser.add_argument("--input", "-i", required=True, help="Liepin ranked/ready JSON from `jobagent liepin rank` or `jobagent liepin greet preview`")
    parser.add_argument("--limit", "-n", type=int, default=5, help="Max Liepin job pages to open")
    parser.add_argument("--start", type=int, default=0, help="Zero-based offset into the input jobs")
    parser.add_argument("--wait-seconds", type=int, default=3, help="Seconds to wait after opening each Liepin page")
    parser.add_argument("--dry-run", action="store_true", help="Plan and audit open targets without opening browser pages")
    parser.add_argument("--require-greeting", action="store_true", help="Fail before opening if selected jobs do not include cloud_greeting/greeting")
    parser.add_argument("--skip-login-check", action="store_true", help="Skip pre-open login check and let opened pages handle login manually")


def _add_liepin_apply_send_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Optional YAML config with platforms.<name>.enabled overrides")
    parser.add_argument("--input", "-i", required=True, help="Liepin ready JSON from `jobagent liepin greet preview`")
    parser.add_argument("--limit", "-n", type=int, default=5, help="Max Liepin jobs to send/apply")
    parser.add_argument("--start", type=int, default=0, help="Zero-based offset into the input jobs")
    parser.add_argument("--wait-seconds", type=int, default=3, help="Seconds to wait after opening each Liepin page")
    parser.add_argument("--dry-run", action="store_true", help="Plan and audit send targets without clicking apply/send")
    parser.add_argument("--confirm-submit", action="store_true", help="Optional explicit marker that this command performs real Liepin send/apply actions")
    parser.add_argument("--require-greeting", action="store_true", help="Fail before sending if selected jobs do not include cloud_greeting/greeting")
    parser.add_argument("--skip-login-check", action="store_true", help="Skip pre-send login check and let opened pages handle login manually")
    parser.add_argument("--no-skip-delivered", action="store_true", help="Do not skip Liepin URLs already marked delivered in the audit log")
    parser.add_argument("--continue-on-failure", action="store_true", help="Continue batch after a failed Liepin send/apply attempt")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobagent", description="Job Agent CLI MVP")
    sub = parser.add_subparsers(dest="command", required=True)

    # ── doctor ──
    doctor = sub.add_parser("doctor", help="Run environment/session checks")
    doctor_sub = doctor.add_subparsers(dest="doctor_target", required=True)
    doctor_boss = doctor_sub.add_parser("boss", help="Check Boss session readiness")
    doctor_boss.add_argument("--job-url", default=DEFAULT_JOB_URL, help="Sample Boss job URL used for doctor checks")
    doctor_liepin = doctor_sub.add_parser("liepin", help="Check Liepin read-only collect readiness")
    doctor_liepin.add_argument("--query", default="产品", help="Sample Liepin query used for readiness checks")
    doctor_liepin.add_argument("--city", default="", help="Optional sample city used for readiness checks")
    doctor_liepin.add_argument("--wait-seconds", type=int, default=5, help="Seconds to wait after opening Liepin pages")
    doctor_liepin.add_argument("--limit", type=int, default=5, help="Max visible cards to test during selector extraction")
    doctor_liepin.add_argument("--with-cloud", action="store_true", help="Also check cloud license readiness for Liepin rank/greet")

    doctor_env = doctor_sub.add_parser("env", help="Check local environment: Python / Chrome / network / license")

    # ── boss ──
    boss = sub.add_parser("boss", help="Boss-related commands")
    boss_sub = boss.add_subparsers(dest="boss_command", required=True)

    probe = boss_sub.add_parser("probe-send", help="Send one real test message and verify delivery")
    probe.add_argument("--job-url", required=True, help="Boss job detail URL")
    probe.add_argument("--message", required=True, help="Greeting message to send")

    verify = boss_sub.add_parser("verify-last-send", help="Verify the last sent message using stored state or explicit message")
    verify.add_argument("--message", help="Explicit message to verify; defaults to last probe-send message")

    boss_collect = boss_sub.add_parser("collect", help="Collect Boss直聘 jobs")
    _add_collect_args(boss_collect)

    boss_rank = boss_sub.add_parser("rank", help="Rank Boss直聘 jobs")
    _add_rank_args(boss_rank)

    boss_greet = boss_sub.add_parser("greet", help="Boss greeting commands")
    boss_greet_sub = boss_greet.add_subparsers(dest="boss_greet_command", required=True)
    boss_preview = boss_greet_sub.add_parser("preview", help="Preview Boss greetings")
    _add_greet_preview_args(boss_preview)
    boss_send = boss_greet_sub.add_parser("send", help="Send Boss greetings after user approval")
    _add_greet_send_args(boss_send)
    boss_audit = boss_greet_sub.add_parser("audit", help="View Boss greeting audit log")
    _add_greet_audit_args(boss_audit)

    # ── liepin ──
    liepin = sub.add_parser("liepin", help="Liepin read-only Beta probe commands")
    liepin_sub = liepin.add_subparsers(dest="liepin_command", required=True)
    liepin_login = liepin_sub.add_parser("login", help="Check or guide Liepin login for read-only collection")
    _add_liepin_login_args(liepin_login)
    liepin_collect = liepin_sub.add_parser("collect", help="Parse a saved Liepin fixture into Job Agent jobs")
    _add_liepin_collect_args(liepin_collect)
    liepin_rank = liepin_sub.add_parser("rank", help="Rank Liepin read-only jobs; cloud by default, local with --local")
    _add_rank_args(liepin_rank)
    liepin_greet = liepin_sub.add_parser("greet", help="Liepin read-only greeting commands")
    liepin_greet_sub = liepin_greet.add_subparsers(dest="liepin_greet_command", required=True)
    liepin_preview = liepin_greet_sub.add_parser("preview", help="Preview Liepin greetings without sending")
    _add_liepin_greet_preview_args(liepin_preview)
    liepin_send = liepin_greet_sub.add_parser("send", help="Safe Liepin manual handoff; automatic send is not supported")
    _add_liepin_greet_send_args(liepin_send)
    liepin_apply = liepin_sub.add_parser("apply", help="Liepin manual apply handoff commands")
    liepin_apply_sub = liepin_apply.add_subparsers(dest="liepin_apply_command", required=True)
    liepin_apply_open = liepin_apply_sub.add_parser("open", help="Open Liepin job pages for manual review")
    _add_liepin_apply_open_args(liepin_apply_open)
    liepin_apply_send = liepin_apply_sub.add_parser("send", help="Automatically send/apply to Liepin jobs")
    _add_liepin_apply_send_args(liepin_apply_send)
    liepin_audit = liepin_sub.add_parser("audit", help="Show Liepin beta action audit log")
    liepin_audit.add_argument("--recent", "-n", type=int, default=20, help="Show N most recent Liepin events")

    # ── zhilian ──
    zhilian = sub.add_parser("zhilian", help="Zhilian read-only spike commands")
    zhilian_sub = zhilian.add_subparsers(dest="zhilian_command", required=True)
    zhilian_login = zhilian_sub.add_parser("login", help="Check or guide Zhilian login for read-only collection")
    _add_zhilian_login_args(zhilian_login)
    zhilian_collect = zhilian_sub.add_parser("collect", help="Parse a saved Zhilian fixture or collect visible cards read-only")
    _add_zhilian_collect_args(zhilian_collect)
    zhilian_rank = zhilian_sub.add_parser("rank", help="Rank Zhilian read-only jobs; cloud by default, local with --local")
    _add_rank_args(zhilian_rank)
    zhilian_greet = zhilian_sub.add_parser("greet", help="Zhilian greeting commands")
    zhilian_greet_sub = zhilian_greet.add_subparsers(dest="zhilian_greet_command", required=True)
    zhilian_preview = zhilian_greet_sub.add_parser("preview", help="Preview Zhilian greetings without sending")
    _add_zhilian_greet_preview_args(zhilian_preview)
    zhilian_apply = zhilian_sub.add_parser("apply", help="Zhilian apply handoff/send commands")
    zhilian_apply_sub = zhilian_apply.add_subparsers(dest="zhilian_apply_command", required=True)
    zhilian_apply_open = zhilian_apply_sub.add_parser("open", help="Open Zhilian job pages for manual review")
    _add_zhilian_apply_open_args(zhilian_apply_open)
    zhilian_apply_send = zhilian_apply_sub.add_parser("send", help="Automatically send/apply to Zhilian jobs")
    _add_zhilian_apply_send_args(zhilian_apply_send)
    zhilian_audit = zhilian_sub.add_parser("audit", help="Show Zhilian action audit log")
    zhilian_audit.add_argument("--recent", "-n", type=int, default=20, help="Show N most recent Zhilian events")

    # ── platforms ──
    platforms = sub.add_parser("platforms", help="List recruiting platform support status")
    platforms_sub = platforms.add_subparsers(dest="platforms_command", required=True)
    platforms_status = platforms_sub.add_parser("status", help="Show platform availability and capability boundaries")
    platforms_status.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Optional YAML config with platforms.<name>.enabled overrides")
    platforms_health = platforms_sub.add_parser("health", help="Run lightweight platform health checks")
    platforms_health.add_argument("--platform", "-p", help="Platform key to check (default: all)")
    platforms_health.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Optional YAML config with platforms.<name>.enabled overrides")

    # ── support ──
    support = sub.add_parser("support", help="Voluntary project support commands")
    support_sub = support.add_subparsers(dest="support_command", required=True)
    support_sub.add_parser("star", help="Show the public GitHub repo for optional starring")

    # ── login ──
    login = sub.add_parser("login", help="Boss login state management")
    login.add_argument("--check", action="store_true", help="Check login status and exit (JSON output)")

    # ── resume ──
    resume = sub.add_parser("resume", help="Resume text extraction (agent feeds this to its own LLM)")
    resume_sub = resume.add_subparsers(dest="resume_command", required=True)

    resume_extract = resume_sub.add_parser("extract", help="Extract plain text from resume PDF/DOCX/TXT")
    resume_extract.add_argument("--file", "-f", required=True, help="Resume file path")

    resume_analyze = resume_sub.add_parser("analyze", help="Extract resume + analyze via Cloud API → save 36-field profile (requires `jobagent init`)")
    resume_analyze.add_argument("--file", "-f", required=True, help="Resume file path (PDF/DOCX/TXT/MD)")
    resume_analyze.add_argument("--target-role", help="Optional hint: target role title (helps disambiguate)")
    resume_analyze.add_argument("--target-cities", nargs="*", help="Optional hint: target city names")
    resume_analyze.add_argument("--output", "-o", help="Output profile JSON path (default: ~/.jobagent/state/profile.json)")
    resume_analyze.add_argument("--local", action="store_true", help="Analyze resume locally into the 36-field profile shape without Cloud API/license")

    # ── profile ──
    profile = sub.add_parser("profile", help="Candidate profile management")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)

    profile_save = profile_sub.add_parser("save", help="Save candidate profile JSON (produced by agent LLM)")
    profile_save.add_argument("--data", "-d", required=True, help='Profile JSON string, e.g. \'{"years_experience":5,...}\'')
    profile_save.add_argument("--output", "-o", help="Output JSON path (default: ~/.jobagent/state/profile.json)")

    profile_show = profile_sub.add_parser("show", help="Display current candidate profile")

    profile_edit = profile_sub.add_parser("edit", help="Open profile.json in $EDITOR (default vim) for manual correction")

    # ── pipeline ──
    pipeline = sub.add_parser("pipeline", help="Full pipeline commands")
    pipeline_sub = pipeline.add_subparsers(dest="pipeline_command", required=True)

    pipeline_run = pipeline_sub.add_parser("run", help="Run the full crawl→filter→rank→greet pipeline")
    pipeline_run.add_argument("--config", "-c", required=True, help="Config YAML file path")

    # ── init (Cloud API setup) ──
    init = sub.add_parser("init", help="Configure Cloud API license key + verify connectivity")
    init.add_argument("--key", required=True, help="License key (e.g. jba_live_xxx)")
    init.add_argument("--no-verify", action="store_true", help="Skip /v1/me verification (offline)")

    return parser


# ── City code lookup (Boss直聘 city codes) ──
# Tier-1 + new tier-1 + tier-2 capitals + provincial economic hubs.
_CITY_CODES = {
    # 一线
    "北京": "101010100",
    "上海": "101020100",
    "广州": "101280100",
    "深圳": "101280600",
    # 新一线
    "成都": "101270100",
    "杭州": "101210100",
    "重庆": "101040100",
    "武汉": "101200100",
    "西安": "101110100",
    "苏州": "101190400",
    "天津": "101030100",
    "南京": "101190100",
    "长沙": "101250100",
    "郑州": "101180100",
    "东莞": "101281600",
    "青岛": "101120200",
    "沈阳": "101070100",
    "宁波": "101210400",
    "昆明": "101290100",
    # 二线 / 主流
    "合肥": "101220100",
    "佛山": "101280800",
    "福州": "101230100",
    "厦门": "101230200",
    "哈尔滨": "101050100",
    "济南": "101120100",
    "无锡": "101190200",
    "大连": "101070200",
    "长春": "101060101",
    "石家庄": "101090100",
    "南昌": "101240100",
    "贵阳": "101260100",
    "南宁": "101300100",
    "兰州": "101160100",
    "海口": "101310100",
    "太原": "101100100",
    "呼和浩特": "101080100",
    "乌鲁木齐": "101130101",
    "银川": "101170101",
    "西宁": "101150101",
    "拉萨": "101140101",
}


def _resolve_city(city_name: str) -> tuple[str, str]:
    """Return (city_name, city_code). Raises ValueError if unknown."""
    code = _CITY_CODES.get(city_name)
    if not code:
        raise ValueError(f"Unknown city: {city_name}. Supported: {', '.join(_CITY_CODES)}")
    return city_name, code


# ── Helpers ───────────────────────────────────────────────

def _load_ranked_jobs(path: str) -> list[RankedJob]:
    """Load RankedJob list from a JSON file (supports wrapped or flat format)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and "jobs" in data:
        raw = data["jobs"]
    elif isinstance(data, list):
        raw = data
    else:
        raise ValueError("Unexpected JSON format: expected list or dict with 'jobs' key")

    job_fields = {"name", "salary", "company", "area", "experience", "degree", "skills", "boss", "city", "url", "platform", "raw_data"}
    ranked: list[RankedJob] = []
    for item in raw:
        job_data = {k: v for k, v in item.items() if k in job_fields}
        job = Job(**job_data)
        ranked.append(RankedJob(
            job=job,
            score=item.get("score", 0),
            match_level=item.get("match_level", "low"),
            reasons=item.get("reasons", []),
            risk_flags=item.get("risk_flags", []),
        ))
    return ranked


# ── Command handlers ──────────────────────────────────────

def _cmd_jobs_collect(args: argparse.Namespace) -> None:
    from jobagent.domain.models import Job
    from jobagent.infra.exceptions import LoginRequiredError
    from jobagent.platforms.boss import BossDataDriver

    _ensure_boss_login()

    import random
    import time

    city_name, city_code = _resolve_city(args.city)
    driver = BossDataDriver()
    pages_total = max(1, getattr(args, "pages", 1))
    page_delay = max(0.0, getattr(args, "page_delay", 5.0))
    page_jitter = max(0.0, getattr(args, "page_delay_jitter", 2.0))
    all_jobs = []
    seen_urls: set[str] = set()
    try:
        for offset in range(pages_total):
            # Courteous sleep between pages (we throttle our own requests to be
            # polite to the upstream API). Skip before the first page.
            if offset > 0 and page_delay > 0:
                delay = page_delay + random.uniform(0, page_jitter)
                print(
                    f"  ⏳ sleeping {delay:.1f}s before next page (throttled fetch)",
                    file=sys.stderr,
                )
                time.sleep(delay)

            cur_page = args.page + offset
            page_jobs = driver.fetch_jobs(
                query=args.query,
                city_code=city_code,
                city_name=city_name,
                page=cur_page,
                page_size=args.page_size,
            )
            new_count = 0
            for j in page_jobs:
                if j.url and j.url in seen_urls:
                    continue
                seen_urls.add(j.url) if j.url else None
                all_jobs.append(j)
                new_count += 1
            if pages_total > 1:
                print(f"  page {cur_page}: +{new_count} new jobs (total {len(all_jobs)})", file=sys.stderr)
            if not page_jobs:  # End of results
                break
    except LoginRequiredError as e:
        print(e, file=sys.stderr)
        sys.exit(2)

    payload = {
        "query": args.query,
        "city": city_name,
        "page": args.page,
        "pages": pages_total,
        "count": len(all_jobs),
        "jobs": [j.to_dict() for j in all_jobs],
    }
    jobs = all_jobs
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Saved {len(jobs)} jobs → {args.output}")
    else:
        print(output)


def _cmd_liepin_collect(args: argparse.Namespace) -> None:
    from jobagent.platforms.liepin import (
        LiepinReadOnlyCollector,
        LiepinSessionGuide,
        collect_liepin_fixture,
        write_liepin_snapshot,
    )

    exit_code = 0
    if args.fixture:
        jobs = collect_liepin_fixture(args.fixture, city_name=args.city)
        payload = {
            "ok": True,
            "platform": "liepin",
            "mode": "fixture",
            "query": args.query,
            "city": args.city,
            "page": max(1, args.page),
            "pages": max(1, args.pages),
            "count": len(jobs),
            "jobs": [job.to_dict() for job in jobs],
            "next_suggested": "jobagent liepin rank --input <liepin.raw.json> --output <liepin.ranked.json>",
        }
    else:
        if not args.query:
            _print_json({
                "ok": False,
                "platform": "liepin",
                "error": "liepin_query_required",
                "message": "Live read-only Liepin collect requires --query.",
                "next_suggested": "jobagent liepin collect --query <关键词> --city <城市> --pages 1",
            })
            sys.exit(2)

        driver = None
        if not args.skip_login_check:
            from jobagent.drivers.boss import create_driver

            driver = create_driver()
            status = LiepinSessionGuide(driver=driver).check(
                query=args.query,
                city=args.city,
                wait_seconds=max(1, args.wait_seconds),
            )
            if not status.logged_in:
                payload = status.to_dict()
                payload["platform"] = "liepin"
                payload["mode"] = "login_check"
                payload["query"] = args.query
                payload["city"] = args.city
                payload["error"] = payload.get("error") or "liepin_login_required"
                output = json.dumps(payload, ensure_ascii=False, indent=2)
                if args.output:
                    Path(args.output).write_text(output, encoding="utf-8")
                    print(f"Saved Liepin login-check payload -> {args.output}")
                else:
                    print(output)
                if payload.get("requires_user_action") and payload.get("user_prompt"):
                    print(payload["user_prompt"], file=sys.stderr)
                    if payload.get("next_suggested"):
                        print(f"Next: {payload['next_suggested']}", file=sys.stderr)
                sys.exit(2)

        result = LiepinReadOnlyCollector(driver=driver).collect(
            query=args.query,
            city=args.city,
            limit=max(1, args.limit),
            wait_seconds=max(1, args.wait_seconds),
            page=max(1, args.page),
            pages=max(1, args.pages),
            page_delay=max(0.0, args.page_delay),
        )
        payload = result.to_payload(include_snapshot=args.include_snapshot)
        if result.ok and args.output:
            payload["next_suggested"] = f"jobagent liepin rank --input {args.output} --output <liepin.ranked.json>"
        if args.snapshot_output:
            write_liepin_snapshot(args.snapshot_output, result.snapshot)
        if not result.ok:
            exit_code = 2

    output = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Saved {payload['count']} Liepin jobs -> {args.output}")
    else:
        print(output)
    if exit_code:
        if payload.get("requires_user_action") and payload.get("user_prompt"):
            print(payload["user_prompt"], file=sys.stderr)
            if payload.get("next_suggested"):
                print(f"Next: {payload['next_suggested']}", file=sys.stderr)
        sys.exit(exit_code)


def _cmd_zhilian_collect(args: argparse.Namespace) -> None:
    from jobagent.platforms.zhilian import (
        ZhilianReadOnlyCollector,
        ZhilianSessionGuide,
        collect_zhilian_fixture,
        write_zhilian_snapshot,
    )

    exit_code = 0
    if args.fixture:
        jobs = collect_zhilian_fixture(args.fixture, city_name=args.city)
        payload = {
            "ok": True,
            "platform": "zhilian",
            "mode": "fixture",
            "query": args.query,
            "city": args.city,
            "page": max(1, args.page),
            "pages": max(1, args.pages),
            "count": len(jobs),
            "jobs": [job.to_dict() for job in jobs],
            "next_suggested": "jobagent zhilian rank --input <zhilian.raw.json> --output <zhilian.ranked.json>",
        }
    else:
        if not args.query:
            _print_json({
                "ok": False,
                "platform": "zhilian",
                "error": "zhilian_query_required",
                "message": "Live read-only Zhilian collect requires --query.",
                "next_suggested": "jobagent zhilian collect --query <关键词> --city <城市> --pages 1",
            })
            sys.exit(2)

        driver = None
        from jobagent.drivers.boss import create_driver

        driver = create_driver()
        status = ZhilianSessionGuide(driver=driver).check(
            query=args.query,
            city=args.city,
            wait_seconds=max(1, args.wait_seconds),
        )
        if not status.logged_in:
            payload = status.to_dict()
            payload["platform"] = "zhilian"
            payload["mode"] = "login_check"
            payload["query"] = args.query
            payload["city"] = args.city
            payload["error"] = payload.get("error") or "zhilian_login_required"
            output = json.dumps(payload, ensure_ascii=False, indent=2)
            if args.output:
                Path(args.output).write_text(output, encoding="utf-8")
                print(f"Saved Zhilian login-check payload -> {args.output}")
            else:
                print(output)
            if payload.get("requires_user_action") and payload.get("user_prompt"):
                print(payload["user_prompt"], file=sys.stderr)
                if payload.get("next_suggested"):
                    print(f"Next: {payload['next_suggested']}", file=sys.stderr)
            sys.exit(2)

        result = ZhilianReadOnlyCollector(driver=driver).collect(
            query=args.query,
            city=args.city,
            limit=max(1, args.limit),
            wait_seconds=max(1, args.wait_seconds),
            page=max(1, args.page),
            pages=max(1, args.pages),
            page_delay=max(0.0, args.page_delay),
            detail_limit=max(0, args.detail_limit),
        )
        payload = result.to_payload(include_snapshot=args.include_snapshot)
        if result.ok and args.output:
            payload["next_suggested"] = f"jobagent zhilian rank --input {args.output} --output <zhilian.ranked.json>"
        if args.snapshot_output:
            write_zhilian_snapshot(args.snapshot_output, result.snapshot)
        if not result.ok:
            exit_code = 2

    output = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Saved {payload['count']} Zhilian jobs -> {args.output}")
    else:
        print(output)
    if exit_code:
        if payload.get("requires_user_action") and payload.get("user_prompt"):
            print(payload["user_prompt"], file=sys.stderr)
            if payload.get("next_suggested"):
                print(f"Next: {payload['next_suggested']}", file=sys.stderr)
        sys.exit(exit_code)


def _cmd_zhilian_login(args: argparse.Namespace) -> None:
    from jobagent.platforms.zhilian import ZhilianSessionGuide

    guide = ZhilianSessionGuide()
    if args.check:
        status = guide.check(
            query=args.query,
            city=args.city,
            wait_seconds=max(1, args.wait_seconds),
        )
        _print_json(status.to_dict())
        if not status.logged_in:
            prompt_payload = status.to_dict()
            if prompt_payload.get("user_prompt"):
                print(prompt_payload["user_prompt"], file=sys.stderr)
            if prompt_payload.get("next_suggested"):
                print(f"Next: {prompt_payload['next_suggested']}", file=sys.stderr)
            sys.exit(2)
        return

    status = guide.wait_for_login(
        timeout=max(1, args.timeout),
        poll_interval=max(1, args.poll_interval),
        wait_seconds=max(1, args.wait_seconds),
    )
    _print_json(status.to_dict())
    if not status.logged_in:
        prompt_payload = status.to_dict()
        if prompt_payload.get("user_prompt"):
            print(prompt_payload["user_prompt"], file=sys.stderr)
        sys.exit(2)


def _cmd_liepin_login(args: argparse.Namespace) -> None:
    from jobagent.platforms.liepin import LiepinSessionGuide

    guide = LiepinSessionGuide()
    if args.check:
        status = guide.check(
            query=args.query,
            city=args.city,
            wait_seconds=max(1, args.wait_seconds),
        )
        _print_json(status.to_dict())
        if status.login_required:
            prompt_payload = status.to_dict()
            print(prompt_payload["user_prompt"], file=sys.stderr)
            print(f"Next: {prompt_payload['next_suggested']}", file=sys.stderr)
        sys.exit(0 if status.logged_in else 2)

    print(
        "\nLiepin login guide: opening a dedicated browser page for read-only collection.\n"
        "Complete login in the browser window. Job Agent will only poll login state;\n"
        "it will not apply, send messages, or upload cookies.\n",
        file=sys.stderr,
    )
    status = guide.wait_for_login(
        timeout=max(1, args.timeout),
        poll_interval=max(1, args.poll_interval),
        wait_seconds=max(1, args.wait_seconds),
    )
    _print_json(status.to_dict())
    if status.login_required:
        prompt_payload = status.to_dict()
        print(prompt_payload["user_prompt"], file=sys.stderr)
        print(f"Next: {prompt_payload['next_suggested']}", file=sys.stderr)
    sys.exit(0 if status.logged_in else 2)


def _cmd_doctor_env(args: argparse.Namespace) -> None:
    """Check Python version, Chrome installed, network to api, license configured."""
    import platform
    from jobagent.drivers.boss.chrome_manager import find_chrome
    from jobagent.infra import cloud_client
    from jobagent.infra.credentials import api_base_url, load_license_key

    checks: list[dict] = []
    system = platform.system()  # "Darwin" / "Linux" / "Windows"

    # 1. Python version (>=3.11)
    py = sys.version_info
    py_ok = (py.major, py.minor) >= (3, 11)
    py_hint_by_os = {
        "Darwin": "brew install python@3.12  (or download from python.org)",
        "Linux": "Use your distro package manager (e.g. apt install python3.12) or pyenv",
        "Windows": "winget install Python.Python.3.12  (or download from https://python.org)",
    }
    checks.append({
        "name": "python_version",
        "ok": py_ok,
        "value": f"{py.major}.{py.minor}.{py.micro}",
        "hint": None if py_ok else f"Need Python 3.11+. {py_hint_by_os.get(system, 'Install Python 3.11 or newer.')}",
    })

    # 2. OS
    checks.append({"name": "platform", "ok": True, "value": platform.platform()})

    # 3. Chrome installed — reuse the same detection logic as the runtime driver
    chrome_path = find_chrome()
    if chrome_path:
        checks.append({"name": "chrome", "ok": True, "value": chrome_path})
    else:
        chrome_hint_by_os = {
            "Darwin": "Install from https://www.google.com/chrome/ (drag into /Applications).",
            "Linux": "Install via your distro: apt install google-chrome-stable, or download from google.com/chrome.",
            "Windows": "Install from https://www.google.com/chrome/ (default install location auto-detected).",
        }
        checks.append({
            "name": "chrome",
            "ok": False,
            "value": None,
            "hint": chrome_hint_by_os.get(system, "Install Google Chrome from https://www.google.com/chrome/"),
        })

    # 4. License configured
    key = load_license_key()
    checks.append({
        "name": "license_key",
        "ok": bool(key),
        "value": (key[:14] + "...") if key else None,
        "hint": None if key else "Run `jobagent init --key jba_live_xxx`",
    })

    # 5. Network to API
    api_ok, api_msg = False, None
    try:
        cloud_client.health()
        api_ok, api_msg = True, "reachable"
    except cloud_client.CloudError as e:
        api_msg = str(e)
    checks.append({
        "name": "api_reachable",
        "ok": api_ok,
        "value": api_base_url(),
        "hint": None if api_ok else f"Cannot reach API: {api_msg}",
    })

    # 6. License verifies (only if key configured + api reachable)
    if key and api_ok:
        try:
            cloud_client.me()
            checks.append({"name": "license_valid", "ok": True})
        except cloud_client.CloudError as e:
            checks.append({
                "name": "license_valid",
                "ok": False,
                "hint": f"License rejected by server: {e}",
            })

    all_ok = all(c["ok"] for c in checks)
    _print_json({"ok": all_ok, "checks": checks})
    if not all_ok:
        print("\n❌ Some checks failed. See `hint` fields above for fixes.\n", file=sys.stderr)
        sys.exit(1)
    print("\n✅ All checks passed.\n", file=sys.stderr)


def _cmd_init(args: argparse.Namespace) -> None:
    """Save license key and verify connectivity by calling /v1/me."""
    from jobagent.infra import cloud_client
    from jobagent.infra.credentials import save_license_key, api_base_url

    path = save_license_key(args.key)

    if args.no_verify:
        _print_json({"ok": True, "saved_to": str(path), "verified": False, "api_base": api_base_url()})
        return

    try:
        info = cloud_client.me()
    except cloud_client.CloudError as e:
        _print_json({
            "ok": False,
            "saved_to": str(path),
            "verified": False,
            "error": str(e),
            "status": e.status,
            "code": e.code,
            "hint": cloud_client.hint_for(e.code),
        })
        sys.exit(3)

    _print_json({
        "ok": True,
        "saved_to": str(path),
        "verified": True,
        "api_base": api_base_url(),
        "license": info.get("license", {}),
        "server": info.get("server", {}),
        "next_suggested": "jobagent resume analyze --file <path-to-your-resume.pdf>",
    })
    print(
        "\n✅ License configured. Next: analyze your resume with\n"
        "   jobagent resume analyze --file <path-to-your-resume.pdf>\n",
        file=sys.stderr,
    )


def _profile_for_cloud() -> dict:
    """Load profile.json for cloud calls.

    The cloud /v1/jobs/rank and /v1/greet/generate expect the 36-field shape
    produced by /v1/resume/analyze (saved via `jobagent resume analyze`).
    If the profile is the legacy 7-field shape, prompt the user to re-run
    resume analyze.
    """
    from jobagent.infra.state import load_json, profile_path

    data = load_json(profile_path())
    if not data:
        _print_json({
            "ok": False,
            "error": "No profile found. Run `jobagent resume analyze --file <path>` first.",
        })
        sys.exit(1)
    if "basic" not in data and "preferences" not in data:
        _print_json({
            "ok": False,
            "error": (
                "Profile is in legacy 7-field shape; cloud needs the 36-field shape. "
                "Run `jobagent resume analyze --file <resume>` to regenerate."
            ),
        })
        sys.exit(1)
    return data


def _cmd_jobs_rank_cloud(
    args: argparse.Namespace,
    raw_jobs: list[dict],
    source_platform: str = "",
) -> None:
    """Rank via Cloud API in batches of 15 (cloud BATCH_LIMIT)."""
    from jobagent.infra import cloud_client

    profile = _profile_for_cloud()

    BATCH = 15
    all_ranked: list[dict] = []
    source_by_cloud_id: dict[str, dict] = {}
    total_in = total_out = 0
    for i in range(0, len(raw_jobs), BATCH):
        batch = raw_jobs[i : i + BATCH]
        # Map common field aliases the cloud expects (id/title required)
        cloud_jobs = []
        for idx, j in enumerate(batch):
            cloud_id = str(j.get("id") or j.get("encryptId") or j.get("securityId") or f"local-{i + idx}")
            source_by_cloud_id[cloud_id] = j
            cloud_jobs.append({
                "id": cloud_id,
                "title": j.get("title") or j.get("jobName") or j.get("name") or "",
                "company": j.get("company") or j.get("brandName"),
                "area": j.get("area") or j.get("areaDistrict") or j.get("city"),
                "salary": j.get("salary") or j.get("salaryDesc"),
                "experience": j.get("experience") or j.get("jobExperience"),
                "degree": j.get("degree") or j.get("jobDegree"),
                "skills": j.get("skills") or ", ".join(j.get("skill_list", []) or []),
                "company_size": j.get("company_size") or j.get("brandScaleName"),
                "industry": j.get("industry") or j.get("brandIndustry"),
                "url": j.get("url"),
                "security_id": j.get("securityId") or j.get("security_id"),
                "jd": j.get("jd") or j.get("postDescription"),
            })
        try:
            resp = cloud_client.jobs_rank(profile, cloud_jobs)
        except cloud_client.CloudError as e:
            _print_json({
                "ok": False,
                "stage": "rank",
                "batch_start": i,
                "error": str(e),
                "status": e.status,
                "code": e.code,
                "hint": cloud_client.hint_for(e.code),
            })
            sys.exit(3)
        all_ranked.extend(resp.get("ranked", []))
        # Note: usage tokens not returned in response; tracked server-side only

    # Sort across batches (each batch sorted internally)
    all_ranked.sort(key=lambda r: r.get("score", 0), reverse=True)
    if args.top:
        all_ranked = all_ranked[: args.top]

    # Map cloud fields → local Job shape so downstream `jobagent boss greet preview/send`
    # work without re-parsing. Preserve cloud-specific fields (score,
    # recommendation, matches, risks) as decoration.
    mapped_jobs = []
    for r in all_ranked:
        source = source_by_cloud_id.get(str(r.get("id") or ""), {})
        mapped_jobs.append({
            "name": r.get("title"),
            "salary": r.get("salary"),
            "company": r.get("company"),
            "area": r.get("area"),
            "city": source.get("city") or r.get("city") or r.get("area") or "",
            "experience": r.get("experience"),
            "degree": r.get("degree"),
            "skills": r.get("skills"),
            "boss": source.get("boss", ""),
            "url": r.get("url"),
            "platform": source.get("platform") or source_platform or "zhipin",
            "score": r.get("score", 0),
            "match_level": r.get("recommendation") or "",
            "reasons": [r["matches"]] if r.get("matches") else [],
            "risk_flags": [r["risks"]] if r.get("risks") else [],
            "cloud_id": r.get("id"),
            "cloud_recommendation": r.get("recommendation"),
        })

    payload = {
        "input": str(args.input),
        "total": len(raw_jobs),
        "ranked": len(mapped_jobs),
        "via": "cloud",
        "jobs": mapped_jobs,
    }
    if source_platform == "liepin":
        ranked_path = str(args.output) if args.output else "<liepin.ranked.json>"
        payload["platform"] = "liepin"
        payload["next_suggested"] = (
            f"jobagent liepin greet preview --input {ranked_path} "
            f"--limit {min(args.top or len(mapped_jobs), len(mapped_jobs) or 1)}"
        )
    elif source_platform == "zhilian":
        ranked_path = str(args.output) if args.output else "<zhilian.ranked.json>"
        payload["platform"] = "zhilian"
        payload["next_suggested"] = (
            f"jobagent zhilian greet preview --input {ranked_path} "
            f"--limit {min(args.top or len(mapped_jobs), len(mapped_jobs) or 1)}"
        )
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Ranked {len(all_ranked)}/{len(raw_jobs)} jobs (via cloud) → {args.output}")
    else:
        print(output)


def _cmd_jobs_rank(args: argparse.Namespace) -> None:
    """Rank crawled Boss jobs via cloud by default, or local rules with --local."""
    _, raw_jobs = _load_jobs_payload_or_exit(args.input)

    if getattr(args, "local", False):
        _cmd_jobs_rank_local(args, raw_jobs, source_platform="zhipin")
        return

    _require_license_or_exit("boss rank")

    _cmd_jobs_rank_cloud(args, raw_jobs)


def _cmd_liepin_rank(args: argparse.Namespace) -> None:
    """Rank Liepin read-only collected jobs via Cloud AI. License required."""
    _, raw_jobs = _load_jobs_payload_or_exit(args.input)
    _require_jobs_platform_or_exit(
        raw_jobs,
        platform="liepin",
        error="liepin_rank_input_platform_mismatch",
        message="Liepin rank expects jobs produced by `jobagent liepin collect`.",
    )
    if getattr(args, "local", False):
        _cmd_liepin_rank_local(args, raw_jobs)
        return

    _require_license_or_exit("liepin rank")
    _cmd_jobs_rank_cloud(args, raw_jobs, source_platform="liepin")


def _cmd_liepin_rank_local(args: argparse.Namespace, raw_jobs: list[dict]) -> None:
    """Rank Liepin jobs locally for internal platform-chain validation."""
    _cmd_jobs_rank_local(args, raw_jobs, source_platform="liepin")


def _cmd_zhilian_rank(args: argparse.Namespace) -> None:
    """Rank Zhilian read-only collected jobs via Cloud AI, or locally with --local."""
    _, raw_jobs = _load_jobs_payload_or_exit(args.input)
    _require_jobs_platform_or_exit(
        raw_jobs,
        platform="zhilian",
        error="zhilian_rank_input_platform_mismatch",
        message="Zhilian rank expects jobs produced by `jobagent zhilian collect`.",
    )
    if getattr(args, "local", False):
        _cmd_jobs_rank_local(args, raw_jobs, source_platform="zhilian")
        return

    _require_license_or_exit("zhilian rank")
    _cmd_jobs_rank_cloud(args, raw_jobs, source_platform="zhilian")


def _cmd_jobs_rank_local(
    args: argparse.Namespace,
    raw_jobs: list[dict],
    source_platform: str = "zhipin",
) -> None:
    """Rank jobs locally for internal platform-chain validation."""
    from jobagent.domain.ranking import RankingEngine

    profile = _candidate_profile_for_local(args.config)
    jobs = [_job_from_common_dict(item, platform=source_platform) for item in raw_jobs]
    ranked = RankingEngine(profile).rank(jobs, top_n=max(1, args.top or len(jobs)))
    mapped_jobs = [item.to_dict() for item in ranked]
    context = _load_local_profile_context()
    if context:
        for item in mapped_jobs:
            evidence = _local_match_evidence(item, context)
            if evidence:
                item.setdefault("reasons", [])
                item["reasons"] = (item["reasons"] + evidence)[:4]
                item["profile_evidence"] = evidence
    platform_label = source_platform or "zhipin"
    output_path = str(args.output) if args.output else f"<{platform_label}.ranked.json>"
    if source_platform == "liepin":
        next_suggested = (
            f"jobagent liepin greet preview --local --input {output_path} "
            f"--limit {min(args.top or len(mapped_jobs), len(mapped_jobs) or 1)}"
        )
    elif source_platform == "zhilian":
        next_suggested = (
            f"jobagent zhilian greet preview --local --input {output_path} "
            f"--limit {min(args.top or len(mapped_jobs), len(mapped_jobs) or 1)}"
        )
    elif source_platform in ("", "zhipin"):
        next_suggested = (
            f"jobagent boss greet preview --local --input {output_path} "
            f"--limit {min(args.top or len(mapped_jobs), len(mapped_jobs) or 1)}"
        )
    else:
        next_suggested = f"Review {output_path}; {platform_label} greet/apply is not implemented yet."
    payload = {
        "input": str(args.input),
        "total": len(raw_jobs),
        "ranked": len(mapped_jobs),
        "via": "local",
        "profile_via": "local_36_fields" if context else "candidate_summary",
        "platform": platform_label,
        "jobs": mapped_jobs,
        "next_suggested": next_suggested,
    }
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Ranked {len(mapped_jobs)}/{len(raw_jobs)} {platform_label} jobs (local) → {args.output}")
    else:
        print(output)


def _candidate_profile_for_local(config_path: str | Path | None) -> CandidateProfile:
    if config_path and Path(config_path).exists():
        from jobagent.infra.config import Config

        return CandidateProfile.from_config(Config.from_yaml(config_path))
    return CandidateProfile()


def _job_from_common_dict(item: dict[str, Any], platform: str = "zhipin") -> Job:
    skills = item.get("skills") or item.get("skill_list") or item.get("skillLabels") or []
    if isinstance(skills, list):
        skills = ", ".join(str(skill) for skill in skills if skill not in (None, ""))
    return Job(
        name=str(item.get("name") or item.get("title") or item.get("jobName") or item.get("jobTitle") or ""),
        salary=str(item.get("salary") or item.get("salaryDesc") or item.get("salaryText") or ""),
        company=str(item.get("company") or item.get("brandName") or item.get("companyName") or ""),
        area=str(item.get("area") or item.get("areaDistrict") or item.get("district") or ""),
        experience=str(item.get("experience") or item.get("jobExperience") or item.get("workYear") or ""),
        degree=str(item.get("degree") or item.get("jobDegree") or item.get("education") or ""),
        skills=str(skills or ""),
        boss=str(item.get("boss") or item.get("recruiterName") or item.get("hrName") or ""),
        city=str(item.get("city") or item.get("cityName") or item.get("dq") or item.get("area") or ""),
        url=str(item.get("url") or item.get("jobUrl") or item.get("pcUrl") or ""),
        platform=str(item.get("platform") or platform),
        raw_data=item.get("raw_data") if isinstance(item.get("raw_data"), dict) else item,
    )


def _load_jobs_payload_or_exit(path: str) -> tuple[object, list]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data, data
    if isinstance(data, dict) and "jobs" in data:
        return data, data["jobs"]
    print("Error: unexpected JSON format", file=sys.stderr)
    sys.exit(1)


def _require_jobs_platform_or_exit(
    raw_jobs: list,
    platform: str,
    error: str,
    message: str,
) -> None:
    invalid = [
        idx
        for idx, job in enumerate(raw_jobs)
        if not isinstance(job, dict) or job.get("platform") != platform
    ]
    if invalid:
        _print_json({
            "ok": False,
            "platform": platform,
            "error": error,
            "message": message,
            "invalid_indexes": invalid[:10],
        })
        sys.exit(2)


def _cmd_greet_preview_cloud(args: argparse.Namespace, next_message: str | None = None) -> None:
    """Generate greeting per job via Cloud /v1/greet/generate (sequential).

    Saves the input ranked.json with a per-job `cloud_greeting` field injected
    so that `jobagent boss greet send --input <output>` will use these greetings instead of
    the local template (closes GAP-15: preview/send data flow disconnect).
    """
    from jobagent.infra import cloud_client

    profile = _profile_for_cloud()

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    is_wrapped = isinstance(data, dict) and "jobs" in data
    raw_jobs = data["jobs"] if is_wrapped else data
    selected = raw_jobs[: args.limit]

    print(f"Generating {len(selected)} greetings via cloud...")
    succeeded = 0
    for i, j in enumerate(selected, 1):
        title = j.get("title") or j.get("name") or j.get("jobName") or ""
        company = j.get("company") or j.get("brandName") or "?"
        cloud_job = {
            "id": str(j.get("id") or j.get("cloud_id") or j.get("encryptId") or f"j{i}"),
            "title": title,
            "company": company,
            "salary": j.get("salary") or j.get("salaryDesc"),
            "skills": j.get("skills") or ", ".join(j.get("skill_list", []) or []),
            "jd": j.get("jd") or j.get("postDescription") or j.get("description"),
        }
        try:
            resp = cloud_client.greet_generate(profile, cloud_job)
            msg = resp.get("message", "")
            chars = resp.get("char_count", 0)
            # Inject back into the original job dict so it persists
            j["cloud_greeting"] = msg
            j["cloud_greeting_chars"] = chars
            print(f"\n[{i}/{len(selected)}] {title} @ {company}")
            print(f"    Chars: {chars}")
            print(f"    Msg:   {msg}")
            succeeded += 1
        except cloud_client.CloudError as e:
            j["cloud_greeting_error"] = str(e)
            j["cloud_greeting_code"] = e.code
            hint = cloud_client.hint_for(e.code)
            print(f"\n[{i}/{len(selected)}] {title} @ {company}")
            print(f"    ERROR: {e}", file=sys.stderr)
            if hint:
                print(f"    HINT:  {hint}", file=sys.stderr)

    # Write the enriched ranked file back to disk so `jobagent boss greet send` can read it.
    output_path = Path(args.output) if args.output else Path(args.input).with_suffix(".with_greetings.json")
    payload = data if is_wrapped else {"jobs": raw_jobs}
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'=' * 50}")
    print(f"Preview complete (cloud): {succeeded}/{len(selected)} succeeded")
    print(f"Saved with greetings → {output_path}")
    if next_message is None:
        next_message = f"Next: jobagent boss greet send --input {output_path} --limit {args.limit}"
    print(next_message.format(output_path=output_path, limit=args.limit))
    print(f"{'=' * 50}")


def _cmd_greet_preview(args: argparse.Namespace) -> None:
    """Generate Boss greeting previews via cloud by default, or template locally."""
    data, raw_jobs = _load_jobs_payload_or_exit(args.input)
    if getattr(args, "local", False):
        _cmd_greet_preview_local(args, data, raw_jobs, source_platform="zhipin")
        return

    _require_license_or_exit("boss greet preview")
    _cmd_greet_preview_cloud(args)


def _cmd_liepin_greet_preview(args: argparse.Namespace) -> None:
    """Generate Liepin greeting previews via cloud without sending."""
    data, raw_jobs = _load_jobs_payload_or_exit(args.input)
    _require_jobs_platform_or_exit(
        raw_jobs,
        platform="liepin",
        error="liepin_greet_preview_input_platform_mismatch",
        message="Liepin greet preview expects jobs produced by `jobagent liepin rank`.",
    )
    if getattr(args, "local", False):
        _cmd_liepin_greet_preview_local(args, data, raw_jobs)
        return

    _require_license_or_exit("liepin greet preview")
    _cmd_greet_preview_cloud(
        args,
        next_message=(
            "Next: review {output_path}, then run "
            "`jobagent liepin apply open --input {output_path} --limit {limit}`. "
            "Automatic Liepin send is not supported."
        ),
    )


def _cmd_liepin_greet_preview_local(
    args: argparse.Namespace,
    data: object,
    raw_jobs: list[dict],
) -> None:
    _cmd_greet_preview_local(args, data, raw_jobs, source_platform="liepin")


def _cmd_zhilian_greet_preview(args: argparse.Namespace) -> None:
    """Generate Zhilian greeting previews via cloud without sending."""
    data, raw_jobs = _load_jobs_payload_or_exit(args.input)
    _require_jobs_platform_or_exit(
        raw_jobs,
        platform="zhilian",
        error="zhilian_greet_preview_input_platform_mismatch",
        message="Zhilian greet preview expects jobs produced by `jobagent zhilian rank`.",
    )
    if getattr(args, "local", False):
        _cmd_greet_preview_local(args, data, raw_jobs, source_platform="zhilian")
        return

    _require_license_or_exit("zhilian greet preview")
    _cmd_greet_preview_cloud(
        args,
        next_message=(
            "Next: review {output_path}, then run "
            "`jobagent zhilian apply open --input {output_path} --limit {limit}` "
            "or `jobagent zhilian apply send --input {output_path} --limit {limit} --require-greeting --dry-run`."
        ),
    )


def _cmd_greet_preview_local(
    args: argparse.Namespace,
    data: object,
    raw_jobs: list[dict],
    source_platform: str = "zhipin",
) -> None:
    selected = raw_jobs[: max(1, args.limit)]
    greeter_config = _greeter_config_for_local(args.config)
    platform_label = {
        "liepin": "Liepin",
        "zhilian": "Zhilian",
        "zhipin": "Boss",
    }.get(source_platform, source_platform or "Boss")

    print(f"Generating {len(selected)} {platform_label} greetings locally...")
    for i, job in enumerate(selected, 1):
        message = _local_greeting(job, greeter_config, source_platform=source_platform)
        job["greeting"] = message
        job["greeting_source"] = "local"
        job["greeting_chars"] = len(message)
        print(f"\n[{i}/{len(selected)}] {job.get('name') or job.get('title') or ''} @ {job.get('company') or '?'}")
        print(f"    Chars: {len(message)}")
        print(f"    Msg:   {message}")

    output_path = Path(args.output) if args.output else Path(args.input).with_suffix(".with_greetings.json")
    payload = data if isinstance(data, dict) else {"jobs": raw_jobs}
    if isinstance(payload, dict):
        payload["via"] = payload.get("via") or "local"
        payload["greeting_via"] = "local"
        payload["platform"] = source_platform if source_platform else "zhipin"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    next_command = (
        f"jobagent liepin apply send --input {output_path} --limit {args.limit} --require-greeting"
        if source_platform == "liepin"
        else f"jobagent zhilian apply send --input {output_path} --limit {args.limit} --require-greeting"
        if source_platform == "zhilian"
        else f"jobagent boss greet send --input {output_path} --limit {args.limit}"
    )
    print(f"\n{'=' * 50}")
    print(f"Preview complete (local): {len(selected)}/{len(selected)} succeeded")
    print(f"Saved with greetings → {output_path}")
    print(f"Next: review {output_path}, then run `{next_command}`.")
    print(f"{'=' * 50}")


def _greeter_config_for_local(config_path: str | Path | None) -> GreeterConfig:
    if config_path and Path(config_path).exists():
        from jobagent.infra.config import Config

        return Config.from_yaml(config_path).greeter
    return GreeterConfig()


def _local_liepin_greeting(job: dict[str, Any], greeter_config: GreeterConfig) -> str:
    return _local_greeting(job, greeter_config, source_platform="liepin")


def _local_greeting(
    job: dict[str, Any],
    greeter_config: GreeterConfig,
    source_platform: str = "zhipin",
) -> str:
    normalized = _job_from_common_dict(job, platform=source_platform)
    profile = _load_local_profile_context()
    tailored = _profile_based_greeting(normalized, job, profile)
    if tailored:
        return tailored

    if greeter_config.template:
        message = greeter_config.get_template(normalized).strip()
        if message:
            return message

    boss = _clean_recruiter_name(normalized.boss) or "您好"
    if not boss.endswith("您好"):
        boss = f"{boss}您好"
    reasons = job.get("reasons") or []
    reason = str(reasons[0]) if reasons else f"{normalized.name}方向和我的背景比较匹配"
    return (
        f"{boss}，我关注到贵司的{normalized.name}岗位，{reason}。"
        "方便的话希望进一步沟通，谢谢。"
    )


def _load_local_profile_context() -> dict[str, Any]:
    from jobagent.domain.local_profile import profile_context
    from jobagent.infra.state import load_json, profile_path

    profile = load_json(profile_path()) or {}
    if isinstance(profile, dict) and ("hardSkills" in profile or "career" in profile):
        return profile_context(profile)
    return {}


def _profile_based_greeting(job: Job, raw_job: dict[str, Any], context: dict[str, Any]) -> str:
    if not context:
        return ""
    boss = _clean_recruiter_name(job.boss) or "您好"
    if not boss.endswith("您好"):
        boss = f"{boss}您好"
    job_text = f"{job.name} {job.skills} {job.company} {' '.join(raw_job.get('reasons') or [])}".lower()
    domains = [str(item) for item in context.get("domains", []) if str(item)]
    achievements = [str(item) for item in context.get("achievements", []) if str(item)]
    matched_domains = [item for item in domains if item.lower() in job_text or any(token.lower() in job_text for token in item.split())]
    domain = matched_domains[0] if matched_domains else (domains[0] if domains else "")
    achievement = _pick_compact_achievement(achievements, job_text)
    if achievement and achievement.startswith(("负责", "主导", "搭建", "构建", "推进", "完成")):
        achievement = f"我过往{achievement}"
    reason_text = _best_reason_for_greeting(raw_job.get("reasons") or [])
    reason_sentence = f"{reason_text}。" if reason_text else ""
    career_level = str(context.get("career_level") or "")
    level_text = "也有团队推进经验，" if career_level in ("manager", "director") else ""
    if domain and achievement:
        return f"{boss}，我看到这个{job.name}岗位和{domain}方向很相关。{reason_sentence}{achievement}，{level_text}希望进一步沟通。"
    if domain:
        return f"{boss}，我关注到贵司{job.name}岗位，{reason_sentence}我过往主要做{domain}相关产品，{level_text}希望进一步沟通。"
    if achievement:
        return f"{boss}，我关注到贵司{job.name}岗位。{reason_sentence}{achievement}，希望进一步沟通。"
    return ""


def _clean_recruiter_name(value: str) -> str:
    name = str(value or "").strip()
    if "·" in name:
        name = name.split("·", 1)[0].strip()
    if name.endswith("您好"):
        name = name[:-2]
    return name


def _best_reason_for_greeting(reasons: list[Any]) -> str:
    cleaned = [str(item).strip() for item in reasons if str(item).strip()]
    for reason in cleaned:
        if "36维画像领域匹配" in reason:
            return reason.split("：", 1)[-1].strip()
    for reason in cleaned:
        if "36维画像成果素材" in reason:
            continue
        if not any(skip in reason for skip in ("城市匹配", "薪资匹配", "经验要求")):
            return reason
    return ""


def _local_match_evidence(job: dict[str, Any], context: dict[str, Any]) -> list[str]:
    job_text = f"{job.get('name', '')} {job.get('skills', '')} {job.get('company', '')}".lower()
    evidence: list[str] = []
    for domain in context.get("domains", []) or []:
        domain_text = str(domain)
        if domain_text and (domain_text.lower() in job_text or any(part and part.lower() in job_text for part in domain_text.split())):
            evidence.append(f"36维画像领域匹配：过往有{domain_text}经验")
            break
    for achievement in context.get("achievements", []) or []:
        achievement_text = str(achievement)
        if achievement_text and any(token.lower() in achievement_text.lower() for token in ("AI", "数据", "平台", "产品", "增长")):
            evidence.append(f"36维画像成果素材：{achievement_text[:48]}")
            break
    career_level = str(context.get("career_level") or "")
    if career_level in ("manager", "director"):
        evidence.append("36维画像层级匹配：具备团队管理/负责人经历")
    return evidence[:2]


def _pick_compact_achievement(achievements: list[str], job_text: str) -> str:
    if not achievements:
        return ""
    scored: list[tuple[int, str]] = []
    for item in achievements:
        score = 0
        if re.search(r"\d", item):
            score += 2
        if any(phrase in item for phrase in ("该公司", "其他各类事务")):
            score -= 3
        if any(phrase in item for phrase in ("整体数据平台", "数据平台系统", "用户画像", "AI要素")):
            score += 2
        for token in ("AI", "数据", "平台", "产品", "增长", "用户", "项目"):
            if token.lower() in item.lower() and token.lower() in job_text:
                score += 1
        scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    text = scored[0][1]
    return _compact_sentence(text, 68)


def _compact_sentence(text: str, limit: int) -> str:
    text = str(text).strip().rstrip("，。；;")
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip("，、；; ")
    return f"{cut}…"


def _cmd_liepin_greet_send(args: argparse.Namespace) -> None:
    """Reject automatic Liepin sending and point to the manual handoff path."""
    _, raw_jobs = _load_jobs_payload_or_exit(args.input)
    _require_jobs_platform_or_exit(
        raw_jobs,
        platform="liepin",
        error="liepin_greet_send_input_platform_mismatch",
        message="Liepin greet send expects jobs produced by `jobagent liepin greet preview`.",
    )

    payload = {
        "ok": False,
        "platform": "liepin",
        "mode": "manual_handoff_required",
        "error": "liepin_automatic_send_not_supported",
        "message": "Liepin automatic greeting send is not supported. Use manual apply-open handoff instead.",
        "input": args.input,
        "limit": max(1, args.limit),
        "next_suggested": (
            f"jobagent liepin apply open --input {args.input} "
            f"--limit {max(1, args.limit)}"
            + (" --dry-run" if args.dry_run else "")
        ),
    }
    _print_json(payload)
    sys.exit(2)


def _cmd_liepin_apply_open(args: argparse.Namespace) -> None:
    from jobagent.platforms.liepin import LiepinApplyOpener, LiepinSessionGuide

    _, raw_jobs = _load_jobs_payload_or_exit(args.input)
    _require_jobs_platform_or_exit(
        raw_jobs,
        platform="liepin",
        error="liepin_apply_open_input_platform_mismatch",
        message="Liepin apply open expects jobs produced by `jobagent liepin rank` or `jobagent liepin greet preview`.",
    )

    selected = raw_jobs[max(0, args.start): max(0, args.start) + max(1, args.limit)]
    missing_greetings = _jobs_missing_greeting_indexes(selected, offset=max(0, args.start))
    if missing_greetings and args.require_greeting:
        _print_json({
            "ok": False,
            "platform": "liepin",
            "mode": "manual_apply_open",
            "error": "liepin_apply_open_missing_greeting",
            "message": "Selected Liepin jobs are missing greeting handoff text. Run `jobagent liepin greet preview` first, or omit --require-greeting.",
            "missing_indexes": missing_greetings,
            "next_suggested": f"jobagent liepin greet preview --input {args.input}",
        })
        sys.exit(2)

    driver = None
    if not args.dry_run and not args.skip_login_check:
        from jobagent.drivers.boss import create_driver

        driver = create_driver()
        status = LiepinSessionGuide(driver=driver).check(wait_seconds=max(1, args.wait_seconds))
        if not status.logged_in:
            payload = status.to_dict()
            payload["platform"] = "liepin"
            payload["mode"] = "apply_open_login_check"
            payload["input"] = args.input
            payload["error"] = payload.get("error") or "liepin_login_required"
            _print_json(payload)
            if payload.get("requires_user_action") and payload.get("user_prompt"):
                print(payload["user_prompt"], file=sys.stderr)
                if payload.get("next_suggested"):
                    print(f"Next: {payload['next_suggested']}", file=sys.stderr)
            sys.exit(2)

    result = LiepinApplyOpener(driver=driver).open_jobs(
        raw_jobs,
        limit=max(1, args.limit),
        start=max(0, args.start),
        wait_seconds=max(1, args.wait_seconds),
        dry_run=bool(args.dry_run),
    )
    payload = result.to_payload()
    if missing_greetings:
        payload["warning"] = "selected_jobs_missing_greeting"
        payload["missing_greeting_indexes"] = missing_greetings
        payload["next_suggested"] = "Run `jobagent liepin greet preview` before manual apply-open for greeting handoff."
    _print_json(payload)
    sys.exit(0 if result.ok else 2)


def _cmd_liepin_apply_send(args: argparse.Namespace) -> None:
    from jobagent.platforms.liepin import LiepinApplySender, LiepinSessionGuide

    _, raw_jobs = _load_jobs_payload_or_exit(args.input)
    _require_jobs_platform_or_exit(
        raw_jobs,
        platform="liepin",
        error="liepin_apply_send_input_platform_mismatch",
        message="Liepin apply send expects jobs produced by `jobagent liepin greet preview`.",
    )

    start = max(0, args.start)
    limit = max(1, args.limit)
    selected = raw_jobs[start: start + limit]
    if not args.dry_run and not getattr(args, "confirm_submit", False):
        _print_json({
            "ok": False,
            "platform": "liepin",
            "mode": "automatic_apply_send",
            "error": "liepin_apply_send_confirmation_required",
            "message": "Real Liepin resume submission requires --confirm-submit.",
            "next_suggested": f"jobagent liepin apply send --input {args.input} --limit {limit} --confirm-submit",
        })
        sys.exit(2)

    missing_greetings = _jobs_missing_greeting_indexes(selected, offset=start)
    if missing_greetings and args.require_greeting:
        _print_json({
            "ok": False,
            "platform": "liepin",
            "mode": "automatic_apply_send",
            "error": "liepin_apply_send_missing_greeting",
            "message": "Selected Liepin jobs are missing greeting text. Run `jobagent liepin greet preview` first, or omit --require-greeting.",
            "missing_indexes": missing_greetings,
            "next_suggested": f"jobagent liepin greet preview --input {args.input}",
        })
        sys.exit(2)

    driver = None
    if not args.dry_run and not args.skip_login_check:
        from jobagent.drivers.boss import create_driver

        driver = create_driver()
        status = LiepinSessionGuide(driver=driver).check(wait_seconds=max(1, args.wait_seconds))
        if not status.logged_in:
            payload = status.to_dict()
            payload["platform"] = "liepin"
            payload["mode"] = "apply_send_login_check"
            payload["input"] = args.input
            payload["error"] = payload.get("error") or "liepin_login_required"
            _print_json(payload)
            if payload.get("requires_user_action") and payload.get("user_prompt"):
                print(payload["user_prompt"], file=sys.stderr)
                if payload.get("next_suggested"):
                    print(f"Next: {payload['next_suggested']}", file=sys.stderr)
            sys.exit(2)

    attempts = LiepinApplySender(driver=driver).send_batch(
        raw_jobs,
        limit=limit,
        start=start,
        wait_seconds=max(1, args.wait_seconds),
        dry_run=bool(args.dry_run),
        skip_delivered=not bool(getattr(args, "no_skip_delivered", False)),
        stop_on_failure=not bool(getattr(args, "continue_on_failure", False)),
    )
    delivered = sum(1 for attempt in attempts if attempt.delivered)
    planned = sum(1 for attempt in attempts if attempt.error == "dry_run")
    skipped = sum(1 for attempt in attempts if attempt.error == "already_delivered")
    failed = len(attempts) - delivered - planned - skipped
    stopped_early = (
        failed > 0
        and not args.dry_run
        and not bool(getattr(args, "continue_on_failure", False))
        and len(attempts) < len(selected)
    )
    payload = {
        "ok": failed == 0,
        "platform": "liepin",
        "mode": "automatic_apply_send",
        "selected": len(selected),
        "total": len(attempts),
        "planned": planned,
        "delivered": delivered,
        "failed": failed,
        "skipped": skipped,
        "stopped_early": stopped_early,
        "attempts": [attempt.to_dict() for attempt in attempts],
        "next_suggested": "Run `jobagent liepin audit` to review Liepin send/apply records.",
    }
    if stopped_early:
        payload["stop_reason"] = "first_send_failure"
        payload["next_suggested"] = (
            "Review the failed attempt and run `jobagent liepin audit` before continuing. "
            "Use --continue-on-failure only for controlled retries."
        )
    if missing_greetings:
        payload["warning"] = "selected_jobs_missing_greeting"
        payload["missing_greeting_indexes"] = missing_greetings
    _print_json(payload)
    from jobagent.infra.support import print_first_delivery_star_prompt_once

    print_first_delivery_star_prompt_once(
        platform="liepin",
        command="jobagent liepin apply send",
        delivered=delivered,
        dry_run=bool(args.dry_run),
    )
    sys.exit(0 if failed == 0 else 2)


def _jobs_missing_greeting_indexes(jobs: list, offset: int = 0) -> list[int]:
    missing: list[int] = []
    for idx, job in enumerate(jobs, start=offset):
        if not isinstance(job, dict):
            missing.append(idx)
            continue
        greeting = job.get("cloud_greeting") or job.get("greeting")
        if not str(greeting or "").strip():
            missing.append(idx)
    return missing


def _cmd_liepin_audit(args: argparse.Namespace) -> None:
    from jobagent.platforms.liepin import LiepinAuditLog

    log = LiepinAuditLog()
    _print_json({
        "platform": "liepin",
        "summary": log.summary(),
        "recent": log.list_recent(args.recent),
    })


def _cmd_zhilian_apply_open(args: argparse.Namespace) -> None:
    from jobagent.platforms.zhilian import ZhilianApplyOpener, ZhilianSessionGuide

    _, raw_jobs = _load_jobs_payload_or_exit(args.input)
    _require_jobs_platform_or_exit(
        raw_jobs,
        platform="zhilian",
        error="zhilian_apply_open_input_platform_mismatch",
        message="Zhilian apply open expects jobs produced by `jobagent zhilian rank` or `jobagent zhilian greet preview`.",
    )

    selected = raw_jobs[max(0, args.start): max(0, args.start) + max(1, args.limit)]
    missing_greetings = _jobs_missing_greeting_indexes(selected, offset=max(0, args.start))
    if missing_greetings and args.require_greeting:
        _print_json({
            "ok": False,
            "platform": "zhilian",
            "mode": "manual_apply_open",
            "error": "zhilian_apply_open_missing_greeting",
            "message": "Selected Zhilian jobs are missing greeting handoff text. Run `jobagent zhilian greet preview` first, or omit --require-greeting.",
            "missing_indexes": missing_greetings,
            "next_suggested": f"jobagent zhilian greet preview --input {args.input}",
        })
        sys.exit(2)

    driver = None
    if not args.dry_run and not args.skip_login_check:
        from jobagent.drivers.boss import create_driver

        driver = create_driver()
        status = ZhilianSessionGuide(driver=driver).check(wait_seconds=max(1, args.wait_seconds))
        if not status.logged_in:
            payload = status.to_dict()
            payload["platform"] = "zhilian"
            payload["mode"] = "apply_open_login_check"
            payload["input"] = args.input
            payload["error"] = payload.get("error") or "zhilian_login_required"
            _print_json(payload)
            if payload.get("requires_user_action") and payload.get("user_prompt"):
                print(payload["user_prompt"], file=sys.stderr)
                if payload.get("next_suggested"):
                    print(f"Next: {payload['next_suggested']}", file=sys.stderr)
            sys.exit(2)

    result = ZhilianApplyOpener(driver=driver).open_jobs(
        raw_jobs,
        limit=max(1, args.limit),
        start=max(0, args.start),
        wait_seconds=max(1, args.wait_seconds),
        dry_run=bool(args.dry_run),
    )
    payload = result.to_payload()
    if missing_greetings:
        payload["warning"] = "selected_jobs_missing_greeting"
        payload["missing_greeting_indexes"] = missing_greetings
        payload["next_suggested"] = "Run `jobagent zhilian greet preview` before manual apply-open for greeting handoff."
    _print_json(payload)
    sys.exit(0 if result.ok else 2)


def _cmd_zhilian_apply_send(args: argparse.Namespace) -> None:
    from jobagent.platforms.zhilian import ZhilianApplySender, ZhilianSessionGuide

    _, raw_jobs = _load_jobs_payload_or_exit(args.input)
    _require_jobs_platform_or_exit(
        raw_jobs,
        platform="zhilian",
        error="zhilian_apply_send_input_platform_mismatch",
        message="Zhilian apply send expects jobs produced by `jobagent zhilian greet preview`.",
    )

    start = max(0, args.start)
    limit = max(1, args.limit)
    selected = raw_jobs[start: start + limit]
    if not args.dry_run and not getattr(args, "confirm_submit", False):
        _print_json({
            "ok": False,
            "platform": "zhilian",
            "mode": "automatic_apply_send",
            "error": "zhilian_apply_send_confirmation_required",
            "message": "Real Zhilian resume submission requires --confirm-submit.",
            "next_suggested": f"jobagent zhilian apply send --input {args.input} --limit {limit} --confirm-submit",
        })
        sys.exit(2)

    missing_greetings = _jobs_missing_greeting_indexes(selected, offset=start)
    if missing_greetings and args.require_greeting:
        _print_json({
            "ok": False,
            "platform": "zhilian",
            "mode": "automatic_apply_send",
            "error": "zhilian_apply_send_missing_greeting",
            "message": "Selected Zhilian jobs are missing greeting text. Run `jobagent zhilian greet preview` first, or omit --require-greeting.",
            "missing_indexes": missing_greetings,
            "next_suggested": f"jobagent zhilian greet preview --input {args.input}",
        })
        sys.exit(2)

    driver = None
    if not args.dry_run and not args.skip_login_check:
        from jobagent.drivers.boss import create_driver

        driver = create_driver()
        status = ZhilianSessionGuide(driver=driver).check(wait_seconds=max(1, args.wait_seconds))
        if not status.logged_in:
            payload = status.to_dict()
            payload["platform"] = "zhilian"
            payload["mode"] = "apply_send_login_check"
            payload["input"] = args.input
            payload["error"] = payload.get("error") or "zhilian_login_required"
            _print_json(payload)
            if payload.get("requires_user_action") and payload.get("user_prompt"):
                print(payload["user_prompt"], file=sys.stderr)
                if payload.get("next_suggested"):
                    print(f"Next: {payload['next_suggested']}", file=sys.stderr)
            sys.exit(2)

    attempts = ZhilianApplySender(driver=driver).send_batch(
        raw_jobs,
        limit=limit,
        start=start,
        wait_seconds=max(1, args.wait_seconds),
        dry_run=bool(args.dry_run),
        skip_delivered=not bool(getattr(args, "no_skip_delivered", False)),
        stop_on_failure=not bool(getattr(args, "continue_on_failure", False)),
    )
    delivered = sum(1 for attempt in attempts if attempt.delivered)
    planned = sum(1 for attempt in attempts if attempt.error == "dry_run")
    skipped = sum(1 for attempt in attempts if attempt.error == "already_delivered")
    failed = len(attempts) - delivered - planned - skipped
    stopped_early = (
        failed > 0
        and not args.dry_run
        and not bool(getattr(args, "continue_on_failure", False))
        and len(attempts) < len(selected)
    )
    payload = {
        "ok": failed == 0,
        "platform": "zhilian",
        "mode": "automatic_apply_send",
        "selected": len(selected),
        "total": len(attempts),
        "planned": planned,
        "delivered": delivered,
        "failed": failed,
        "skipped": skipped,
        "stopped_early": stopped_early,
        "batch_review": _zhilian_apply_send_batch_review(attempts, start=start),
        "safety_harness": {
            "goal": "真实智联批量投递验收",
            "skip_delivered": not bool(getattr(args, "no_skip_delivered", False)),
            "stop_on_failure": not bool(getattr(args, "continue_on_failure", False)),
            "confirm_submit": bool(getattr(args, "confirm_submit", False)),
            "dry_run": bool(args.dry_run),
            "acceptance_criteria": [
                "每条岗位输出 delivered / failed / skipped / planned",
                "已投递岗位默认跳过，除非显式传 --no-skip-delivered",
                "遇到登录、验证码、安全验证、简历选择等情况时提示用户介入",
                "真实投递后用智联页面状态或审计日志确认结果",
            ],
        },
        "attempts": [attempt.to_dict() for attempt in attempts],
        "next_suggested": "Run `jobagent zhilian audit` to review Zhilian send/apply records.",
    }
    user_action = _zhilian_apply_send_user_action(attempts)
    if user_action:
        payload.update(user_action)
        if payload.get("user_prompt"):
            print(payload["user_prompt"], file=sys.stderr)
    if stopped_early:
        payload["stop_reason"] = "first_send_failure"
        payload["next_suggested"] = (
            "Review the failed attempt and run `jobagent zhilian audit` before continuing. "
            "Use --continue-on-failure only for controlled retries."
        )
    if missing_greetings:
        payload["warning"] = "selected_jobs_missing_greeting"
        payload["missing_greeting_indexes"] = missing_greetings
    _print_json(payload)
    from jobagent.infra.support import print_first_delivery_star_prompt_once

    print_first_delivery_star_prompt_once(
        platform="zhilian",
        command="jobagent zhilian apply send",
        delivered=delivered,
        dry_run=bool(args.dry_run),
    )
    sys.exit(0 if failed == 0 else 2)


def _zhilian_apply_send_batch_review(attempts: list[Any], start: int = 0) -> dict[str, Any]:
    already_delivered_indexes: list[int] = []
    user_action_required_indexes: list[int] = []
    greeting_not_supported_indexes: list[int] = []

    for offset, attempt in enumerate(attempts):
        index = start + offset
        if getattr(attempt, "error", "") == "already_delivered":
            already_delivered_indexes.append(index)
        if _zhilian_attempt_user_action(attempt):
            user_action_required_indexes.append(index)
        if _zhilian_attempt_greeting_not_supported(attempt):
            greeting_not_supported_indexes.append(index)

    already_delivered = len(already_delivered_indexes)
    user_action_required = len(user_action_required_indexes)
    greeting_not_supported = len(greeting_not_supported_indexes)
    return {
        "actionable": max(0, len(attempts) - already_delivered),
        "already_delivered": already_delivered,
        "already_delivered_indexes": already_delivered_indexes,
        "user_action_required": user_action_required,
        "user_action_required_indexes": user_action_required_indexes,
        "greeting_not_supported": greeting_not_supported,
        "greeting_not_supported_indexes": greeting_not_supported_indexes,
        "greeting_not_filled": greeting_not_supported,
        "greeting_not_filled_indexes": greeting_not_supported_indexes,
    }


def _zhilian_apply_send_user_action(attempts: list[Any]) -> dict[str, Any]:
    for attempt in attempts:
        action = _zhilian_attempt_user_action(attempt)
        if not action:
            continue
        prompts = {
            "captcha_required": "请先完成智联安全验证，然后回复我“已完成验证”，我再继续投递。",
            "resume_selection_required": "请先在智联页面选择或完善简历，然后回复我“已完成”，我再继续投递。",
            "login_required": "请先登录智联账号，然后回复我“已登录”，我再继续投递。",
        }
        return {
            "requires_user_action": True,
            "user_action": action,
            "user_prompt": prompts.get(action, f"请先处理智联页面上的 `{action}`，完成后回复我继续。"),
            "next_suggested": "Complete the requested user action in the open Zhilian page, then rerun the same command.",
        }
    return {}


def _zhilian_attempt_user_action(attempt: Any) -> str:
    error = str(getattr(attempt, "error", "") or "")
    if error in {"captcha_required", "resume_selection_required", "login_required"}:
        return error
    for step in getattr(attempt, "steps", []) or []:
        if isinstance(step, dict) and step.get("requires_user_action"):
            return str(step.get("user_action") or "user_action_required")
    return ""


def _zhilian_attempt_greeting_not_supported(attempt: Any) -> bool:
    if not str(getattr(attempt, "message", "") or "").strip():
        return False
    for step in getattr(attempt, "steps", []) or []:
        if not isinstance(step, dict):
            continue
        if step.get("step") == "zhilian_greeting_not_supported":
            return True
        if step.get("step") == "fill_zhilian_message":
            return not bool(step.get("ok") and step.get("filled"))
    return False


def _cmd_zhilian_audit(args: argparse.Namespace) -> None:
    from jobagent.platforms.zhilian import ZhilianAuditLog

    log = ZhilianAuditLog()
    _print_json({
        "platform": "zhilian",
        "summary": log.summary(),
        "recent": log.list_recent(args.recent),
    })


def _cmd_greet_send(args: argparse.Namespace) -> None:
    from datetime import datetime
    from jobagent.domain.greeter import GreeterEngine
    from jobagent.infra.config import Config

    ranked = _load_ranked_jobs(args.input)

    # Build cloud-greeting overrides keyed by RankedJob.job.url (GAP-15).
    # `jobagent boss greet preview` injects `cloud_greeting` per item in the same
    # file; we iterate raw items in lockstep (same order, same length) instead
    # of url-keyed lookup, which fails if a job lacks a stable url.
    with open(args.input, encoding="utf-8") as _f:
        _raw = json.load(_f)
    _items = _raw["jobs"] if isinstance(_raw, dict) and "jobs" in _raw else _raw
    message_overrides: dict[str, str] = {}
    if len(_items) == len(ranked):
        for item, rj in zip(_items, ranked):
            msg = item.get("cloud_greeting") or item.get("greeting")
            if msg and rj.job.url:
                message_overrides[rj.job.url] = msg
    if message_overrides:
        source = "cloud/local"
        if all(item.get("greeting") for item in _items[: len(message_overrides)]):
            source = "local"
        elif all(item.get("cloud_greeting") for item in _items[: len(message_overrides)]):
            source = "cloud"
        print(f"Using {len(message_overrides)} {source} greetings from preview input")
    elif any(it.get("cloud_greeting") or it.get("greeting") for it in _items):
        # greeting present but couldn't be mapped (e.g., jobs without url)
        print(
            "⚠️  greeting text present in input but could not be mapped to send targets "
            "(jobs missing `url`). Falling back to local template.",
            file=sys.stderr,
        )

    config_path = Path(args.config)
    if config_path.exists():
        config = Config.from_yaml(config_path)
        greeter_config = config.greeter
    else:
        greeter_config = GreeterConfig()

    engine = GreeterEngine(greeter_config)
    results = engine.send_batch(ranked, limit=args.limit, message_overrides=message_overrides)

    # Save detailed results
    payload = {
        "total": len(results),
        "delivered": sum(1 for r in results if r.delivered),
        "timestamp": datetime.now().isoformat(),
        "attempts": [r.to_dict() for r in results],
    }
    results_dir = Path("data/results")
    results_dir.mkdir(parents=True, exist_ok=True)
    output_path = results_dir / f"greet_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Detailed results saved → {output_path}")
    from jobagent.infra.support import print_first_delivery_star_prompt_once

    print_first_delivery_star_prompt_once(
        platform="boss",
        command="jobagent boss greet send",
        delivered=payload["delivered"],
    )


def _cmd_greet_audit(args: argparse.Namespace) -> None:
    from jobagent.infra.audit import AuditLog

    log = AuditLog()
    summary = log.summary()

    print(f"\n📊 Greeting Audit Summary")
    print(f"{'=' * 50}")
    print(f"Total attempts:    {summary['total']}")
    print(f"Delivered:         {summary['delivered']}")
    print(f"Failed:            {summary['failed']}")
    print(f"Success rate:      {summary['success_rate'] * 100:.0f}%")

    if summary["error_breakdown"]:
        print(f"\nError breakdown:")
        for err, count in summary["error_breakdown"].items():
            print(f"  {err}: {count}")

    if summary["daily_stats"]:
        print(f"\nDaily stats:")
        for day, stats in sorted(summary["daily_stats"].items()):
            print(f"  {day}: {stats['delivered']}/{stats['total']} delivered")

    records = log.list_recent(args.recent)
    if records:
        print(f"\nRecent {len(records)} attempts:")
        for r in records:
            status = "✅" if r.get("delivered") else "❌"
            name = r.get("job_name", "Unknown")
            company = r.get("company", "")
            error = f" ({r.get('error', '')})" if not r.get("delivered") else ""
            print(f"  {status} {name} @ {company}{error}")
    else:
        print("\nNo records found.")
    print(f"{'=' * 50}\n")


def _cmd_platforms_status(args: argparse.Namespace) -> None:
    from jobagent.platforms import list_platforms

    overrides = _load_yaml_if_exists(args.config)
    _print_json({
        "config": str(args.config),
        "platforms": [platform.to_dict() for platform in list_platforms(overrides)],
    })


def _cmd_platforms_health(args: argparse.Namespace) -> None:
    from jobagent.platforms import check_all_platforms, check_platform_health

    overrides = _load_yaml_if_exists(args.config)
    health = (
        [check_platform_health(args.platform, overrides)]
        if args.platform
        else check_all_platforms(overrides)
    )
    _print_json({
        "config": str(args.config),
        "health": [item.to_dict() for item in health],
    })


def _load_yaml_if_exists(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    config_path = Path(path)
    if not config_path.exists():
        return {}
    import yaml
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _cmd_resume_extract(args: argparse.Namespace) -> None:
    """Extract plain text from resume — output goes to agent's LLM."""
    from jobagent.domain.resume_parser import ResumeParser

    parser = ResumeParser()
    try:
        text = parser.parse(args.file)
    except Exception as e:
        _print_json({"ok": False, "error": str(e)})
        sys.exit(1)

    _print_json({
        "ok": True,
        "file": str(args.file),
        "chars": len(text),
        "text": text,
    })
    _print_cloud_upgrade_hint("resume")


def _cmd_resume_analyze(args: argparse.Namespace) -> None:
    """Extract resume + save a 36-field profile.

    Cloud is used when configured; otherwise the internal local analyzer keeps
    the product capability available for self-use/offline validation.
    """
    from jobagent.domain.resume_parser import ResumeParser
    from jobagent.infra.credentials import load_license_key
    from jobagent.infra import cloud_client
    from jobagent.infra.state import save_json, profile_path

    # 1. Local extract
    parser = ResumeParser()
    try:
        text = parser.parse(args.file)
    except Exception as e:
        _print_json({"ok": False, "stage": "extract", "error": str(e)})
        sys.exit(1)

    # 2. Cloud analyze
    hints = {}
    if args.target_role:
        hints["target_role"] = args.target_role
    if args.target_cities:
        hints["target_cities"] = args.target_cities

    if args.local or not load_license_key():
        from jobagent.domain.local_profile import analyze_resume_local

        analysis = analyze_resume_local(
            text,
            file_name=Path(args.file).name,
            target_role=args.target_role,
            target_cities=args.target_cities,
        )
        output_path = Path(args.output) if args.output else profile_path()
        save_json(output_path, analysis.profile)
        _print_json({
            "ok": True,
            "via": "local",
            "saved_to": str(output_path),
            "chars": len(text),
            "field_groups": analysis.profile.get("_meta", {}).get("fieldGroups"),
            "field_count": analysis.profile.get("_meta", {}).get("fieldCount"),
            "simplified": analysis.simplified,
            "fields": sorted(k for k in analysis.profile.keys() if not k.startswith("_")),
            "next_suggested": "jobagent boss collect --city <city> --query <role>",
        })
        return

    try:
        resp = cloud_client.resume_analyze(
            resume_text=text,
            file_name=Path(args.file).name,
            hints=hints or None,
        )
    except cloud_client.NotConfiguredError as e:
        _print_json({"ok": False, "stage": "configure", "error": str(e)})
        sys.exit(2)
    except cloud_client.CloudError as e:
        _print_json({
            "ok": False,
            "stage": "analyze",
            "error": str(e),
            "status": e.status,
            "code": e.code,
            "hint": cloud_client.hint_for(e.code),
        })
        sys.exit(3)

    profile_obj = resp.get("profile", {})

    # 3. Save (36-field shape — overwrites legacy 7-field profile if any)
    output_path = Path(args.output) if args.output else profile_path()
    save_json(output_path, profile_obj)

    _print_json({
        "ok": True,
        "saved_to": str(output_path),
        "chars": len(text),
        "fields": sorted(profile_obj.keys()),
        "next_suggested": resp.get("next_suggested"),
    })


def _cmd_profile_save(args: argparse.Namespace) -> None:
    """Save candidate profile JSON (produced by the agent's own LLM)."""
    from jobagent.domain.profile_builder import ProfileBuilder
    from jobagent.infra.state import save_json, profile_path

    # Parse JSON from agent
    try:
        raw = json.loads(args.data)
    except json.JSONDecodeError as e:
        _print_json({"ok": False, "error": f"Invalid JSON: {e}"})
        sys.exit(1)

    # Validate & coerce into CandidateProfile
    try:
        profile = ProfileBuilder.build(raw)
    except Exception as e:
        _print_json({"ok": False, "error": f"Profile validation failed: {e}"})
        sys.exit(1)

    # Save
    output_path = Path(args.output) if args.output else profile_path()
    profile_dict = {
        "years_experience": profile.years_experience,
        "target_roles": profile.target_roles,
        "skills": profile.skills,
        "preferred_cities": profile.preferred_cities,
        "salary_expectation": profile.salary_expectation,
        "industry_preferences": profile.industry_preferences,
        "exclusions": profile.exclusions,
    }
    save_json(output_path, profile_dict)

    _print_json({
        "ok": True,
        "saved_to": str(output_path),
        "profile": profile_dict,
    })


def _cmd_profile_edit(args: argparse.Namespace) -> None:
    """Open profile.json in $EDITOR for manual correction (GAP-11)."""
    import os
    import subprocess
    from jobagent.infra.state import profile_path

    path = profile_path()
    if not path.exists():
        _print_json({
            "ok": False,
            "error": f"Profile not found at {path}. Run `jobagent resume analyze --file <path>` first.",
        })
        sys.exit(1)
    editor = os.environ.get("EDITOR") or ("notepad" if sys.platform == "win32" else "vim")
    try:
        subprocess.run([editor, str(path)], check=False)
    except FileNotFoundError:
        _print_json({
            "ok": False,
            "error": f"Editor `{editor}` not found. Set $EDITOR or install vim/notepad.",
        })
        sys.exit(1)
    # Validate JSON after edit
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"⚠️  Profile JSON is invalid after edit: {e}", file=sys.stderr)
        print(f"   File: {path}", file=sys.stderr)
        sys.exit(1)
    print(f"✅ Profile updated: {path}", file=sys.stderr)


def _cmd_profile_show(args: argparse.Namespace) -> None:
    from jobagent.infra.state import load_json, profile_path

    data = load_json(profile_path())
    if not data:
        _print_json({
            "ok": False,
            "error": "no_profile",
            "message": "No profile found. Run `jobagent profile save --data '{...}'` first.",
        })
        sys.exit(1)

    _print_json({"ok": True, "profile": data})


def _cmd_pipeline_run(args: argparse.Namespace) -> None:
    """Legacy all-in-one pipeline. Now gated on license — the underlying
    ranking + greeting steps depend on our cloud IP. New users should follow
    docs/agent-onboarding.md instead.
    """
    _require_license_or_exit("pipeline run")
    print(
        "ℹ️  `pipeline run` is the legacy local flow. Prefer the agent-driven\n"
        "   flow described in docs/agent-onboarding.md (init → resume analyze\n"
        "   → boss collect → boss rank → boss greet preview → boss greet send).\n",
        file=sys.stderr,
    )
    from jobagent.application.pipeline import Pipeline
    from jobagent.infra.config import Config

    config = Config.from_yaml(args.config)
    pipeline = Pipeline(config)
    summary = pipeline.run()
    _print_json(summary)
    from jobagent.infra.support import print_first_delivery_star_prompt_once

    print_first_delivery_star_prompt_once(
        platform=str(summary.get("platform") or "boss"),
        command="jobagent pipeline run",
        delivered=int(summary.get("delivered") or 0),
    )


def _cmd_support_star(args: argparse.Namespace) -> None:
    from jobagent.infra.support import support_star_payload

    _print_json(support_star_payload())


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "login":
        from jobagent.drivers.boss import create_driver
        from jobagent.drivers.boss.cdp_driver import CDPBossDriver

        driver = create_driver()
        if isinstance(driver, CDPBossDriver):
            if args.check:
                _print_json({"logged_in": driver.check_login_status()})
                sys.exit(0)
            # Active login guide. Print onboarding for first-time users (GAP-12).
            print(
                "\n🔐 即将启动一个独立的 Chrome 实例用于 Boss 直聘自动化。\n"
                "   • 这是一个隔离的 Chrome（独立 user-data-dir 在 ~/.jobagent/chrome-profile/），\n"
                "     与你日常的 Chrome 完全隔离——不会污染你的书签/扩展/cookie。\n"
                "   • 接下来会打开 zhipin.com 登录页，请在新窗口中扫码登录。\n"
                "   • 登录成功后，cookie 会持久化在本机；以后无需重复扫码（除非主动登出）。\n"
                "   • 整个登录过程只发生在你本地，cookie 永远不上传到我们的服务器。\n"
                "\n按 Enter 继续...",
                file=sys.stderr,
                end="",
            )
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                # Allow non-interactive environments (e.g. piped) to skip the gate
                print(file=sys.stderr)
            ok = driver.ensure_logged_in()
            _print_json({"logged_in": ok})
            sys.exit(0 if ok else 2)
        else:
            _print_json({
                "logged_in": False,
                "error": "passive_login_only_available_with_cdp_driver",
            })
            sys.exit(2)

    if args.command == "doctor" and args.doctor_target == "env":
        _cmd_doctor_env(args)
        return

    if args.command == "doctor" and args.doctor_target == "boss":
        _require_platform_enabled_or_exit("boss", args)
        report = run_boss_doctor(job_url=args.job_url)
        _print_json(report.to_dict())
        sys.exit(0 if report.status == "READY" else 2)

    if args.command == "doctor" and args.doctor_target == "liepin":
        _require_platform_enabled_or_exit("liepin", args)
        report = run_liepin_doctor(
            query=args.query,
            city=args.city,
            wait_seconds=max(1, args.wait_seconds),
            limit=max(1, args.limit),
            check_cloud=bool(args.with_cloud),
        )
        payload = report.to_dict()
        _print_json(payload)
        for check in report.checks:
            prompt = check.evidence.get("user_prompt")
            next_suggested = check.evidence.get("next_suggested")
            if prompt:
                print(prompt, file=sys.stderr)
                if next_suggested:
                    print(f"Next: {next_suggested}", file=sys.stderr)
                break
        sys.exit(0 if report.status == "READY" else 2)

    if args.command == "boss" and args.boss_command == "probe-send":
        _require_platform_enabled_or_exit("boss", args)
        _ensure_boss_login()
        attempt = run_probe_send(job_url=args.job_url, message=args.message)
        _print_json(attempt.to_dict())
        sys.exit(0 if attempt.delivered else 2)

    if args.command == "boss" and args.boss_command == "verify-last-send":
        _require_platform_enabled_or_exit("boss", args)
        attempt = run_verify_last_send(message=args.message)
        _print_json(attempt.to_dict())
        sys.exit(0 if attempt.delivered else 2)

    if args.command == "boss" and args.boss_command == "collect":
        _require_platform_enabled_or_exit("boss", args)
        _cmd_jobs_collect(args)
        return

    if args.command == "boss" and args.boss_command == "rank":
        _require_platform_enabled_or_exit("boss", args)
        _cmd_jobs_rank(args)
        return

    if args.command == "boss" and args.boss_command == "greet" and args.boss_greet_command == "preview":
        _require_platform_enabled_or_exit("boss", args)
        _cmd_greet_preview(args)
        return

    if args.command == "boss" and args.boss_command == "greet" and args.boss_greet_command == "send":
        _require_platform_enabled_or_exit("boss", args)
        _ensure_boss_login()
        _cmd_greet_send(args)
        return

    if args.command == "boss" and args.boss_command == "greet" and args.boss_greet_command == "audit":
        _require_platform_enabled_or_exit("boss", args)
        _cmd_greet_audit(args)
        return

    if args.command == "liepin" and args.liepin_command == "login":
        _require_platform_enabled_or_exit("liepin", args)
        _cmd_liepin_login(args)
        return

    if args.command == "liepin" and args.liepin_command == "collect":
        _require_platform_enabled_or_exit("liepin", args)
        _cmd_liepin_collect(args)
        return

    if args.command == "liepin" and args.liepin_command == "rank":
        _require_platform_enabled_or_exit("liepin", args)
        _cmd_liepin_rank(args)
        return

    if args.command == "liepin" and args.liepin_command == "greet" and args.liepin_greet_command == "preview":
        _require_platform_enabled_or_exit("liepin", args)
        _cmd_liepin_greet_preview(args)
        return

    if args.command == "liepin" and args.liepin_command == "greet" and args.liepin_greet_command == "send":
        _require_platform_enabled_or_exit("liepin", args)
        _cmd_liepin_greet_send(args)
        return

    if args.command == "liepin" and args.liepin_command == "apply" and args.liepin_apply_command == "open":
        _require_platform_enabled_or_exit("liepin", args)
        _cmd_liepin_apply_open(args)
        return

    if args.command == "liepin" and args.liepin_command == "apply" and args.liepin_apply_command == "send":
        _require_platform_enabled_or_exit("liepin", args)
        _cmd_liepin_apply_send(args)
        return

    if args.command == "liepin" and args.liepin_command == "audit":
        _require_platform_enabled_or_exit("liepin", args)
        _cmd_liepin_audit(args)
        return

    if args.command == "zhilian" and args.zhilian_command == "login":
        _require_platform_enabled_or_exit("zhilian", args)
        _cmd_zhilian_login(args)
        return

    if args.command == "zhilian" and args.zhilian_command == "collect":
        _require_platform_enabled_or_exit("zhilian", args)
        _cmd_zhilian_collect(args)
        return

    if args.command == "zhilian" and args.zhilian_command == "rank":
        _require_platform_enabled_or_exit("zhilian", args)
        _cmd_zhilian_rank(args)
        return

    if args.command == "zhilian" and args.zhilian_command == "greet" and args.zhilian_greet_command == "preview":
        _require_platform_enabled_or_exit("zhilian", args)
        _cmd_zhilian_greet_preview(args)
        return

    if args.command == "zhilian" and args.zhilian_command == "apply" and args.zhilian_apply_command == "open":
        _require_platform_enabled_or_exit("zhilian", args)
        _cmd_zhilian_apply_open(args)
        return

    if args.command == "zhilian" and args.zhilian_command == "apply" and args.zhilian_apply_command == "send":
        _require_platform_enabled_or_exit("zhilian", args)
        _cmd_zhilian_apply_send(args)
        return

    if args.command == "zhilian" and args.zhilian_command == "audit":
        _require_platform_enabled_or_exit("zhilian", args)
        _cmd_zhilian_audit(args)
        return

    if args.command == "platforms" and args.platforms_command == "status":
        _cmd_platforms_status(args)
        return

    if args.command == "platforms" and args.platforms_command == "health":
        _cmd_platforms_health(args)
        return

    if args.command == "support" and args.support_command == "star":
        _cmd_support_star(args)
        return

    if args.command == "resume" and args.resume_command == "extract":
        _cmd_resume_extract(args)
        return

    if args.command == "resume" and args.resume_command == "analyze":
        _cmd_resume_analyze(args)
        return

    if args.command == "init":
        _cmd_init(args)
        return

    if args.command == "profile" and args.profile_command == "save":
        _cmd_profile_save(args)
        return

    if args.command == "profile" and args.profile_command == "show":
        _cmd_profile_show(args)
        return

    if args.command == "profile" and args.profile_command == "edit":
        _cmd_profile_edit(args)
        return

    if args.command == "pipeline" and args.pipeline_command == "run":
        _require_license_or_exit("pipeline run")  # gate before Boss login (need license first)
        _ensure_boss_login()
        _cmd_pipeline_run(args)
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
