---
name: job-agent
description: Use when the user wants help finding jobs on Boss直聘/Zhipin, 猎聘/Liepin, or 智联招聘/Zhilian, analyzing a resume for job search, ranking job listings, drafting or reviewing platform-specific greetings, opening/applying to jobs after confirmation, or auditing past Job Agent actions.
version: 0.2.1
metadata:
  openclaw:
    emoji: "💼"
    homepage: https://github.com/jiyangnan/AgentMesh-JobAgent
    requires:
      anyBins:
        - jobagent
        - curl
        - powershell
    envVars:
      - name: JOBAGENT_API_BASE
        required: false
        description: Optional override for the Job Agent Cloud API endpoint.
---

# Job Agent for Boss直聘 / 猎聘 / 智联招聘

Help the user run AgentMesh Job Agent, a local CLI workflow for Chinese job search platforms. The agent drives the CLI; the user keeps control of login, confirmation, and final sends/applications.

## Safety Rules

- Never send greetings or submit applications until the user has reviewed the generated previews/handoff and explicitly approved the action.
- Never invent an AgentMesh360 API key. If the user lacks one, stop and point them to `https://agentmesh360.com/app/`.
- Run platforms serially in this order: Boss直聘 -> 猎聘 -> 智联招聘. They share the local Chrome session.
- Do not run platform collect commands in parallel, set page delay to zero, or wrap them with faster retry loops.
- Treat platform login, resume originals, browser cookies, and sending/apply actions as local user-controlled steps.
- If a platform shows login, CAPTCHA, security verification, or missing resume state, pause and ask the user to intervene.
- Zhilian apply send submits an attachment resume; it does not send an in-page greeting text.
- If the CLI prints the optional GitHub star prompt after the first successful real send/apply, relay it once. Do not repeat it after later commands.

## Setup

If `jobagent` is unavailable, install the public CLI:

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/jiyangnan/AgentMesh-JobAgent/main/scripts/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/jiyangnan/AgentMesh-JobAgent/main/scripts/install.ps1 | iex
```

Cloud features require an AgentMesh360 API key. Ask the user to register/log in at `https://agentmesh360.com/app/`, copy the API key from the account dashboard, then initialize:

```bash
jobagent init --key <your_api_key>
jobagent doctor env
```

Optional support command:

```bash
jobagent support star
```

Starring the public repo is voluntary and must never be presented as required for install, account setup, or usage.

## Workflow

1. Analyze the resume:

```bash
jobagent resume analyze --file <resume.pdf> --target-role "<role>" --target-cities <city1> <city2>
```

2. Run platforms serially. Start with Boss直聘, then continue to 猎聘, then 智联招聘 when the user wants broader coverage.

### Boss直聘 stable flow

Before running `jobagent login`, tell the user:

> I will open a separate Chrome window for Boss直聘 login. Please scan the QR code in that new Chrome window with the Boss app, wait until the page reaches your Boss workspace, then come back and tell me "logged in". Do not close that Chrome window; Job Agent uses it for job collection and confirmed sending.

```bash
jobagent login
jobagent boss collect --city <city> --query "<role keyword>" --pages 3 --output boss.raw.json
jobagent boss rank --input boss.raw.json --top 20 --output boss.ranked.json
jobagent boss greet preview --input boss.ranked.json --limit 10 --output boss.ready.json
```

Show every greeting preview and ask which ones to send. Send only after explicit approval:

```bash
jobagent boss greet send --input boss.ready.json --limit 10
jobagent boss greet audit
```

### 猎聘 beta flow

```bash
jobagent liepin login --check
jobagent liepin collect --query "<role keyword>" --city <city> --pages 1 --output liepin.raw.json
jobagent liepin rank --input liepin.raw.json --top 20 --output liepin.ranked.json
jobagent liepin greet preview --input liepin.ranked.json --limit 10 --output liepin.ready.json
jobagent liepin apply open --input liepin.ready.json --limit 5
```

Use `apply open` for manual handoff first. Real apply/send requires explicit user approval:

```bash
jobagent liepin apply send --input liepin.ready.json --limit 5 --confirm-submit
jobagent liepin audit
```

### 智联招聘 beta flow

```bash
jobagent zhilian login --check
jobagent zhilian collect --query "<role keyword>" --city <city> --pages 1 --detail-limit 2 --output zhilian.raw.json
jobagent zhilian rank --input zhilian.raw.json --top 20 --output zhilian.ranked.json
jobagent zhilian greet preview --input zhilian.ranked.json --limit 10 --output zhilian.ready.json
jobagent zhilian apply open --input zhilian.ready.json --limit 5
```

Zhilian only supports immediate resume submission on the site; `greeting` is a review note for the user, not text sent into Zhilian. Real submit requires explicit approval:

```bash
jobagent zhilian apply send --input zhilian.ready.json --limit 5 --confirm-submit
jobagent zhilian audit
```

## Common Handling

| Situation | Response |
|---|---|
| `missing_api_key` | Ask the user to provide their AgentMesh360 API key, then run `jobagent init --key ...`. |
| `invalid_api_key` | Surface the CLI error. Do not retry with invented keys. |
| `quota_exceeded` / `insufficient_credits` | Tell the user to check credit in the AgentMesh360 account dashboard. |
| Login timeout | Re-run `jobagent login` when the user is ready to scan. |
| Scanned/image resume | Ask for a text-based PDF, DOCX, TXT, or Markdown resume. |

## Links

- Product: `https://jobagent.agentmesh360.com`
- Public CLI: `https://github.com/jiyangnan/AgentMesh-JobAgent`
- AgentMesh ecosystem: `https://github.com/jiyangnan/agentmesh-core`
