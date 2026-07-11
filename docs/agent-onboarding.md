# Job Agent: Canonical Agent Workflow

This is the public instruction source for an Agent operating Job Agent on behalf of a user.

## Non-Negotiable Rules

1. Never invent, infer or fabricate an AgentMesh API Key.
2. Never send a greeting or submit a resume until the user has reviewed the decision and explicitly confirmed the real action.
3. Run recruiting platforms serially in this order: Boss直聘 -> 猎聘 -> 智联招聘 -> 51Job.
4. When CLI output contains `requires_user_action=true`, stop immediately, relay `user_prompt` to the user and wait for their reply.
5. Never treat `review` or `rejected` as automatically sendable. A `review` job needs an explicit user override; `rejected` remains excluded.
6. Do not close the dedicated Job Agent Chrome window during a workflow.

## Goal, Actions and Acceptance

Before each platform, state:

- Goal: complete one platform Discover and let the user decide what to send.
- Actions: login check, Discover, signed review, explicit confirmation, send, audit.
- Acceptance: valid signed decision; every candidate classified once; previously delivered jobs excluded; only confirmed jobs attempted; audit records the actual result.

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

Show the user the `selected`, `review`, `rejected` and `skipped_delivered` sections and each remaining selected greeting. `skipped_delivered` jobs are not sendable. To include a review job, only use:

```bash
jobagent boss greet preview --promote <job-id> --confirm-promote
```

After the user explicitly approves the displayed send list:

```bash
jobagent boss greet send --confirm-send
jobagent boss audit
```

The send command rechecks local delivery history. A stale or edited review file must not be used to contact a previously delivered Boss job again. If Boss automatically sends its own default introduction while opening a new conversation, that event alone is not successful personalized delivery; the CLI must continue and verify the reviewed greeting itself.

### 猎聘

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

### 智联招聘

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

### 前程无忧 / 51Job

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

猎聘、智联和 51Job send the resume attached to the user's platform account. Do not describe their action as sending a greeting. The 51Job web chat entry is a QR handoff and is not part of the send flow.

## 5. Handling Review Jobs

Only the user can promote a `review` job:

```bash
jobagent <platform> apply review \
  --promote <job-id-1> <job-id-2> \
  --confirm-promote
```

For Boss, use `greet preview` instead of `apply review`.

Always show the resulting `send_count` and reviewed file before asking for send confirmation. Never add IDs that the user did not select.

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

Do not report success based only on a click. Use the CLI's delivered result and audit record.
