---
name: job-agent
description: AgentMesh Job Agent for resume-driven job discovery, signed review, user-confirmed delivery and audit on Boss直聘, 猎聘, 智联招聘 and 51Job. Use for 找工作, 投简历, 简历分析, job matching and recruiter greetings.
version: 0.6.16
---

# Job Agent

Operate Job Agent as an Agent-native CLI. The user controls API Key setup, platform login and review overrides.

## Host-independent workflow contract

Read `jobagent workflow contract` once for the installed protocol, command catalog
and input schemas. Protocol 2 uses the top-level `action`. The legacy
`workflow-contract` alias and `agent_action` field remain available for older
integrations. Use the current protocol returned by the installed CLI.

After the installation/account handoff, use this loop:

1. When the user explicitly requests a new job-search round, save their actual
   intent as JSON and run `jobagent workflow submit --input FILE`. Keep the same
   request ID and file when recovering that submission. Never infer roles/cities
   from examples or saved material. Submit does not create a round or spend credits.
2. Run `jobagent workflow next` to read one next action. This does not execute the
   action or issue a native browser permit. Repeated reads preserve the action ID.
3. Handle the returned `action.type` exactly, then return to `workflow next`:
   - `run`: execute the complete `action.argv` without a shell. For an issued
     workflow action this calls `workflow advance` with its ID and expected
     revision. Do not construct commands from `business_argv`, alter arguments,
     parallelize steps or retry an action with a new ID.
   - `ask`: display the product's complete card and all delivery-preview rows,
     including unknown filtering evidence and coverage notices. Wait for an
     actual answer, then use the exact interaction ID. An answer JSON file may
     contain only `choice`, `resume_id`, `target_roles`, `target_cities` and/or
     `exclude_indices`, as allowed by this card. Defaults are recommendations.
     If cards are unavailable in the current surface, relay the exact fallback.
   - `handoff`: explain the returned URL, user task, how to return to this same
     conversation and the CLI recheck. Workbench data is read through the CLI;
     the host must not scrape workbench pages as a data fallback.
   - `native_work`: act only on the current task returned by an offered
     `work begin`, with its nonce, binding, allowed mode and result schema.
     Use callable native Computer Use tools, fresh observations and serial UI
     actions. `reconcile_only` never permits another send/submit click. Missing
     native capability follows the returned pause schema; never switch drivers.
   - `wait`: wait for the returned interval and query that same operation.
   - `blocked`: relay the typed cause and exact recovery. Unknown results remain
     unresolved; never guess success, rewrite state or create a replacement task.
   - `done`: report its scope. Only `scope=round` means the four-platform round
     is complete; a setup or command result is not the end of a requested round.

Wait for every CLI process to exit. Progress events, including `credit_quote`,
are informational. A quote does not debit or reserve credits; the paid business
step checks the current account again. After a lost response, use
`workflow status --operation-id ID`. An old native result is not a new permit.

After a successful `doctor tls`, execute its safe `doctor env` continuation and
read the preserved work's current permissions. Repairing HTTPS never resets
observation attempts or authorizes a browser action. Product-managed public CA
roots are loaded automatically; do not depend on pip's private certifi path.

For an exhausted read-only collection, `work next`, `work status`,
`workflow next`, and a rejected `work begin` expose the same `recovery` object.
When `recovery.status=confirmation_required`, explain the declared recovery
scope and obtain explicit user consent before using `after_confirmation_argv`.
Existing consent for that exact scope remains valid. A `receipt_only` recovery
status permits submission of complete evidence already obtained, not another UI
observation, renewed budget or recovery task. Never infer recovery eligibility
from `reconcile_only` and the attempt count alone; read the actual action,
side-effect flag and current recovery contract.

The input shape for a new request is `{"request_id":"...","criteria":{...}}`.
Criteria can contain explicitly stated `target_roles`, `target_cities`, `salary`
and `company`. Read the installed contract for supported fields; never add
arbitrary commands or success receipts. Only `round start` creates a round.
Installation alone authorizes setup checks, not a paid analysis or new round.

All hosts follow Boss -> Liepin -> Zhilian -> 51Job as complete vertical chains:
login -> resume-sync confirmation -> credit check -> discover -> signed review ->
complete preview -> separate platform confirmation -> send -> audit.

- Use `resume list` / `resume status --id ID` for online resume facts. Bound
  resumes retain their own direction; a different role requires a matching
  resume. For an active round, the CLI asks before ending the old round and
  reselecting. Never offer a hard-apply override or silently choose the classic path.
- Resume synchronization is checked before search. `user_attested` means the
  user said it is synced; it is not observed proof of the platform attachment.
  New `pause_platform`/`skip_platform` choices skip this round's platform. A whole
  round pause and persisted legacy holds remain resumable.
- `cancel_delivery` cancels only the current list. It and excluding every row
  lead to a second card with exactly `search_again` or `skip_platform`. Wait for
  the user's choice. Re-search is a new paid request; pure filtering is free.
- For city/salary/company changes, submit a `round update` JSON containing
  `request_id` and `patch`, using `criteria_revision` from `round status` as
  `--expected-revision`. Omitted fields remain unchanged; `clear` explicitly
  clears salary/company filters. Show the regenerated full preview and obtain
  fresh confirmation. Unknown company facts are never assumed to match; missing
  city coverage does not mean the new city has no jobs.

Skills guide the host. They cannot intercept tools invoked outside this protocol.
Product-side account, signature, scope, ordering, preview and BrowserWork checks
remain authoritative. Unsupported protocol/capability errors must be surfaced;
do not revert to an improvised workflow.

## Installation-to-account handoff

An installation request includes the setup handoff. After every successful install,
reinstall or update requested by the user, read the final `onboarding_handoff`
output (or run `jobagent onboarding`). This read-only command works offline and
never starts a round, changes account state or charges credits. Do not finish the
turn with only “installed successfully” or a version check. If PATH is not yet
refreshed, use the returned `cli_command` argument array for this installation.
When the installer is used by a CLI-directed repair, preserve and resume the
original recovery continuation instead of starting a separate setup flow.

- If `onboarding.stage=api_key_required`, show the account-center link and the
  complete `user_prompt`: register/sign in, generate an API Key, then **return to
  this same Agent conversation** to continue configuration. The user can provide
  the Key in a trusted private conversation or configure it in their own terminal
  with `jobagent init --key <your_api_key>`, then return and say “我已配置 API Key，请继续
  Job Agent 设置。” Never run a placeholder or echo a supplied secret.
- If a Key is already configured, immediately run `jobagent doctor env`; local
  credential presence does not prove verification or completed setup. Do not ask
  for another Key merely because the client was reinstalled or updated.
- After successful `init`, run `jobagent doctor env` before any paid or browser
  action. Respect account ownership/recovery errors and distinguish
  `environment_healthy` from `workflow.ready`. A temporary verification outage
  preserves the Key and does not mean the user must register again.
- Relay each current setup prompt, including what the user should do on the web,
  **how to return here**, and what the Agent will do next. After a user reports
  that their workbench resume is ready, use `jobagent resume list` before proposing
  another analysis. Ask for missing resume/city/role inputs only; never infer them.
- Show the workbench URL and the next concrete step. Recommend a paid pass only
  after the CLI reports insufficient credits with `paid_pass_required=true`.
  After a purchase, ask the user to return here; recheck with `jobagent doctor env`.

A setup turn ends with a clear user handoff (including the return instruction),
a concrete recovery blocker, or verified readiness plus the next user choice.
An install alone does not authorize a new round, paid analysis or delivery.
Preserve existing rounds and confirmations. All later platform actions retain
Boss -> Liepin -> Zhilian -> 51Job order and final-list confirmation.

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

When the current task offers `app_scoped_window`, a native host without window
IDs can use its actual app reference with fresh window-selection evidence. This
is not a persistent physical-window handle. Before every UI action, read fresh
native state. Task-permitted window/tab selection or navigation to the declared
official URL may prepare the target; inspect again afterward. Before collection,
job/receipt inspection or recruiting actions, verify the selected window, bound
profile, official task page and bound account; otherwise pause.
A missing ID/list does not mean missing capability or prove a unique window.
Use the current schema and typed binding fields; never copy a changing title as
a stable ID or skip evidence to get a success receipt.

If native window actions are unavailable (for example `noWindowsAvailable`), or
AX and screenshots disagree about the selected context, stop collecting. If the
host exposes a native selection/activation API, use it once to activate the
existing bound Chrome, then read fresh state. Do not invent APIs or replay timed-out input
or sends. If still unusable, submit the minimal `pause_result_schema` with
`reason=permission_required` and `evidence.host_window_issue` set to the observed
`window_unavailable` or `ax_visual_mismatch`. Relay the CLI prompt asking the user
to bring the original window forward once. Do not infer lockscreen, logout or
missing Computer Use, or cancel, rebind, start a round or clear state to activate
it. Afterward, freshly verify window, profile, official page and bound account;
continue only under current work permissions, without repeating side effects.
If that one user foregrounding does not restore consistent observations, retain
the pause and report the host failure; do not ask again or loop actions.
Ordinary job-identity or page-evidence failures still use the technical blocked
branch, not this host-window pause. Foregrounding never resets attempts: when
the budget is exhausted, submit existing complete evidence only through the
returned `completion_command`; if further observation is needed, use the existing
explicitly confirmed read-only recovery offer. Otherwise retain the same work
and nonce.

For an eligible paused read-only `collect_search_page` task without a
`delivery_source`, follow the CLI's `work recover` offer after the user explicitly
confirms closing that failed task and verifying the same Chrome profile/platform
account to resume the preserved request. One confirmation covers this scope;
the agent handles window and page diagnosis. Run the returned `recover_session`
task before any further collection. A foreground Gmail tab or changed title does
not prove the window is lost; use current native observations and a host-provided
stable window ID/handle where available, never a copied old title. Successful
recovery may update actual window/group references while retaining the logical
session, round, request, Discover, completed pages and candidates. It creates no
new paid request; normal later cloud decision billing still applies. Delivery
work cannot use this recovery. During browser inspection, pause for actual
unavailable access, unresolved session/account ambiguity or a user
login/verification challenge; do not loop or
ask the customer to reconstruct missing historical calls. Use fresh receipt IDs
for new observations and reuse an ID only for an identical receipt replay.

Recovery preflight preserves the bound resume, round, pending request and
checkpoint on failure. Report the returned error and `recovery_cause` accurately:
`resume_binding_material_unavailable` is not proof that the source page is no
longer current. Honor `retryable`; a non-retryable material error needs its stated
prerequisite resolved before using the offered command for the original work.
Do not replace that command with a platform Discover or repeatedly cancel work.

If `0.6.12` previously cleared a local resume binding during failed recovery, the
updated CLI may recover only the original binding from the verified signed
SearchPlan. It must match the account, round, request, Discover, session and
confirmed intent, and pass a fresh check of the same server material. Preflight
does not write the binding; successful recovery continuation checks it again
before restoring it. No signed binding means no binding can be reconstructed.
Never substitute a local profile, current selection or new resume revision.

On `recovery_requires_new_round=true` for a stale or released binding, stop
retrying the original request. Explain that its frozen material is no longer
usable and obtain one explicit business confirmation to end the old round's
remaining platforms, preserve its history and start a new round with the user's
chosen current resume, role and cities. Existing approval of this exact scope
remains valid; technical recovery approval alone does not cover it. First run
`jobagent round status` and `jobagent work status`. If the original read-only
source remains open, use only the returned `cancel_command` for that same
`collect_search_page` task with `side_effect=false` and no `delivery_source`,
covered by the confirmed old-round closure. Require `ok=true` and
`event=browser_work_cancelled`, then check work status again. Do not cancel other
pending work or uncertain delivery to unlock the round; if no safe cancellation
is offered, follow the returned reconciliation flow. Once the original source
is closed and no other open work remains, use the existing
`jobagent round skip --platform <current_platform> --confirm-skip` serially for
the returned current platform, checking each successful result. Only after
`workflow.workflow_complete=true`, run `jobagent round start` and answer its
resume/role/city interactions with the user's choices. Let the CLI archive old
pending state; never delete history or reuse old signatures/candidates as new
results. Do not rerun resume analysis merely for this handoff or promise that
the new round is free; its cloud operations follow their normal billing contract.

If an error reports `recovery_receipt_saved=true` and
`browser_replay_permitted=false`, the successful UI receipt is already saved,
but continuation remains blocked. Do not claim no state changed, repeat the
browser action or submit a new observation to replace it. After the prerequisite
is resolved, use the offered original recovery command, or the explicit new-round
path when required; do not repeat `work begin` for that saved recovery receipt.

## Required Behavior

- On Liepin `liepin_verification_required`, keep the existing browser tab and stop for the returned user prompt. Only after the user completes verification, run the exact `next_suggested` Discover command. Completed query pages and collected candidates resume from the preserved account-bound checkpoint; do not restart the round, request another login or repeat completed searches. Explicit no-results or verified final-page evidence retires only that query. An older request without a checkpoint remains preserved; never invent missing progress.

- Never invent an AgentMesh360 API Key. Without one, ask the user to create a universal Key at `https://agentmesh360.com/app/` and wait. Registration and Key creation are free; cloud capabilities require available credits.
- After configuring the Key, run `jobagent doctor env`. Read `environment_healthy` and `workflow.ready` separately. If `cloud_access.usable=true`, briefly report the active balance source and run the top-level `next_suggested` when no user action is pending. If `requires_user_action=true`, relay the setup prompt and wait; never execute resume or Key placeholders. Never block on `Pass: not purchased`; ask for a purchase only when `paid_pass_required=true` or a real cloud command returns `insufficient_credits`.
- New accounts start with zero cloud credits. Eligible new accounts receive a 3-day welcome pass with 30 shared credits after account verification and the first successful sign-in — no card required, nothing renews automatically. For grandfathered `signup_trial_active`, tell the user: `你的 AgentMesh360 账户仍有此前发放的体验额度：剩余 {credit} credits，有效期至 {expires_at}。无需购买通行证，我现在继续执行下一步。` Then execute `next_suggested` without asking for confirmation.
- `jobagent init` returns `workbench_url=https://agentmesh360.com/workbench/` for the first-run handoff. Existing accounts may receive top-level `announcements` once after a successful account-verified command. For `id=jobagent_workbench_launch_202608`, show a non-blocking information card when the current host interface supports it, or a concise localized message with the URL, then continue the original `next_suggested`. Never ask for acknowledgement, rerun `init`, or repeat the announcement.
- Run platforms as complete vertical chains: Boss直聘 -> 猎聘 -> 智联招聘 -> 51Job. Never pre-login future platforms; complete the current platform's `login -> discover -> review -> delivery preview -> delivery confirmation -> send -> audit` chain and complete its audit before logging in to the next platform.
- When output contains `requires_user_action=true`, stop, relay `user_prompt` and wait for the user.
- Report `selected / review / rejected`, then show the complete final `selected` list and stop for the user's delivery decision.
- On `event=delivery_preview` with `error=interaction_required`, show every row in `delivery_preview.items`, then render the returned confirmation card. Never choose for the user. Map `confirm_all`, `exclude_jobs` or `cancel_delivery` through the exact interaction ID.
- For `exclude_jobs`, collect displayed job numbers and pass each as `--exclude-index`. Show the regenerated complete preview and stop for final confirmation again. Run send only after `event=delivery_authorized`, using the exact command containing both `--preview-id` and `--authorization-id`.
- On a verified Zhilian query and city route, reject cross-city fallback cards and treat that query as empty; preserve valid candidates already collected by earlier signed queries.
- Never guess or hardcode a Liepin city code. The CLI first discovers unbundled cities from official links on the current result page, then uses an official search-results surface before the city-directory fallback. A readable city route is accepted only after the route changes and the page metadata/title, visible search input/URL query, and real result or explicit no-result state agree. City-home recommendations are not search results; a later numeric code is cached only after independent cross-verification. On a city-resolution error, preserve the current round, browser profile and request; follow the exact top-level recovery without starting another Discover or clearing state.
- During an authorized Liepin send, use each reviewed signed job-detail URL directly. Never rebuild a city search or require a numeric city code before delivery. Verify that the browser reached the exact signed detail route before any click; only an observed login wall may produce a login prompt. Preserve the same preview and authorization on any pre-action failure, with zero additional credits.
- If Zhilian review reports that a preserved signed decision has a generic title or missing company/salary, run its exact `jobagent zhilian apply review` recovery. The CLI reads only the signed job detail URLs, repairs trusted fields, safely excludes only a candidate that remains unreviewable, and requests a replacement signature for the same Discover with zero additional credits. It regenerates the remaining complete preview and still waits for the user's confirmation. Never start another round or Discover for this repair.
- On `delivery_preview_required` or `delivery_confirmation_required`, run only the returned safe review command, display the regenerated preview and obtain fresh confirmation. Preserve existing promotions; do not recollect or recharge.
- On 51Job `delivery_verification_indeterminate` with `retryable=true` and `request_preserved=true`, run the exact `next_suggested` with the same preview and authorization. Pending clicked jobs are reconciled from current-site evidence and are never clicked again; the remaining authorized jobs continue. Do not repeat Discover, clear state, rebuild the round or infer logout from the legacy history domain.
- On 51Job `completion_state=completed_with_unresolved`, do not run send again. Report the cumulative delivered, unavailable and unresolved counts separately, then run the exact `jobagent 51job audit` continuation. An unresolved job is neither delivered nor failed and must never be clicked again.
- `review` is excluded by default. Promote only IDs named by the user and always pass `--confirm-promote`.
- Never automatically promote `rejected`.
- Show `skipped_delivered` when present and never add those jobs back to the send list.
- Keep the dedicated Job Agent Chrome window open.
- On Boss, do not report success from the platform's default introduction; require verification of the reviewed personalized greeting.
- Never stop after one platform. Follow `workflow.next_suggested` while `workflow.continue_required=true`; only `workflow.workflow_complete=true` ends the round.
- Create a round only by executing `jobagent round start`. Never infer that `doctor env`, `round status` or a platform command created or authorized a new round.
- Never copy a target role from README, skill examples, prior users or test data. Pass `--target-role` only when the current user explicitly stated that role; otherwise omit it and use the returned target-role interaction.
- Require at least one user-confirmed target city. If `resume analyze` returns `target_cities_required`, ask for the cities and rerun the same resume command with `--target-cities`; no cloud analysis charge occurred. If `round start` returns `target_city_input`, continue through its exact interaction ID with repeated `--target-city` arguments. Never infer a city from browser location or old examples.
- On `error=interaction_required`, use the host's native prompt card only when the card interface is callable in the current surface and mode. Codex uses the ready-to-call `host_presentations.adapters.codex.arguments` when `request_user_input` is callable and maps the returned label through `answer_mapping`. Other hosts map each `single` field's label, prompt, options and `default_option_ids` without inventing choices. If the native interface is unavailable in the current mode, show `interaction.fallback_text` unchanged and say only that the current mode is using the text form; never claim the host lacks cards. Continue every answer through `jobagent interaction respond` with the exact interaction ID. Append/replace may return a second role-input interaction. If the user already named a target role, pass it directly to `round start` and do not ask again.
- Skip a platform only after explicit user approval with `jobagent round skip --platform <platform> --confirm-skip`.
- After an existing installation updates, run `jobagent upgrade-check` and resolve its `next_suggested` action before opening a platform. Never delete `~/.jobagent` or the Job Agent Chrome profile as a general fix; preserve credentials, login cookies, profiles, audits and preferences.
- Forward `client_update_detected -> client_update_started -> client_update_completed -> client_command_resumed` once in the user's language. Do not ask permission for a managed signed update and do not stop after success; continue the original command. Stop only on `client_update_failed`, report its `message`, and follow `next_suggested`. Older clients may first emit only the compatibility completion/resume pair.
- For `client_update_failed` with `error_code=release_artifact_hash_mismatch`, run the returned official-installer recovery command once and repeat the original command. It preserves Job Agent state and browser sessions; never disable the signature/tag/commit/archive checks or delete the managed profile.
- When a cloud command returns `retryable=true` and `request_preserved=true`, do not ask the user to retry, re-login or recollect jobs. Run the exact `next_suggested` command immediately. A failed start reuses its persisted `request_id` and has `billing_status=not_charged`; a failed decision reuses its `discover_id` and preserved candidates without an additional charge. If a valid signed SearchPlan expires during a preserved request, the CLI renews that same `request_id` and `discover_id` automatically with zero renewal charge; never create a replacement round or recollect jobs. Signature, account or context mismatches remain hard stops.
- Treat every signed SearchPlan `page_limit` as an upper bound. The CLI stops a query after explicit no-results or a verified final page, then continues any remaining signed queries. On `no_candidates` with `search_exhausted=true`, show the empty outcome and wait for the user's explicit platform-skip decision; never repeat the same Discover command.
- `round status` and a user-confirmed `round skip` may return `offline=true, stale=true` during a transient cloud outage after the CLI verifies the current API Key against its local account proof. Continue from the returned local workflow. Never claim a platform was skipped unless the skip command itself returns `ok=true`. On `offline_account_proof_required` or `offline_account_proof_mismatch`, stop and use the declared recovery; never edit or delete local state.
- Profiles, rounds, decisions and audits are account-bound. On `local_state_owner_required`, ask the user to confirm ownership and run `jobagent account bind --confirm-legacy`. On `local_state_account_mismatch`, ask the user to confirm the account switch and run `jobagent account switch --new-state`. Never edit the owner file manually.
- For browser slowness or conflicting login evidence, run `jobagent browser diagnose --platform <platform>` before asking for another login. It is read-only; `login.state=unknown` or `conflicting` is not `login_required`.
- On Zhilian, a recent login check bound to the current round and managed Chrome may bridge a search-results page that omits the account header. Strong login forms still stop the flow. An independently verified readable city route may proceed without a numeric city code, but city-homepage recommendations are never search results; the readable query and city must be verified again after the search route changes. Treat `zhilian_job_cards_not_found` as a retryable selector diagnostic with no charge, not as proof that the user logged out. For `zhilian_search_input_not_committed`, `zhilian_search_submit_control_not_activated`, or `zhilian_search_transition_not_observed`, show the redacted `diagnostics.action_receipt`, do not repeat Discover, and run the returned read-only browser diagnostic.
- A managed Zhilian profile may contain the current homepage and an older result tab. Do not select a tab manually or reuse the old city. The CLI compares opaque target-state fingerprints, adopts only one uniquely changed official target after the visible city action, and still requires the readable city, original query and result state to agree. Ambiguous target changes stop safely with the same request and no extra charge.
- Forward progress stages and heartbeats during long operations. Use `jobagent round audit` for the compact result; use `--failures-only` or `--details` only when investigation requires records.

## Setup

```bash
jobagent init --key <your_api_key>
jobagent doctor env
jobagent resume analyze --file <resume-path> --target-cities <city1> [city2 ...]
jobagent round start
```

When the current user has explicitly named a role, pass that exact value to
both commands with `--target-role "<user-stated target role>"`.

One completed Discover covers one platform, processes at most 100 candidate jobs and costs a fixed 10 credits. Cloud resume analysis costs 5 credits. Registration, API Key creation, and the open-source client are free; new accounts start with zero cloud credits. Eligible new accounts receive a 3-day welcome pass with 30 shared credits after account verification and the first successful sign-in — no card required, nothing renews automatically. The signed cloud response is authoritative for charges and refunds. The AgentMesh360 Standard Pass costs CNY 29.99 for 30 days and includes 1,000 shared credits; the Pro Pass costs CNY 69.99 for 30 days and includes 3,000 shared credits. An active pass can receive a CNY 15 add-on with 500 credits. Passes do not renew automatically. Previously issued signup-trial credits remain usable until their original expiry.

First-run handoff: after `jobagent init` succeeds and `jobagent doctor env` verifies the account and environment, proactively tell the user three things before any job work: (1) the web workbench `https://agentmesh360.com/workbench/` (also returned as `workbench_url` in the `init` output) — the resume profile, tailored question banks, voice mock interviews with dual-track reports, 8-stage application tracking, offer compare and negotiation practice live there; (2) only if doctor returns insufficient_credits with paid_pass_required=true, the pass page `https://agentmesh360.com/app/?lang=zh-CN#pricing` (Standard CNY 29.99 / 1,000 credits; Pro CNY 69.99 / 3,000 credits; 30 days, no auto-renewal); (3) the recommended first action: build the resume profile — open the workbench profile page and paste the resume, or run `jobagent resume analyze --file <resume>` here (5 credits). Question banks, mock interviews and Discover all build on that profile. When directing the user to the workbench, explicitly ask them to return to this same Agent conversation and say “简历已准备好，请继续”; inspect existing online resumes with `jobagent resume list` before requesting another analysis.

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

Liepin city metadata is client-managed and live-verified. Do not replace it with a remembered numeric code or reuse the city shown on an older tab; the CLI rejects cross-city evidence, keeps pagination on the verified readable route and caches a later numeric code only after independent cross-verification.

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

### HTTPS dependency recovery

The client loads its bundled public root certificates automatically while preserving system trust. Explicit `SSL_CERT_FILE` or `SSL_CERT_DIR` settings remain authoritative. The installer checks cloud HTTPS without an API Key or business-state changes; a successful installation alone does not mean the account or workflow is ready. Normal `jobagent onboarding` remains offline.

For a TLS failure, run `jobagent doctor tls` once. Follow its typed `dependency_repair` command at most once if the product CA dependency is missing, then check again in a fresh process and resume the original command when verified. Keep the original work, nonce, round and credentials. Certificate expiry, hostname mismatch and untrusted custom/proxy certificates remain blocked: relay the specific guidance instead of repeatedly reinstalling or asking for another API Key. Never disable TLS/hostname verification, import an unverified certificate, modify Keychain, clear state or replay delivery to repair connectivity.
