---
name: job-agent
description: Use AgentMesh Job Agent for resume-driven job discovery, signed review and automatic selected delivery on Boss直聘, 猎聘, 智联招聘 and 51Job.
version: 0.4.5
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

- Never invent an API Key. Ask the user to create an AgentMesh360 universal Key at `https://agentmesh360.com/app/` and wait. Registration and Key creation are free; cloud capabilities require available credits.
- After configuring the Key, run `jobagent doctor env`. Read `environment_healthy` and `workflow.ready` separately. If `cloud_access.usable=true`, briefly report the active balance source and run the top-level `next_suggested` immediately. Never block on `Pass: not purchased`; ask for a purchase only when `paid_pass_required=true` or a real cloud command returns `insufficient_credits`.
- New accounts start with zero cloud credits. For grandfathered `signup_trial_active`, tell the user: `你的 AgentMesh360 账户仍有此前发放的体验额度：剩余 {credit} credits，有效期至 {expires_at}。无需购买通行证，我现在继续执行下一步。` Then execute `next_suggested` without asking for confirmation.
- Run Boss直聘 -> 猎聘 -> 智联招聘 -> 51Job as complete vertical chains. Never pre-login future platforms; complete the current platform's `login -> discover -> review -> send -> audit` chain and complete its audit before logging in to the next platform. Never operate their shared browser concurrently.
- Stop whenever `requires_user_action=true`; relay `user_prompt` exactly and wait.
- Report `selected / review / rejected`, then automatically deliver the signed `selected` list without asking again.
- Show `skipped_delivered` when present and never add those jobs back to the send list.
- Never promote `review` without IDs chosen by the user and `--confirm-promote`. Never auto-promote `rejected`.
- Starting the round authorizes real actions for `selected`; send commands have no per-platform confirmation flag.
- On Boss, a platform default introduction is not the reviewed greeting. Require the CLI's exact personalized-delivery verification.
- Never stop after one platform. Follow `workflow.next_suggested` while `workflow.continue_required=true`; only `workflow.workflow_complete=true` ends the round.
- Create a round only by executing `jobagent round start`. Never infer that `doctor env`, `round status` or a platform command created or authorized a new round.
- Skip a platform only after explicit user approval with `jobagent round skip --platform <platform> --confirm-skip`.
- After an existing installation updates, run `jobagent upgrade-check` and resolve its `next_suggested` action before opening a platform. Never delete `~/.jobagent` or the Job Agent Chrome profile as a general fix; preserve credentials, login cookies, profiles, audits and preferences.
- Forward `client_update_detected -> client_update_started -> client_update_completed -> client_command_resumed` once in the user's language. Do not ask permission for a managed signed update and do not stop after success; continue the original command. Stop only on `client_update_failed`, report its `message`, and follow `next_suggested`. Older clients may first emit only the compatibility completion/resume pair.
- When a cloud command returns `retryable=true` and `request_preserved=true`, do not ask the user to retry, re-login or recollect jobs. Run the returned `next_suggested` command immediately; Discover resumes the same signed request and candidate set without another charge.
- Profiles, rounds, decisions and audits are account-bound. On `local_state_owner_required`, ask the user to confirm ownership and run `jobagent account bind --confirm-legacy`. On `local_state_account_mismatch`, ask the user to confirm switching accounts and run `jobagent account switch --new-state`. Never edit account-state files manually.
- Diagnose browser slowness or conflicting login evidence with `jobagent browser diagnose --platform <platform>` before asking for another login. Treat `login.state=unknown` or `conflicting` as inconclusive.
- Forward CLI progress stages and heartbeats. Use compact `jobagent round audit` by default; expand only failures or explicitly requested details.

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
jobagent round start
```

Each completed platform Discover accepts at most 100 candidate jobs and costs a fixed 10 credits. Cloud resume analysis costs 5 credits. Registration, API Key creation, and the open-source client are free; new accounts start with zero cloud credits. The signed cloud response is authoritative for charges and refunds. The optional AgentMesh360 monthly pass costs CNY 29, lasts 30 days, and includes 1,000 credits shared across AgentMesh360 cloud products without automatic renewal. Previously issued signup-trial credits remain usable until their original expiry.

## Platform Flow

```bash
jobagent round start
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

Boss and 猎聘 greetings must be signed, non-empty and at most 100 characters before preview or delivery. Never describe a 智联 or 51Job review note as a sent greeting.

## Review Override

```bash
jobagent <platform> apply review --promote <job-id> --confirm-promote
```

For Boss use `greet preview` in place of `apply review`.

## Completion Report

Include round ID, platform, Discover ID, category counts, credits, explicit overrides, attempted/delivered/failed/skipped counts, audit evidence and remaining platforms. Never report overall completion unless `workflow.workflow_complete=true`. Relay the optional one-time GitHub star prompt only if the CLI emits it.
