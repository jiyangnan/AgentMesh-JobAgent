# Project Instructions for Codex

## What This Project Is

This is the public Job Agent CLI repository in the AgentMesh ecosystem. It contains the local client, installer scripts, public docs, and agent onboarding. Cloud AI features use the public AgentMesh360 API with a user-provided API Key.

## Start Here

1. `README.md` for public product scope and user setup.
2. `docs/agent-onboarding.md` for the canonical agent-driven workflow.
3. `docs/client-upgrade-contract.md` before changing persisted state or upgrade behavior.
4. `skills/claude-code/SKILL.md` and `skills/openclaw-job-agent/SKILL.md` when updating skill distribution assets.
5. `pyproject.toml` for package metadata and CLI entry points.

## Repo Map

- `src/` - CLI implementation.
- `tests/` - tests for public client behavior.
- `scripts/` - public install and helper scripts.
- `skills/` - public agent skills.
- `docs/agent-onboarding.md` - canonical instructions agents should follow.
- `README.md` - public-facing product and usage guide.

## Common Commands

- Install dev deps: `python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`
- Run CLI locally: `jobagent --help`
- Doctor: `jobagent doctor env`
- Test: `pytest`

## Product Rules

### Native execution takes precedence in 0.6.0

- Browser-facing `login`, `discover`, `review`, `send` and `browser diagnose` commands use the account-bound BrowserWork protocol. Read the returned task, result schema, `allowed_mode` and top-level `next_suggested`; these are authoritative for the current action.
- Use native Computer Use from the host's current callable tool documentation. Never invent a host API. Keep one bound Chrome window/profile and a small reusable task tab group; all real browser actions remain serial.
- Run the exact `work begin` command before an allowed action and submit only actual UI observations with its nonce and binding. `reconcile_only` permits receipt inspection, never another send or submit click. Missing native capability, login, verification, permission or inconclusive state requires the returned user handoff; do not fall back to CDP, browser JavaScript, hidden site APIs or another driver.
- Keep account ownership, signed search scope, user-confirmed cities and roles, full final preview and per-platform delivery authorization unchanged. Default platform greetings are not custom-message success; resume and application evidence remain separate. Uncertain external outcomes remain unresolved and are never retried by clicking again.
- The older CDP and driver-specific rules below govern maintenance of legacy implementation only. They do not authorize a customer agent to override a native task or return to a legacy browser executor. The Skill is operating guidance, not a technical interceptor of host UI actions.

- On Liepin `liepin_verification_required`, keep the existing browser tab and stop for the returned user prompt. Only after the user completes verification, run the exact `next_suggested` Discover command. Completed query pages and collected candidates resume from the preserved account-bound checkpoint; do not restart the round, request another login or repeat completed searches. Explicit no-results or verified final-page evidence retires only that query. An older request without a checkpoint remains preserved; never invent missing progress.

- Before suggesting a paid pass, run `jobagent doctor env`. Read `environment_healthy` separately from `workflow.ready`. New accounts start with zero cloud credits. If Core returns `signup_trial_active` for a grandfathered account, report the returned balance and expiry and continue with the top-level `next_suggested`; do not block on the absence of a paid pass. Suggest a pass only for `insufficient_credits` with `paid_pass_required=true`.
- `jobagent init` returns `workbench_url=https://agentmesh360.com/workbench/` for the first-run handoff. Existing accounts may receive top-level `announcements` once after a successful account-verified command. For `id=jobagent_workbench_launch_202608`, show a non-blocking information card when the current host interface supports it, or a concise localized message with the URL, then continue the original `next_suggested`. Never ask for acknowledgement, rerun `init`, stop the recruiting workflow, or repeat the announcement.
- Forward managed-update stages once in the user's language. Do not ask permission for a signed automatic update or stop after `client_update_completed`; continue the original command on `client_command_resumed`. Stop only on `client_update_failed`, report its recovery fields, and never expose command arguments in update telemetry.
- If `client_update_failed` reports `release_artifact_hash_mismatch`, use the returned official-installer recovery command once and then continue the original command. This repairs canonical Git archive configuration without deleting `~/.jobagent`, the managed Chrome profile, credentials, profiles, rounds, decisions or audits. Never bypass the tag, commit, signature or archive check.
- When a cloud command returns `retryable=true` and `request_preserved=true`, execute its exact `next_suggested` command immediately without asking the user to retry, re-login or recollect jobs. A failed Discover start reuses its persisted `request_id` and reports `billing_status=not_charged`; a failed decision reuses its `discover_id` and preserved candidates without an additional charge. Do not start a replacement round.
- During a classified transient cloud outage, `round status` and an explicitly confirmed `round skip` may return account-bound local state with `offline=true` and `stale=true`. Continue from that workflow, but never report a platform as skipped unless the skip command returns `ok=true`. `offline_account_proof_required` and `offline_account_proof_mismatch` are hard recovery boundaries: do not edit `state_owner.json`, claim another account's state, delete `~/.jobagent`, or delete the Job Agent Chrome profile.
- For Zhilian, treat every `kw...` URL segment as opaque platform state. Never report it as the cloud search keyword, feed it back into another search, or use it as grounds to skip Zhilian; trust the CLI's readable `query`, machine-readable error and `next_suggested`.
- A managed Zhilian Chrome profile may contain both the current homepage and an older result tab. Do not choose or close a tab manually. The CLI may adopt one uniquely changed official target after a visible city action, but it must still verify the requested readable city, original query and result state; ambiguous target changes remain fail-closed and preserve the request without another charge.
- Zhilian search submission succeeds only after the readable input and a real route, history, navigation or result-state change are verified. The CLI may try controlled input, the search button, input Enter and form submit once each in a bounded sequence. For `zhilian_search_input_not_committed`, `zhilian_search_submit_control_not_activated` or `zhilian_search_transition_not_observed`, relay the redacted `diagnostics.action_receipt`, do not rerun Discover, and execute the returned read-only `browser diagnose`. `zhilian_search_navigation_pending` has a separate bounded recovery and must never be described as a city-evidence failure.
- Treat each signed Zhilian `page_limit` as an upper bound. Explicit no-results or verified final-page evidence ends only that readable query; later signed queries continue. When all queries return `no_candidates` with `search_exhausted=true`, show the empty outcome and stop for the user's explicit skip decision instead of repeating Discover.
- Zhilian may keep a search page in `loading` or retain a generic login/register entry after the account area is ready. That generic entry is weak login evidence and must not override independent account-navigation plus resume/activity evidence. A visible credential form or login challenge is strong evidence; if strong login and strong account evidence coexist, remain `unknown` and stop safely. Treat `zhilian_session_state_unknown` and `zhilian_page_state_unknown` as inconclusive, not logged out; follow their exact recovery and do not ask for another login unless the CLI returns `zhilian_login_required`. Never hard-code a replacement `jl` city code. An existing numeric city route describes only the current page and cannot prove a newly requested city; if its readable city differs, continue through an exact official target-city route and do not collect the old page. An independently verified readable city slug may continue to submit the original readable SearchPlan query without a numeric code, but city-homepage recommendations are not search results. The result route must change and re-verify the readable query and city before candidate extraction; a later numeric code is cached only after cross-verification.
- For Liepin, never guess or hard-code a city code. Resolve an unbundled target city from official city links on the current trusted result page, then use a bounded official search-results fallback before the city directory. A readable official city-slug route may continue without a numeric code only after the route changes and the page metadata plus title, visible search input plus URL query, and a real result or explicit no-result state all agree. Keep pagination on that route, reject city-homepage recommendations and stale cross-city controls, and cache a later numeric code only after independent cross-verification. A city-directory redirect to the candidate homepage is not login evidence. On failure, preserve the round, browser profile and Discover request.
- During an authorized Liepin send, open every reviewed signed job-detail URL directly. Do not rebuild a city search or require a numeric city code before delivery. Verify the exact signed detail route before any platform action; only an observed login wall may request login. A pre-action failure must preserve the original preview, authorization and round with zero additional credits.
- Never guess or hard-code a Liepin city code. The CLI resolves an unbundled target city from Liepin's official city directory, then requires independent agreement among the readable city control, page metadata or title, original readable query, and a real result or explicit no-result state. Cache city metadata only after that verification; reject an older tab showing another city. On a city-resolution failure, preserve the current round, browser profile and Discover request, and follow the exact returned recovery command without starting another round or clearing state.
- Follow the persisted workflow and `next_suggested`; never invent a parallel or batch-login workflow.
- Platforms run as complete vertical chains in this order: Boss -> Liepin -> Zhilian -> 51Job. Complete the current platform's `login -> discover -> review -> delivery preview -> delivery confirmation -> send -> audit` chain before logging in to the next platform.
- Starting a job-search round authorizes discovery and signed review, not final delivery. Each platform's complete final list requires a separate structured user confirmation before send.
- When review returns `event=delivery_preview` with `error=interaction_required`, show every row in `delivery_preview.items`, then stop for the declared confirmation. Offer exactly `confirm_all`, `exclude_jobs`, and `cancel_delivery`; never choose for the user. Use the native card when callable, otherwise relay `delivery_preview.fallback_text` unchanged.
- For exclusions, pass each displayed job number as `--exclude-index`, show the regenerated complete list, and stop for final confirmation again. Run send only after `interaction respond` returns `event=delivery_authorized` and an exact command containing both `--preview-id` and `--authorization-id`.
- If send returns `delivery_preview_required` or `delivery_confirmation_required`, execute only its safe review recovery command. Preserve prior promotions and exclusions, do not recollect or recharge, and obtain a fresh user confirmation before delivery.
- If 51Job send returns `delivery_verification_indeterminate` with `retryable=true` and `request_preserved=true`, execute the exact `next_suggested` command with the same preview and authorization. The client reconciles pending clicked jobs from current `we.51job.com` evidence without clicking them again and continues the remaining authorized batch. Do not repeat Discover, clear local state, rebuild the round, or treat the separately authenticated legacy history domain as the current session authority.
- If 51Job send returns `completion_state=completed_with_unresolved`, do not execute send again. Report cumulative delivered, unavailable and unresolved outcomes separately, then execute the exact audit continuation. A terminal unresolved item is neither delivered nor failed and must never be reopened or clicked again.
- If an upgraded Zhilian review repairs incomplete title, company, or salary fields, follow its exact `next_suggested` review command. The repair is bound to the original signed Discover and detail URLs, adds zero credits, replaces only the stale preview and unanswered interaction, and must stop again at the regenerated complete delivery preview for user confirmation. Never start another round or Discover for this recovery.
- `review` jobs require explicit user-selected IDs and `--confirm-promote`. Never auto-promote `rejected` jobs.
- Stop and relay the exact prompt whenever the CLI returns `requires_user_action=true`.
- Never delete `~/.jobagent` or the Job Agent Chrome profile as a general upgrade fix. Follow `client_upgrade_required`, `conflicts`, and `next_suggested`.
- Local profiles, rounds, signed decisions, archives and audits are account-bound. Never silently claim legacy state or bypass an account mismatch; preserve the explicit `account bind --confirm-legacy` and `account switch --new-state` handoffs.
- `jobagent round start` is the only operation that creates a round. Status, doctor, browser helpers and platform internals must not create one implicitly.
- Require at least one user-confirmed target city. If `resume analyze` returns `target_cities_required`, ask for the cities and rerun the same resume command with `--target-cities`; the rejected attempt did not call the cloud or consume profile-analysis credits. If an older profile reaches `round start` without cities, continue its shared `target_city_input` interaction through the exact interaction ID with repeated `--target-city` arguments. Never infer a city from the browser location, documentation, fixtures or another user.
- After cities are confirmed, a new `jobagent round start` returns the shared AgentMesh360 `interaction_required` target-role confirmation. Use a native prompt card only when the host exposes that interface in the current surface and mode. Codex uses the ready-to-call `host_presentations.adapters.codex.arguments` when `request_user_input` is callable and maps answers through `answer_mapping` or `free_text_other`; other hosts map the declared fields and options exactly. Use `default_option_ids` for the recommendation and continue with `jobagent interaction respond` using the exact interaction ID. If the current mode does not expose cards, relay `interaction.fallback_text` unchanged; do not claim the host itself lacks card support. Only a CLI-accepted response may create the round. If the user already named the role, pass it directly with `round start --target-role` and do not ask again.
- Never infer user intent from README, skill examples, fixtures, prior users or a saved pre-policy-v2 profile. Pass `--target-role` only when the current user explicitly named it. Otherwise omit the flag and complete the returned interaction; this confirmation does not require another resume-analysis charge.
- A round bound to a workbench resume derives its suggested role and profile digest from that resume's own bound material, never from the local profile snapshot, and delivers only the binding's direction. Confirmed cities prefer the bound material's cities and fall back to this machine's last confirmed cities only when the resume names none. If the user deliberately wants a different direction, guide them to re-bind a matching resume: answer the target-role card with `--choice rebind_resume` and re-run `jobagent round start` (or keep the classic path with `--no-resume-binding`). Direct `--target-role` values outside the bound direction and `append_roles`/`replace_roles` responses on a bound card are refused by design; relay the refusal instead of working around it. Never attach a binding to an active round whose confirmed intent already points at a different direction; guide the user to finish or skip that round first, or re-select a matching resume.
- For browser incidents, preserve the Chrome profile and use the read-only `jobagent browser diagnose --platform <platform>` before asking for another login. `unknown` or `conflicting` login evidence is inconclusive.
- When a cloud command returns `retryable=true` and `request_preserved=true`, run its exact `next_suggested` command without asking the user to retry, re-login or recollect jobs. An expired but otherwise valid signed SearchPlan is renewed against the same `request_id` and `discover_id` with zero renewal charge; never create a replacement round or recollect jobs. Signature, account and context mismatches remain hard stops.
- Normal audit output is compact. Use round-level summaries by default and expand bounded failures/details only when investigation requires records.
- Boss and Liepin require signed, non-empty personalized greetings of at most 100 characters and exact outgoing-message evidence. Zhilian and 51Job are resume-submit-only and must never be reported as greeting delivery.

## Safety Rules

- Do not add internal-only strategy, progress, launch reports, admin runbooks, server operations, private prompts, infrastructure details, or secrets to this public repo.
- Use public-safe wording. Avoid exposing anti-abuse internals, platform evasion language, or private operational tactics.
- Do not weaken signed-decision verification, platform order, user-intervention prompts, review overrides, delays, audit logging, or privacy boundaries without a clear product decision.
- User resumes, cookies, local profiles, API Keys, and audit logs are sensitive.
- Real browser actions are serial. Never run shared Chrome sessions or shared audit/state writes concurrently.

## Collaboration Rules (added 2026-08-27 per Ferdinand)

- Multiple agents (codex, zcode) work here concurrently. Branch naming:
  `<agent>/<short-description>-<YYYYMMDD>`; always branch from the latest
  `origin/main`; never force-push `main`.
- **Worktree/branch merge-back**: if work developed in a worktree or branch
  ships anywhere (release, docs users install, skills users download), the
  same session that shipped it must land it on `main` and push. Shipped
  artifacts that `main` does not contain are a P0 process violation.
  Remove worktree registrations once merged; docs/review-only branches must
  be resolved (merge or delete) within one week.

## Current Focus

This repository should remain a clean public distribution surface: installable CLI, public docs, public skills, tests, and user-safe onboarding. Internal R&D decisions belong elsewhere.

## Done Means

- Public wording and links are safe for GitHub.
- `pytest` or a focused CLI smoke check was run, or the final answer explains why not.
- README, onboarding, Skills, and CLI output agree on platform order, user-confirmed delivery, review overrides, upgrade recovery, and user-intervention points.
- The final handoff lists changed files, verification, and whether any real platform/session data was touched.
