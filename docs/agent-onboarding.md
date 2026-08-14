# Job Agent: Canonical Agent Workflow

This is the public instruction source for an Agent operating Job Agent on behalf of a user.

## Non-Negotiable Rules

1. Never invent, infer or fabricate an AgentMesh API Key.
2. Starting a job-search round authorizes discovery and signed review, not final delivery. After each platform's complete delivery preview, stop and obtain the user's final list decision.
3. Run recruiting platforms as complete vertical chains in this order: Boss直聘 -> 猎聘 -> 智联招聘 -> 51Job. Never pre-login future platforms; complete the current platform's `login -> discover -> review -> delivery preview -> delivery confirmation -> send -> audit` chain and complete its audit before logging in to the next platform.
4. When CLI output contains `requires_user_action=true`, stop immediately, relay `user_prompt` to the user and wait for their reply.
5. Never treat `review` or `rejected` as automatically sendable. A `review` job needs an explicit user override; `rejected` remains excluded.
6. Do not close the dedicated Job Agent Chrome window during a workflow.
7. Do not stop after one platform. Read the `workflow` object after every command and continue while `workflow.continue_required=true`.
8. Report the overall round complete only when `workflow.workflow_complete=true`. Skipping a platform requires the user's explicit approval and `jobagent round skip --platform <platform> --confirm-skip`.
9. Create a round only with `jobagent round start`. `doctor env`, `round status` and platform commands must never be treated as implicit authorization to create another round.
10. When a cloud command returns `retryable=true` and `request_preserved=true`, immediately execute its exact `next_suggested` command. Do not ask the user to click retry, re-login or recollect jobs. A failed start reuses its persisted `request_id` with `billing_status=not_charged`; a failed decision reuses its `discover_id` and candidates without an additional charge. If that preserved request crosses a valid signed SearchPlan's expiry, the CLI renews the same `request_id` and `discover_id` automatically with zero renewal charge. Signature, account or context mismatches remain hard stops.
11. When output returns `error=interaction_required`, use the host's native prompt card only when that interface is callable in the current surface and mode. Codex must use the ready-to-call `host_presentations.adapters.codex.arguments` when its `request_user_input` tool is callable, then map the returned label with `answer_mapping`; map its automatically added free-text Other answer using `free_text_other`. Other hosts map the declared fields and options exactly. If the interface is unavailable in the current mode, show `interaction.fallback_text` unchanged; do not claim the host has no card capability. Continue every answer through `jobagent interaction respond` using the exact interaction ID. Do not create a round until the CLI accepts the required answer.
12. When review returns `event=delivery_preview` with `error=interaction_required`, show every item in `delivery_preview.items`, then stop. Render the delivery-confirmation card or exact fallback and wait for the user to choose `confirm_all`, `exclude_jobs`, or `cancel_delivery`. Never select the recommended option automatically.
13. Continue the user's answer through the exact interaction ID. For exclusions, pass each displayed job number as `--exclude-index`, show the regenerated complete list, and stop for final confirmation again. Run send only after the CLI returns `event=delivery_authorized` with both `--preview-id` and `--authorization-id` in its exact command.
14. When send returns `delivery_preview_required` or `delivery_confirmation_required`, run only its safe review recovery command. Preserve prior promotions, do not recollect or recharge, and obtain a fresh user confirmation.
15. If Zhilian review detects a generic title or missing company/salary in an older preserved signed decision, run its exact `jobagent zhilian apply review` recovery. It reads only the signed detail URLs, replaces the signature on the same Discover with zero additional credits, regenerates the complete preview and still waits for confirmation. Never create a replacement round or Discover.

## Goal, Actions and Acceptance

Before each platform, state:

- Goal: complete one platform Discover and let the user approve the exact final delivery list.
- Actions: login check, Discover, signed review, complete delivery-preview display, user confirmation or exclusions, authorized delivery, audit.
- Acceptance: valid signed decision; every candidate classified once; previously delivered jobs excluded; every send candidate shown before action; authorization matches the confirmed final list; only authorized jobs are attempted; audit records the actual result.

At the start of a round, run:

```bash
jobagent round start
```

For a new round this first returns an AgentMesh360 `interaction_required` target-role confirmation. It contains three choices: accept the suggestions, append roles, or replace the suggestions. Render its native card when the current host interface is callable; otherwise show the numbered text fallback. Continue with the exact interaction ID:

```bash
# Accept the suggested roles
jobagent interaction respond --interaction-id "<id>" --choice accept_suggested

# Keep suggestions and request a role-input interaction
jobagent interaction respond --interaction-id "<id>" --choice append_roles

# Replace suggestions and request a role-input interaction
jobagent interaction respond --interaction-id "<id>" --choice replace_roles

# Answer the returned role-input interaction
jobagent interaction respond --interaction-id "<follow-up-id>" --target-role "<target role>"
```

If the user already named the target role in the current request, use `round start --target-role` directly and do not ask again. After confirmation, run `jobagent round status`. The CLI validates the interaction ID and profile digest, persists the confirmed role intent and four-platform order, then returns one `next_suggested` command. Follow it after each platform audit. A platform-level success is an intermediate milestone, not completion of the user's overall job-search round.

Do not treat a role that appears only in this document, README, a skill example,
test data or a previous conversation as user intent. If the current user did not
state a role, omit `--target-role` and use the CLI interaction. Profiles created
before target-role policy v2 are intentionally treated as unverified and require
one explicit role answer; they do not require another paid resume analysis.

Do not collect logins as a separate setup phase. At round start, log in to Boss only. Do not open or request the Liepin login until Boss audit has advanced `workflow.current_platform` to `liepin`; apply the same rule to Zhilian and 51Job.

One completed platform Discover accepts at most 100 candidate jobs and costs a fixed 10 credits. Cloud resume analysis costs 5 credits. The signed cloud response is authoritative for charges and refunds: pre-decision browser failures are not charged, cloud-decision failures are refunded, and retrying the same task does not charge twice. Registration, API Key creation, and the open-source client are free; new accounts start with zero cloud credits. The optional AgentMesh360 monthly pass costs CNY 29, lasts 30 days, and includes 1,000 shared credits without automatic renewal. Previously issued signup-trial credits remain usable until their original expiry.

Discover automatically retries bounded transient TLS, connection and gateway failures. Stable codes distinguish `tls_connection_eof`, `network_timeout`, `cloud_gateway_unavailable` and the non-retryable `tls_certificate_verification_failed`. After retries are exhausted, run the exact `next_suggested` command immediately. At start, `request_preserved=true` means the same persisted `request_id` will be used and no charge has occurred. At decision, it means the same `discover_id` and collected candidates will be used and a retry cannot add a duplicate charge. If a verified plan expires before either stage resumes, the CLI requests a freshly signed plan bound to those same IDs and continues without another collection or renewal fee. `search_plan_expired_recovery_pending` is safe to retry through its exact `next_suggested`; `search_plan_expired_recovery_required` stops for support because identity or protocol proof was insufficient. Do not replace this handoff with repeated health checks or user confirmation.

When `/v1/me` is temporarily unreachable, `round status` and a user-confirmed `round skip` may continue from key-bound local state. Treat `offline=true, stale=true` as an explicit freshness marker, not as a new round or a successful cloud check. Report a skip only after that command returns `ok=true`, then follow its local `workflow.next_suggested`. On `offline_account_proof_required` or `offline_account_proof_mismatch`, stop and follow the returned recovery; never edit account-state files or delete Job Agent state or its Chrome profile.

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

> 请打开 https://agentmesh360.com/app/ 免费注册或登录，在个人中心生成 AgentMesh360 全平台通用 API Key。开源客户端可免费使用；调用 AgentMesh360 云端能力需要可用 credits，新账户初始云端积分为 0。拿到 API Key 以后发给我，我再继续。请不要把 API Key 发到公开 Issue。

After the API Key is configured, run `jobagent doctor env`. Treat `environment_healthy` as the environment result and `workflow.ready` as execution readiness; do not reinterpret one as the other. If `cloud_access.usable=true`, tell the user which balance source is active and immediately execute the top-level `next_suggested`; `signup_trial_active` is a grandfathered entitlement and explicitly means no paid pass is required. Do not inspect or block on the dashboard's pass-purchase status. Ask the user to purchase only when the CLI returns `cloud_access.reason=insufficient_credits` with `paid_pass_required=true`, or a real cloud command returns `insufficient_credits`.

When `cloud_access.reason=signup_trial_active`, say this before continuing, filling in the returned values:

> 你的 AgentMesh360 账户仍有此前发放的体验额度：剩余 `{credit}` credits，有效期至 `{expires_at}`。无需购买通行证，我现在继续执行下一步。

Immediately run the returned `next_suggested` command after this message. Do not ask for confirmation.

Wait for the user. Then run:

```bash
jobagent init --key <your_api_key>
```

If authentication fails, show the exact error. Do not silently change workflows.

## 3. Analyze Resume

Ask for a PDF, DOCX, TXT or Markdown resume. Target role and cities are optional hints when the user has already stated them.

```bash
jobagent resume analyze --file <resume-path> \
  --target-role "<target role>" \
  --target-cities <city1> <city2>
```

Acceptance: output reports `ok=true` and a saved profile path.

Then execute the returned `jobagent round start`. If it returns `interaction_required`, render its card or fallback text and wait for the target-role answer. Continue through `jobagent interaction respond`; only an accepted structured response or an already-explicit direct `round start` creates the four-platform round. Final delivery authorization is still collected separately after each platform preview.

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

Report the `selected`, `review`, `rejected` and `skipped_delivered` counts. Then render the complete `delivery_preview.items` list as a compact table, or show `delivery_preview.fallback_text` unchanged. `skipped_delivered` jobs are not sendable. Render the returned confirmation card and wait. To include a review job, the user must independently choose its ID and authorize:

```bash
jobagent boss greet preview --promote <job-id> --confirm-promote
```

```bash
jobagent interaction respond --interaction-id "<id>" --choice confirm_all
# Or request exclusions, then pass every displayed number:
jobagent interaction respond --interaction-id "<id>" --choice exclude_jobs
jobagent interaction respond --interaction-id "<follow-up-id>" --exclude-index 2 --exclude-index 5
# Execute only the exact authorized command returned by the CLI:
jobagent boss greet send --input <review_file> --preview-id <preview_id> --authorization-id <authorization_id>
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
jobagent interaction respond --interaction-id "<id>" --choice confirm_all
jobagent liepin apply send --input <review_file> --preview-id <preview_id> --authorization-id <authorization_id>
jobagent liepin audit
```

### 智联招聘

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

智联结果页中的 `kw...` URL 片段属于平台内部状态，不代表云端签发的职位词。不要解析、回填或向用户展示它作为搜索条件；发生智联关键词错误时，报告 CLI 的可读 `query` 与机器错误，并遵循 `next_suggested`。没有用户明确批准，不得跳过智联。

智联页面可能在导航完成前长时间保持 `loading`，也可能在已登录首页保留通用“登录/注册”入口。该入口是弱证据，不得压过个人中心导航与简历管理、投递活动等独立强账户证据；可见凭据表单或登录验证界面才是强未登录证据。强登录与强账户证据同时存在时仍保持 `unknown` 并安全停止。`zhilian_session_state_unknown` 或 `zhilian_page_state_unknown` 不是未登录：不要要求用户重复登录，按 `retryable`、`request_preserved` 和精确 `next_suggested` 恢复。只有 `zhilian_login_required` 才请求用户介入。不要猜测或硬编码 `jl` 城市码；CLI 会根据页面标题、可见城市和岗位卡片的多源一致证据动态验证，证据不足则不返回候选且不收费。

智联搜索结果页不一定重复展示首页账户区。当前轮次和同一受管 Chrome 中未过期的成功登录检查可以作为辅助证据，但强登录表单或验证界面始终优先。`zhilian_job_cards_not_found` 表示结果页已就绪但岗位 DOM 无法安全解析，不表示用户掉线；按其 `retryable`、`request_preserved` 和精确 `next_suggested` 恢复，不要重新登录、新建轮次或删除 profile。签名 SearchPlan 的 `page_limit` 是上限而不是必须采满的页数：明确无结果或页面证据确认最后一页时立即结束当前可读查询，后续签名查询继续执行。全部查询耗尽时显示 `no_candidates` 与 `search_exhausted=true`，停下等待用户明确决定是否执行返回的 `round skip --confirm-skip`；不得重复同一 Discover。智联 URL 的 `kw...` 永远不能作为可读查询或恢复输入。

智联城市页可能只有可读 slug 而没有数值 city code。CLI 会先独立验证官方城市路由，再从该页提交原始可读 query；城市首页的推荐岗位不属于搜索结果。只有搜索路由已改变，并再次验证可读 query 与城市后，岗位才能进入候选。若结果页随后暴露数值 city code，CLI 会交叉验证后缓存；Agent 不得自行补码或跳过验证。

智联搜索动作按按钮、输入框 Enter、表单提交的一次性有界顺序执行，动作发出本身不代表成功。CLI 必须验证输入最终可读值，以及 URL、history、文档导航或结果状态的真实变化。若返回 `zhilian_search_input_not_committed`、`zhilian_search_submit_control_not_activated` 或 `zhilian_search_transition_not_observed`，向用户展示脱敏后的 `diagnostics.action_receipt`，不要重复 Discover，立即执行顶层只读诊断命令。`zhilian_search_navigation_pending` 只表示已经观察到搜索状态开始变化但未完成，沿用原 `request_id` 和零重复收费恢复；它拥有独立的有界恢复终态，不能改写成城市证据耗尽。

### 前程无忧 / 51Job

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

猎聘 must deliver both the account resume and the signed personalized greeting for each selected job. Verify the resume card/message and the exact greeting separately; the platform's default introduction is not the personalized greeting. 智联和 51Job only submit the account resume. The 51Job web chat entry is a QR handoff and is not part of the send flow.

## 5. Handling Review Jobs

Only the user can promote a `review` job:

```bash
jobagent <platform> apply review \
  --promote <job-id-1> <job-id-2> \
  --confirm-promote
```

For Boss, use `greet preview` instead of `apply review`.

Report the resulting `send_count` and reviewed file, display the regenerated complete `delivery_preview`, then stop for its delivery-confirmation interaction. Never add IDs that the user did not select or confirm delivery on the user's behalf.

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

When a managed client updates, forward these stderr progress stages in the user's language:

- `client_update_detected`: briefly report the old and new versions; do not ask for permission.
- `client_update_started`: report that the signed update is being installed before the requested command.
- `client_update_completed`: report that the update succeeded.
- `client_command_resumed`: continue the original command immediately; do not stop for another confirmation.
- `client_update_failed`: stop, report `message` and follow `next_suggested`. For `error_code=release_artifact_hash_mismatch`, run the returned official-installer recovery command once, then repeat the original command; do not disable verification or delete local state/browser profiles.

These stages are emitted only when a newer release is involved. Do not invent an update message for `status=current` or repeat a successful update notice on later commands. A first upgrade from an older client may begin with the compatibility `client_update_completed` and `client_command_resumed` stages because that older process could not emit the earlier stages.

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
