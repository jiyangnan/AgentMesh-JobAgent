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

## Goal, Actions and Acceptance

Before each platform, state:

- Goal: complete one platform Discover and automatically deliver its signed `selected` jobs.
- Actions: login check, Discover, signed review, automatic selected delivery, audit.
- Acceptance: valid signed decision; every candidate classified once; previously delivered jobs excluded; only `selected` jobs attempted; audit records the actual result.

At the start of a round, run:

```bash
jobagent round status
```

The CLI persists the four-platform order and returns one `next_suggested` command. Follow it after each platform audit. A platform-level success is an intermediate milestone, not completion of the user's overall job-search round.

Do not collect logins as a separate setup phase. At round start, log in to Boss only. Do not open or request the Liepin login until Boss audit has advanced `workflow.current_platform` to `liepin`; apply the same rule to Zhilian and 51Job.

One completed platform Discover accepts at most 100 candidate jobs. AgentMesh 360 is currently in free-open mode: every account has unlimited access and Discover deducts 0 credits. Treat the signed cloud response as the authority for any future charge or refund policy.

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

## 2. Configure API Key

If the user has not supplied an API Key, say:

> 请打开 https://agentmesh360.com/app/ 注册或登录，在个人中心生成 Job Agent API Key。拿到以后发给我，我再继续。请不要把 API Key 发到公开 Issue。

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

Known intervention states include login, CAPTCHA, security verification and resume selection. When one appears:

1. Stop the current action.
2. Keep the dedicated browser open.
3. Relay `user_prompt` exactly.
4. Wait for the user to reply that the action is complete.
5. Repeat the relevant login check or send command.

Do not keep retrying while the user is expected to act.

## 7. Updates

Official installer-managed clients check signed release policy between commands and update only when no Discover/send action is active. Developer source checkouts receive a notice and are not modified.

Manual status check:

```bash
jobagent update check
```

An `update_required` response must be resolved before continuing a cloud workflow.

## 8. Completion Report

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
