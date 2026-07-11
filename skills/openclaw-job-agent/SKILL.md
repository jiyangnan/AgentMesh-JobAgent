---
name: job-agent
description: Use AgentMesh Job Agent for resume-driven job discovery, signed review and automatic selected delivery on Boss直聘, 猎聘, 智联招聘 and 51Job.
version: 0.3.12
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

Drive the official Job Agent CLI while keeping the user in control of credentials, login and review overrides.

## Safety Contract

- Never invent an API Key. Ask the user to create one at `https://agentmesh360.com/app/` and wait.
- Run Boss直聘 -> 猎聘 -> 智联招聘 -> 51Job serially. Never operate their shared browser concurrently.
- Stop whenever `requires_user_action=true`; relay `user_prompt` exactly and wait.
- Report `selected / review / rejected`, then automatically deliver the signed `selected` list without asking again.
- Show `skipped_delivered` when present and never add those jobs back to the send list.
- Never promote `review` without IDs chosen by the user and `--confirm-promote`. Never auto-promote `rejected`.
- Starting the round authorizes real actions for `selected`; send commands have no per-platform confirmation flag.
- On Boss, a platform default introduction is not the reviewed greeting. Require the CLI's exact personalized-delivery verification.
- Never stop after one platform. Follow `workflow.next_suggested` while `workflow.continue_required=true`; only `workflow.workflow_complete=true` ends the round.
- Skip a platform only after explicit user approval with `jobagent round skip --platform <platform> --confirm-skip`.

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

Each completed platform Discover accepts at most 100 candidate jobs. AgentMesh 360 is currently in free-open mode: every account has unlimited access and Discover deducts 0 credits. Treat the signed cloud response as authoritative for future policy changes.

## Platform Flow

```bash
jobagent round status
```

After every command, read the returned `workflow` object. Each audit must advance to the next platform until the four-platform round is complete.

Boss直聘:

```bash
jobagent boss login --check
jobagent boss discover
jobagent boss greet preview
jobagent boss greet send
jobagent boss audit
```

猎聘:

```bash
jobagent liepin login --check
jobagent liepin discover
jobagent liepin apply review
jobagent liepin apply send
jobagent liepin audit
```

智联招聘:

```bash
jobagent zhilian login --check
jobagent zhilian discover
jobagent zhilian apply review
jobagent zhilian apply send
jobagent zhilian audit
```

51Job:

```bash
jobagent 51job login --check
jobagent 51job discover
jobagent 51job apply review
jobagent 51job apply send
jobagent 51job audit
```

Run each send line automatically after review. Do not ask for per-platform approval. 猎聘 must verify both the account resume and the exact signed personalized greeting; a platform default introduction is not enough. 智联 and 51Job submit resumes only. 51Job's web chat is QR-only.

## Review Override

```bash
jobagent <platform> apply review --promote <job-id> --confirm-promote
```

For Boss use `greet preview` in place of `apply review`.

## Completion Report

Include round ID, platform, Discover ID, category counts, credits, explicit overrides, attempted/delivered/failed/skipped counts, audit evidence and remaining platforms. Never report overall completion unless `workflow.workflow_complete=true`. Relay the optional one-time GitHub star prompt only if the CLI emits it.
