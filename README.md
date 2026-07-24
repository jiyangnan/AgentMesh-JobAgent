# AgentMesh Job Agent

Job Agent is an Agent-native job-search product. Its open-source CLI connects recruiting platforms through the user's own browser session, while AgentMesh360 cloud intelligence provides the official candidate profile, job decisions and personalized communication.

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
  -> Boss Discover / signed review / automatic selected delivery / audit
  -> Liepin Discover / signed review / automatic selected delivery / audit
  -> Zhilian Discover / signed review / automatic selected delivery / audit
  -> 51Job Discover / signed review / automatic selected delivery / audit
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
jobagent resume analyze --file ~/Downloads/resume.pdf \
  --target-role "AI产品经理" \
  --target-cities 深圳 北京
jobagent round start
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
jobagent round status
```

Every platform command returns a `workflow` object. A platform audit does not end the overall task while `workflow.continue_required=true`; the Agent must run `workflow.next_suggested` and continue to the next platform. The overall task is complete only when `workflow.workflow_complete=true`. A platform may be skipped for the current round only after the user explicitly approves:

```bash
jobagent round skip --platform <platform> --confirm-skip
```

Long Discover and delivery operations emit timestamped stage events and periodic heartbeats. Transient TLS, connection and gateway failures are retried automatically for idempotent Discover requests. If all bounded attempts fail, the CLI returns `retryable=true`, `request_preserved=true` and one `next_suggested` command. The Agent must execute it directly: Job Agent resumes the same signed Discover and locally preserved candidate set instead of reopening the platform, recollecting jobs or charging again.

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
jobagent boss greet send
jobagent boss audit
```

Boss uses a personalized greeting. `greet preview` shows the signed decision and greeting before any real send. A platform-generated default introduction may establish the conversation, but it does not count as delivery until the reviewed personalized greeting is also verified in the chat.

### 猎聘

```bash
jobagent liepin login
jobagent liepin discover
jobagent liepin apply review
jobagent liepin apply send
jobagent liepin audit
```

### 智联招聘

```bash
jobagent zhilian login
jobagent zhilian discover
jobagent zhilian apply review
jobagent zhilian apply send
jobagent zhilian audit
```

智联结果页中的 `kw...` URL 片段是平台内部状态，不是云端生成的职位搜索词。Agent 必须以 CLI 返回的可读 `query`、错误码和 `next_suggested` 为准，不得把该片段重新用于搜索，也不得据此自行跳过智联。

### 前程无忧 / 51Job

```bash
jobagent 51job login
jobagent 51job discover
jobagent 51job apply review
jobagent 51job apply send
jobagent 51job audit
```

猎聘 completes two verified actions for every selected job: it sends the resume associated with the user's platform account and then sends the signed personalized greeting generated from the resume profile and job. A platform-owned default introduction does not count as the personalized greeting. 智联招聘 and 51Job remain resume-submit workflows; on 51Job, the web chat entry is a mobile QR handoff.

Boss and 猎聘 require a non-empty signed personalized greeting of at most 100 characters before either preview or real delivery can proceed. Their success records include the delivered-message evidence. 智联招聘 and 51Job explicitly report personalized message delivery as unsupported instead of treating a review note as a sent message.

## Review Rules

- `selected` jobs are delivered automatically after signed review; the Agent does not ask for another confirmation on each platform.
- `review` jobs are excluded unless the user explicitly promotes their job IDs with `--promote ... --confirm-promote`.
- `rejected` jobs are never automatically promoted.
- Boss review excludes jobs already recorded as successfully delivered, and the send command checks the audit history again before opening any job page.
- Send commands intentionally have no per-platform confirmation flag.
- Recruiting-platform browser actions run serially in the product order shown above.
- `jobagent round start` is the only command that creates a new round. A completed round stays completed until that explicit command runs.
- Never pre-login future platforms. Enter only the current platform, finish its `login -> discover -> review -> send -> audit` chain, and complete its audit before logging in to the next platform.
- Completing one platform is not completing the round. The Agent must follow `workflow.next_suggested` until `workflow.workflow_complete=true`.
- One send covers the complete reviewed selected list, up to 100 jobs. The default send limit is 100.
- If the CLI reports login, CAPTCHA, verification or resume-selection intervention, the Agent must stop and ask the user to complete it.

Example review override:

```bash
jobagent liepin apply review --promote <job-id> --confirm-promote
jobagent liepin apply send
```

## Signed Decisions

The cloud service returns signed SearchPlans and DecisionManifests. The CLI verifies the signature, protocol version, platform, expiry, Discover ID and candidate digest before saving or using a decision. Invalid or expired decisions cannot enter the official send workflow.

The local decision file contains the signed result needed for review and delivery. It does not persist the transient raw candidate pool.

## Updates

```bash
jobagent update check
```

Official managed installations verify the Core ReleaseManifest, exact Git tag and commit, canonical archive SHA256 and smoke test. Updates are deferred while a Discover or send action is active and roll back if verification or installation fails.

## Agent Instructions

The canonical agent workflow is in [docs/agent-onboarding.md](docs/agent-onboarding.md). Distribution assets are available for:

- [Claude Code](skills/claude-code/SKILL.md)
- [OpenClaw / ClawHub](skills/openclaw-job-agent/SKILL.md)

## Safety and Privacy

- Never paste API Keys, browser cookies or complete resume text into issues.
- Starting a job-search round authorizes automatic delivery of signed `selected` jobs; do not ask for another confirmation before each platform.
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
