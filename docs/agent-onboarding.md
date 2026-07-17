# Job Agent: Canonical Agent Workflow

This is the public instruction source for an Agent operating Job Agent on behalf of a user.

## Non-Negotiable Rules

1. Never invent, infer or fabricate an AgentMesh API Key.
2. Starting a job-search round authorizes automatic delivery of every signed `selected` job. Do not ask for another confirmation before each platform or send command.
3. Run recruiting platforms as complete vertical chains in this order: Boss直聘 -> 猎聘 -> 智联招聘 -> 51Job. Never pre-login future platforms; complete the current platform's `login -> discover -> review -> send -> audit` chain and complete its audit before logging in to the next platform.
4. When CLI output contains `requires_user_action=true`, stop immediately, relay `user_prompt` to the user and wait for their reply.
5. Never treat `review` or `rejected` as automatically sendable. A `review` job needs an explicit user override; `rejected` remains excluded.
6. Do not close the dedicated Job Agent Chrome window during a workflow.
7. Do not stop after one platform. Read the `workflow` object after every command and continue while `workflow.continue_required=true`.
8. Report the overall round complete only when `workflow.workflow_complete=true`. Skipping a platform requires the user's explicit approval and `jobagent round skip --platform <platform> --confirm-skip`.
9. Create a round only with `jobagent round start`. `doctor env`, `round status` and platform commands must never be treated as implicit authorization to create another round.

## Goal, Actions and Acceptance

Before each platform, state:

- Goal: complete one platform Discover and automatically deliver its signed `selected` jobs.
- Actions: login check, Discover, signed review, automatic selected delivery, audit.
- Acceptance: valid signed decision; every candidate classified once; previously delivered jobs excluded; only `selected` jobs attempted; audit records the actual result.

At the start of a round, run:

```bash
jobagent round start
jobagent round status
```

The CLI persists the four-platform order and returns one `next_suggested` command. Follow it after each platform audit. A platform-level success is an intermediate milestone, not completion of the user's overall job-search round.

Do not collect logins as a separate setup phase. At round start, log in to Boss only. Do not open or request the Liepin login until Boss audit has advanced `workflow.current_platform` to `liepin`; apply the same rule to Zhilian and 51Job.

One completed platform Discover accepts at most 100 candidate jobs and costs a fixed 10 credits. Cloud resume analysis costs 5 credits. The signed cloud response is authoritative for charges and refunds: pre-decision browser failures are not charged, cloud-decision failures are refunded, and retrying the same task does not charge twice. A verified new account receives 50 shared trial credits valid for 14 days and can use them immediately without a paid pass. After those credits are insufficient or expire, the optional AgentMesh360 monthly pass costs CNY 29, lasts 30 days and includes 1,000 shared credits without automatic renewal.

## 1. Install

If `jobagent --version` is unavailable, install the official client.

macOS or Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/jiyangnan/AgentMesh-JobAgent/main/scripts/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/jiyangnan/AgentMesh-JobAgent/main/scripts/install.ps1 | iex
```

Verify:

```bash
jobagent --version
jobagent doctor env
```

For an existing installation that has just updated, run:

```bash
jobagent upgrade-check
```

The CLI automatically migrates compatible state and clears only rebuildable runtime caches. Do not delete `~/.jobagent` or the Job Agent Chrome profile: API Keys, site login cookies, profiles, audits and user preferences must survive upgrades.

Do not start a platform while `upgrade-check` returns `ok=false` or a command returns `client_upgrade_required`. Relay all conflicts, run the first `next_suggested` recovery action, and repeat `upgrade-check` until all persisted state is compatible.

Starting with `0.4.0`, profiles, rounds, decisions and audits are bound to the opaque AgentMesh account behind the active API Key. If the CLI returns `local_state_owner_required`, do not continue or infer ownership. Ask the user to confirm that the existing local Job Agent state belongs to the current account, then run exactly:

```bash
jobagent account bind --confirm-legacy
```

If it returns `local_state_account_mismatch`, explain that the configured Key belongs to another account. After the user confirms the account switch, run:

```bash
jobagent account switch --new-state
```

This preserves the previous account's local state and reuses the recruiting-site Chrome profile. Never edit `state_owner.json` or move account state manually.

## 2. Configure API Key

If the user has not supplied an API Key, say:

> 请打开 https://agentmesh360.com/app/ 注册或登录，在个人中心生成 AgentMesh360 全平台通用 API Key。新用户完成验证后会获得 50 个共享体验 credits，14 天内可直接使用，不需要先购买通行证。拿到 API Key 以后发给我，我再继续。请不要把 API Key 发到公开 Issue。

After the API Key is configured, run `jobagent doctor env`. Treat `environment_healthy` as the environment result and `workflow.ready` as execution readiness; do not reinterpret one as the other. If `cloud_access.usable=true`, tell the user which balance source is active and immediately execute the top-level `next_suggested`; `signup_trial_active` explicitly means no paid pass is required. Do not inspect or block on the dashboard's pass-purchase status. Ask the user to purchase only when the CLI returns `cloud_access.reason=insufficient_credits` with `paid_pass_required=true`, or a real cloud command returns `insufficient_credits`.

When `cloud_access.reason=signup_trial_active`, say this before continuing, filling in the returned values:

> 你的 AgentMesh360 新用户体验额度当前有效：剩余 `{credit}` credits，有效期至 `{expires_at}`。无需购买通行证，我现在继续执行下一步。

Immediately run the returned `next_suggested` command after this message. Do not ask for confirmation.

Wait for the user. Then run:

```bash
jobagent init --key <your_api_key>
```

If authentication fails, show the exact error. Do not silently change workflows.

## 3. Analyze Resume

Ask for a PDF, DOCX, TXT or Markdown resume and the target role/cities.

```bash
jobagent resume analyze --file <resume-path> \
  --target-role "<target role>" \
  --target-cities <city1> <city2>
```

Acceptance: output reports `ok=true` and a saved profile path.

Then execute the returned `jobagent round start`. This explicit command begins the four-platform round and authorizes automatic delivery of signed `selected` jobs for that round.

## 4. Run One Platform

### Boss直聘

```bash
jobagent boss login --check
```

If login is required, run `jobagent boss login`, relay its `user_prompt`, and wait until the user says they have logged in. Then repeat `--check`.

```bash
jobagent boss discover
jobagent boss greet preview
```

Report the `selected`, `review`, `rejected` and `skipped_delivered` counts and continue with the signed `selected` list. `skipped_delivered` jobs are not sendable. Do not ask whether to send. To include a review job, the user must independently choose its ID and authorize:

```bash
jobagent boss greet preview --promote <job-id> --confirm-promote
```

```bash
jobagent boss greet send
jobagent boss audit
```

After audit, inspect `workflow`. When it points to `jobagent liepin login --check`, continue immediately unless user intervention is required.

The send command rechecks local delivery history. A stale or edited review file must not be used to contact a previously delivered Boss job again. If Boss automatically sends its own default introduction while opening a new conversation, that event alone is not successful personalized delivery; the CLI must continue and verify the reviewed greeting itself.

### 猎聘

```bash
jobagent liepin login --check
jobagent liepin discover
jobagent liepin apply review
```

```bash
jobagent liepin apply send
jobagent liepin audit
```

### 智联招聘

```bash
jobagent zhilian login --check
jobagent zhilian discover
jobagent zhilian apply review
```

```bash
jobagent zhilian apply send
jobagent zhilian audit
```

### 前程无忧 / 51Job

```bash
jobagent 51job login --check
jobagent 51job discover
jobagent 51job apply review
```

```bash
jobagent 51job apply send
jobagent 51job audit
```

猎聘 must deliver both the account resume and the signed personalized greeting for each selected job. Verify the resume card/message and the exact greeting separately; the platform's default introduction is not the personalized greeting. 智联和 51Job only submit the account resume. The 51Job web chat entry is a QR handoff and is not part of the send flow.

## 5. Handling Review Jobs

Only the user can promote a `review` job:

```bash
jobagent <platform> apply review \
  --promote <job-id-1> <job-id-2> \
  --confirm-promote
```

For Boss, use `greet preview` instead of `apply review`.

Report the resulting `send_count` and reviewed file, then continue automatically. Never add IDs that the user did not select.

## 6. User Intervention

Known intervention states include login, CAPTCHA, security verification, slow page loading and resume selection. When one appears:

1. Stop the current action.
2. Keep the dedicated browser open.
3. Relay `user_prompt` exactly.
4. Wait for the user to reply that the action is complete.
5. Repeat the relevant login check or send command.

For `boss_search_page_load_timeout`, keep the dedicated Chrome open and relay the
returned `user_prompt`. A retry reuses an already loaded matching search page
instead of refreshing it, so wait for the visible job list before continuing.

Do not keep retrying while the user is expected to act.

When the page appears slow or login evidence conflicts with what the user sees, run the read-only diagnostic before asking the user to log in again:

```bash
jobagent browser diagnose --platform <platform>
```

It must not launch Chrome or navigate. Interpret `cdp_reachable`, `page.ready_state`, `login.state` and `ready_for_platform_work` separately. `page_observed` with `login.state=unknown` or `conflicting` is not proof that the user is logged out.

## 7. Progress and Audit

Forward timestamped stage events and heartbeat updates during long Discover and delivery operations so the user knows the task is active. Do not replace the CLI's completed/failed counts with estimates.

Use the compact round summary for normal completion reporting:

```bash
jobagent round audit
```

Read expanded records only when needed:

```bash
jobagent round audit --failures-only
jobagent round audit --platform <platform> --details --recent 20
```

Do not dump complete local audit files into the conversation.

## 8. Updates

Official installer-managed clients check signed release policy between commands and update only when no Discover/send action is active. Developer source checkouts receive a notice and are not modified.

Manual status check:

```bash
jobagent update check
```

An `update_required` response must be resolved before continuing a cloud workflow.

## 9. Completion Report

Report:

- Platform and Discover ID.
- Candidate, selected, review and rejected counts.
- Credits charged or refunded.
- Which job IDs the user explicitly promoted.
- Attempted, delivered, failed and skipped counts.
- Any user intervention or unresolved platform issue.
- Audit command/result.
- Round ID, remaining platforms and final `workflow.workflow_complete` value.

Do not report success based only on a click. Use the CLI's delivered result and audit record. Do not report the overall task complete while `workflow.continue_required=true`.
