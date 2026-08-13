---
name: job-agent
description: AgentMesh Job Agent for resume-driven job discovery, signed review, user-confirmed delivery and audit on Boss直聘, 猎聘, 智联招聘 and 51Job. Use for 找工作, 投简历, 简历分析, job matching and recruiter greetings.
version: 0.5.11
---

# Job Agent

Operate Job Agent as an Agent-native CLI. The user controls API Key setup, platform login and review overrides.

## Required Behavior

- Never invent an AgentMesh360 API Key. Without one, ask the user to create a universal Key at `https://agentmesh360.com/app/` and wait. Registration and Key creation are free; cloud capabilities require available credits.
- After configuring the Key, run `jobagent doctor env`. Read `environment_healthy` and `workflow.ready` separately. If `cloud_access.usable=true`, briefly report the active balance source and run the top-level `next_suggested` immediately. Never block on `Pass: not purchased`; ask for a purchase only when `paid_pass_required=true` or a real cloud command returns `insufficient_credits`.
- New accounts start with zero cloud credits. For grandfathered `signup_trial_active`, tell the user: `你的 AgentMesh360 账户仍有此前发放的体验额度：剩余 {credit} credits，有效期至 {expires_at}。无需购买通行证，我现在继续执行下一步。` Then execute `next_suggested` without asking for confirmation.
- Run platforms as complete vertical chains: Boss直聘 -> 猎聘 -> 智联招聘 -> 51Job. Never pre-login future platforms; complete the current platform's `login -> discover -> review -> delivery preview -> delivery confirmation -> send -> audit` chain and complete its audit before logging in to the next platform.
- When output contains `requires_user_action=true`, stop, relay `user_prompt` and wait for the user.
- Report `selected / review / rejected`, then show the complete final `selected` list and stop for the user's delivery decision.
- On `event=delivery_preview` with `error=interaction_required`, show every row in `delivery_preview.items`, then render the returned confirmation card. Never choose for the user. Map `confirm_all`, `exclude_jobs` or `cancel_delivery` through the exact interaction ID.
- For `exclude_jobs`, collect displayed job numbers and pass each as `--exclude-index`. Show the regenerated complete preview and stop for final confirmation again. Run send only after `event=delivery_authorized`, using the exact command containing both `--preview-id` and `--authorization-id`.
- On `delivery_preview_required` or `delivery_confirmation_required`, run only the returned safe review command, display the regenerated preview and obtain fresh confirmation. Preserve existing promotions; do not recollect or recharge.
- `review` is excluded by default. Promote only IDs named by the user and always pass `--confirm-promote`.
- Never automatically promote `rejected`.
- Show `skipped_delivered` when present and never add those jobs back to the send list.
- Keep the dedicated Job Agent Chrome window open.
- On Boss, do not report success from the platform's default introduction; require verification of the reviewed personalized greeting.
- Never stop after one platform. Follow `workflow.next_suggested` while `workflow.continue_required=true`; only `workflow.workflow_complete=true` ends the round.
- Create a round only by executing `jobagent round start`. Never infer that `doctor env`, `round status` or a platform command created or authorized a new round.
- Never copy a target role from README, skill examples, prior users or test data. Pass `--target-role` only when the current user explicitly stated that role; otherwise omit it and use the returned target-role interaction.
- On `error=interaction_required`, use the host's native prompt card only when the card interface is callable in the current surface and mode. Codex uses the ready-to-call `host_presentations.adapters.codex.arguments` when `request_user_input` is callable and maps the returned label through `answer_mapping`. Other hosts map each `single` field's label, prompt, options and `default_option_ids` without inventing choices. If the native interface is unavailable in the current mode, show `interaction.fallback_text` unchanged and say only that the current mode is using the text form; never claim the host lacks cards. Continue every answer through `jobagent interaction respond` with the exact interaction ID. Append/replace may return a second role-input interaction. If the user already named a target role, pass it directly to `round start` and do not ask again.
- Skip a platform only after explicit user approval with `jobagent round skip --platform <platform> --confirm-skip`.
- After an existing installation updates, run `jobagent upgrade-check` and resolve its `next_suggested` action before opening a platform. Never delete `~/.jobagent` or the Job Agent Chrome profile as a general fix; preserve credentials, login cookies, profiles, audits and preferences.
- Forward `client_update_detected -> client_update_started -> client_update_completed -> client_command_resumed` once in the user's language. Do not ask permission for a managed signed update and do not stop after success; continue the original command. Stop only on `client_update_failed`, report its `message`, and follow `next_suggested`. Older clients may first emit only the compatibility completion/resume pair.
- For `client_update_failed` with `error_code=release_artifact_hash_mismatch`, run the returned official-installer recovery command once and repeat the original command. It preserves Job Agent state and browser sessions; never disable the signature/tag/commit/archive checks or delete the managed profile.
- When a cloud command returns `retryable=true` and `request_preserved=true`, do not ask the user to retry, re-login or recollect jobs. Run the exact `next_suggested` command immediately. A failed start reuses its persisted `request_id` and has `billing_status=not_charged`; a failed decision reuses its `discover_id` and preserved candidates without an additional charge. If a valid signed SearchPlan expires during a preserved request, the CLI renews that same `request_id` and `discover_id` automatically with zero renewal charge; never create a replacement round or recollect jobs. Signature, account or context mismatches remain hard stops.
- `round status` and a user-confirmed `round skip` may return `offline=true, stale=true` during a transient cloud outage after the CLI verifies the current API Key against its local account proof. Continue from the returned local workflow. Never claim a platform was skipped unless the skip command itself returns `ok=true`. On `offline_account_proof_required` or `offline_account_proof_mismatch`, stop and use the declared recovery; never edit or delete local state.
- Profiles, rounds, decisions and audits are account-bound. On `local_state_owner_required`, ask the user to confirm ownership and run `jobagent account bind --confirm-legacy`. On `local_state_account_mismatch`, ask the user to confirm the account switch and run `jobagent account switch --new-state`. Never edit the owner file manually.
- For browser slowness or conflicting login evidence, run `jobagent browser diagnose --platform <platform>` before asking for another login. It is read-only; `login.state=unknown` or `conflicting` is not `login_required`.
- Forward progress stages and heartbeats during long operations. Use `jobagent round audit` for the compact result; use `--failures-only` or `--details` only when investigation requires records.

## Setup

```bash
jobagent init --key <your_api_key>
jobagent doctor env
jobagent resume analyze --file <resume-path>
jobagent round start
```

When the current user has explicitly named a role, pass that exact value to
both commands with `--target-role "<user-stated target role>"`.

One completed Discover covers one platform, processes at most 100 candidate jobs and costs a fixed 10 credits. Cloud resume analysis costs 5 credits. Registration, API Key creation, and the open-source client are free; new accounts start with zero cloud credits. The signed cloud response is authoritative for charges and refunds. The optional AgentMesh360 monthly pass costs CNY 29, lasts 30 days, and includes 1,000 credits shared across AgentMesh360 cloud products without automatic renewal. Previously issued signup-trial credits remain usable until their original expiry.

## Boss直聘

Start the four-platform round explicitly with:

```bash
jobagent round start
# After the target-role card:
jobagent interaction respond --interaction-id "<id>" --choice accept_suggested
jobagent round status
```

```bash
jobagent boss login --check
jobagent boss discover
jobagent boss greet preview
```

Report the signed decision, show the complete delivery preview, then continue with the exact bound command:

```bash
jobagent interaction respond --interaction-id "<id>" --choice confirm_all
jobagent boss greet send --input <review_file> --preview-id <preview_id> --authorization-id <authorization_id>
jobagent boss audit
```

## 猎聘

```bash
jobagent liepin login --check
jobagent liepin discover
jobagent liepin apply review
```

```bash
jobagent interaction respond --interaction-id "<id>" --choice confirm_all
jobagent liepin apply send --input <review_file> --preview-id <preview_id> --authorization-id <authorization_id>
jobagent liepin audit
```

## 智联招聘

```bash
jobagent zhilian login --check
jobagent zhilian discover
jobagent zhilian apply review
```

```bash
jobagent interaction respond --interaction-id "<id>" --choice confirm_all
jobagent zhilian apply send --input <review_file> --preview-id <preview_id> --authorization-id <authorization_id>
jobagent zhilian audit
```

Treat any Zhilian `kw...` URL segment as opaque platform state, never as the cloud-issued role keyword. Do not parse it, feed it back into search, or skip Zhilian because of it; follow the CLI's readable `query`, error and `next_suggested`.
Treat `zhilian_session_state_unknown` and `zhilian_page_state_unknown` as slow-loading or conflicting evidence, not as logged out. A persistent generic login/register entry is weak evidence and does not override independent account-navigation plus resume/activity evidence. A visible credential form or login challenge is strong evidence; strong login and strong account evidence together remain unknown and stop safely. Follow preserved-request recovery and ask the user to log in only for `zhilian_login_required`. Never guess or hard-code a `jl` city code: the CLI verifies changed codes from independent readable page evidence and returns no candidates/no charge when city evidence is insufficient.

## 51Job

```bash
jobagent 51job login --check
jobagent 51job discover
jobagent 51job apply review
```

```bash
jobagent interaction respond --interaction-id "<id>" --choice confirm_all
jobagent 51job apply send --input <review_file> --preview-id <preview_id> --authorization-id <authorization_id>
jobagent 51job audit
```

猎聘 must verify both the account resume and the exact signed personalized greeting. A platform default introduction is not the personalized greeting. 智联 and 51Job submit the account resume only; the 51Job web chat entry is a QR handoff and is not used by this flow.

Boss and 猎聘 signed personalized greetings must be non-empty and no longer than 100 characters. If validation fails, stop before opening the job page and report the CLI error. Never describe a 智联 or 51Job review note as a sent greeting.

## Review Override

For non-Boss platforms:

```bash
jobagent <platform> apply review --promote <job-id> --confirm-promote
```

For Boss, replace `apply review` with `greet preview`.

## Completion

Report the round ID, platform, Discover ID, candidate/category counts, credits, user overrides, attempted/delivered/failed/skipped counts, user interventions, audit result and remaining platforms. Do not infer delivery from a button click alone, and do not report overall completion unless `workflow.workflow_complete=true`.

Canonical guide: `docs/agent-onboarding.md`.
