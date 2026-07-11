---
name: job-agent
description: Use AgentMesh Job Agent for resume-driven job discovery, signed review and confirmed delivery on Boss直聘, 猎聘, 智联招聘 and 51Job.
version: 0.3.1
metadata:
  openclaw:
    emoji: "💼"
    homepage: https://jobagent.agentmesh360.com/
    requires:
      bins:
        - jobagent
    envVars:
      - name: JOBAGENT_API_BASE
        required: false
        description: Optional Job Agent API override for testing.
---

# AgentMesh Job Agent

Drive the official Job Agent CLI while keeping the user in control of credentials, login and every real delivery.

## Safety Contract

- Never invent an API Key. Ask the user to create one at `https://agentmesh360.com/app/` and wait.
- Run Boss直聘 -> 猎聘 -> 智联招聘 -> 51Job serially. Never operate their shared browser concurrently.
- Stop whenever `requires_user_action=true`; relay `user_prompt` exactly and wait.
- Show `selected / review / rejected` before any real action.
- Never promote `review` without IDs chosen by the user and `--confirm-promote`. Never auto-promote `rejected`.
- Real actions require `--confirm-send` or `--confirm-submit`.

## Install and Profile

macOS/Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/jiyangnan/AgentMesh-JobAgent/main/scripts/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/jiyangnan/AgentMesh-JobAgent/main/scripts/install.ps1 | iex
```

```bash
jobagent init --key <your_api_key>
jobagent doctor env
jobagent resume analyze --file <resume-path> --target-role "<role>" --target-cities <cities>
```

Each completed platform Discover costs 10 credits and accepts at most 100 candidate jobs. Browser failure before decision is not charged; server decision failure after charging is refunded.

## Platform Flow

Boss直聘:

```bash
jobagent boss login --check
jobagent boss discover
jobagent boss greet preview
jobagent boss greet send --confirm-send
jobagent boss audit
```

猎聘:

```bash
jobagent liepin login --check
jobagent liepin discover
jobagent liepin apply review
jobagent liepin apply send --confirm-submit
jobagent liepin audit
```

智联招聘:

```bash
jobagent zhilian login --check
jobagent zhilian discover
jobagent zhilian apply review
jobagent zhilian apply send --confirm-submit
jobagent zhilian audit
```

51Job:

```bash
jobagent 51job login --check
jobagent 51job discover
jobagent 51job apply review
jobagent 51job apply send --confirm-submit
jobagent 51job audit
```

Do not run the send line until the user has explicitly approved the preceding review. 猎聘、智联和 51Job submit resumes and do not send Boss-style greetings. 51Job's web chat is QR-only.

## Review Override

```bash
jobagent <platform> apply review --promote <job-id> --confirm-promote
```

For Boss use `greet preview` in place of `apply review`.

## Completion Report

Include platform, Discover ID, category counts, credits, explicit overrides, attempted/delivered/failed/skipped counts and audit evidence. Relay the optional one-time GitHub star prompt only if the CLI emits it.
