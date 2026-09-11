"""Job Agent 0.3 public command surface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from jobagent import __version__


_UPDATE_RESUME_ENV = "JOBAGENT_UPDATE_RESUME"


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
    parser.add_argument(
        "--preview-id",
        help="Delivery-preview handoff ID emitted by review",
    )
    parser.add_argument(
        "--authorization-id",
        help="User-confirmed delivery authorization emitted by interaction respond",
    )
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

    account = sub.add_parser("account", help="Inspect or switch local account-bound state")
    account_sub = account.add_subparsers(dest="account_command", required=True)
    account_sub.add_parser("status")
    bind = account_sub.add_parser("bind")
    bind.add_argument("--confirm-legacy", action="store_true")
    switch = account_sub.add_parser("switch")
    switch.add_argument("--new-state", action="store_true")

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
    resume_sub.add_parser("list", help="List the online resumes kept in the workbench")
    resume_status = resume_sub.add_parser(
        "status", help="Summarize online resume preparation state"
    )
    resume_status.add_argument("--id", help="Reserved for a single-resume view (planned)")

    profile = sub.add_parser("profile", help="View the current resume profile")
    profile.add_subparsers(dest="profile_command", required=True).add_parser("show")

    platforms = sub.add_parser("platforms", help="View supported recruiting platforms")
    platforms_sub = platforms.add_subparsers(dest="platforms_command", required=True)
    platforms_sub.add_parser("status")
    health = platforms_sub.add_parser("health")
    health.add_argument("--platform", choices=["boss", "liepin", "zhilian", "51job"])

    browser = sub.add_parser("browser", help="Inspect the dedicated browser without navigation")
    browser_sub = browser.add_subparsers(dest="browser_command", required=True)
    diagnose = browser_sub.add_parser("diagnose")
    diagnose.add_argument("--platform", required=True, choices=["boss", "liepin", "zhilian", "51job"])

    work = sub.add_parser("work", help="Continue native Computer Use tasks without a browser driver")
    work_sub = work.add_subparsers(dest="work_command", required=True)
    for name in ("next", "status", "contract"):
        work_sub.add_parser(name)
    for name in ("begin", "submit", "cancel"):
        action = work_sub.add_parser(name)
        action.add_argument("--work-id", required=True)
        if name == "submit":
            action.add_argument("--result", required=True, help="Local typed UI-observation JSON")
        if name == "cancel":
            action.add_argument("--confirm-cancel", action="store_true")

    update = sub.add_parser("update", help="Check signed client release policy")
    update.add_subparsers(dest="update_command", required=True).add_parser("check")

    support = sub.add_parser("support", help="Voluntary project support")
    support.add_subparsers(dest="support_command", required=True).add_parser("star")

    interaction = sub.add_parser("interaction", help="Continue a structured host interaction")
    interaction_sub = interaction.add_subparsers(dest="interaction_command", required=True)
    interaction_respond = interaction_sub.add_parser("respond")
    interaction_respond.add_argument("--interaction-id", required=True)
    interaction_respond.add_argument(
        "--choice",
        choices=[
            "accept_suggested",
            "append_roles",
            "replace_roles",
            "rebind_resume",
            "confirm_all",
            "exclude_jobs",
            "cancel_delivery",
        ],
    )
    interaction_respond.add_argument(
        "--target-role",
        action="append",
        default=[],
        help="Target role supplied for append or replace choices; repeat for multiple roles",
    )
    interaction_respond.add_argument(
        "--target-city",
        action="append",
        default=[],
        help="Target city supplied for city confirmation; repeat for multiple cities",
    )
    interaction_respond.add_argument(
        "--resume-id",
        help="Resume id chosen for the round resume-binding confirmation",
    )
    interaction_respond.add_argument(
        "--exclude-index",
        action="append",
        type=int,
        default=[],
        help="Displayed job number to exclude; repeat for multiple jobs",
    )

    delivery_round = sub.add_parser("round", help="View or update the multi-platform round")
    round_sub = delivery_round.add_subparsers(dest="round_command", required=True)
    round_start = round_sub.add_parser("start")
    round_start.add_argument(
        "--accept-suggested",
        action="store_true",
        help="Confirm the target roles suggested from the current profile",
    )
    round_start.add_argument(
        "--target-role",
        action="append",
        default=[],
        help="Explicit target role for this round; repeat for multiple roles",
    )
    round_start.add_argument(
        "--resume-binding",
        metavar="BINDING_ID",
        help="Bind this round's deliveries to an existing workbench resume binding",
    )
    round_start.add_argument(
        "--no-resume-binding",
        action="store_true",
        help="Skip resume binding and use the local profile for this round",
    )
    round_sub.add_parser("status")
    round_audit = round_sub.add_parser("audit")
    round_audit.add_argument("--platform", choices=["boss", "liepin", "zhilian", "51job"])
    round_audit.add_argument("--recent", "-n", type=int, default=20)
    round_audit.add_argument("--details", action="store_true")
    round_audit.add_argument("--failures-only", action="store_true")
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
        audit.add_argument("--details", action="store_true")
        audit.add_argument("--failures-only", action="store_true")
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
    payload: dict[str, Any] = {
        "ok": True,
        "credentials_path": str(path),
        "workbench_url": "https://agentmesh360.com/workbench/",
    }
    if account is not None:
        from jobagent.infra.account_state import AccountStateError, ensure_account_state
        from jobagent.infra.product_announcements import (
            mark_workbench_launch_announced,
        )

        payload["account"] = account
        payload["cloud_access"] = _cloud_access(account, profile_exists=False)
        try:
            payload["local_state"] = ensure_account_state(
                account,
                api_key=args.key.strip(),
            )
        except AccountStateError as exc:
            payload.update(exc.payload)
            payload["credentials_path"] = str(path)
            payload["account"] = account
            return payload
        _record_initialized_safely(args.key.strip())
        mark_workbench_launch_announced()
    access = payload.get("cloud_access") or {}
    payload["next_suggested"] = (
        "jobagent resume analyze --file <resume>"
        if access.get("usable")
        else "https://agentmesh360.com/app/?lang=zh-CN#pricing"
        if access.get("paid_pass_required")
        else "jobagent doctor env"
    )
    return payload


def _schedule_analytics_flush_safely() -> None:
    """Give a pending spool command lifetime without affecting the command."""

    try:
        from jobagent.infra.analytics import schedule_flush

        schedule_flush()
    except Exception:
        pass


def _record_initialized_safely(api_key: str) -> None:
    try:
        from jobagent.infra.analytics import record_jobagent_initialized

        record_jobagent_initialized(api_key=api_key)
    except Exception:
        pass


def _account(args: argparse.Namespace) -> dict[str, Any]:
    from jobagent.infra import cloud_client
    from jobagent.infra.account_state import (
        account_ref_from_response,
        bind_legacy_state,
        state_owner_status,
        switch_account_state,
    )
    from jobagent.infra.credentials import load_api_key

    account_response = cloud_client.me()
    api_key = load_api_key()
    if args.account_command == "status":
        account_ref = account_ref_from_response(account_response)
        return {
            "ok": True,
            "local_state": state_owner_status(account_ref),
            "next_suggested": None,
        }
    if args.account_command == "bind":
        result = bind_legacy_state(
            account_response,
            confirm_legacy=args.confirm_legacy,
            api_key=api_key,
        )
    else:
        result = switch_account_state(
            account_response,
            new_state=args.new_state,
            api_key=api_key,
        )
    if result.get("ok") is True:
        _record_initialized_safely(str(api_key or ""))
    return result


def _attach_pending_product_announcements(
    result: dict[str, Any],
    *,
    account_verified: bool,
) -> dict[str, Any]:
    if not account_verified or result.get("ok") is False:
        return result
    from jobagent.infra.product_announcements import (
        claim_workbench_launch_announcement,
    )

    announcement = claim_workbench_launch_announcement()
    if not announcement:
        return result
    existing = result.get("announcements")
    announcements = list(existing) if isinstance(existing, list) else []
    announcements.append(announcement)
    return {**result, "announcements": announcements}


def _offline_local_control_allowed(args: argparse.Namespace) -> bool:
    return bool(
        args.command == "round"
        and (
            args.round_command == "status"
            or (args.round_command == "skip" and args.confirm_skip)
        )
    )


def _verify_state_owner_for_command(args: argparse.Namespace) -> dict[str, Any] | None:
    if args.command == "work" and args.work_command == "contract":
        return None
    if args.command in {"account", "doctor", "init", "platforms", "update", "upgrade-check"}:
        return None
    if (
        args.command == "round"
        and args.round_command == "skip"
        and not args.confirm_skip
    ):
        return None
    from jobagent.infra import cloud_client
    from jobagent.infra.account_state import (
        AccountStateError,
        ensure_account_state,
        verify_offline_account_state,
    )
    from jobagent.infra.credentials import load_api_key

    api_key = load_api_key()
    if not api_key:
        raise AccountStateError(
            {
                "ok": False,
                "error": "api_key_required",
                "message": "Configure an AgentMesh API key before reading or changing account-bound state.",
                "next_suggested": "jobagent init --key <your_api_key>",
            }
        )
    try:
        local_state = ensure_account_state(
            cloud_client.me(),
            api_key=api_key,
        )
    except cloud_client.CloudError as exc:
        if not (
            _offline_local_control_allowed(args)
            and cloud_client.is_transient_transport_error(exc)
        ):
            raise
        proof = verify_offline_account_state(api_key)
        return {
            "mode": "offline",
            "offline": True,
            "stale": True,
            "verified_at": proof.get("verified_at"),
            "reason_code": exc.code,
        }
    return {
        "mode": "online",
        "offline": False,
        "stale": False,
        "verified_at": local_state.get("verified_at"),
    }


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
    python_available = bool(shutil.which("python3"))
    chrome_available = bool(
        Path("/Applications/Google Chrome.app").exists()
        or shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
    )
    cloud_healthy = cloud.get("status") == "ok"
    environment_healthy = bool(
        python_available and chrome_available and key_present and key_valid and cloud_healthy
    )
    local_state: dict[str, Any] = {
        "status": "account_unverified",
        "ready": False,
    }
    profile_exists = False
    if account_response is not None:
        from jobagent.infra.account_state import AccountStateError, ensure_account_state

        try:
            local_state = ensure_account_state(
                account_response,
                api_key=key,
            )
        except AccountStateError as exc:
            local_state = {
                key: value
                for key, value in exc.payload.items()
                if key not in {"ok", "message"}
            }
    if local_state.get("ready"):
        from jobagent.infra.state import profile_path

        profile_exists = profile_path().exists()
    access = (
        _cloud_access(account_response, profile_exists=profile_exists)
        if account_response is not None
        else {
            "usable": False,
            "reason": key_error or "api_key_required",
            "credit": None,
            "source": "none",
            "expires_at": None,
            "required_credits": 5,
            "paid_pass_required": None,
        }
    )
    from jobagent.infra.rounds import round_status

    workflow = round_status() if local_state.get("ready") else None
    blocked_by: list[str] = []
    if not environment_healthy:
        blocked_by.append("environment")
    if not local_state.get("ready"):
        blocked_by.append(str(local_state.get("error") or local_state.get("status")))
    if not access.get("usable"):
        blocked_by.append(str(access.get("reason") or "cloud_access"))
    if not key_present or not key_valid:
        next_suggested = "jobagent init --key <your_api_key>"
    elif not cloud_healthy:
        next_suggested = "jobagent doctor env"
    elif not local_state.get("ready"):
        next_suggested = str(local_state.get("next_suggested") or "jobagent account status")
    elif not access.get("usable"):
        next_suggested = "https://agentmesh360.com/app/?lang=zh-CN#pricing"
    elif not profile_exists:
        next_suggested = "jobagent resume analyze --file <resume>"
    else:
        next_suggested = str((workflow or {}).get("next_suggested") or "jobagent round start")
    return {
        "ok": environment_healthy,
        "environment_healthy": environment_healthy,
        "browser_executor": "codex_native",
        "host_capability": {"native_computer_use": "requires_host_verification", "browser_driver": "none"},
        "python": sys.version.split()[0],
        "chrome": chrome_available,
        "api_key_configured": key_present,
        "api_key_valid": key_valid,
        "api_key_error": key_error,
        "api_key_action": (
            None
            if key_valid
            else "jobagent init --key <your_api_key>"
        ),
        "account": account_response.get("account") if account_response else None,
        "local_state": local_state,
        "cloud_access": access,
        "round": workflow,
        "workflow": {
            "ready": not blocked_by,
            "blocked_by": blocked_by,
            "profile_exists": profile_exists,
            "next_suggested": next_suggested,
        },
        "next_suggested": next_suggested,
        "cloud": cloud,
    }


def _resume_center_overview() -> dict[str, Any]:
    """Read-only workbench facts: online resumes, confirmation state, receipt."""
    from jobagent.infra import cloud_client

    preparation = cloud_client.resume_center_preparation()
    resumes = [
        {
            "id": resume.get("id"),
            "name": resume.get("name"),
            "target_role": resume.get("target_role"),
            "version": resume.get("version"),
            "confirmed": bool(resume.get("confirmed_revision_id")),
            "has_draft": bool(resume.get("has_draft")),
            "updated_at": resume.get("updated_at"),
        }
        for resume in preparation.get("resumes") or []
        if isinstance(resume, dict)
    ]
    return {
        "ok": True,
        "source": "resume_center",
        "state": preparation.get("state"),
        "ready": bool(preparation.get("ready")),
        "resumes": resumes,
        "receipt": preparation.get("receipt"),
        "next_suggested": preparation.get("next_suggested"),
        "workbench_url": preparation.get("workbench_url"),
    }


def _profile_show() -> dict[str, Any]:
    """Cloud facts first (resume center), local snapshot only as a fallback."""
    from jobagent.infra import cloud_client
    from jobagent.infra.state import load_json, profile_path

    fallback_reason: str | None = None
    try:
        overview = _resume_center_overview()
        if overview["resumes"]:
            return {
                "ok": True,
                "source": "resume_center",
                "state": overview["state"],
                "resumes": overview["resumes"],
                "receipt": overview["receipt"],
                "next_suggested": overview["next_suggested"],
                "workbench_url": overview["workbench_url"],
            }
        fallback_reason = "resume_center_empty"
    except cloud_client.CloudError as exc:
        # Auth and permission failures must surface, never mask as local data.
        if exc.status in (401, 403, 404):
            raise
        fallback_reason = f"cloud_error:{exc.code or exc.status}"
    return {
        "ok": True,
        "source": "local_snapshot",
        "profile": load_json(profile_path()),
        "stale_warning": (
            "This is the local snapshot from the last CLI analysis on this "
            "machine. It may be outdated and does not include workbench "
            "resumes."
        ),
        "fallback_reason": fallback_reason,
        "next_suggested": "jobagent resume list",
    }


def _resume_analyze(args: argparse.Namespace) -> dict[str, Any]:
    from jobagent.domain.resume_parser import ResumeParser
    from jobagent.application.round_intent import (
        confirmed_target_cities,
        with_target_cities,
    )
    from jobagent.infra import cloud_client
    from jobagent.infra import rounds
    from jobagent.infra.state import (
        current_round_path,
        load_json,
        profile_path,
        save_json,
    )

    source = Path(args.file).expanduser()
    canonical_profile_path = profile_path()
    output = Path(args.output).expanduser() if args.output else canonical_profile_path
    existing_profile = load_json(canonical_profile_path) or {}
    target_cities = list(args.target_cities or confirmed_target_cities(existing_profile))
    if not target_cities:
        prompt = (
            "当前简历画像还没有目标城市。请告诉我本轮想看的城市，可以填写多个，"
            "例如：郑州、杭州。"
        )
        return {
            "ok": False,
            "error": "target_cities_required",
            "requires_user_action": True,
            "user_action": "confirm_target_cities",
            "user_prompt": prompt,
            "next_suggested": (
                "jobagent resume analyze --file <resume> "
                "--target-cities <city1> [city2 ...]"
            ),
        }
    if output == canonical_profile_path:
        current = load_json(current_round_path()) or {}
        conflict = rounds.active_round_profile_refresh_conflict(current)
        if conflict is not None:
            return {
                "ok": False,
                "error": "profile_update_blocked_active_round",
                "message": conflict["message"],
                "conflict": conflict["code"],
                "requires_user_action": True,
                "user_prompt": (
                    "当前轮次已经产生可审计进度，不能在本轮中更换简历画像。"
                    "请先查看当前轮次状态。"
                ),
                "next_suggested": "jobagent round status",
            }
    text = ResumeParser().parse(source)
    hints = {
        key: value
        for key, value in {
            "target_role": args.target_role,
            "target_cities": target_cities,
        }.items()
        if value
    }
    response = cloud_client.resume_analyze(text, source.name, hints or None)
    from jobagent.infra.profile_contract import stamp_profile

    profile = stamp_profile(response["profile"])
    profile, target_cities = with_target_cities(profile, target_cities)
    save_json(output, profile)
    result = {
        "ok": True,
        "profile_path": str(output),
        "target_cities": target_cities,
        "next_suggested": "jobagent round start",
    }
    if output == canonical_profile_path:
        reconciliation = rounds.reconcile_active_round_profile(profile)
        workflow = reconciliation["workflow"]
        if workflow.get("status") == "active":
            result.update(
                {
                    "profile_reconciled": reconciliation["changed"],
                    "workflow": workflow,
                    "next_suggested": workflow.get("next_suggested"),
                }
            )
    return result


def _staged_resume_binding() -> dict[str, Any] | None:
    from jobagent.application.round_resume_binding import load_pending_binding

    staged = load_pending_binding()
    return (staged or {}).get("binding") or None


def _consume_staged_resume_binding() -> None:
    from jobagent.application.round_resume_binding import clear_pending_binding

    clear_pending_binding()


def _interaction_respond(args: argparse.Namespace) -> dict[str, Any]:
    from jobagent.application.round_intent import (
        REBIND_RESUME_CHOICE,
        build_round_intent_from_choice,
        confirmed_target_cities,
        target_role_input_request,
        with_target_cities,
    )
    from jobagent.infra.interaction_state import (
        clear_pending_interaction,
        load_pending_interaction,
        save_pending_interaction,
    )
    from jobagent.infra.protocol import digest_payload
    from jobagent.infra.rounds import (
        reconcile_active_round_profile,
        round_status,
        start_new_round,
        utc_now,
    )
    from jobagent.infra.state import (
        current_round_path,
        load_json,
        profile_path,
        save_json,
    )

    interaction_id = str(args.interaction_id or "").strip()
    pending = load_pending_interaction()
    if pending and str(pending.get("kind") or "").startswith("delivery_"):
        if str(pending.get("interaction_id") or "") != interaction_id:
            return {
                "ok": False,
                "error": "interaction_not_pending",
                "message": "This delivery interaction is no longer waiting for that answer.",
                "next_suggested": str(
                    (pending.get("interaction") or {}).get("fallback_text") or ""
                ),
            }
        from jobagent.application.delivery_confirmation import (
            respond_delivery_confirmation,
        )

        return respond_delivery_confirmation(
            pending,
            choice=str(args.choice or pending.get("choice") or ""),
            exclude_indices=list(args.exclude_index or []),
        )
    if pending and str(pending.get("stage") or "") == "resume_binding":
        from jobagent.application.round_resume_binding import (
            load_pending_binding,
            respond_resume_selection,
        )

        if str(pending.get("interaction_id") or "") != interaction_id:
            return {
                "ok": False,
                "error": "interaction_not_pending",
                "message": "This resume selection is no longer waiting for that answer.",
                "next_suggested": "jobagent round start",
            }
        resume_id = str(args.resume_id or "").strip()
        if not resume_id:
            return {
                "ok": False,
                "error": "invalid_interaction_response",
                "message": "Pass --resume-id with one of the offered resume ids.",
                "next_suggested": str(
                    (pending.get("interaction") or {}).get("fallback_text") or ""
                ),
            }
        result = respond_resume_selection(pending, resume_id)
        if result.get("ok"):
            clear_pending_interaction()
            staged = load_pending_binding()
            binding = (staged or {}).get("binding") or {}
            current = load_json(current_round_path())
            if binding.get("id") and current and current.get("status") == "active" and current.get("round_id"):
                from jobagent.infra.rounds import attach_round_resume_binding

                attach_round_resume_binding(binding)
                result["workflow"] = round_status()
        return result
    profile = load_json(profile_path())
    if not profile:
        return {
            "ok": False,
            "error": "profile_required",
            "message": "Analyze a resume before answering the target-role interaction.",
            "next_suggested": "jobagent resume analyze --file <resume>",
        }
    local_profile = profile
    # A pending target-role interaction for a bound round is answered against
    # the bound resume's own material — the same profile the suggestion and
    # digest were built from — never the stale local snapshot.
    respond_binding = None
    respond_cities: list[str] | None = None
    if (
        pending
        and str(pending.get("stage") or "") in {"choice", "roles"}
        and (_staged_resume_binding() or {}).get("id")
    ):
        from jobagent.application.round_resume_binding import binding_material_profile
        from jobagent.infra import cloud_client

        respond_binding = _staged_resume_binding()
        try:
            material = binding_material_profile(respond_binding)
        except cloud_client.CloudError as exc:
            if exc.code == "preparation_required":
                _consume_staged_resume_binding()
                clear_pending_interaction()
                return {
                    "ok": False,
                    "error": "resume_binding_paused",
                    "message": (
                        "绑定的简历已变更或不再可用。请重新选择简历后再开轮。"
                    ),
                    "next_suggested": "jobagent round start",
                }
            return {
                "ok": False,
                "error": "resume_binding_material_unavailable",
                "message": (
                    "暂时无法获取绑定简历的材料（网络或服务不可用）。请稍后重试。"
                ),
                "retryable": bool(exc.retryable),
                "next_suggested": "jobagent round start",
            }
        profile = material["profile"]
        respond_cities = confirmed_target_cities(profile) or confirmed_target_cities(local_profile)

    current = load_json(current_round_path()) or {}
    receipt = current.get("interaction_receipt") or {}
    receipt_ids = {
        str(value)
        for value in receipt.get("interaction_ids") or []
        if str(value).strip()
    }
    if interaction_id in receipt_ids:
        recorded_choice = str(receipt.get("choice") or "")
        if args.choice and args.choice != recorded_choice:
            return {
                "ok": False,
                "error": "interaction_response_conflict",
                "message": "This interaction already created a round with a different answer.",
                "next_suggested": "jobagent round status",
            }
        if args.target_role:
            replay_binding = (current.get("resume_binding") if isinstance(current, dict) else None) or respond_binding
            try:
                requested = build_round_intent_from_choice(
                    profile,
                    choice=recorded_choice,
                    target_roles=args.target_role,
                    resume_binding=replay_binding,
                    target_cities=(
                        ((current.get("intent") or {}).get("target_cities"))
                        if isinstance(current, dict)
                        else None
                    )
                    or respond_cities
                    or [],
                )
            except ValueError as exc:
                return {
                    "ok": False,
                    "error": "invalid_interaction_response",
                    "message": str(exc),
                    "next_suggested": "jobagent round status",
                }
            recorded_roles = (current.get("intent") or {}).get("target_roles") or []
            if [role.casefold() for role in requested["target_roles"]] != [
                str(role).casefold() for role in recorded_roles
            ]:
                return {
                    "ok": False,
                    "error": "interaction_response_conflict",
                    "message": "This interaction already created a round with different target roles.",
                    "next_suggested": "jobagent round status",
                }
        return {
            "ok": True,
            "idempotent_replay": True,
            "interaction_receipt": receipt,
            "workflow": round_status(),
        }

    if not pending or str(pending.get("interaction_id") or "") != interaction_id:
        return {
            "ok": False,
            "error": "interaction_not_pending",
            "message": "This interaction is no longer waiting for an answer.",
            "next_suggested": "jobagent round start",
        }
    if str(pending.get("profile_digest") or "") != digest_payload(profile):
        clear_pending_interaction()
        return {
            "ok": False,
            "error": "interaction_context_changed",
            "message": "The resume profile changed after this interaction was created.",
            "next_suggested": "jobagent round start",
        }

    stage = str(pending.get("stage") or "")
    if stage == "cities" or str(pending.get("kind") or "") == "target_city_input":
        try:
            updated_profile, target_cities = with_target_cities(
                profile,
                list(args.target_city or []),
            )
        except ValueError as exc:
            return {
                "ok": False,
                "error": "invalid_interaction_response",
                "message": str(exc),
                "next_suggested": str(
                    (pending.get("interaction") or {}).get("fallback_text") or ""
                ),
            }
        save_json(profile_path(), updated_profile)
        reconciliation = reconcile_active_round_profile(updated_profile)
        clear_pending_interaction()
        workflow = reconciliation["workflow"]
        next_suggested = (
            workflow.get("next_suggested")
            if workflow.get("status") == "active"
            else "jobagent round start"
        )
        return {
            "ok": True,
            "target_cities": target_cities,
            "profile_reconciled": reconciliation["changed"],
            "workflow": workflow,
            "next_suggested": next_suggested,
        }

    choice = str(args.choice or pending.get("choice") or "")
    if stage == "choice" and not args.choice:
        return {
            "ok": False,
            "error": "invalid_interaction_response",
            "message": "Select one target-role option before continuing.",
            "next_suggested": str(
                (pending.get("interaction") or {}).get("fallback_text") or ""
            ),
        }
    if stage == "roles" and args.choice and args.choice != pending.get("choice"):
        return {
            "ok": False,
            "error": "interaction_response_conflict",
            "message": "The role-entry answer does not match the selected option.",
            "next_suggested": str(
                (pending.get("interaction") or {}).get("fallback_text") or ""
            ),
        }
    if respond_binding and choice == REBIND_RESUME_CHOICE:
        clear_pending_interaction()
        _consume_staged_resume_binding()
        return {
            "ok": True,
            "rebind_requested": True,
            "message": (
                "已取消本轮开始。请重新执行 round start，选择（或上传）目标方向的简历后再开轮。"
            ),
            "next_suggested": "jobagent round start",
        }
    if respond_binding and choice in {"append_roles", "replace_roles"}:
        return {
            "ok": False,
            "error": "invalid_interaction_response",
            "message": (
                "本轮已绑定简历，只能投递该简历的方向。"
                "如需投递其他方向，请换绑对应方向的简历（重新执行 round start 选择简历）。"
            ),
            "next_suggested": "jobagent round start",
        }
    if choice in {"append_roles", "replace_roles"} and not args.target_role:
        follow_up = target_role_input_request(
            profile,
            root_interaction_id=str(pending.get("root_interaction_id") or interaction_id),
            choice=choice,
        )
        follow_up_interaction = follow_up["interaction"]
        save_pending_interaction(
            follow_up_interaction,
            stage="roles",
            profile_digest=digest_payload(profile),
            suggested_roles=list(pending.get("suggested_roles") or []),
            previous_round_id=pending.get("previous_round_id"),
            root_interaction_id=str(pending.get("root_interaction_id") or interaction_id),
            choice=choice,
        )
        return follow_up
    try:
        intent = build_round_intent_from_choice(
            profile,
            choice=choice,
            target_roles=args.target_role,
            resume_binding=respond_binding,
            target_cities=respond_cities,
        )
    except ValueError as exc:
        return {
            "ok": False,
            "error": "invalid_interaction_response",
            "message": str(exc),
            "next_suggested": str(
                (pending.get("interaction") or {}).get("fallback_text") or ""
            ),
        }

    root_interaction_id = str(pending.get("root_interaction_id") or interaction_id)
    interaction_ids = list(dict.fromkeys([root_interaction_id, interaction_id]))
    receipt = {
        "protocol": "agentmesh360.interaction_required",
        "protocol_version": 1,
        "interaction_ids": interaction_ids,
        "choice": choice,
        "response_digest": digest_payload(
            {
                "interaction_id": interaction_id,
                "choice": choice,
                "target_roles": intent["target_roles"],
            }
        ),
        "completed_at": utc_now(),
    }
    created = start_new_round(
        intent,
        interaction_receipt=receipt,
        resume_binding=_staged_resume_binding(),
    )
    if not created.get("resume_binding") and _staged_resume_binding():
        # The round already existed; attach the staged binding to it — but
        # never a binding whose direction contradicts the active round.
        from jobagent.application.round_resume_binding import binding_direction_conflict
        from jobagent.infra.rounds import attach_round_resume_binding

        staged_binding = _staged_resume_binding()
        conflict = binding_direction_conflict(created, staged_binding)
        if conflict:
            return conflict
        attach_round_resume_binding(staged_binding)
    clear_pending_interaction()
    _consume_staged_resume_binding()
    return {
        "ok": True,
        "interaction_receipt": receipt,
        "workflow": round_status(),
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
    resumable_statuses = {
        "discovered",
        "awaiting_delivery_confirmation",
        "reviewed",
        "sent",
    }
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
        "schema_version": 1,
        "logged_in": logged_in,
        "platform": platform,
        "round_id": workflow_before.get("round_id"),
        "browser_session_id": workflow_before.get("browser_session_id"),
        "verified_at": rounds.utc_now() if logged_in else None,
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
    from jobagent.infra.diagnostics import emit_stage

    resume_raw = os.environ.pop(_UPDATE_RESUME_ENV, None)
    if resume_raw:
        try:
            resume = json.loads(resume_raw)
        except json.JSONDecodeError:
            resume = None
        if isinstance(resume, dict):
            from_version = resume.get("from_version")
            to_version = resume.get("to_version")
            command = resume.get("command")
            if all(
                isinstance(value, str) and value
                for value in (from_version, to_version, command)
            ):
                emit_stage(
                    "client_command_resumed",
                    from_version=from_version,
                    to_version=to_version,
                    command=command,
                    message="Job Agent is continuing the requested command after updating.",
                )
                setattr(args, "_client_update_resume_reported", True)
    if os.environ.get("JOBAGENT_SKIP_UPDATE") == "1" or args.command == "update":
        return
    from jobagent.infra.browser_work import has_inflight
    if has_inflight():
        # A UI executor lives outside the CLI process. PID expiry cannot make an
        # unresolved action safe to replay or replace during an update.
        setattr(args, "_native_update_deferred", True)
        return
    from jobagent.infra.release_update import maybe_auto_update

    result = maybe_auto_update(on_event=emit_stage)
    if result.get("status") == "updated":
        os.environ[_UPDATE_RESUME_ENV] = json.dumps(
            {
                "from_version": result["from_version"],
                "to_version": result["to_version"],
                "command": f"jobagent {args.command}",
            },
            separators=(",", ":"),
        )
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
    bootstrap_from_version = report.get("from_version") if report.get("version_changed") else None
    if (
        not bootstrap_from_version
        and report.get("upgrade_detected")
        and report.get("from_version") == "unknown"
        and not getattr(args, "_client_update_resume_reported", False)
    ):
        from jobagent.infra.release_update import previous_managed_version

        bootstrap_from_version = previous_managed_version()
    bootstrap_update = bool(
        bootstrap_from_version
        and bootstrap_from_version != report.get("to_version")
        and not getattr(args, "_client_update_resume_reported", False)
    )
    if bootstrap_update:
        from jobagent.infra.diagnostics import emit_stage

        emit_stage(
            "client_update_completed",
            from_version=bootstrap_from_version,
            to_version=report["to_version"],
            automatic=True,
            bootstrap_compatibility=True,
            message="Job Agent updated successfully.",
        )
    command = args.command
    if command == "round":
        command = f"round-{args.round_command}"
    if command == "work":
        command = f"work-{args.work_command}"
    result = enforce_upgrade_for_command(command, report)
    if bootstrap_update:
        emit_stage(
            "client_command_resumed",
            from_version=bootstrap_from_version,
            to_version=report["to_version"],
            command=f"jobagent {args.command}",
            bootstrap_compatibility=True,
            message="Job Agent is continuing the requested command after updating.",
        )
    return result


def _native_dispatch(args: argparse.Namespace) -> dict[str, Any] | None:
    from jobagent.application import native_work
    from jobagent.infra import browser_work, rounds, state
    changes_context = (
        args.command == "init"
        or (args.command == "account" and args.account_command in {"bind", "switch"})
        or (args.command == "round" and args.round_command in {"start", "skip"})
        or (args.command == "resume" and args.resume_command == "analyze")
        or args.command == "interaction"
    )
    if changes_context and browser_work.has_open():
        return {"ok": False, "error": "native_work_context_locked", "request_preserved": True,
                "message": "Complete or explicitly cancel safe pending native work before changing account, profile or round. An issued external action requires receipt reconciliation.",
                "next_suggested": "jobagent work status"}
    if args.command == "work":
        if args.work_command == "contract":
            from jobagent.infra.codex_skill import skill_contract
            return skill_contract()
        if args.work_command == "next":
            return native_work.next_work()
        if args.work_command == "status":
            return native_work.status()
        if args.work_command == "begin":
            return native_work.begin(args.work_id)
        if args.work_command == "submit":
            return native_work.submit(args.work_id, args.result)
        return native_work.cancel(args.work_id, confirmed=args.confirm_cancel)
    if args.command == "browser":
        return native_work.request_login(args.platform, diagnose=True)
    if args.command == "round" and args.round_command == "audit":
        active = state.load_json(state.current_round_path()) or {}
        if any(item.get("native_delivery") for item in active.get("platforms", {}).values()):
            return native_work.audit_round(args.platform)
    if args.command == "platforms" and args.platforms_command == "health":
        return {"ok": True, "browser_executor": "codex_native", "browser_probe_executed": False,
                "message": "Browser health is verified through the current platform's native session task; no separate browser is started.",
                "next_suggested": "jobagent work next"}
    if args.command not in native_work.PLATFORMS:
        return None
    platform = args.command
    action = args.platform_command
    if action == "login":
        return native_work.request_login(platform)
    if action == "discover":
        return native_work.request_discovery(platform)
    if action == "audit":
        active = state.load_json(state.current_round_path()) or {}
        if active.get("platforms", {}).get(platform, {}).get("native_delivery"):
            return native_work.audit(platform)
        return None
    subcommand = getattr(args, "greet_command", None) or getattr(args, "apply_command", None)
    if subcommand in {"preview", "review"}:
        rounds.assert_platform_turn(platform)
        from jobagent.application.native_repair import prepare_review
        response = prepare_review(platform, input_path=args.input, promoted_ids=args.promote,
            confirm_promote=args.confirm_promote, output_path=args.output)
        return native_work.present(response["work"]) if response.get("work") else response
    if subcommand == "send":
        return native_work.start_delivery(platform, input_path=args.input,
            preview_id=args.preview_id, authorization_id=args.authorization_id,
            limit=args.limit, dry_run=args.dry_run, stop_on_failure=not args.continue_on_failure)
    return None


def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    from jobagent.infra.native_command_lock import command_lock
    if getattr(args, "_native_command_locked", False):
        return _dispatch_unlocked(args)
    # Serializes only CLI ledger/checkpoint/round transitions. It is deliberately
    # not held while Codex is operating the external native browser UI.
    if args.command in {"boss", "liepin", "zhilian", "51job", "browser", "work", "round", "interaction", "resume", "init", "account"} and not (args.command == "work" and args.work_command == "contract"):
        with command_lock():
            return _dispatch_unlocked(args)
    return _dispatch_unlocked(args)


def _dispatch_unlocked(args: argparse.Namespace) -> dict[str, Any]:
    native = _native_dispatch(args)
    if native is not None:
        return native
    if args.command == "init":
        return _init(args)
    if args.command == "account":
        return _account(args)
    if args.command == "upgrade-check":
        from jobagent.infra.upgrade_readiness import run_upgrade_check

        return run_upgrade_check(
            client_state=getattr(args, "_client_upgrade_report", None),
        )
    if args.command == "doctor":
        return _doctor_env()
    if args.command == "resume":
        if args.resume_command == "analyze":
            return _resume_analyze(args)
        if args.resume_command in ("list", "status"):
            if getattr(args, "id", None):
                from jobagent.infra.cloud_client import CloudError

                raise CloudError(
                    "Single-resume detail arrives in a later release; "
                    "this version lists every online resume.",
                    status=400,
                    code="resume_detail_not_available",
                )
            return _resume_center_overview()
        raise ValueError(f"unknown resume command: {args.resume_command}")
    if args.command == "profile":
        return _profile_show()
    if args.command == "platforms":
        from jobagent.platforms import check_all_platforms, check_platform_health, list_platforms

        if args.platforms_command == "status":
            return {"platforms": [platform.to_dict() for platform in list_platforms()]}
        if args.platform:
            return check_platform_health(args.platform).to_dict()
        return {"platforms": [health.to_dict() for health in check_all_platforms()]}
    if args.command == "browser":
        from jobagent.infra.browser_diagnostics import diagnose_browser

        return diagnose_browser(args.platform)
    if args.command == "update":
        from jobagent.infra.release_update import check_for_update

        return check_for_update(auto_apply=False, force=True)
    if args.command == "support":
        from jobagent.infra.support import support_star_payload

        return support_star_payload()
    if args.command == "interaction":
        return _interaction_respond(args)
    if args.command == "round":
        from jobagent.infra.rounds import (
            assert_platform_turn,
            round_status,
            set_platform_status,
            start_new_round,
        )

        if args.round_command == "start":
            from jobagent.application.round_intent import (
                build_round_intent,
                confirmed_target_cities,
                target_city_input_request,
                target_role_confirmation,
            )
            from jobagent.infra.interaction_state import (
                clear_pending_interaction,
                save_pending_interaction,
            )
            from jobagent.infra.protocol import digest_payload
            from jobagent.infra.state import current_round_path, load_json, profile_path

            current = load_json(current_round_path())
            profile = load_json(profile_path())
            if not profile:
                return {
                    "ok": False,
                    "error": "profile_required",
                    "message": "Analyze a resume before confirming target roles.",
                    "next_suggested": "jobagent resume analyze --file <resume>",
                }
            # --- user-confirmed resume binding for this round (P2) ---
            from jobagent.application.round_resume_binding import (
                binding_summary,
                resolve_round_binding,
            )

            binding_stage = resolve_round_binding(
                explicit_binding=args.resume_binding,
                no_binding=args.no_resume_binding,
                current_round=current,
            )
            if binding_stage.get("error"):
                return binding_stage["error"]
            if binding_stage.get("interaction"):
                return binding_stage["interaction"]
            round_binding = binding_stage.get("binding")
            resume_notice = binding_stage.get("notice")
            # Bound rounds source the suggestion, digest and delivery profile
            # from the bound resume's own material — never the stale local
            # snapshot left by the last local `resume analyze`.
            binding_material = None
            if round_binding and round_binding.get("id"):
                from jobagent.application.round_resume_binding import (
                    binding_material_profile,
                    clear_pending_binding,
                )
                from jobagent.infra import cloud_client

                try:
                    binding_material = binding_material_profile(round_binding)
                except cloud_client.CloudError as exc:
                    if exc.code == "preparation_required":
                        # The bound resume changed underneath; pause and ask
                        # for a fresh user-confirmed selection.
                        clear_pending_binding()
                        return {
                            "ok": False,
                            "error": "resume_binding_paused",
                            "message": (
                                "绑定的简历已变更或不再可用，本轮未开始。"
                                "请重新选择简历后再开轮。"
                            ),
                            "reason": exc.details.get("reason")
                            if isinstance(exc.details, dict)
                            else None,
                            "next_suggested": "jobagent round start",
                        }
                    return {
                        "ok": False,
                        "error": "resume_binding_material_unavailable",
                        "message": (
                            "暂时无法获取绑定简历的材料（网络或服务不可用），"
                            "本轮未开始。请稍后重试。"
                        ),
                        "retryable": bool(exc.retryable),
                        "next_suggested": "jobagent round start",
                    }
                intent_profile = binding_material["profile"]
            else:
                intent_profile = profile
            effective_cities = confirmed_target_cities(intent_profile) or confirmed_target_cities(profile)
            if not effective_cities:
                confirmation = target_city_input_request(
                    profile,
                    previous_round_id=(
                        str(current.get("round_id"))
                        if current and current.get("round_id")
                        else None
                    ),
                )
                save_pending_interaction(
                    confirmation["interaction"],
                    stage="cities",
                    profile_digest=digest_payload(profile),
                    previous_round_id=(
                        str(current.get("round_id"))
                        if current and current.get("round_id")
                        else None
                    ),
                )
                return confirmation
            if current and current.get("status") == "active" and current.get("round_id"):
                from jobagent.infra.rounds import reconcile_active_round_profile

                reconcile_active_round_profile(profile)
                current = load_json(current_round_path())
            if (
                current
                and current.get("status") == "active"
                and current.get("round_id")
                and not args.accept_suggested
                and not args.target_role
            ):
                clear_pending_interaction()
                active = start_new_round()
                if not active.get("resume_binding") and round_binding:
                    from jobagent.application.round_resume_binding import (
                        binding_direction_conflict,
                    )
                    from jobagent.infra.rounds import attach_round_resume_binding

                    conflict = binding_direction_conflict(active, round_binding)
                    if conflict:
                        return conflict
                    attach_round_resume_binding(round_binding)
                    _consume_staged_resume_binding()
                return {
                    "ok": True,
                    "resume_binding": binding_summary(
                        (load_json(current_round_path()) or {}).get("resume_binding")
                    ),
                    "resume_notice": resume_notice,
                    "workflow": round_status(),
                }
            if not args.accept_suggested and not args.target_role:
                confirmation = target_role_confirmation(
                    intent_profile,
                    previous_round_id=(
                        str(current.get("round_id")) if current and current.get("round_id") else None
                    ),
                    resume_binding=round_binding,
                )
                save_pending_interaction(
                    confirmation["interaction"],
                    stage="choice" if confirmation["suggested_roles"] else "roles",
                    profile_digest=digest_payload(intent_profile),
                    suggested_roles=confirmation["suggested_roles"],
                    previous_round_id=(
                        str(current.get("round_id"))
                        if current and current.get("round_id")
                        else None
                    ),
                    choice=None if confirmation["suggested_roles"] else "replace_roles",
                )
                return confirmation
            try:
                intent = build_round_intent(
                    intent_profile,
                    accept_suggested=args.accept_suggested,
                    target_roles=args.target_role,
                    resume_binding=round_binding,
                    target_cities=effective_cities,
                )
            except ValueError as exc:
                return {
                    "ok": False,
                    "error": "invalid_round_intent",
                    "message": str(exc),
                    "next_suggested": "jobagent round start",
                }
            created = start_new_round(intent, resume_binding=round_binding)
            clear_pending_interaction()
            if round_binding:
                _consume_staged_resume_binding()
            return {
                "ok": True,
                "resume_binding": binding_summary(created.get("resume_binding")),
                "resume_notice": resume_notice,
                "workflow": round_status(),
            }
        if args.round_command == "status":
            return {"ok": True, "workflow": round_status()}
        if args.round_command == "audit":
            from jobagent.application.delivery import audit_round

            return audit_round(
                platform=args.platform,
                recent=args.recent,
                details=args.details,
                failures_only=args.failures_only,
            )
        if not args.confirm_skip:
            return {
                "ok": False,
                "error": "user_confirmation_required",
                "platform": args.platform,
                "message": "Explicitly confirm skipping this platform for the current round.",
            }
        assert_platform_turn(args.platform)
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
        return audit_platform(
            platform,
            recent=args.recent,
            details=args.details,
            failures_only=args.failures_only,
        )
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
        preview_id=args.preview_id,
        authorization_id=args.authorization_id,
        limit=args.limit,
        dry_run=args.dry_run,
        stop_on_failure=not args.continue_on_failure,
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    account_verification: dict[str, Any] | None = None
    try:
        from jobagent.infra.native_command_lock import command_lock
        # Keep the update/migration check and intent issuance in one critical
        # section. Python file descriptors are non-inheritable: an updater exec
        # releases this lock and the resumed command acquires it anew.
        with command_lock():
            setattr(args, "_native_command_locked", True)
            _maybe_update(args)
            _prepare_client_upgrade(args)
            skill_installation = None
            if not getattr(args, "_native_update_deferred", False):
                from jobagent.infra.browser_work import has_inflight
                if not has_inflight():
                    from jobagent.infra.codex_skill import install_skill
                    # Refresh before a business error can consume update state.
                    skill_installation = install_skill()
            account_verification = _verify_state_owner_for_command(args)
            _schedule_analytics_flush_safely()
            result = _dispatch(args)
        if skill_installation and skill_installation.get("status") != "current":
            result["codex_skill_installation"] = skill_installation
        if getattr(args, "_native_update_deferred", False):
            result["client_update_deferred"] = {"reason": "native_browser_work_inflight",
                "next_suggested": "jobagent work next", "request_preserved": True}
        if account_verification and account_verification.get("offline"):
            result = {
                **result,
                "offline": True,
                "stale": True,
                "account_verification": account_verification,
            }
        result = _attach_pending_product_announcements(
            result,
            account_verified=account_verification is not None,
        )
        _print(result)
        _schedule_analytics_flush_safely()
        if result.get("ok") is False:
            raise SystemExit(2)
    except KeyboardInterrupt:
        _print({"ok": False, "error": "interrupted"}, stream=sys.stderr)
        raise SystemExit(130) from None
    except Exception as exc:
        from jobagent.infra.cloud_client import CloudError
        from jobagent.infra.account_state import AccountStateError
        from jobagent.infra.client_upgrade import UpgradeCompatibilityError
        from jobagent.infra.platform_lock import PlatformLockError
        from jobagent.infra.rounds import RoundOrderError
        from jobagent.infra.protocol import ProtocolError
        from jobagent.infra.browser_work import BrowserWorkError
        from jobagent.platforms.discovery import CollectionError

        try:
            from jobagent.application.decision_repair import DecisionRepairError
            from jobagent.application.delivery import UserInterventionRequired
            from jobagent.infra.delivery_preview import DeliveryPreviewError
            from jobagent.infra.delivery_authorization import DeliveryAuthorizationError
        except ImportError:
            DecisionRepairError = ()  # type: ignore[assignment,misc]
            UserInterventionRequired = ()  # type: ignore[assignment,misc]
            DeliveryPreviewError = ()  # type: ignore[assignment,misc]
            DeliveryAuthorizationError = ()  # type: ignore[assignment,misc]
        if isinstance(exc, BrowserWorkError):
            payload = exc.payload
        elif isinstance(exc, AccountStateError):
            payload = exc.payload
        elif isinstance(exc, UpgradeCompatibilityError):
            payload = exc.payload
        elif isinstance(exc, CollectionError):
            details = dict(exc.details or {})
            payload = {
                **details,
                "ok": False,
                "error": exc.code,
                "message": exc.message,
                "no_charge": details.get("no_charge", True),
                "requires_user_action": details.get("requires_user_action", bool(exc.user_prompt)),
                "user_prompt": exc.user_prompt or details.get("user_prompt") or None,
            }
        elif UserInterventionRequired and isinstance(exc, UserInterventionRequired):
            payload = {
                "ok": False,
                "error": exc.code,
                "requires_user_action": True,
                "user_prompt": exc.prompt,
            }
        elif DeliveryPreviewError and isinstance(exc, DeliveryPreviewError):
            payload = exc.payload
        elif DeliveryAuthorizationError and isinstance(exc, DeliveryAuthorizationError):
            payload = exc.payload
        elif DecisionRepairError and isinstance(exc, DecisionRepairError):
            payload = exc.payload
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
                "retryable": exc.retryable,
                "attempts": exc.attempts,
                **exc.details,
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
        if account_verification and account_verification.get("offline"):
            payload = {
                **payload,
                "offline": True,
                "stale": True,
                "account_verification": account_verification,
            }
        _print(payload, stream=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
