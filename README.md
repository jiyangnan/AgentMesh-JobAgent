# AgentMesh360 Job Agent

**Official website:** [jobagent.agentmesh360.com](https://jobagent.agentmesh360.com/)

AgentMesh360 Job Agent is an Agent-native job-search product. Its open-source CLI connects recruiting platforms through the user's own browser session, while AgentMesh360 cloud intelligence provides the official candidate profile, job decisions and personalized communication.

The cloud turns the resume into a recruiter-side 36-dimension candidate profile, creates profile-driven search plans, classifies every deduplicated job into signed `selected / review / rejected` results with reasons and risks, and generates evidence-grounded personalized greetings where the platform supports them. The CLI verifies those official results before delivery.

It supports four independent recruiting-platform workflows:

1. Boss直聘
2. 猎聘
3. 智联招聘
4. 前程无忧 / 51Job

Each platform is isolated from the others. A page change on one platform does not disable the remaining workflows.

## Product Flow

```text
Resume profile
  -> Boss Discover / signed review / delivery preview / user confirmation / delivery / audit
  -> Liepin Discover / signed review / delivery preview / user confirmation / delivery / audit
  -> Zhilian Discover / signed review / delivery preview / user confirmation / delivery / audit
  -> 51Job Discover / signed review / delivery preview / user confirmation / delivery / audit
  -> completed round
```

One completed Discover covers one platform, processes up to 100 deduplicated candidate jobs and costs a fixed 10 credits. Cloud resume analysis costs 5 credits. The signed cloud response remains authoritative for charges and refunds; pre-decision browser failures are not charged, cloud-decision failures are refunded, and retrying the same task does not charge twice.

Job Agent uses an AgentMesh360 universal API Key. Registration and API Key creation are free in the [AgentMesh360 account center](https://agentmesh360.com/app/). The open-source client is free; AgentMesh360 cloud capabilities use credits, and new accounts start with zero cloud credits. The optional monthly pass costs CNY 29, lasts 30 days, and includes 1,000 credits shared across AgentMesh360 cloud products. It does not renew automatically, and unused credits expire with the pass. Previously issued signup-trial credits remain usable until their original expiry.

## Install

macOS or Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/jiyangnan/AgentMesh-JobAgent/main/scripts/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/jiyangnan/AgentMesh-JobAgent/main/scripts/install.ps1 | iex
```

The official installer creates a managed installation. Starting with `0.3.0`, managed installations verify signed release policy and safely update between commands. When an update is found, the CLI emits machine-readable progress events for detection, start, completion and original-command continuation. These events appear only for a real update; an up-to-date client stays quiet. A developer source checkout is never changed automatically.

## Set Up

```bash
jobagent init --key <your_api_key>
jobagent doctor env
jobagent resume analyze --file ~/Downloads/resume.pdf
jobagent round start
```

The Agent must not copy a target role from documentation examples or infer that
the user already chose one. When the user explicitly states a target role, pass
that exact intent directly:

```bash
jobagent resume analyze --file ~/Downloads/resume.pdf --target-role "<user-stated target role>"
jobagent round start --target-role "<user-stated target role>"
```

After an existing installation updates, Job Agent automatically clears rebuildable runtime caches and migrates compatible saved state before any platform command. API Keys, recruiting-site login cookies, resume profiles, audit history and user preferences are preserved. Run `jobagent upgrade-check`; if it returns `ok=false`, follow `next_suggested` and repeat the check before opening a platform.

Local profiles, rounds, decisions and audits are bound to the opaque AgentMesh account behind the configured API Key. Existing pre-`0.4.0` state is never claimed silently. Confirm it once only when it belongs to the current account:

```bash
jobagent account status
jobagent account bind --confirm-legacy
```

When changing to a Key from another account, preserve the previous account's state and enter the new account namespace explicitly:

```bash
jobagent init --key <new_api_key>
jobagent account switch --new-state
```

The recruiting-site Chrome profile remains available; account-owned profiles, rounds, decisions and audits are saved and restored separately.

Do not delete `~/.jobagent` or the Job Agent Chrome profile as a general upgrade fix. When the CLI returns `client_upgrade_required`, relay every reported conflict and use its recovery command instead.

The resume original and recruiting-site cookies remain on the user's machine. The profile and candidate job fields needed for Discover are sent to the Job Agent cloud service for decision.

## Platform Commands

Start a new round explicitly. Reading status never creates a round:

```bash
jobagent round start
```

When no target role was supplied, `round start` returns an AgentMesh360
`interaction_required` payload with three stable choices: accept the suggested
roles, keep them and append roles, or replace them. A host Agent must render a
native prompt card when the card interface is callable in its current surface
and mode. If that interface is unavailable, it displays the included
numbered-text fallback without changing the choices. Do not describe this as
the host lacking card support when only the current mode lacks the interface.
For Codex, the response includes ready-to-call
`host_presentations.adapters.codex.arguments` plus an `answer_mapping`; use it
when `request_user_input` is callable in the current mode.

The host continues every card or text answer through the interaction ID:

```bash
jobagent interaction respond --interaction-id "<id>" --choice accept_suggested
jobagent interaction respond --interaction-id "<id>" --choice append_roles
jobagent interaction respond --interaction-id "<id>" --choice replace_roles
jobagent interaction respond --interaction-id "<id>" --target-role "<user-stated target role>"
jobagent round status
```

Append and replace choices return a second role-input interaction when no role
was included in the first response. The CLI validates the interaction ID and
profile digest, and repeated responses cannot create a duplicate round.

Direct commands remain available when the user's message already contains the
intent:

```bash
jobagent round start --accept-suggested
jobagent round start --target-role "<user-stated target role>"
jobagent round start --accept-suggested --target-role "<user-stated target role>"
```

The confirmed target roles belong to this round and do not rewrite resume job
history. If the user already named a target role, the host Agent passes it
directly and does not ask again.

Every platform command returns a `workflow` object. A platform audit does not end the overall task while `workflow.continue_required=true`; the Agent must run `workflow.next_suggested` and continue to the next platform. The overall task is complete only when `workflow.workflow_complete=true`. A platform may be skipped for the current round only after the user explicitly approves:

```bash
jobagent round skip --platform <platform> --confirm-skip
```

Long Discover and delivery operations emit timestamped stage events and periodic heartbeats. Transient TLS, connection and gateway failures are retried automatically for idempotent Discover requests. If all bounded attempts fail, the CLI returns a stable machine code, `retryable=true`, `request_preserved=true` and one exact `next_suggested` command. Before a SearchPlan arrives, the persisted `request_id` is reused and `billing_status=not_charged`; after candidate collection, the same `discover_id` and local candidate set are reused without an additional charge. If a valid signed SearchPlan expires while that request is preserved, the CLI renews the plan against the same IDs with zero renewal charge and continues automatically. Signature, account or request-context mismatches are never treated as recoverable expiry. Do not create a replacement round, reopen the platform or recollect jobs.

If only cloud account verification is temporarily unreachable, `jobagent round status` and a user-confirmed `jobagent round skip ... --confirm-skip` can use key-bound local state. Their output is explicitly marked `offline=true` and `stale=true`; the next platform is available only after the skip command itself returns `ok=true`. Missing or mismatched local proof returns `offline_account_proof_required` or `offline_account_proof_mismatch` without exposing round state. Do not edit account files or delete `~/.jobagent` or its Chrome profile.

Audits are compact by default:

```bash
jobagent round audit
jobagent round audit --failures-only
jobagent round audit --platform liepin --details --recent 20
```

If an existing Job Agent browser appears slow, stuck or incorrectly classified as logged out, inspect it without launching Chrome or navigating away from the current page:

```bash
jobagent browser diagnose --platform boss
```

The diagnostic separates CDP reachability, tab presence, page readiness and login evidence. Follow its `next_suggested`; do not clear the Chrome profile as a first response.

### Boss直聘

```bash
jobagent boss login
jobagent boss discover
jobagent boss greet preview
jobagent interaction respond --interaction-id "<id>" --choice confirm_all
jobagent boss greet send --input <review_file> --preview-id <preview_id> --authorization-id <authorization_id>
jobagent boss audit
```

Boss uses a personalized greeting. `greet preview` shows the signed decision and greeting before any real send. A platform-generated default introduction may establish the conversation, but it does not count as delivery until the reviewed personalized greeting is also verified in the chat.

### 猎聘

```bash
jobagent liepin login
jobagent liepin discover
jobagent liepin apply review
jobagent interaction respond --interaction-id "<id>" --choice confirm_all
jobagent liepin apply send --input <review_file> --preview-id <preview_id> --authorization-id <authorization_id>
jobagent liepin audit
```

### 智联招聘

```bash
jobagent zhilian login
jobagent zhilian discover
jobagent zhilian apply review
jobagent interaction respond --interaction-id "<id>" --choice confirm_all
jobagent zhilian apply send --input <review_file> --preview-id <preview_id> --authorization-id <authorization_id>
jobagent zhilian audit
```

智联结果页中的 `kw...` URL 片段是平台内部状态，不是云端生成的职位搜索词。Agent 必须以 CLI 返回的可读 `query`、错误码和 `next_suggested` 为准，不得把该片段重新用于搜索，也不得据此自行跳过智联。

智联慢导航期间，`loading` 或登录/账号证据冲突只会返回 `unknown`，不会要求用户重复登录。首页常驻的通用“登录/注册”入口属于弱证据；当个人中心导航与简历管理、投递活动等独立账户证据一致时，CLI 会继续按已登录处理。可见凭据表单或登录验证界面属于强未登录证据；强登录与强账户证据并存时仍会安全停止。只有稳定的 `zhilian_login_required` 才需要用户介入。城市筛选控件改版或旧城市 seed 失效时，CLI 会继续读取只读快照，并用页面标题、可见城市和岗位卡片等独立证据验证新城市码；单独一个 `jl` URL 码不会被信任。证据不足时流程关闭且不收费。若返回 `zhilian_page_state_unknown`、`retryable=true` 和 `request_preserved=true`，直接执行精确的 `next_suggested`，原 `request_id` 会被复用。

### 前程无忧 / 51Job

```bash
jobagent 51job login
jobagent 51job discover
jobagent 51job apply review
jobagent interaction respond --interaction-id "<id>" --choice confirm_all
jobagent 51job apply send --input <review_file> --preview-id <preview_id> --authorization-id <authorization_id>
jobagent 51job audit
```

猎聘 completes two verified actions for every selected job: it sends the resume associated with the user's platform account and then sends the signed personalized greeting generated from the resume profile and job. A platform-owned default introduction does not count as the personalized greeting. 智联招聘 and 51Job remain resume-submit workflows; on 51Job, the web chat entry is a mobile QR handoff.

Boss and 猎聘 require a non-empty signed personalized greeting of at most 100 characters before either preview or real delivery can proceed. Their success records include the delivered-message evidence. 智联招聘 and 51Job explicitly report personalized message delivery as unsupported instead of treating a review note as a sent message.

## Review Rules

- Before any real delivery, review emits `agentmesh360.delivery_preview v1` with every pending job and stops with a structured confirmation. The Agent must show the complete table, or the exact text fallback, and wait for the user's choice.
- The stable choices are `confirm_all`, `exclude_jobs`, and `cancel_delivery`. Exclusions use displayed job numbers, regenerate the complete list and require another final confirmation. The Agent never chooses on the user's behalf.
- Only an accepted interaction creates `agentmesh360.delivery_authorization v1`. Send requires both its bound `--preview-id` and `--authorization-id`; missing, stale, cross-platform, cross-round or changed-list authorization fails before a recruiting page opens.
- An older review file may return `delivery_preview_required` or `delivery_confirmation_required`. The Agent reruns only the returned review command, shows the regenerated list and waits for a fresh confirmation; prior user promotions are preserved and no new Discover charge is created.
- `review` jobs are excluded unless the user explicitly promotes their job IDs with `--promote ... --confirm-promote`.
- `rejected` jobs are never automatically promoted.
- Boss review excludes jobs already recorded as successfully delivered, and the send command checks the audit history again before opening any job page.
- A raw send command is not user authorization. Confirmation is completed only through the pending `interaction_required` response.
- Recruiting-platform browser actions run serially in the product order shown above.
- `jobagent round start` is the only command that creates a new round. A completed round stays completed until that explicit command runs.
- Never pre-login future platforms. Enter only the current platform, finish its `login -> discover -> review -> delivery preview -> delivery confirmation -> send -> audit` chain, and complete its audit before logging in to the next platform.
- Completing one platform is not completing the round. The Agent must follow `workflow.next_suggested` until `workflow.workflow_complete=true`.
- One send covers the complete reviewed selected list, up to 100 jobs. The default send limit is 100.
- If the CLI reports login, CAPTCHA, verification or resume-selection intervention, the Agent must stop and ask the user to complete it.

Example review override:

```bash
jobagent liepin apply review --promote <job-id> --confirm-promote
jobagent interaction respond --interaction-id "<id>" --choice confirm_all
jobagent liepin apply send --input <review_file> --preview-id <preview_id> --authorization-id <authorization_id>
```

## Signed Decisions

The cloud service returns signed SearchPlans and DecisionManifests. The CLI verifies the signature, protocol version, platform, expiry, Discover ID and candidate digest before saving or using a decision. Invalid or expired decisions cannot enter the official send workflow.

The local decision file contains the signed result needed for review and delivery. It does not persist the transient raw candidate pool.

## Updates

```bash
jobagent update check
```

Official managed installations verify the Core ReleaseManifest, exact Git tag and commit, canonical archive SHA256 and smoke test. The installer fixes the canonical Git archive permissions so machine-level Git settings cannot change the digest. Updates are deferred while a Discover or send action is active and roll back if verification or installation fails. If an older installation reports `release artifact hash mismatch`, re-run the official installer once; it preserves Job Agent account state, browser sessions, profiles, rounds and audits.

## Agent Instructions

The canonical agent workflow is in [docs/agent-onboarding.md](docs/agent-onboarding.md). Distribution assets are available for:

- [Claude Code](skills/claude-code/SKILL.md)
- [OpenClaw / ClawHub](skills/openclaw-job-agent/SKILL.md)

## Safety and Privacy

- Never paste API Keys, browser cookies or complete resume text into issues.
- Starting a job-search round authorizes discovery and signed review. Each platform's final delivery list still requires explicit user confirmation.
- Never auto-promote `review` jobs or send `rejected` jobs.
- Do not run shared browser actions in parallel.
- Stop immediately when the CLI requests user intervention.
- Use the platform normally and comply with its terms and applicable law.

## Support

- Product: [jobagent.agentmesh360.com](https://jobagent.agentmesh360.com/)
- Account and API Key: [agentmesh360.com/app](https://agentmesh360.com/app/)
- Public repository: [jiyangnan/AgentMesh-JobAgent](https://github.com/jiyangnan/AgentMesh-JobAgent)

After the first successful real delivery, Job Agent displays one optional GitHub star prompt. It is shown once and never affects installation or use.

## License

Apache License 2.0. See [LICENSE](LICENSE).
