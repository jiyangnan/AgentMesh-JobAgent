# AgentMesh Job Agent

Job Agent is an Agent-native CLI for discovering and applying to relevant jobs with the user's own browser session.

It supports four independent recruiting-platform workflows:

1. Boss直聘
2. 猎聘
3. 智联招聘
4. 前程无忧 / 51Job

Each platform is isolated from the others. A page change on one platform does not disable the remaining workflows.

## Product Flow

```text
Resume profile
  -> platform Discover
  -> signed selected / review / rejected decision
  -> user review
  -> explicit confirmation
  -> greeting or resume submission
  -> local audit
```

One completed Discover covers one platform and processes up to 100 deduplicated candidate jobs. AgentMesh 360 is currently in free-open mode, so every account has unlimited access and a completed Discover deducts 0 credits. If paid mode is introduced later, the signed cloud response remains the authority for any charge or refund.

Job Agent uses an AgentMesh API Key. Create one from the [AgentMesh 360 account center](https://agentmesh360.com/app/).

## Install

macOS or Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/jiyangnan/AgentMesh-JobAgent/main/scripts/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/jiyangnan/AgentMesh-JobAgent/main/scripts/install.ps1 | iex
```

The official installer creates a managed installation. Starting with `0.3.0`, managed installations verify signed release policy and safely update between commands. A developer source checkout is never changed automatically.

## Set Up

```bash
jobagent init --key <your_api_key>
jobagent doctor env
jobagent resume analyze --file ~/Downloads/resume.pdf \
  --target-role "AI产品经理" \
  --target-cities 深圳 北京
```

The resume original and recruiting-site cookies remain on the user's machine. The profile and candidate job fields needed for Discover are sent to the Job Agent cloud service for decision.

## Platform Commands

### Boss直聘

```bash
jobagent boss login
jobagent boss discover
jobagent boss greet preview
jobagent boss greet send --confirm-send
jobagent boss audit
```

Boss uses a personalized greeting. `greet preview` shows the signed decision and greeting before any real send. A platform-generated default introduction may establish the conversation, but it does not count as delivery until the reviewed personalized greeting is also verified in the chat.

### 猎聘

```bash
jobagent liepin login
jobagent liepin discover
jobagent liepin apply review
jobagent liepin apply send --confirm-submit
jobagent liepin audit
```

### 智联招聘

```bash
jobagent zhilian login
jobagent zhilian discover
jobagent zhilian apply review
jobagent zhilian apply send --confirm-submit
jobagent zhilian audit
```

### 前程无忧 / 51Job

```bash
jobagent 51job login
jobagent 51job discover
jobagent 51job apply review
jobagent 51job apply send --confirm-submit
jobagent 51job audit
```

猎聘、智联招聘和 51Job submit the resume associated with the user's platform account. They do not send a Boss-style greeting. On 51Job, the web chat entry remains a mobile QR handoff; Job Agent only uses the resume-submit action.

## Review Rules

- `selected` jobs enter the default send list.
- `review` jobs are excluded unless the user explicitly promotes their job IDs with `--promote ... --confirm-promote`.
- `rejected` jobs are never automatically promoted.
- Boss review excludes jobs already recorded as successfully delivered, and the send command checks the audit history again before opening any job page.
- Every real greeting or application still requires `--confirm-send` or `--confirm-submit`.
- Recruiting-platform browser actions run serially in the product order shown above.
- If the CLI reports login, CAPTCHA, verification or resume-selection intervention, the Agent must stop and ask the user to complete it.

Example review override:

```bash
jobagent liepin apply review --promote <job-id> --confirm-promote
jobagent liepin apply send --confirm-submit
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
- Never send a greeting or application without the user's explicit confirmation.
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
