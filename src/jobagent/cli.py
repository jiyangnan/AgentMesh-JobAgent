"""Job Agent 0.3 public command surface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from jobagent import __version__


def _print(payload: Any, *, stream=None) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=stream or sys.stdout)


def _add_login(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--check", action="store_true", help="Check the current login state")
    parser.add_argument("--wait", action="store_true", help="Wait for the user to complete login")
    parser.add_argument("--timeout", type=int, default=300, help="Login wait timeout in seconds")


def _add_discover(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--wait-seconds", type=int, default=6, help="Page load wait per search page")
    parser.add_argument("--page-delay", type=float, default=2.0, help="Delay between search pages")


def _add_review(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", "-i", help="Signed decision file; defaults to latest")
    parser.add_argument("--promote", nargs="*", default=[], metavar="JOB_ID")
    parser.add_argument("--confirm-promote", action="store_true")
    parser.add_argument("--output", "-o", help="Reviewed decision output path")


def _add_send(parser: argparse.ArgumentParser, confirmation: str) -> None:
    parser.add_argument("--input", "-i", help="Reviewed decision file; defaults to latest")
    parser.add_argument("--limit", type=int, default=20, help="Maximum jobs in this send batch")
    parser.add_argument(confirmation, action="store_true", help="Explicitly confirm real platform actions")
    parser.add_argument("--dry-run", action="store_true", help="Plan without touching platform buttons")
    parser.add_argument("--continue-on-failure", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobagent", description="AgentMesh 360 Job Agent")
    parser.add_argument("--version", action="version", version=f"jobagent {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Configure an AgentMesh API Key")
    init.add_argument("--key", required=True)
    init.add_argument("--no-verify", action="store_true")

    doctor = sub.add_parser("doctor", help="Check the local and cloud environment")
    doctor.add_subparsers(dest="doctor_command", required=True).add_parser("env")

    resume = sub.add_parser("resume", help="Analyze a resume into the current profile")
    resume_sub = resume.add_subparsers(dest="resume_command", required=True)
    analyze = resume_sub.add_parser("analyze")
    analyze.add_argument("--file", "-f", required=True)
    analyze.add_argument("--target-role")
    analyze.add_argument("--target-cities", nargs="*")
    analyze.add_argument("--output", "-o")

    profile = sub.add_parser("profile", help="View the current resume profile")
    profile.add_subparsers(dest="profile_command", required=True).add_parser("show")

    platforms = sub.add_parser("platforms", help="View supported recruiting platforms")
    platforms_sub = platforms.add_subparsers(dest="platforms_command", required=True)
    platforms_sub.add_parser("status")
    health = platforms_sub.add_parser("health")
    health.add_argument("--platform", choices=["boss", "liepin", "zhilian", "51job"])

    update = sub.add_parser("update", help="Check signed client release policy")
    update.add_subparsers(dest="update_command", required=True).add_parser("check")

    support = sub.add_parser("support", help="Voluntary project support")
    support.add_subparsers(dest="support_command", required=True).add_parser("star")

    for platform, display in (
        ("boss", "Boss直聘"),
        ("liepin", "猎聘"),
        ("zhilian", "智联招聘"),
        ("51job", "前程无忧 / 51Job"),
    ):
        platform_parser = sub.add_parser(platform, help=display)
        platform_sub = platform_parser.add_subparsers(dest="platform_command", required=True)
        login = platform_sub.add_parser("login")
        _add_login(login)
        discover = platform_sub.add_parser("discover")
        _add_discover(discover)
        if platform == "boss":
            greet = platform_sub.add_parser("greet")
            greet_sub = greet.add_subparsers(dest="greet_command", required=True)
            preview = greet_sub.add_parser("preview")
            _add_review(preview)
            send = greet_sub.add_parser("send")
            _add_send(send, "--confirm-send")
        else:
            apply = platform_sub.add_parser("apply")
            apply_sub = apply.add_subparsers(dest="apply_command", required=True)
            review = apply_sub.add_parser("review")
            _add_review(review)
            send = apply_sub.add_parser("send")
            _add_send(send, "--confirm-submit")
        audit = platform_sub.add_parser("audit")
        audit.add_argument("--recent", "-n", type=int, default=20)
    return parser


def _init(args: argparse.Namespace) -> dict[str, Any]:
    from jobagent.infra import cloud_client
    from jobagent.infra.credentials import save_api_key

    path = save_api_key(args.key)
    payload: dict[str, Any] = {"ok": True, "credentials_path": str(path)}
    if not args.no_verify:
        payload["account"] = cloud_client.me()
    payload["next_suggested"] = "jobagent resume analyze --file <resume>"
    return payload


def _doctor_env() -> dict[str, Any]:
    import shutil

    from jobagent.infra import cloud_client
    from jobagent.infra.credentials import load_api_key

    key_present = bool(load_api_key())
    cloud: dict[str, Any]
    try:
        cloud = cloud_client.health()
    except Exception as exc:
        cloud = {"ok": False, "error": str(exc)}
    return {
        "ok": bool(shutil.which("python3") and key_present and cloud.get("status") == "ok"),
        "python": sys.version.split()[0],
        "chrome": bool(
            Path("/Applications/Google Chrome.app").exists()
            or shutil.which("google-chrome")
            or shutil.which("google-chrome-stable")
        ),
        "api_key_configured": key_present,
        "cloud": cloud,
    }


def _resume_analyze(args: argparse.Namespace) -> dict[str, Any]:
    from jobagent.domain.resume_parser import ResumeParser
    from jobagent.infra import cloud_client
    from jobagent.infra.state import profile_path, save_json

    source = Path(args.file).expanduser()
    text = ResumeParser().parse(source)
    hints = {
        key: value
        for key, value in {
            "target_role": args.target_role,
            "target_cities": args.target_cities,
        }.items()
        if value
    }
    response = cloud_client.resume_analyze(text, source.name, hints or None)
    profile = response["profile"]
    output = Path(args.output).expanduser() if args.output else profile_path()
    save_json(output, profile)
    return {
        "ok": True,
        "profile_path": str(output),
        "next_suggested": "jobagent boss discover",
    }


def _login(platform: str, args: argparse.Namespace) -> dict[str, Any]:
    if platform == "boss":
        from jobagent.drivers.boss import create_driver
        from jobagent.drivers.boss.cdp_driver import CDPBossDriver

        driver = create_driver(platform="boss")
        if not isinstance(driver, CDPBossDriver):
            result = driver.open_url_in_new_tab(
                "https://www.zhipin.com/web/user/?ka=header-login", wait_seconds=2
            )
            return {"platform": platform, **result}
        if driver.check_login_status():
            return {"ok": True, "platform": platform, "logged_in": True}
        if args.wait:
            logged_in = driver.ensure_logged_in(timeout=args.timeout)
            return {"ok": logged_in, "platform": platform, "logged_in": logged_in}
        driver.open_url_in_new_tab("https://www.zhipin.com/web/user/?ka=header-login", wait_seconds=2)
        return {
            "ok": False,
            "platform": platform,
            "logged_in": False,
            "requires_user_action": True,
            "user_action": "login_boss",
            "user_prompt": "请在已经打开的 Job Agent 浏览器中登录 Boss 直聘，完成后回复我“已登录”。",
        }

    if platform == "liepin":
        from jobagent.platforms.liepin.session import LiepinSessionGuide

        guide = LiepinSessionGuide()
    elif platform == "zhilian":
        from jobagent.platforms.zhilian.session import ZhilianSessionGuide

        guide = ZhilianSessionGuide()
    else:
        from jobagent.platforms.job51.session import Job51SessionGuide

        guide = Job51SessionGuide()
    if args.check:
        status = guide.check()
    elif args.wait:
        status = guide.wait_for_login(timeout=args.timeout)
    else:
        status = guide.open_login()
    return status.to_dict()


def _maybe_update(args: argparse.Namespace) -> None:
    if os.environ.get("JOBAGENT_SKIP_UPDATE") == "1" or args.command == "update":
        return
    from jobagent.infra.release_update import maybe_auto_update

    result = maybe_auto_update()
    if result.get("status") == "updated":
        os.execv(sys.executable, [sys.executable, "-m", "jobagent", *sys.argv[1:]])
    if result.get("status") == "update_required":
        _print(result, stream=sys.stderr)
        raise SystemExit(3)
    if result.get("status") == "update_available":
        _print(result, stream=sys.stderr)


def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "init":
        return _init(args)
    if args.command == "doctor":
        return _doctor_env()
    if args.command == "resume":
        return _resume_analyze(args)
    if args.command == "profile":
        from jobagent.infra.state import load_json, profile_path

        return {"ok": True, "profile": load_json(profile_path())}
    if args.command == "platforms":
        from jobagent.platforms import check_all_platforms, check_platform_health, list_platforms

        if args.platforms_command == "status":
            return {"platforms": [platform.to_dict() for platform in list_platforms()]}
        if args.platform:
            return check_platform_health(args.platform).to_dict()
        return {"platforms": [health.to_dict() for health in check_all_platforms()]}
    if args.command == "update":
        from jobagent.infra.release_update import check_for_update

        return check_for_update(auto_apply=False)
    if args.command == "support":
        from jobagent.infra.support import support_star_payload

        return support_star_payload()

    platform = args.command
    if args.platform_command == "login":
        return _login(platform, args)
    if args.platform_command == "discover":
        from jobagent.application.discover import run_discover

        return run_discover(platform, wait_seconds=args.wait_seconds, page_delay=args.page_delay)
    if args.platform_command == "audit":
        from jobagent.application.delivery import audit_platform

        return audit_platform(platform, recent=args.recent)
    if platform == "boss" and args.platform_command == "greet":
        if args.greet_command == "preview":
            from jobagent.application.review import review_decision

            return review_decision(
                platform,
                input_path=args.input,
                promoted_ids=args.promote,
                confirm_promote=args.confirm_promote,
                output_path=args.output,
            )
        confirmation = args.confirm_send
    else:
        if args.apply_command == "review":
            from jobagent.application.review import review_decision

            return review_decision(
                platform,
                input_path=args.input,
                promoted_ids=args.promote,
                confirm_promote=args.confirm_promote,
                output_path=args.output,
            )
        confirmation = args.confirm_submit
    if not confirmation and not args.dry_run:
        return {
            "ok": False,
            "error": "user_confirmation_required",
            "platform": platform,
            "message": "Review the selected jobs and explicitly confirm the real send action.",
        }
    from jobagent.application.delivery import send_reviewed

    return send_reviewed(
        platform,
        input_path=args.input,
        limit=args.limit,
        dry_run=args.dry_run,
        stop_on_failure=not args.continue_on_failure,
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        _maybe_update(args)
        result = _dispatch(args)
        _print(result)
        if result.get("ok") is False:
            raise SystemExit(2)
    except KeyboardInterrupt:
        _print({"ok": False, "error": "interrupted"}, stream=sys.stderr)
        raise SystemExit(130) from None
    except Exception as exc:
        from jobagent.infra.cloud_client import CloudError
        from jobagent.infra.platform_lock import PlatformLockError
        from jobagent.infra.protocol import ProtocolError
        from jobagent.platforms.discovery import CollectionError

        try:
            from jobagent.application.delivery import UserInterventionRequired
        except ImportError:
            UserInterventionRequired = ()  # type: ignore[assignment,misc]
        if isinstance(exc, CollectionError):
            payload = {
                "ok": False,
                "error": exc.code,
                "message": exc.message,
                "no_charge": True,
                "requires_user_action": bool(exc.user_prompt),
                "user_prompt": exc.user_prompt or None,
            }
        elif UserInterventionRequired and isinstance(exc, UserInterventionRequired):
            payload = {
                "ok": False,
                "error": exc.code,
                "requires_user_action": True,
                "user_prompt": exc.prompt,
            }
        elif isinstance(exc, PlatformLockError):
            payload = exc.payload
        elif isinstance(exc, CloudError):
            payload = {
                "ok": False,
                "error": exc.code or "cloud_error",
                "status": exc.status,
                "message": str(exc),
            }
        elif isinstance(exc, ProtocolError):
            payload = {"ok": False, "error": "protocol_verification_failed", "message": str(exc)}
        else:
            payload = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        _print(payload, stream=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
