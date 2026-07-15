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


def _add_send(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", "-i", help="Reviewed decision file; defaults to latest")
    parser.add_argument("--limit", type=int, default=100, help="Maximum jobs in this send batch")
    parser.add_argument("--dry-run", action="store_true", help="Plan without touching platform buttons")
    parser.add_argument("--continue-on-failure", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobagent", description="AgentMesh 360 Job Agent")
    parser.add_argument("--version", action="version", version=f"jobagent {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Configure an AgentMesh API Key")
    init.add_argument("--key", required=True)
    init.add_argument("--no-verify", action="store_true")

    sub.add_parser("upgrade-check", help="Check saved state after a Job Agent upgrade")

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

    delivery_round = sub.add_parser("round", help="View or update the multi-platform round")
    round_sub = delivery_round.add_subparsers(dest="round_command", required=True)
    round_sub.add_parser("status")
    round_skip = round_sub.add_parser("skip")
    round_skip.add_argument("--platform", required=True, choices=["boss", "liepin", "zhilian", "51job"])
    round_skip.add_argument("--confirm-skip", action="store_true")

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
            _add_send(send)
        else:
            apply = platform_sub.add_parser("apply")
            apply_sub = apply.add_subparsers(dest="apply_command", required=True)
            review = apply_sub.add_parser("review")
            _add_review(review)
            send = apply_sub.add_parser("send")
            _add_send(send)
        audit = platform_sub.add_parser("audit")
        audit.add_argument("--recent", "-n", type=int, default=20)
    return parser


def _cloud_access(account_response: dict[str, Any], *, profile_exists: bool) -> dict[str, Any]:
    account = account_response.get("account") or {}
    credit = account.get("credit")
    unlimited = bool(account.get("unlimited") or credit == "unlimited")
    numeric_credit: int | None = None
    if not unlimited:
        try:
            numeric_credit = int(credit)
        except (TypeError, ValueError):
            numeric_credit = None
    required_credits = 10 if profile_exists else 5
    usable = unlimited or (numeric_credit is not None and numeric_credit >= required_credits)
    source = account.get("source") or "none"
    if unlimited:
        reason = "unlimited"
    elif usable and source == "signup_trial":
        reason = "signup_trial_active"
    elif usable:
        reason = "credits_available"
    elif numeric_credit is None:
        reason = "credit_status_unavailable"
    else:
        reason = "insufficient_credits"
    return {
        "usable": usable,
        "reason": reason,
        "credit": credit,
        "source": source,
        "expires_at": account.get("expires_at"),
        "required_credits": required_credits,
        "paid_pass_required": (
            False if usable else reason == "insufficient_credits" or None
        ),
        "next_suggested": (
            "jobagent boss discover"
            if usable and profile_exists
            else "jobagent resume analyze --file <resume>"
            if usable
            else None
        ),
    }


def _init(args: argparse.Namespace) -> dict[str, Any]:
    from jobagent.infra import cloud_client
    from jobagent.infra.credentials import save_api_key

    if args.key.strip().startswith("jba_live_"):
        raise ValueError(
            "This retired license is not an AgentMesh360 API key. "
            "Create a current API key in your AgentMesh360 account, then run "
            "`jobagent init --key <your_api_key>`."
        )
    account = None
    if not args.no_verify:
        account = cloud_client.me(api_key=args.key.strip())
    path = save_api_key(args.key)
    payload: dict[str, Any] = {"ok": True, "credentials_path": str(path)}
    if account is not None:
        payload["account"] = account
        payload["cloud_access"] = _cloud_access(account, profile_exists=False)
    access = payload.get("cloud_access") or {}
    payload["next_suggested"] = access.get("next_suggested") or (
        "https://agentmesh360.com/app/#pricing"
        if access.get("paid_pass_required")
        else "jobagent doctor env"
    )
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
    key_valid = False
    key_error: str | None = None
    account_response: dict[str, Any] | None = None
    if key_present:
        key = str(load_api_key() or "")
        if key.startswith("jba_live_"):
            key_error = "retired_license_key"
        elif cloud.get("status") == "ok":
            try:
                account_response = cloud_client.me()
                key_valid = True
            except cloud_client.CloudError as exc:
                key_error = exc.code or "api_key_verification_failed"
    from jobagent.infra.state import profile_path

    access = (
        _cloud_access(account_response, profile_exists=profile_path().exists())
        if account_response is not None
        else {
            "usable": False,
            "reason": key_error or "api_key_required",
            "credit": None,
            "source": "none",
            "expires_at": None,
            "required_credits": 5,
            "paid_pass_required": None,
            "next_suggested": None,
        }
    )
    return {
        "ok": bool(
            shutil.which("python3")
            and key_present
            and key_valid
            and access["usable"]
            and cloud.get("status") == "ok"
        ),
        "python": sys.version.split()[0],
        "chrome": bool(
            Path("/Applications/Google Chrome.app").exists()
            or shutil.which("google-chrome")
            or shutil.which("google-chrome-stable")
        ),
        "api_key_configured": key_present,
        "api_key_valid": key_valid,
        "api_key_error": key_error,
        "api_key_action": (
            None
            if key_valid and access["usable"]
            else "https://agentmesh360.com/app/#pricing"
            if access.get("paid_pass_required")
            else "jobagent init --key <your_api_key>"
        ),
        "account": account_response.get("account") if account_response else None,
        "cloud_access": access,
        "next_suggested": access.get("next_suggested"),
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
    from jobagent.infra.profile_contract import stamp_profile

    profile = stamp_profile(response["profile"])
    output = Path(args.output).expanduser() if args.output else profile_path()
    save_json(output, profile)
    return {
        "ok": True,
        "profile_path": str(output),
        "next_suggested": "jobagent boss discover",
    }


def _with_login_workflow(platform: str, payload: dict[str, Any]) -> dict[str, Any]:
    from jobagent.infra import rounds

    logged_in = bool(payload.get("ok") and payload.get("logged_in"))
    workflow_before = rounds.round_status()
    platform_before = dict(
        (workflow_before.get("platforms") or {}).get(platform) or {}
    )
    status_before = str(platform_before.get("status") or "pending")
    evidence_before = dict(platform_before.get("evidence") or {})
    resumable_statuses = {"discovered", "reviewed", "sent"}
    stored_next = str(platform_before.get("next_suggested") or "")

    inferred_status = ""
    if " audit" in stored_next:
        inferred_status = "sent"
    elif " greet send" in stored_next or " apply send" in stored_next:
        inferred_status = "reviewed"
    elif " greet preview" in stored_next or " apply review" in stored_next:
        inferred_status = "discovered"

    if logged_in:
        restored_status = (
            str(evidence_before.get("resume_status") or inferred_status)
            if status_before in {"active", "blocked"}
            else status_before
        )
        preserve_progress = restored_status in resumable_statuses
        target_status = restored_status if preserve_progress else "login_verified"
        next_suggested = (
            str(evidence_before.get("resume_next_suggested") or stored_next)
            if status_before in {"active", "blocked"} and preserve_progress
            else stored_next
            if preserve_progress
            else ""
        ) or rounds._default_next_command(platform, target_status)
        evidence = (
            {
                key: value
                for key, value in evidence_before.items()
                if key not in {"resume_status", "resume_next_suggested"}
            }
            if preserve_progress
            else {}
        )
    else:
        target_status = "blocked"
        next_suggested = f"jobagent {platform} login --check"
        evidence = dict(evidence_before) if status_before == "blocked" else {}
        resumable_status = (
            status_before if status_before in resumable_statuses else inferred_status
        )
        if resumable_status in resumable_statuses:
            evidence["resume_status"] = resumable_status
            evidence["resume_next_suggested"] = (
                stored_next
                or rounds._default_next_command(platform, resumable_status)
            )

    evidence["login"] = {
        "logged_in": logged_in,
        "requires_user_action": bool(payload.get("requires_user_action")),
        "error": payload.get("error"),
    }
    rounds.set_platform_status(
        platform,
        target_status,
        command=f"jobagent {platform} login",
        evidence=evidence,
        next_suggested=next_suggested,
    )
    workflow = rounds.round_status()
    payload["next_suggested"] = workflow.get("next_suggested") or next_suggested
    payload["workflow"] = workflow
    return payload


def _login(platform: str, args: argparse.Namespace) -> dict[str, Any]:
    if platform == "boss":
        from jobagent.drivers.boss import create_driver
        from jobagent.drivers.boss.cdp_driver import CDPBossDriver

        driver = create_driver(platform="boss")
        if not isinstance(driver, CDPBossDriver):
            result = driver.open_url_in_new_tab(
                "https://www.zhipin.com/web/user/?ka=header-login", wait_seconds=2
            )
            return _with_login_workflow(platform, {"platform": platform, **result})
        if driver.check_login_status():
            return _with_login_workflow(
                platform,
                {"ok": True, "platform": platform, "logged_in": True},
            )
        if args.wait:
            from jobagent.infra.diagnostics import emit_stage

            logged_in = driver.ensure_logged_in(
                timeout=args.timeout,
                on_waiting=lambda _visible: emit_stage(
                    "login_waiting",
                    platform="boss",
                    browser="Job Agent dedicated Chrome",
                    user_prompt="请在标题含 [Job Agent] 的浏览器窗口中登录 Boss 直聘。",
                ),
            )
            return _with_login_workflow(
                platform,
                {"ok": logged_in, "platform": platform, "logged_in": logged_in},
            )
        driver.open_url_in_new_tab("https://www.zhipin.com/web/user/?ka=header-login", wait_seconds=2)
        return _with_login_workflow(
            platform,
            {
                "ok": False,
                "platform": platform,
                "logged_in": False,
                "requires_user_action": True,
                "user_action": "login_boss",
                "user_prompt": "请在已经打开的 Job Agent 浏览器中登录 Boss 直聘，完成后回复我“已登录”。",
            },
        )

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
    return _with_login_workflow(platform, status.to_dict())


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


def _prepare_client_upgrade(args: argparse.Namespace) -> dict[str, Any]:
    from jobagent.infra.client_upgrade import (
        enforce_upgrade_for_command,
        run_client_upgrade,
    )

    report = run_client_upgrade()
    setattr(args, "_client_upgrade_report", report)
    command = args.command
    if command == "round":
        command = f"round-{args.round_command}"
    return enforce_upgrade_for_command(command, report)


def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "init":
        return _init(args)
    if args.command == "upgrade-check":
        from jobagent.infra.upgrade_readiness import run_upgrade_check

        return run_upgrade_check(
            client_state=getattr(args, "_client_upgrade_report", None),
        )
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

        return check_for_update(auto_apply=False, force=True)
    if args.command == "support":
        from jobagent.infra.support import support_star_payload

        return support_star_payload()
    if args.command == "round":
        from jobagent.infra.rounds import round_status, set_platform_status

        if args.round_command == "status":
            return {"ok": True, "workflow": round_status()}
        if not args.confirm_skip:
            return {
                "ok": False,
                "error": "user_confirmation_required",
                "platform": args.platform,
                "message": "Explicitly confirm skipping this platform for the current round.",
            }
        set_platform_status(args.platform, "skipped_this_round", command="jobagent round skip")
        return {"ok": True, "platform": args.platform, "workflow": round_status()}

    platform = args.command
    from jobagent.infra.rounds import assert_platform_turn

    if args.platform_command == "login":
        assert_platform_turn(platform)
        return _login(platform, args)
    if args.platform_command == "discover":
        from jobagent.application.discover import run_discover

        assert_platform_turn(platform)
        return run_discover(platform, wait_seconds=args.wait_seconds, page_delay=args.page_delay)
    if args.platform_command == "audit":
        from jobagent.application.delivery import audit_platform

        assert_platform_turn(platform)
        return audit_platform(platform, recent=args.recent)
    if platform == "boss" and args.platform_command == "greet":
        if args.greet_command == "preview":
            from jobagent.application.review import review_decision

            assert_platform_turn(platform)
            return review_decision(
                platform,
                input_path=args.input,
                promoted_ids=args.promote,
                confirm_promote=args.confirm_promote,
                output_path=args.output,
            )
    else:
        if args.apply_command == "review":
            from jobagent.application.review import review_decision

            assert_platform_turn(platform)
            return review_decision(
                platform,
                input_path=args.input,
                promoted_ids=args.promote,
                confirm_promote=args.confirm_promote,
                output_path=args.output,
            )
    assert_platform_turn(platform)
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
        _prepare_client_upgrade(args)
        result = _dispatch(args)
        _print(result)
        if result.get("ok") is False:
            raise SystemExit(2)
    except KeyboardInterrupt:
        _print({"ok": False, "error": "interrupted"}, stream=sys.stderr)
        raise SystemExit(130) from None
    except Exception as exc:
        from jobagent.infra.cloud_client import CloudError
        from jobagent.infra.client_upgrade import UpgradeCompatibilityError
        from jobagent.infra.platform_lock import PlatformLockError
        from jobagent.infra.rounds import RoundOrderError
        from jobagent.infra.protocol import ProtocolError
        from jobagent.platforms.discovery import CollectionError

        try:
            from jobagent.application.delivery import UserInterventionRequired
        except ImportError:
            UserInterventionRequired = ()  # type: ignore[assignment,misc]
        if isinstance(exc, UpgradeCompatibilityError):
            payload = exc.payload
        elif isinstance(exc, CollectionError):
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
        elif isinstance(exc, RoundOrderError):
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
            from jobagent.infra.diagnostics import write_exception_log

            log_path = write_exception_log(exc, command=" ".join(sys.argv))
            payload = {
                "ok": False,
                "error": type(exc).__name__,
                "message": str(exc),
                "diagnostic_log": str(log_path),
            }
        _print(payload, stream=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
