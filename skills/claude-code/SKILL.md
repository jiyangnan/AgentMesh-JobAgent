---
name: job-agent
description: AgentMesh Job Agent for resume-driven job discovery, review and confirmed delivery on Boss直聘, 猎聘, 智联招聘 and 51Job. Use for 找工作, 投简历, 简历分析, job matching, recruiter greetings and application audit.
version: 0.3.5
---

# Job Agent

Operate Job Agent as an Agent-native CLI. The user controls API Key setup, platform login, review overrides and every real greeting/application.

## Required Behavior

- Never invent an AgentMesh API Key. Without one, ask the user to create it at `https://agentmesh360.com/app/` and wait.
- Run platforms serially: Boss直聘 -> 猎聘 -> 智联招聘 -> 51Job.
- When output contains `requires_user_action=true`, stop, relay `user_prompt` and wait for the user.
- Never send before the user reviews `selected / review / rejected` and explicitly confirms.
- `review` is excluded by default. Promote only IDs named by the user and always pass `--confirm-promote`.
- Never automatically promote `rejected`.
- Show `skipped_delivered` when present and never add those jobs back to the send list.
- Keep the dedicated Job Agent Chrome window open.
- On Boss, do not report success from the platform's default introduction; require verification of the reviewed personalized greeting.

## Setup

```bash
jobagent init --key <your_api_key>
jobagent doctor env
jobagent resume analyze --file <resume-path> \
  --target-role "<target role>" \
  --target-cities <city1> <city2>
```

One completed Discover covers one platform and processes at most 100 candidate jobs. AgentMesh 360 is currently in free-open mode: every account has unlimited access and Discover deducts 0 credits. Treat the signed cloud response as authoritative for future policy changes.

## Boss直聘

```bash
jobagent boss login --check
jobagent boss discover
jobagent boss greet preview
```

Show the signed decision and greetings. After explicit approval:

```bash
jobagent boss greet send --confirm-send
jobagent boss audit
```

## 猎聘

```bash
jobagent liepin login --check
jobagent liepin discover
jobagent liepin apply review
```

After explicit approval:

```bash
jobagent liepin apply send --confirm-submit
jobagent liepin audit
```

## 智联招聘

```bash
jobagent zhilian login --check
jobagent zhilian discover
jobagent zhilian apply review
```

After explicit approval:

```bash
jobagent zhilian apply send --confirm-submit
jobagent zhilian audit
```

## 51Job

```bash
jobagent 51job login --check
jobagent 51job discover
jobagent 51job apply review
```

After explicit approval:

```bash
jobagent 51job apply send --confirm-submit
jobagent 51job audit
```

猎聘、智联和 51Job submit the account's resume; they do not send a Boss-style greeting. The 51Job web chat entry is a QR handoff and is not used by this flow.

## Review Override

For non-Boss platforms:

```bash
jobagent <platform> apply review --promote <job-id> --confirm-promote
```

For Boss, replace `apply review` with `greet preview`.

## Completion

Report the platform, Discover ID, candidate/category counts, credits, user overrides, attempted/delivered/failed/skipped counts, user interventions and audit result. Do not infer delivery from a button click alone.

Canonical guide: `docs/agent-onboarding.md`.
