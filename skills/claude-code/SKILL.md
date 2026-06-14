---
name: job-agent
description: AgentMesh Job Agent — agent-driven job hunting on Boss直聘. Use when the user asks for help finding jobs, analyzing their resume for the job market, batch greeting recruiters on Boss直聘, or auditing past greetings. Trigger phrases include "找工作", "投简历", "Boss直聘", "打招呼", "简历分析", "求职 agent", "match jobs", "send greetings".
---

# Job Agent (AgentMesh)

You are helping the user run **Job Agent**, AgentMesh's vertical product for AI-driven job hunting on Boss直聘. The CLI is pre-installed; your job is to drive it through the workflow.

## What this product does

Closes the loop: **Resume → Cloud-analyzed candidate profile → Boss job crawl → Cloud-ranked match → Cloud-personalized greeting → Sent on Boss直聘**.

- **Sensitive data stays local**: resume original file, Boss cookie, browser action all run on the user's machine.
- **Algorithm runs on cloud** (`api.jobagent.agentmesh360.com`): resume analysis (36-field profile), match scoring, greeting generation.

## Hard requirements before running anything

1. **License key required** (format `jba_live_xxx`). If user doesn't have one, **stop and tell them to request one** via either:
   - **Application form (recommended)**: `https://jobagent.agentmesh360.com/#apply` — 30-second structured form, replies via email
   - **GitHub Issue**: `https://github.com/jiyangnan/AgentMesh-JobAgent/issues/new?template=license-request.yml` — public, replies on the issue thread
   - **Email**: `hello@agentmesh360.com` — private

   **As of 2026-05-11 the license is hard-enforced** — `jobagent boss rank` / `jobagent boss greet preview` / `jobagent boss greet send` / `jobagent pipeline run` / `jobagent resume analyze` exit with code 2 if no license. There is no "local fallback" to silently degrade to. Make sure the user has run `jobagent init --key …` before reaching any of these commands.
2. **Google Chrome** installed (Boss automation requires real Chrome).
3. **Resume file** (PDF / DOCX / TXT / MD).
4. **Never invent or fabricate a license key.** If `init` fails with `invalid_license`, surface that error verbatim to the user.

## Standard workflow

Run these commands in order. Read each output before proceeding to the next; some steps require human action.

### 1. One-time setup

```bash
jobagent init --key <jba_live_xxx>
# Verifies connectivity. Prints license info.

jobagent doctor env
# Sanity-check Python, Chrome, network, license. Run if anything looks off.
```

### 2. Analyze resume → 36-field profile

```bash
jobagent resume analyze --file <path-to-resume.pdf> \
    --target-role "<user's target role>" \
    --target-cities <city1> <city2>
# Saves to ~/.jobagent/state/profile.json. Takes ~25s.
```

If the user wants to fine-tune the generated profile:

```bash
jobagent profile edit       # opens in $EDITOR (vim by default)
jobagent profile show       # display current profile
```

### 3. Log in to Boss直聘 (one-time per machine)

**Before running the command**, read this prompt to the user **verbatim** (do not paraphrase, do not skip the URL):

> 接下来我要登录 Boss 直聘。我会启动一个**独立的 Chrome 窗口**（不是你日常用的那个 Chrome；这是为了隔离 cookie、保护你的隐私）。
>
> 请按以下步骤操作：
>
> 1. 我马上会执行 `jobagent login`。一个新的 Chrome 窗口会自动弹出，地址栏会是 **https://www.zhipin.com/**
> 2. 在那个新弹出的 Chrome 窗口里（不是你平时的 Chrome），用 Boss 直聘 App 扫码登录。
> 3. 扫完后页面会跳转到你的 Boss 工作台，**回到这里告诉我"登录好了"**。
> 4. 我收到你的"登录好了"后，会继续下一步。
>
> ⚠️ 不要关闭那个 Chrome 窗口；后续抓岗位和发招呼语都依赖它保持登录状态。
> （5 分钟未扫码会超时——告诉我即可，我重新跑一遍。）

Then run:

```bash
jobagent login
```

The command will block (up to 5 min) polling the login status. **Do not background it, do not timeout it early.** When the user says "登录好了", the command auto-detects and exits cleanly; proceed to step 4.

### 4. Crawl jobs (local Chrome → Boss API)

```bash
jobagent boss collect \
    --city <city> --query "<role keyword>" \
    --pages 3 \
    --output raw.json
```

`--pages` controls how many pages of results to fetch (15 jobs/page).

**⚠️ Throttling constraint** — the CLI auto-sleeps **5–7 seconds between pages** (5.0 base + 2.0 jitter) to be courteous to the upstream API. This is mandatory:

- ❌ Do NOT pass `--page-delay 0` to "speed up" (unless `--pages 1`).
- ❌ Do NOT run `jobagent boss collect` in parallel processes.
- ❌ Do NOT bypass with your own retry/sleep wrapper.
- The CLI prints `⏳ sleeping X.Xs before next page` to stderr — this is **not** the process hanging. Relay the message to the user so they know it's intentional.

### 5. Cloud rank

```bash
jobagent boss rank --input raw.json --top 20 --output ranked.json
```

Returns each job with `score` (0-100), `match_level` (strong_match/match/partial_match/weak_match/no_match), `reasons`, `risk_flags`. Sorted descending.

### 6. Cloud-generated personalized greetings

```bash
jobagent boss greet preview --input ranked.json --limit 10 --output ready.json
```

Each greeting is ≤150 chars, cites quantified achievements, no "您好我对贵公司XX岗位很感兴趣" boilerplate.

**Show the previews to the user. Get explicit confirmation before sending.**

### 7. Send (with rate limiting)

```bash
jobagent boss greet send --input ready.json --limit 10
```

This drives the user's local Chrome to send actual greetings on Boss直聘. The CLI auto-uses the cloud greetings stored in `ready.json` (will fall back to local template if the field is missing).

### 8. Audit results

```bash
jobagent boss greet audit
```

## Additional platform workflows

Run these after the Boss flow when the user wants broader coverage. Keep one local Chrome session in order; do not run platform workflows in parallel.

### Liepin beta

```bash
jobagent liepin login --check
jobagent liepin collect --query "<role keyword>" --city <city> --pages 1 --output liepin.raw.json
jobagent liepin rank --input liepin.raw.json --top 20 --output liepin.ranked.json
jobagent liepin greet preview --input liepin.ranked.json --limit 10 --output liepin.ready.json
jobagent liepin apply open --input liepin.ready.json --limit 5
```

Use `apply open` for manual review first. Only run real apply/send after the user explicitly approves:

```bash
jobagent liepin apply send --input liepin.ready.json --limit 5 --confirm-submit
jobagent liepin audit
```

### Zhilian beta

```bash
jobagent zhilian login --check
jobagent zhilian collect --query "<role keyword>" --city <city> --pages 1 --detail-limit 2 --output zhilian.raw.json
jobagent zhilian rank --input zhilian.raw.json --top 20 --output zhilian.ranked.json
jobagent zhilian greet preview --input zhilian.ranked.json --limit 10 --output zhilian.ready.json
jobagent zhilian apply open --input zhilian.ready.json --limit 5
```

Zhilian apply send submits the user's attachment resume. It does not send the greeting/review note into the page. Only run real submit after explicit approval:

```bash
jobagent zhilian apply send --input zhilian.ready.json --limit 5 --confirm-submit
jobagent zhilian audit
```

## Common errors & how to handle them

| Error | What happened | Tell the user |
|-------|---------------|---------------|
| `missing_license` (401) | No license key configured | Run `jobagent init --key ...` |
| `license_revoked` (403) | Key is revoked | Contact the maintainer for a new key |
| `quota_exceeded` (429) | Monthly cloud calls exhausted | Wait until next month or contact maintainer |
| Verification challenge in send result | Upstream redirected to a verify page | Pause for a while; resume later with longer delays |
| `login_timeout` | User didn't scan QR within 5 min | Retry `jobagent login` |
| Resume `<100 chars` | Scanned/image PDF | Ask user for a text-based resume |

## Rules

- **Never** send greetings without explicit user confirmation of the previews
- **Never** invent a license key
- **Always** show match scores and reasons before sending so the user can deselect bad matches
- For batch sends > 20 jobs, suggest the user split into multiple sessions
- Sensitive data (resume original, Boss cookie) never leaves the user's machine; you can reassure them of this
