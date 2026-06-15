# Job Agent


> 🟣 Part of **[AgentMesh](https://github.com/jiyangnan/agentmesh-core)** — see the [ecosystem index](https://github.com/jiyangnan/agentmesh-core/blob/main/docs/ECOSYSTEM.md) ([中文](https://github.com/jiyangnan/agentmesh-core/blob/main/docs/ECOSYSTEM.zh.md)) for all related repos, the [roadmap](https://github.com/jiyangnan/agentmesh-core/blob/main/docs/ROADMAP.md), and [architecture](https://github.com/jiyangnan/agentmesh-core/blob/main/docs/ARCHITECTURE.md).
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-early%20access-orange.svg)](#status)
[![Brand](https://img.shields.io/badge/brand-AgentMesh-6E4AFF.svg)](https://github.com/jiyangnan/agentmesh-core)

> AI-driven job hunting automation for Boss直聘 (Zhipin), 猎聘 (Liepin), and 智联招聘 (Zhilian) — built to be controlled by your AI agent (Claude Code, OpenClaw, etc.) from chat.

**Core loop**: Resume → 36-field candidate profile (cloud) → Job crawl (local Chrome) → Match scoring (cloud) → Personalized greetings (cloud) → Batch send with delivery verification (local Chrome).

**Architecture in one breath**: Browser automation, platform cookies, and your resume original file always stay on your machine. Only stripped text and structured profile travel to our cloud API for matching/greeting generation.

This is part of the [**AgentMesh**](https://github.com/jiyangnan/agentmesh-core) ecosystem — a series of vertical AI agents for specific industries.

> **⚠️ Early access**. Cloud features require a license key (request from the maintainer). The public release is intentionally low-key while we collect early feedback.

---

## Architecture — client plus Cloud API

You're looking at the public client repo. It contains local browser automation, PDF/DOCX parsing, platform-specific CLI commands, audit files, and agent-friendly onboarding. Cloud AI features call `api.jobagent.agentmesh360.com` with your configured license key.

You do not need access to any private server code. The Cloud API endpoint and a license key are enough to use the public CLI.

---

## Features

- **Resume Text Extraction** — PDF / DOCX / TXT / Markdown → plain text
- **Profile Management** — Structured candidate profile (JSON), auto-merged into config
- **Platform Workflows** — Boss直聘 stable; Liepin and Zhilian beta platform chains are exposed as separate commands
- **Smart Filtering** — Exclude keywords, salary range, experience, degree
- **AI Ranking** — Rule-based scoring + optional LLM reranking
- **Controlled Apply / Greeting** — Boss sends greetings after approval; Liepin and Zhilian keep platform-specific apply flows and audit trails
- **Audit Log** — Full send history with success/failure tracking
- **CDP Cross-Platform Driver** — Real Chrome on macOS / Windows / Linux (not AppleScript-only)
- **Passive Login Guide** — Chrome auto-opens for login; agent notifies user; continues automatically

---

## Requirements

- Python >= 3.11
- Google Chrome installed
- macOS / Windows / Linux

---

## Installation

### One-liner (recommended)

**macOS / Linux** (Terminal):

```bash
curl -fsSL https://raw.githubusercontent.com/jiyangnan/AgentMesh-JobAgent/main/scripts/install.sh | bash
```

**Windows** (PowerShell, regular user — no admin needed):

```powershell
irm https://raw.githubusercontent.com/jiyangnan/AgentMesh-JobAgent/main/scripts/install.ps1 | iex
```

Both installers do the same six steps: check prereqs (Python 3.11+, git, Chrome), clone the repo to a hidden user-local directory, create an isolated venv, install the CLI, drop a `jobagent` shim on your PATH. **After install, open a new terminal** and you can run `jobagent` from anywhere.

### Manual (developers / contributors)

```bash
git clone https://github.com/jiyangnan/AgentMesh-JobAgent.git
cd job-agent
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows:     .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

---

## 🎁 Free vs License

CLI 本体永远免费 —— 抓岗位、管简历文件、自动化登录这些"agent 自己干不了"的事都给你。AI 那一段（简历理解、岗位匹配、招呼语）走云端，需要 license（M1 阶段免费申请，几小时内回）。

| | **Free** · 本地工具版 | **License** · 完整云端 AI 版 |
|---|---|---|
|  | `git clone` 直接跑 | M1 阶段[免费申请](https://jobagent.agentmesh360.com/#apply) |
| 简历文本提取（PDF/DOCX → text） | ✅ | ✅ |
| Boss 岗位抓取（多页） | ✅ | ✅ |
| 独立 Chrome 自动化登录（cookie 留本地） | ✅ | ✅ |
| Profile JSON 手动管理（save/show/edit） | ✅ | ✅ |
| 投递审计日志 | ✅ | ✅ |
| **AI 简历分析（36 字段招聘方画像）** | ❌ | ✅ |
| **AI 岗位匹配（5 维 + 5 档推荐 + 风险点）** | ❌ | ✅ |
| **AI 个性化招呼语（≤150 字 + 量化成果）** | ❌ | ✅ |
| **批量发送 + 送达验证** | ❌ | ✅ |
| **云端算法持续迭代**（无需更新客户端） | ❌ | ✅ |
| **未来产品免费试用**（AgentMesh 矩阵） | ❌ | ✅ |

> 受 license 保护的命令：`jobagent resume analyze` · `jobagent boss rank` · `jobagent boss greet preview` · `jobagent boss greet send` · `jobagent pipeline run`。运行前会自动检查 license，没有就会停下来给你引导申请。
>
> Free 版可直接用：`jobagent resume extract`、`jobagent profile save/show/edit`、`jobagent boss collect`、`jobagent login`、`jobagent doctor`、`jobagent init`、`jobagent support star`、`jobagent boss greet audit`。

---

## 🤖 For Agent Users (Claude Code / OpenClaw / Cursor / Codex …)

**TL;DR**: Don't paste this URL alone — paste [docs/agent-onboarding.md](docs/agent-onboarding.md) instead.

This product is **built to be driven by an AI agent**: users chat with a host agent, and the agent drives the CLI locally. But just handing your agent the GitHub link is risky — several steps need explicit handling:

Platform commands intentionally use explicit platform namespaces: `jobagent boss ...`, `jobagent liepin ...`, and `jobagent zhilian ...`. The old top-level `jobagent jobs ...` and `jobagent greet ...` commands are removed and are not compatibility aliases.

Current platform order is Boss直聘 → 猎聘 → 智联招聘. They share one local Chrome session, so agents should run platform workflows serially.

| Concern | Why agent will trip |
|---|---|
| Cross-platform install one-liner | Windows users may be on Git Bash, can't run PowerShell `irm` |
| `jobagent login` | Requires user to scan QR code in Chrome — agent must wait, not skip |
| Platform collection throttling | Collect commands use deliberate pacing. Agent must NOT bypass with zero delay or parallel platform calls. |
| `greet send` / `apply send` | Can send real messages or submit resumes depending on platform — agent must ask for explicit confirmation before running real actions. |
| First real successful application/send | CLI may print one optional GitHub star prompt to stderr after the first successful real delivery only. Agents should relay it once if visible, then never repeat it themselves. |
| Quota / verification errors | Should stop and surface to user, not auto-retry with shorter delays |

### 🔑 The Boss-login step is the #1 thing agents fumble — here's what your agent MUST say

When the agent reaches `jobagent login`, it must read **this exact prompt** to you (the human) — verbatim, not paraphrased:

> **Agent → User:**
>
> 接下来我要登录 Boss 直聘。我会启动一个**独立的 Chrome 窗口**（不是你日常用的那个 Chrome；这是为了隔离 cookie、保护你的隐私）。
>
> 请按以下步骤操作：
>
> 1. 我马上会执行 `jobagent login`。一个新的 Chrome 窗口会自动弹出，地址栏是 **`https://www.zhipin.com/`**。
> 2. 在**那个新弹出的 Chrome 窗口里**（不是你平时的 Chrome），用 Boss 直聘 App 扫码登录。
> 3. 扫完后页面会跳转到你的 Boss 工作台，**回到这里告诉我「登录好了」**。
> 4. 我收到你的"登录好了"后，会继续下一步。
>
> ⚠️ 不要关闭那个 Chrome 窗口；后续抓岗位和发招呼语都依赖它保持登录状态。
>
> （5 分钟未扫码会超时，命令会自动退出——告诉我即可，我重新跑一遍。）

This explicit hand-off is the cornerstone of the IM→Agent→CLI flow. The agent should **never** silently run `jobagent login` and assume the user will figure out what to do. **Always read the prompt above to the user, then run the command.**

### The canonical agent script

[`docs/agent-onboarding.md`](docs/agent-onboarding.md) — copy the "快速指令" section, replace 4 placeholders, send to your agent. It addresses all the above pitfalls inline, including the literal login prompt above.

If you're a developer integrating with this product programmatically, the Claude Code skill at [`skills/claude-code/SKILL.md`](skills/claude-code/SKILL.md) is the same workflow in Claude's preferred format — install once and the agent picks it up via natural-language trigger phrases.

---

## Quick Start

> **TL;DR — which path should you pick?**
>
> | Setup | Recommended path |
> |---|---|
> | You have a Cloud API license key (or can request one — currently free during M1) | **Path A: Cloud-driven** (below). Best matches, best greetings, recruiter-perspective 36-field profile. |
> | You can't get a license today / want to evaluate first | Path B: Pure-local (below). Works, but rule-based matching and template greetings — much weaker than cloud. |
> | You're a power user editing YAML configs by hand | Path C: Manual config (further below). |
>
> Requesting a license is fast and free during M1 — pick whichever channel fits:
>
> - **Application form**: [Apply here](https://jobagent.agentmesh360.com/#apply) — 30-second structured form. Recommended.
> - **GitHub issue**: [open a license-request issue](https://github.com/jiyangnan/AgentMesh-JobAgent/issues/new?template=license-request.yml) (public by default).
> - **Email**: `hello@agentmesh360.com`.
>
> All three reach the same queue; usually replied within a few hours.

### Path A: Cloud-driven (recommended — uses Job Agent Cloud API)

```bash
# 1. One-time setup with your license key
jobagent init --key jba_live_xxxxxx
# Verifies connectivity, saves key to ~/.jobagent/credentials (mode 600)

# 2. Analyze resume (local extract → Cloud /v1/resume/analyze → save 36-field profile)
jobagent resume analyze --file resume.pdf --target-role "AI产品经理" --target-cities 深圳 杭州

# 3. Boss直聘 stable flow
jobagent boss collect --city 深圳 --query "AI产品经理" --output boss.raw.json
jobagent boss rank --input boss.raw.json --top 20 --output boss.ranked.json
jobagent boss greet preview --input boss.ranked.json --limit 10 --output boss.ready.json
jobagent boss greet send --input boss.ready.json --limit 10

# 4. 猎聘 beta flow
jobagent liepin login --check
jobagent liepin collect --query "AI产品经理" --city 深圳 --pages 1 --output liepin.raw.json
jobagent liepin rank --input liepin.raw.json --top 20 --output liepin.ranked.json
jobagent liepin greet preview --input liepin.ranked.json --limit 10 --output liepin.ready.json
jobagent liepin apply open --input liepin.ready.json --limit 5
# Real apply/send requires explicit confirmation:
# jobagent liepin apply send --input liepin.ready.json --limit 5 --confirm-submit

# 5. 智联招聘 beta flow
jobagent zhilian login --check
jobagent zhilian collect --query "AI产品经理" --city 深圳 --pages 1 --detail-limit 2 --output zhilian.raw.json
jobagent zhilian rank --input zhilian.raw.json --top 20 --output zhilian.ranked.json
jobagent zhilian greet preview --input zhilian.ranked.json --limit 10 --output zhilian.ready.json
jobagent zhilian apply open --input zhilian.ready.json --limit 5
# Zhilian's real action is attachment resume submit, not in-page greeting text:
# jobagent zhilian apply send --input zhilian.ready.json --limit 5 --confirm-submit
```

The Cloud API endpoint is `https://api.jobagent.agentmesh360.com` (override with
`JOBAGENT_API_BASE` env var). Browser automation, platform cookies, and your
resume original file always stay on your machine — only stripped text /
structured profile is sent to the cloud. See the Privacy & Data section below for the data boundary.

### Path B: Free-tier commands (no license)

Without a license you can still use the Free-tier commands as standalone tools — useful for evaluating the product locally before applying:

```bash
# Extract resume text (PDF/DOCX/TXT → text); useful even without a license
jobagent resume extract --file resume.pdf
# stderr prints a hint about the License version's upgrades

# Manage the profile JSON by hand (e.g. your own agent's LLM fills it)
jobagent profile save --data '{"basic":{...},"hardSkills":{...},...}'
jobagent profile show
jobagent profile edit

# Crawl jobs from Boss直聘 — landing in raw.json for inspection
jobagent boss collect --city 深圳 --query "AI产品经理" --output raw.json

# Sanity-check the local environment
jobagent doctor env

# Read past send history (still works after license expiry)
jobagent boss greet audit
```

**`jobagent boss rank`, `jobagent boss greet preview`, `jobagent boss greet send`, `jobagent resume analyze`, `jobagent pipeline run` all require a license** — they call the Cloud API and will exit with a friendly prompt if no license is configured. [Apply for one here](https://jobagent.agentmesh360.com/#apply); M1 stage is free.

---

## CLI Command Reference

```bash
# ── Login ──
jobagent login --check                          # Check Boss login status
jobagent login                                  # Open Chrome login page, poll until logged in

# ── Resume / Profile (Step 1) ──
jobagent resume extract --file resume.pdf       # Extract text from resume
jobagent profile save --data '{...}'            # Save candidate profile JSON
jobagent profile show                           # Display current profile

# ── Boss直聘 stable workflow ──
jobagent boss collect --city 深圳 --query "AI产品经理" [--output boss.raw.json]
jobagent boss rank --input boss.raw.json --config config.yaml [--top 20]
jobagent boss greet preview --input boss.ranked.json [--limit 10]
jobagent boss greet send --input boss.ready.json [--limit 10]
jobagent boss greet audit [--recent 20]
jobagent doctor boss
jobagent boss probe-send --job-url <url> --message "..."
jobagent boss verify-last-send

# ── 猎聘 beta workflow ──
jobagent doctor liepin
jobagent liepin login --check
jobagent liepin collect --query "AI产品经理" --city 深圳 [--pages 1] [--output liepin.raw.json]
jobagent liepin rank --input liepin.raw.json [--top 20]
jobagent liepin greet preview --input liepin.ranked.json [--limit 10]
jobagent liepin apply open --input liepin.ready.json [--limit 5]
jobagent liepin apply send --input liepin.ready.json --confirm-submit [--limit 5]
jobagent liepin audit [--recent 20]

# ── 智联招聘 beta workflow ──
jobagent zhilian login --check
jobagent zhilian collect --query "AI产品经理" --city 深圳 [--pages 1] [--detail-limit 2] [--output zhilian.raw.json]
jobagent zhilian rank --input zhilian.raw.json [--top 20]
jobagent zhilian greet preview --input zhilian.ranked.json [--limit 10]
jobagent zhilian apply open --input zhilian.ready.json [--limit 5]
jobagent zhilian apply send --input zhilian.ready.json --confirm-submit [--limit 5]
jobagent zhilian audit [--recent 20]

# ── Optional support ──
jobagent support star                           # Show the public GitHub repo if you want to star it

# ── Full Pipeline ──
jobagent pipeline run --config config.yaml
```

---

## Agent Integration

This tool is designed to be orchestrated by an agent. Example workflow:

```python
import subprocess, json

# Step 1: Extract resume
result = subprocess.run(
    ["jobagent", "resume", "extract", "--file", "resume.pdf"],
    capture_output=True, text=True
)
resume_text = json.loads(result.stdout)["text"]

# Step 2: Agent uses its own LLM to build profile
profile = agent.llm.analyze_resume(resume_text)
# → {"years_experience": 10, "target_roles": ["AI产品经理"], ...}

# Step 3: Save profile
subprocess.run([
    "jobagent", "profile", "save",
    "--data", json.dumps(profile, ensure_ascii=False)
])

# Step 4: Run pipeline
subprocess.run(["jobagent", "pipeline", "run", "--config", "config.yaml"])
```

The product handles everything the agent **cannot** do: browser control, PDF parsing, Boss API calls, DOM automation. The agent handles everything it **can** do better: semantic understanding, LLM reasoning.

---

## Configuration

### `config/config.yaml`

```yaml
platform: zhipin

crawler:
  queries: ["AI产品经理", "高级产品经理"]
  cities:
    - name: 深圳
      code: "101280600"
  pages_per_query: 3

filter:
  exclude_keywords: [销售, 客服, 保险]
  max_salary_k: 0          # 0 = unlimited
  require_degree_above: "大专"

greeter:
  enabled: true
  dry_run: false           # true = preview only
  template: |
    您好，看了{company}的{job_name}岗位，我认为比较符合。
    我有{years_experience}年{skills}相关经验，希望进一步沟通。
  verify: true             # verify [送达] mark after send
```

### Profile auto-merge

If `config.yaml` has no `candidate:` section, `Config.from_yaml()` automatically loads `~/.jobagent/state/profile.json` (created by `jobagent profile save`).

Priority: **YAML explicit config > profile.json > defaults**

---

## Architecture

```
src/jobagent/
├── cli.py                 # Unified CLI entry
├── domain/                # Business logic
│   ├── models.py          # Job, RankedJob, CandidateProfile, SendAttempt
│   ├── filter.py          # FilterEngine (rule-based)
│   ├── ranking.py         # RankingEngine (5-dim scoring + optional LLM rerank)
│   ├── greeter.py         # GreeterEngine (preview / send_batch / verify)
│   ├── resume_parser.py   # PDF/DOCX/TXT text extraction
│   └── profile_builder.py # Data validation (zero LLM)
├── drivers/boss/          # Browser automation
│   ├── cdp_driver.py      # CDP implementation (cross-platform, primary)
│   ├── applescript_driver.py  # AppleScript fallback (macOS only)
│   ├── chrome_manager.py  # Chrome process management
│   ├── data_driver.py     # Boss API fetching via Chrome XHR
│   └── __init__.py        # create_driver() factory
├── application/           # Orchestration
│   ├── pipeline.py        # 4-phase: crawl → filter → rank → greet
│   ├── doctor_boss.py     # Session readiness checks
│   ├── probe_send.py      # Single send test
│   └── verify_last_send.py
└── infra/                 # Infrastructure
    ├── config.py          # YAML loading + profile.json merge
    ├── state.py           # State persistence paths
    ├── audit.py           # Send audit log
    └── exceptions.py      # LoginRequiredError
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Chrome not found | Install Google Chrome. Paths checked: `/Applications/Google Chrome.app` (macOS), `C:\Program Files\Google\Chrome\Application\chrome.exe` (Windows), `/usr/bin/google-chrome` (Linux) |
| "登录态已失效" | Run `jobagent login` — Chrome will open automatically, log in, and the command continues |
| LLM not configured | **This is expected.** The product has no internal LLM. Your agent uses its own LLM. |
| Resume extraction fails (<100 chars) | The PDF may be a scanned image without OCR text layer. Try a text-based PDF. |
| Send fails with a verification challenge | The upstream redirected to a verify page. Pause for a while and retry. |

---

## Development

```bash
# Run tests
pytest tests/

# Install in editable mode with dev deps
pip install -e ".[dev]"
```

---

## Contact / Request a License

M1 license keys are free — we hand them out so we can track who's evaluating the product and collect feedback. Three ways to apply, pick whichever fits:

### Option 1 — Application form (recommended)

[**Apply here (Tally form)**](https://jobagent.agentmesh360.com/#apply) — 30-second structured form, no GitHub account required. Goes to our queue; usually replied within a few hours via email.

### Option 2 — GitHub Issue

[Open a license-request issue](https://github.com/jiyangnan/AgentMesh-JobAgent/issues/new?template=license-request.yml) — the template will collect what we need. Responses go back on the issue thread, so this option is **public** by default; choose Option 1 or 3 if you'd rather not have your request indexed.

### Option 3 — Email

`hello@agentmesh360.com` — write a short note about what you're hunting and how you found us. We'll reply with a key.

### What we look at

- Real-job-hunt use vs evaluation
- Which host agent you'd use (Claude Code / OpenClaw / Cursor / Codex / …)
- Where you heard about us

Nothing of the above is required to receive a key. M1 is free; we just want signal on who is reaching the product.

---

## Privacy & Data

- **简历文件**：始终保留在本机，不上传至任何云服务
- **Cookie**：Boss 直聘登录态仅在本机 Chrome 中使用，不离开本机
- **AI 处理**：简历提取文本和岗位信息通过加密通道发送至云端，由 **DeepSeek** 进行 AI 分析（匹配评分、招呼语生成）。数据按 [DeepSeek 隐私政策](https://www.deepseek.com/privacy) 处理
- **服务端日志**：仅记录 API 调用时间、接口名和 Token 用量，不记录简历内容
- **数据删除**：如需删除使用记录，请发邮件至 hello@agentmesh360.com

## Terms & Disclaimer

Job Agent 是独立第三方工具，与 Boss 直聘无合作关系。

- **服务边界**：当前公开 CLI 支持 Boss 直聘稳定链路，并提供猎聘、智联招聘 beta 链路；处于 Early Access 阶段，功能仍在迭代
- **平台风险**：我们不承诺规避平台规则，账号被限制或封禁的风险由用户自行承担
- **用户责任**：发送前请自行审核每条消息内容；禁止用于骚扰式海投
- **免责声明**：不保证获得回复、面试或录用；对账号损失、数据丢失等不承担责任；产品按"现状"提供


## License

MIT
