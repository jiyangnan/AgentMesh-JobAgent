from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jobagent.application.doctor_boss import DEFAULT_JOB_URL, run_boss_doctor
from jobagent.application.probe_send import run_probe_send
from jobagent.application.verify_last_send import run_verify_last_send
from jobagent.domain.models import Job, RankedJob
from jobagent.infra.config import GreeterConfig


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
        "   2) GitHub Issue   → https://github.com/jiyangnan/AgentMesh-JobAgent/issues/new?template=license-request.yml\n"
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
        "resume": "用招聘方视角的 36 字段 profile（vs agent 自己 LLM 出的简版）",
        "jobs": "用招聘方权重表的二次匹配打分（vs 5 维通用规则）",
        "greet": "含量化成果、避免套话的个性化招呼语（vs 模板填充）",
    }
    feature = cloud_features.get(command_name, "云端 AI 优化的全链路")

    msg = (
        "\n" + "─" * 60 + "\n"
        "💡 你正在使用 **本地模式**（不需要 license）。\n"
        "   云端模式（推荐）有这些优势：\n"
        f"     • {feature}\n"
        "     • 三大业务 endpoint 共享同一份招聘方视角 profile\n"
        "     • 算法持续迭代，prompt 是核心 IP\n"
        "\n"
        "   申请 license（M1 阶段免费）三选一：\n"
        "   • 申请表单（推荐）: https://jobagent.agentmesh360.com/#apply\n"
        "   • GitHub Issue:    https://github.com/jiyangnan/AgentMesh-JobAgent/issues/new?template=license-request.yml\n"
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobagent", description="Job Agent CLI MVP")
    sub = parser.add_subparsers(dest="command", required=True)

    # ── doctor ──
    doctor = sub.add_parser("doctor", help="Run environment/session checks")
    doctor_sub = doctor.add_subparsers(dest="doctor_target", required=True)
    doctor_boss = doctor_sub.add_parser("boss", help="Check Boss session readiness")
    doctor_boss.add_argument("--job-url", default=DEFAULT_JOB_URL, help="Sample Boss job URL used for doctor checks")

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
    boss_collect.add_argument("--city", required=True, help="City name (e.g. 深圳)")
    boss_collect.add_argument("--query", required=True, help="Search query (e.g. AI产品经理)")
    boss_collect.add_argument("--page", type=int, default=1, help="Starting page (default: 1)")
    boss_collect.add_argument("--pages", type=int, default=1, help="How many pages to fetch starting from --page (default: 1; tip: use 3-5 to get 45-75 jobs)")
    boss_collect.add_argument("--page-size", type=int, default=15, help="Results per page (default: 15)")
    boss_collect.add_argument(
        "--page-delay", type=float, default=5.0,
        help="Seconds to sleep between pages (default: 5.0). Recommended ≥ 4 to be courteous to the upstream API. 0 disables (only safe for --pages 1).",
    )
    boss_collect.add_argument(
        "--page-delay-jitter", type=float, default=2.0,
        help="Random extra delay added per page (default: 2.0). Actual sleep = page-delay + uniform(0, jitter).",
    )
    boss_collect.add_argument("--output", "-o", help="Output JSON file path (default: stdout)")

    boss_rank = boss_sub.add_parser(
        "rank",
        help="Cloud-AI ranking of crawled Boss jobs (license required). Outputs match score + reasoning per job.",
    )
    boss_rank.add_argument("--input", "-i", required=True, help="Input JSON file with job list (from `jobagent boss collect`)")
    boss_rank.add_argument("--top", "-n", type=int, default=20, help="Keep only top N results (default: 20)")
    boss_rank.add_argument("--output", "-o", help="Output JSON file path (default: stdout)")

    boss_greet = boss_sub.add_parser("greet", help="Boss greeting commands")
    boss_greet_sub = boss_greet.add_subparsers(dest="boss_greet_command", required=True)

    boss_preview = boss_greet_sub.add_parser(
        "preview",
        help="Cloud-AI personalised Boss greetings per job (license required). Run before `jobagent boss greet send`.",
    )
    boss_preview.add_argument("--input", "-i", required=True, help="Ranked JSON from `jobagent boss rank`")
    boss_preview.add_argument("--limit", "-n", type=int, default=10, help="Max jobs to preview")
    boss_preview.add_argument(
        "--output", "-o",
        help="Save ranked jobs with cloud greetings injected; defaults to <input>.with_greetings.json. `jobagent boss greet send --input <output>` will then use those.",
    )

    boss_send = boss_greet_sub.add_parser("send", help="Send Boss greetings after user approval")
    boss_send.add_argument("--input", "-i", required=True, help="Input JSON file with ranked jobs")
    boss_send.add_argument("--limit", "-n", type=int, default=10, help="Max jobs to greet")
    boss_send.add_argument("--config", "-c", default="config/config.yaml", help="Config YAML for greeter settings")

    boss_audit = boss_greet_sub.add_parser("audit", help="View Boss greeting audit log and statistics")
    boss_audit.add_argument("--recent", "-n", type=int, default=20, help="Show N most recent records")

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
    from jobagent.drivers.boss.data_driver import BossDataDriver
    from jobagent.domain.models import Job
    from jobagent.infra.exceptions import LoginRequiredError

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


def _cmd_jobs_rank_cloud(args: argparse.Namespace, raw_jobs: list[dict]) -> None:
    """Rank via Cloud API in batches of 15 (cloud BATCH_LIMIT)."""
    from jobagent.infra import cloud_client

    profile = _profile_for_cloud()

    BATCH = 15
    all_ranked: list[dict] = []
    total_in = total_out = 0
    for i in range(0, len(raw_jobs), BATCH):
        batch = raw_jobs[i : i + BATCH]
        # Map common field aliases the cloud expects (id/title required)
        cloud_jobs = [
            {
                "id": str(j.get("id") or j.get("encryptId") or j.get("securityId") or f"local-{i + idx}"),
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
            }
            for idx, j in enumerate(batch)
        ]
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
        mapped_jobs.append({
            "name": r.get("title"),
            "salary": r.get("salary"),
            "company": r.get("company"),
            "area": r.get("area"),
            "experience": r.get("experience"),
            "degree": r.get("degree"),
            "skills": r.get("skills"),
            "url": r.get("url"),
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
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Ranked {len(all_ranked)}/{len(raw_jobs)} jobs (via cloud) → {args.output}")
    else:
        print(output)


def _cmd_jobs_rank(args: argparse.Namespace) -> None:
    """Rank crawled jobs via Cloud AI. License required."""
    _require_license_or_exit("boss rank")

    # Load input jobs
    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        raw_jobs = data
    elif isinstance(data, dict) and "jobs" in data:
        raw_jobs = data["jobs"]
    else:
        print("Error: unexpected JSON format", file=sys.stderr)
        sys.exit(1)

    _cmd_jobs_rank_cloud(args, raw_jobs)


def _cmd_greet_preview_cloud(args: argparse.Namespace) -> None:
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
    print(f"Next: jobagent boss greet send --input {output_path} --limit {args.limit}")
    print(f"{'=' * 50}")


def _cmd_greet_preview(args: argparse.Namespace) -> None:
    """Generate AI greetings via cloud. License required."""
    _require_license_or_exit("boss greet preview")
    _cmd_greet_preview_cloud(args)


def _cmd_greet_send(args: argparse.Namespace) -> None:
    _require_license_or_exit("boss greet send")
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
            msg = item.get("cloud_greeting")
            if msg and rj.job.url:
                message_overrides[rj.job.url] = msg
    if message_overrides:
        print(f"Using {len(message_overrides)} cloud-generated greetings (run via `jobagent boss greet preview`)")
    elif any(it.get("cloud_greeting") for it in _items):
        # cloud_greeting present but couldn't be mapped (e.g., jobs without url)
        print(
            "⚠️  cloud_greeting present in input but could not be mapped to send targets "
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
    """Extract resume locally + analyze via Cloud API, save 36-field profile."""
    from jobagent.domain.resume_parser import ResumeParser
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
        report = run_boss_doctor(job_url=args.job_url)
        _print_json(report.to_dict())
        sys.exit(0 if report.status == "READY" else 2)

    if args.command == "boss" and args.boss_command == "probe-send":
        _ensure_boss_login()
        attempt = run_probe_send(job_url=args.job_url, message=args.message)
        _print_json(attempt.to_dict())
        sys.exit(0 if attempt.delivered else 2)

    if args.command == "boss" and args.boss_command == "verify-last-send":
        attempt = run_verify_last_send(message=args.message)
        _print_json(attempt.to_dict())
        sys.exit(0 if attempt.delivered else 2)

    if args.command == "boss" and args.boss_command == "collect":
        _cmd_jobs_collect(args)
        return

    if args.command == "boss" and args.boss_command == "rank":
        _cmd_jobs_rank(args)
        return

    if args.command == "boss" and args.boss_command == "greet" and args.boss_greet_command == "preview":
        _cmd_greet_preview(args)
        return

    if args.command == "boss" and args.boss_command == "greet" and args.boss_greet_command == "send":
        _require_license_or_exit("boss greet send")  # gate before Boss login (need license first)
        _ensure_boss_login()
        _cmd_greet_send(args)
        return

    if args.command == "boss" and args.boss_command == "greet" and args.boss_greet_command == "audit":
        _cmd_greet_audit(args)
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
