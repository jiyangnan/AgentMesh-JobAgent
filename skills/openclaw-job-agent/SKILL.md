---
name: job-agent
description: Use when the user wants help finding jobs on Boss直聘/Zhipin, analyzing a resume for job search, ranking Boss job listings, drafting personalized recruiter greetings, sending confirmed greetings, or auditing past Job Agent sends.
version: 0.1.0
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

# Job Agent for Boss直聘

Help the user run AgentMesh Job Agent, a local CLI workflow for Boss直聘 job search. The agent drives the CLI; the user keeps control of login and final sends.

## Safety Rules

- Never send greetings until the user has reviewed the generated previews and explicitly approved sending.
- Never invent a license key. If the user lacks one, stop and point them to the application form, GitHub issue, or email below.
- Do not run `jobs collect` in parallel, set page delay to zero, or wrap it with faster retry loops.
- Treat Boss login, resume originals, browser cookies, and sending actions as local user-controlled steps.
- If Boss shows a verification challenge or the CLI reports an upstream verify redirect, pause and ask the user how they want to proceed.

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

Cloud features require a license key beginning with `jba_live_`. Request one through:

- Form: `https://jobagent.agentmesh360.com/#apply`
- GitHub Issue: `https://github.com/jiyangnan/AgentMesh-JobAgent/issues/new?template=license-request.yml`
- Email: `hello@agentmesh360.com`

Initialize after the user provides a real key:

```bash
jobagent init --key <jba_live_xxx>
jobagent doctor env
```

## Workflow

1. Analyze the resume:

```bash
jobagent resume analyze --file <resume.pdf> --target-role "<role>" --target-cities <city1> <city2>
```

2. Log in to Boss直聘. Before running the command, tell the user:

> I will open a separate Chrome window for Boss直聘 login. Please scan the QR code in that new Chrome window with the Boss app, wait until the page reaches your Boss workspace, then come back and tell me "logged in". Do not close that Chrome window; Job Agent uses it for job collection and confirmed sending.

Then run:

```bash
jobagent login
```

3. Collect jobs:

```bash
jobagent jobs collect --city <city> --query "<role keyword>" --pages 3 --output raw.json
```

4. Rank jobs:

```bash
jobagent jobs rank --input raw.json --top 20 --output ranked.json
```

Show the scores, match levels, reasons, and risk flags to the user.

5. Generate greeting previews:

```bash
jobagent greet preview --input ranked.json --limit 10 --output ready.json
```

Show every greeting preview and ask which ones to send.

6. Send only after explicit approval:

```bash
jobagent greet send --input ready.json --limit 10
```

7. Audit:

```bash
jobagent greet audit
```

## Common Handling

| Situation | Response |
|---|---|
| `missing_license` | Ask the user to request or provide a real `jba_live_...` key, then run `jobagent init --key ...`. |
| `invalid_license` | Surface the CLI error. Do not retry with invented keys. |
| `quota_exceeded` | Tell the user the cloud quota is exhausted and suggest contacting `hello@agentmesh360.com`. |
| Login timeout | Re-run `jobagent login` when the user is ready to scan. |
| Scanned/image resume | Ask for a text-based PDF, DOCX, TXT, or Markdown resume. |

## Links

- Product: `https://jobagent.agentmesh360.com`
- Public CLI: `https://github.com/jiyangnan/AgentMesh-JobAgent`
- AgentMesh ecosystem: `https://github.com/jiyangnan/agentmesh-core`
