---
name: job-agent
description: Use AgentMesh Job Agent for resume-driven job discovery, signed review, user-confirmed delivery and audit on Boss直聘, 猎聘, 智联招聘 and 51Job.
version: 0.6.2
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

## Native mode takes precedence

In Codex, use the dedicated `codex-job-agent` skill and start with
`jobagent work next`. In `codex_native` mode, existing browser-facing commands
below return host tasks, not permission to use a legacy driver. Read each task's
complete instructions and result schema; run `jobagent work begin --work-id ID`
before the allowed native UI action, then
`jobagent work submit --work-id ID --result FILE`. On uncertainty, use
`jobagent work status` and read-only reconciliation, never repeat a send click.
Discover native Computer Use from this host's current tool documentation. If
unavailable, pause; do not fall back to CDP, browser JavaScript or hidden site
APIs. Reuse the bound session and preserve the final-preview confirmation rules.
Legacy platform examples below remain compatible command entry points; their
driver-specific recovery descriptions do not override native tasks. The full
Codex instructions are in the public
[Codex skill](https://github.com/jiyangnan/AgentMesh-JobAgent/blob/main/skills/codex-job-agent/SKILL.md).

## Safety Contract

- On Liepin `liepin_verification_required`, keep the existing browser tab and stop for the returned user prompt. Only after the user completes verification, run the exact `next_suggested` Discover command. Completed query pages and collected candidates resume from the preserved account-bound checkpoint; do not restart the round, request another login or repeat completed searches. Explicit no-results or verified final-page evidence retires only that query. An older request without a checkpoint remains preserved; never invent missing progress.

- Never invent an API Key. Ask the user to create an AgentMesh360 universal Key at `https://agentmesh360.com/app/` and wait. Registration and Key creation are free; cloud capabilities require available credits.
- After configuring the Key, run `jobagent doctor env`. Read `environment_healthy` and `workflow.ready` separately. If `cloud_access.usable=true`, briefly report the active balance source and run the top-level `next_suggested` immediately. Never block on `Pass: not purchased`; ask for a purchase only when `paid_pass_required=true` or a real cloud command returns `insufficient_credits`.
- New accounts start with zero cloud credits. For grandfathered `signup_trial_active`, tell the user: `你的 AgentMesh360 账户仍有此前发放的体验额度：剩余 {credit} credits，有效期至 {expires_at}。无需购买通行证，我现在继续执行下一步。` Then execute `next_suggested` without asking for confirmation.
- `jobagent init` returns `workbench_url=https://agentmesh360.com/workbench/` for the first-run handoff. Existing accounts may receive top-level `announcements` once after a successful account-verified command. For `id=jobagent_workbench_launch_202608`, show a non-blocking information card when the current host interface supports it, or a concise localized message with the URL, then continue the original `next_suggested`. Never ask for acknowledgement, rerun `init`, or repeat the announcement.
- Run Boss直聘 -> 猎聘 -> 智联招聘 -> 51Job as complete vertical chains. Never pre-login future platforms; complete the current platform's `login -> discover -> review -> delivery preview -> delivery confirmation -> send -> audit` chain and complete its audit before logging in to the next platform. Never operate their shared browser concurrently.
- Stop whenever `requires_user_action=true`; relay `user_prompt` exactly and wait.
- On a verified Zhilian query and city route, reject cross-city fallback cards and treat that query as empty; preserve valid candidates already collected by earlier signed queries.
- Never guess or hardcode a Liepin city code. The CLI first discovers unbundled cities from official links on the current result page, then uses an official search-results surface before the city-directory fallback. A readable city route is accepted only after the route changes and the page metadata/title, visible search input/URL query, and real result or explicit no-result state agree. City-home recommendations are not search results; a later numeric code is cached only after independent cross-verification. On a city-resolution error, preserve the current round, browser profile and request; follow the exact top-level recovery without starting another Discover or clearing state.
- During an authorized Liepin send, use each reviewed signed job-detail URL directly. Never rebuild a city search or require a numeric city code before delivery. Verify that the browser reached the exact signed detail route before any click; only an observed login wall may produce a login prompt. Preserve the same preview and authorization on any pre-action failure, with zero additional credits.
- If Zhilian review detects a generic title or missing company/salary in a preserved signed decision, follow its exact `jobagent zhilian apply review` recovery. It reads only the signed job detail URLs, repairs trusted fields, safely excludes only a candidate that remains unreviewable, and re-signs the same Discover with zero additional credits. It regenerates the remaining complete preview and still stops for the user's confirmation. Never replace it with a new round or Discover.
- Report `selected / review / rejected`, then show the complete final `selected` list and stop for the user's delivery decision.
- On `event=delivery_preview` with `error=interaction_required`, show every row in `delivery_preview.items`, then render the returned confirmation card. Never choose for the user. Map `confirm_all`, `exclude_jobs` or `cancel_delivery` through the exact interaction ID.
- For `exclude_jobs`, collect displayed job numbers and pass each as `--exclude-index`. Show the regenerated complete preview and stop for final confirmation again. Run send only after `event=delivery_authorized`, using the exact command containing both `--preview-id` and `--authorization-id`.
- On `delivery_preview_required` or `delivery_confirmation_required`, run only the returned safe review command, display the regenerated preview and obtain fresh confirmation. Preserve existing promotions; do not recollect or recharge.
- On 51Job `delivery_verification_indeterminate` with `retryable=true` and `request_preserved=true`, run the exact `next_suggested` with the same preview and authorization. Pending clicked jobs are reconciled from current-site evidence and are never clicked again; the remaining authorized jobs continue. Do not repeat Discover, clear state, rebuild the round or infer logout from the legacy history domain.
- On 51Job `completion_state=completed_with_unresolved`, do not run send again. Report cumulative delivered, unavailable and unresolved outcomes separately, then run the exact `jobagent 51job audit` continuation. Unresolved is neither delivered nor failed and must never be clicked again.
- Show `skipped_delivered` when present and never add those jobs back to the send list.
- Never promote `review` without IDs chosen by the user and `--confirm-promote`. Never auto-promote `rejected`.
- Starting the round authorizes discovery and signed review. Each platform's final list requires its own structured confirmation before real delivery.
- On Boss, a platform default introduction is not the reviewed greeting. Require the CLI's exact personalized-delivery verification.
- Never stop after one platform. Follow `workflow.next_suggested` while `workflow.continue_required=true`; only `workflow.workflow_complete=true` ends the round.
- Create a round only by executing `jobagent round start`. Never infer that `doctor env`, `round status` or a platform command created or authorized a new round.
- Never copy a target role from README, skill examples, prior users or test data. Pass `--target-role` only when the current user explicitly stated that role; otherwise omit it and use the returned target-role interaction.
- Require at least one user-confirmed target city. If `resume analyze` returns `target_cities_required`, ask for the cities and rerun the same resume command with `--target-cities`; no cloud analysis charge occurred. If `round start` returns `target_city_input`, continue through its exact interaction ID with repeated `--target-city` arguments. Never infer a city from browser location or old examples.
- On `error=interaction_required`, render the structured `interaction` as a native host card only when the card interface is callable in the current surface and mode. Codex uses the ready-to-call `host_presentations.adapters.codex.arguments` when `request_user_input` is callable and maps the returned label through `answer_mapping`; other hosts map every declared field option exactly and use `default_option_ids` as the recommendation marker. If the interface is unavailable in the current mode, show `interaction.fallback_text` unchanged and describe that as a mode-level text fallback, not a lack of host card support. Continue every answer through `jobagent interaction respond` with the exact interaction ID. Append/replace may return a second role-input interaction. If the user already named a target role, pass it directly to `round start` and do not ask again.
- Skip a platform only after explicit user approval with `jobagent round skip --platform <platform> --confirm-skip`.
- After an existing installation updates, run `jobagent upgrade-check` and resolve its `next_suggested` action before opening a platform. Never delete `~/.jobagent` or the Job Agent Chrome profile as a general fix; preserve credentials, login cookies, profiles, audits and preferences.
- Forward `client_update_detected -> client_update_started -> client_update_completed -> client_command_resumed` once in the user's language. Do not ask permission for a managed signed update and do not stop after success; continue the original command. Stop only on `client_update_failed`, report its `message`, and follow `next_suggested`. Older clients may first emit only the compatibility completion/resume pair.
- For `client_update_failed` with `error_code=release_artifact_hash_mismatch`, run the returned official-installer recovery command once and repeat the original command. It preserves Job Agent state and browser sessions; never disable the signature/tag/commit/archive checks or delete the managed profile.
- When a cloud command returns `retryable=true` and `request_preserved=true`, do not ask the user to retry, re-login or recollect jobs. Run the exact `next_suggested` command immediately. A failed start reuses its persisted `request_id` and has `billing_status=not_charged`; a failed decision reuses its `discover_id` and preserved candidates without an additional charge. If a valid signed SearchPlan expires during a preserved request, the CLI renews that same `request_id` and `discover_id` automatically with zero renewal charge; never create a replacement round or recollect jobs. Signature, account or context mismatches remain hard stops.
- Treat every signed SearchPlan `page_limit` as an upper bound. The CLI stops a query after explicit no-results or a verified final page, then continues any remaining signed queries. On `no_candidates` with `search_exhausted=true`, show the empty outcome and wait for the user's explicit platform-skip decision; never repeat the same Discover command.
- `round status` and a user-confirmed `round skip` may return `offline=true, stale=true` during a transient cloud outage after the CLI verifies the current API Key against its local account proof. Continue from the returned local workflow. Never claim a platform was skipped unless the skip command itself returns `ok=true`. On `offline_account_proof_required` or `offline_account_proof_mismatch`, stop and follow the declared recovery without editing or deleting local state.
- Profiles, rounds, decisions and audits are account-bound. On `local_state_owner_required`, ask the user to confirm ownership and run `jobagent account bind --confirm-legacy`. On `local_state_account_mismatch`, ask the user to confirm switching accounts and run `jobagent account switch --new-state`. Never edit account-state files manually.
- Diagnose browser slowness or conflicting login evidence with `jobagent browser diagnose --platform <platform>` before asking for another login. Treat `login.state=unknown` or `conflicting` as inconclusive.
- On Zhilian, a recent login check bound to the current round and managed Chrome may bridge a search-results page that omits the account header. Strong login forms still stop the flow. An independently verified readable city route may proceed without a numeric city code, but city-homepage recommendations are never search results; the readable query and city must be verified again after the search route changes. Treat `zhilian_job_cards_not_found` as a retryable selector diagnostic with no charge, not as proof that the user logged out. For `zhilian_search_input_not_committed`, `zhilian_search_submit_control_not_activated`, or `zhilian_search_transition_not_observed`, show the redacted `diagnostics.action_receipt`, do not repeat Discover, and run the returned read-only browser diagnostic.
- A managed Zhilian profile may contain the current homepage and an older result tab. Do not select a tab manually or reuse the old city. The CLI compares opaque target-state fingerprints, adopts only one uniquely changed official target after the visible city action, and still requires the readable city, original query and result state to agree. Ambiguous target changes stop safely with the same request and no extra charge.
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
jobagent resume analyze --file <resume-path> --target-cities <city1> [city2 ...]
jobagent round start
```

When the current user has explicitly named a role, pass that exact value to
both commands with `--target-role "<user-stated target role>"`.

Each completed platform Discover accepts at most 100 candidate jobs and costs a fixed 10 credits. Cloud resume analysis costs 5 credits. Registration, API Key creation, and the open-source client are free; new accounts start with zero cloud credits. The signed cloud response is authoritative for charges and refunds. The AgentMesh360 Standard Pass costs CNY 29.99 for 30 days and includes 1,000 shared credits; the Pro Pass costs CNY 69.99 for 30 days and includes 3,000 shared credits. An active pass can receive a CNY 15 add-on with 500 credits. Passes do not renew automatically. Previously issued signup-trial credits remain usable until their original expiry.

First-run handoff: after `jobagent init` succeeds and `jobagent doctor env` reports ready, proactively tell the user three things before any job work: (1) the web workbench `https://agentmesh360.com/workbench/` (also returned as `workbench_url` in the `init` output) — the resume profile, tailored question banks, voice mock interviews with dual-track reports, 8-stage application tracking, offer compare and negotiation practice live there; (2) if the account has no usable pass, the pass page `https://agentmesh360.com/app/?lang=zh-CN#pricing` (Standard CNY 29.99 / 1,000 credits; Pro CNY 69.99 / 3,000 credits; 30 days, no auto-renewal); (3) the recommended first action: build the resume profile — open the workbench profile page and paste the resume, or run `jobagent resume analyze --file <resume>` here (5 credits). Question banks, mock interviews and Discover all build on that profile.

## Platform Flow

```bash
jobagent round start
# After the target-role card:
jobagent interaction respond --interaction-id "<id>" --choice accept_suggested
jobagent round status
```

After every command, read the returned `workflow` object. Each audit must advance to the next platform until the four-platform round is complete.

Boss直聘:

```bash
jobagent boss login --check
jobagent boss discover
jobagent boss greet preview
jobagent interaction respond --interaction-id "<id>" --choice confirm_all
jobagent boss greet send --input <review_file> --preview-id <preview_id> --authorization-id <authorization_id>
jobagent boss audit
```

猎聘:

```bash
jobagent liepin login --check
jobagent liepin discover
jobagent liepin apply review
jobagent interaction respond --interaction-id "<id>" --choice confirm_all
jobagent liepin apply send --input <review_file> --preview-id <preview_id> --authorization-id <authorization_id>
jobagent liepin audit
```

Liepin city metadata is client-managed and live-verified. Do not substitute a remembered numeric code or reuse the city shown on an older tab; the CLI rejects cross-city evidence, keeps pagination on the verified readable route and caches a later numeric code only after independent cross-verification.

智联招聘:

```bash
jobagent zhilian login --check
jobagent zhilian discover
jobagent zhilian apply review
jobagent interaction respond --interaction-id "<id>" --choice confirm_all
jobagent zhilian apply send --input <review_file> --preview-id <preview_id> --authorization-id <authorization_id>
jobagent zhilian audit
```

Treat any Zhilian `kw...` URL segment as opaque platform state, never as the cloud-issued role keyword. Do not parse it, feed it back into search, or skip Zhilian because of it; follow the CLI's readable `query`, error and `next_suggested`.
Treat `zhilian_session_state_unknown` and `zhilian_page_state_unknown` as slow-loading or conflicting evidence, not as logged out. A persistent generic login/register entry is weak evidence and does not override independent account-navigation plus resume/activity evidence. A visible credential form or login challenge is strong evidence; strong login and strong account evidence together remain unknown and stop safely. Follow preserved-request recovery and ask the user to log in only for `zhilian_login_required`. Never guess or hard-code a `jl` city code: the CLI verifies changed codes from independent readable page evidence and returns no candidates/no charge when city evidence is insufficient.

51Job:

```bash
jobagent 51job login --check
jobagent 51job discover
jobagent 51job apply review
jobagent interaction respond --interaction-id "<id>" --choice confirm_all
jobagent 51job apply send --input <review_file> --preview-id <preview_id> --authorization-id <authorization_id>
jobagent 51job audit
```

Run each send line only after the CLI has accepted the user's final list confirmation. 猎聘 must verify both the account resume and the exact signed personalized greeting; a platform default introduction is not enough. 智联 and 51Job submit resumes only. 51Job's web chat is QR-only.

Boss and 猎聘 greetings must be signed, non-empty and at most 100 characters before preview or delivery. Never describe a 智联 or 51Job review note as a sent greeting.

## Review Override

```bash
jobagent <platform> apply review --promote <job-id> --confirm-promote
```

For Boss use `greet preview` in place of `apply review`.

## Completion Report

Include round ID, platform, Discover ID, category counts, credits, explicit overrides, attempted/delivered/failed/skipped counts, audit evidence and remaining platforms. Never report overall completion unless `workflow.workflow_complete=true`. Relay the optional one-time GitHub star prompt only if the CLI emits it.
