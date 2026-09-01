# Changelog

All notable public Job Agent client changes are documented here.

## [0.5.41] - 2026-09-01

### Added

- Record one account-bound `jobagent_initialized` fact after the first verifiable online account setup observed by this client, including a successful legacy bind or account switch, without claiming an installation that predates this instrumentation.
- Record one `delivery_verified` fact per recruiting platform only after a completed real delivery has `delivered > 0` and the corresponding success audit is already persisted. Dry runs, incomplete batches and zero-delivery outcomes do not qualify.
- Relay these two privacy-whitelisted facts through the authenticated AgentMesh endpoint on a best-effort background thread. A bounded `0600` local spool retains rejected, unacknowledged and offline events; opt-out and kill-switch controls stop both collection and relay.

### Compatibility

- The new spool follows the existing account namespace during account switches and never contains account identifiers, API Keys, rounds, jobs, resumes, URLs or command arguments. Owner or Key proof mismatches fail closed.
- Analytics network work has a short timeout and is never joined by the business command. It cannot change command output, exit codes, workflow state, browser sessions, delivery authorization, credits or recruiting-platform actions.

## [0.5.40] - 2026-08-28

### Added

- Return the Job Agent web workbench URL during first-time API Key setup so a new user's Agent can introduce the persistent resume, interview, application, offer and negotiation workspace before job-platform work begins.
- Notify existing account-bound installations about the workbench once on their next successful verified command. The machine-readable announcement is non-blocking and keeps the original workflow continuation unchanged.
- Persist the once-only delivery marker inside each account's local namespace so managed upgrades and account switching neither repeat the notice nor leak it across accounts.

### Compatibility

- Existing API Keys, managed Chrome profiles, recruiting-site sessions, resume profiles, active rounds, signed decisions, delivery previews, authorizations and audits remain unchanged. The announcement does not create a round, consume credits, open a browser or perform a recruiting action.
- Failed or unverified commands do not consume the announcement. New installations already receiving the first-run workbench handoff do not receive a duplicate launch notice.

## [0.5.39] - 2026-08-23

### Fixed

- Bind the initial Zhilian keyword activation and every bounded fallback submission method to the same CDP target lifecycle. A search action can no longer create an unowned page that survives a preserved-request retry.
- Adopt one exact opener-linked official Zhilian target even while it is still on a provisional root page, close the previous action origin, and leave readable city, original query and result-state validation to the existing fail-closed collector.
- Return `zhilian_search_target_cleanup_unverified` with a redacted action receipt when the exact action target cannot be reconciled or closed safely, instead of continuing against an ambiguous historical tab.
- Strengthen the isolated Linux/Xvfb gate with two consecutive production search cycles in a high-cardinality target registry. Each cycle must adopt the action-owned target, close the prior origin and restore the platform target count before release.

### Compatibility

- Existing API Keys, account binding, managed Chrome profile, login session, active round, completed audits and the preserved uncharged Zhilian Discover request remain unchanged. Managed clients resume the same `jobagent zhilian discover` command after upgrade; no new round, relogin, profile cleanup, recollection or additional charge is required.

## [0.5.38] - 2026-08-23

### Fixed

- Verify that the exact CDP target identity disappears after Chrome accepts a close request. A plain-text close response alone is no longer treated as proof that the page target was removed.
- Apply bounded provisional-target cleanup to Zhilian search actions as well as city-selection actions. When the action origin completes the requested transition, the client keeps that origin and removes the action-created generic child instead of leaving one more historical page after every recovery attempt.
- Fail closed with `action_target_cleanup_unverified` when the exact target cannot be proven closed or its identity changes during cleanup, rather than continuing with an ambiguous target registry.
- Extend the isolated Linux/Xvfb release gate with two consecutive real search-control activations in a high-cardinality target registry. Both cycles must reuse the correct origin, remove the exact action child and leave the platform target count unchanged.

### Compatibility

- Existing API Keys, account binding, managed Chrome profile, login session, active round, completed audits and the preserved uncharged Zhilian Discover request remain unchanged. Managed clients resume the same `jobagent zhilian discover` command after upgrade; no new round, relogin, profile cleanup, recollection or additional charge is required.

## [0.5.37] - 2026-08-23

### Fixed

- Keep observing both the Zhilian action origin and its newly opened platform page until one official destination proves the requested readable city. A generic root page is no longer committed merely because it belongs to the official domain.
- Reuse the action origin when it completes the city transition, and close only the unique platform page created by that action when the child remains on the generic root. A bounded failed transition preserves the origin and reports `action_target_navigation_not_observed` instead of accumulating another page target.
- Extend the isolated Linux/Xvfb release gate to model a high-cardinality managed profile where the action child stays on the root page while the origin reaches the target-city results. The gate covers two cities and requires the platform target count to remain stable.

### Compatibility

- Existing API Keys, account binding, managed Chrome profile, login session, active round, completed audits and the preserved uncharged Zhilian Discover request remain unchanged. Managed clients resume the same `jobagent zhilian discover` command after upgrade; no new round, relogin, profile cleanup, recollection or additional charge is required.

## [0.5.36] - 2026-08-23

### Fixed

- Recover Zhilian city and search transitions when a managed Chrome profile contains many historical platform tabs. The client aligns the action origin with the actual CDP connection and prefers the one official target opened by that action instead of relying on a globally unique changed tab.
- Keep action-created targets under bounded observation while they move through a transient blank or loading state. The target is adopted only after it reaches a trusted official page; multiple action-linked targets, non-official pages and incomplete evidence remain blocked.
- Extend the isolated Linux/Xvfb release gate to at least 16 historical Zhilian targets, one real action child and one unrelated changing target. The gate covers two target cities and verifies readable city, original query and result state without an account, user data or recruiting action.

### Compatibility

- Existing API Keys, account binding, managed Chrome profile, login session, active round, completed audits and the preserved uncharged Zhilian Discover request remain unchanged. Managed clients resume the same `jobagent zhilian discover` command after upgrade; no new round, relogin, profile cleanup, recollection or additional charge is required.

## [0.5.35] - 2026-08-23

### Fixed

- Recover Zhilian city selection when a managed Chrome profile contains both the current homepage and an older search-result target. The client records only opaque URL/title fingerprints, adopts one uniquely changed official target, and then applies the existing readable-city, original-query and result-state verification before collection.
- Keep multi-target recovery fail-closed. Multiple changed targets, non-official pages, stale-city evidence or incomplete city/query evidence cannot enter candidate collection.
- Strengthen the Linux/Xvfb release gate with an isolated active round, platform target registry, disposable managed Chrome profile, recent login receipt and two pre-existing Zhilian targets. The gate covers two target cities and performs no account, user-data or recruiting action.

### Compatibility

- Existing API Keys, account binding, profile, managed Chrome profile, login session, active round, completed audits and the preserved uncharged Zhilian Discover request remain unchanged. Managed clients resume the same `jobagent zhilian discover` command after upgrade; no new round, relogin, recollection or additional charge is required.

## [0.5.34] - 2026-08-23

### Fixed

- Recover Zhilian searches when an authenticated session redirects an independently discovered target-city route back to the generic homepage. The client now uses a bounded visible-city-control path, then resubmits the original readable query and requires matching city, query and result-page evidence before collecting candidates.
- Disable city-directory and route fallbacks while the authenticated-homepage recovery is selecting the target city, preventing the client from looping back to the stale city or directory before the visible selection can settle.
- Keep stale-city and login protections fail-closed: an older numeric city route cannot prove the newly requested city, and any strong login form or challenge still stops before collection.
- Extend the isolated Linux/Xvfb release gate with the production Collector path for two target cities. The gate proves stale-city rejection, official city discovery, authenticated-homepage redirect recovery, visible city selection, readable-query continuity, target-city-only candidates and no second-page probe without an account, user data or recruiting action.

### Compatibility

- Existing API Keys, account binding, profile, managed Chrome profile, login session, active round, completed audits and the preserved uncharged Zhilian Discover request remain unchanged. Managed clients resume the same `jobagent zhilian discover` command after upgrade; no new round, relogin, recollection or additional charge is required.

## [0.5.33] - 2026-08-23

### Fixed

- Complete Zhilian stale-city recovery through the platform's current official public flow: discover the exact readable target city from the city directory, independently verify its city homepage, then submit the original readable query and adopt only a result route whose city and query both match.
- Clear stale result-page evidence before probing a newly opened target-city page, so an older Shenzhen route cannot keep a Zhengzhou, Hangzhou, or other signed city request trapped in bounded navigation recovery.
- Keep login classification fail-closed while allowing the read-only city-directory bootstrap needed to diagnose and recover a stale official result page. A real login form or challenge still stops before search or candidate collection.
- Add an isolated Linux/Xvfb headed Chrome release gate that proves two consecutive real public-page city transitions, readable-query continuity, result-page readiness, candidate-surface detection, and no blind second-page navigation without using an account or performing a recruiting action. If the public page presents an explicit login wall, the report keeps candidate reviewability as a named unverified remainder instead of treating it as production evidence.

### Compatibility

- Existing API Keys, account binding, profile, managed Chrome profile, login session, active round, completed audits and preserved uncharged Zhilian Discover request remain unchanged. Managed clients resume the same `jobagent zhilian discover` command after upgrade; no new round, relogin, recollection or additional charge is required.

## [0.5.32] - 2026-08-22

### Fixed

- Require both the original readable query and the requested readable city before a Zhilian search transition can enter candidate collection. A result route that still belongs to a previously selected city is no longer treated as ready.
- Discover an exact official readable target-city route even when the stale result URL already contains another numeric city code. The client verifies the target-city bootstrap page, resubmits the original readable query, and verifies the resulting city and query before collecting jobs.
- Keep dynamic numeric city caching behind the existing multi-source evidence contract. Missing or conflicting target-city evidence remains fail-closed, returns no stale-city candidates, preserves the request and adds no charge.

### Compatibility

- Existing API Keys, account binding, managed Chrome profile, login session, active round, completed platform audits and preserved Zhilian Discover request remain unchanged. Resume the same `jobagent zhilian discover` command after the managed update; no new round, login reset, recollection or additional charge is required.

## [0.5.31] - 2026-08-22

### Fixed

- Normalize Zhilian account navigation, profile presence, resume management and historical application activity before classifying session state. An authenticated page that retains a generic login/register entry is no longer rejected solely by that weak control.
- Keep real login routes, visible credential forms and login challenges fail-closed. Conflicting strong login and strong account evidence remains `zhilian_session_state_unknown` instead of being treated as authenticated.
- Return one consistent normalized `sessionState`, `sessionReason` and evidence decision from login checks and Discover snapshots, without returning account names or page content as new evidence fields.

### Compatibility

- Existing API Keys, account binding, managed Chrome profile, login session, active round, completed Boss/Liepin audits and current Zhilian stage remain unchanged. No migration, repeated Discover or additional charge is required.

## [0.5.30] - 2026-08-22

### Fixed

- Send authorized Liepin jobs directly from each reviewed signed job-detail URL instead of rebuilding a city search from the first job. Route-only cities such as Zhengzhou or Hangzhou no longer require a numeric city code, and an older tab showing another city cannot influence the authorized batch.
- Verify that the browser reached the exact signed Liepin detail route before any resume or greeting action. Preserve the existing login handoff only for an observed login wall; a city or detail navigation failure now remains an accurate machine-readable delivery error.
- Preserve the original delivery preview, authorization, round and audit on pre-action failures. The recovery path adds zero credits and does not repeat Discover, review or user confirmation.

### Compatibility

- Existing API Keys, account binding, profile, managed Chrome profile, Liepin login state, active round, signed decision, review file, delivery preview, authorization and audit remain unchanged. Managed clients resume the original authorized send command after upgrade; no state migration or new charge is required.

## [0.5.29] - 2026-08-22

### Fixed

- Resolve unbundled Liepin cities from official city links already present on a trusted result page, with a bounded official search-results fallback when the managed session is currently on the candidate homepage and the legacy city-directory URL redirects there.
- Continue on an official readable city-slug search route when no numeric code is exposed. Require a real route transition plus matching page metadata/title, visible search input/URL query and real result or explicit no-result evidence before candidate extraction.
- Keep every signed page on the verified readable route. Reject city-homepage recommendations and stale cross-city controls, and cache a later numeric city code only after the original numeric evidence contract independently verifies it.
- Return redacted route-verification and directory-redirect diagnostics without treating a candidate-home redirect as logout or exposing account/session data.

### Compatibility

- Existing API Keys, account binding, managed Chrome profile, active round, Liepin login receipt, preserved SearchPlan request, profile and audits remain unchanged. Resume with `jobagent liepin discover`; the same preserved request is reused and the prior browser collection remains uncharged. No state migration, new round, login reset or profile replacement is required.

## [0.5.28] - 2026-08-21

### Fixed

- Discover unbundled Liepin cities from the platform's official city directory instead of requiring a bundled city-code entry. The same flow covers cities such as Zhengzhou and Hangzhou without adding one-off mappings.
- Verify the requested city, readable keyword and real result state across the city control, page metadata, title, search input and result cards before accepting a candidate or caching city metadata.
- Reject stale or conflicting city pages, including a previously open Shenzhen page when the signed query requests another city. An unresolved city remains fail-closed and does not return cross-city candidates.
- Persist only cross-verified city metadata. A stale cached or bundled candidate is revalidated against the live result page and removed when it no longer agrees.
- Return redacted city-resolution diagnostics without exposing account, profile, page content or query data.

### Compatibility

- Existing API Keys, account binding, managed Chrome profile, active round, login receipt, preserved SearchPlan request and audits remain in place. Resume with `jobagent liepin discover`; the same request ID is reused and the failed browser collection adds no charge. Do not create another round, clear `~/.jobagent` or replace the Chrome profile.

## [0.5.27] - 2026-08-21

### Fixed

- Require at least one explicit target city before paid resume analysis or round creation, and provide a structured city-input interaction when an older profile has no city.
- Preserve explicitly supplied target cities in the saved profile even when cloud analysis omits them, with normalized ordering, deduplication and a five-city bound.
- Rebind an active round to an explicitly refreshed profile only before candidate discovery has produced signed or delivery evidence. The original round and recent platform login receipt remain available, while stale failed-start context is replaced without charging for the failed browser collection.
- Block profile replacement after candidate discovery, preview, authorization or delivery evidence exists so results from one profile cannot be delivered under another.

### Compatibility

- The `0.5.26 -> 0.5.27` migration repairs the specific pre-delivery state where a user added cities after an empty-city Discover failure. It preserves the account, API Key, managed Chrome profile, recent login receipt, round ID, profile and audits; clears only the uncharged stale Discover-start context; and resumes from the current platform's Discover command. Progressed rounds fail closed and remain unchanged.

## [0.5.26] - 2026-08-15

### Fixed

- Bound 51Job clicked-but-unverified reconciliation to two read-only attempts or 24 hours. When evidence still cannot converge, preserve the item as terminal `delivery_unresolved` instead of returning the same send command forever.
- Never reopen or click a delivered, unavailable or terminal-unresolved 51Job item. Older v0.5.25 append-only audit records are migrated in place without losing click evidence, the original round, preview or authorization.
- Build send and audit summaries from each reviewed job's cumulative terminal outcome instead of the latest command run. A later recovery now retains prior delivered jobs and reports unavailable, pending and unresolved outcomes separately.
- Complete the send stage and continue to audit when every reviewed job has a terminal outcome, including unresolved items. Unresolved is never counted as delivered or failed, and no additional Discover or cloud charge is created.

### Compatibility

- Existing API Keys, account binding, managed Chrome profile, round, paid Discover, signed decision, preview, authorization and audit log remain in place. Follow the installed client's exact `next_suggested`: retry only while `retryable=true`; when `completion_state=completed_with_unresolved`, continue directly to `jobagent 51job audit`.

## [0.5.25] - 2026-08-15

### Fixed

- Verify 51Job resume submission from the current `we.51job.com` surface using an explicit success notice or a stable, exact-card apply-button transition instead of depending on the legacy application-history domain, whose login session may be separate.
- Recover older clicked-but-unverified audit records as an idempotent `delivery_indeterminate` state. A later missing card no longer erases the observed click, and recovery inspects before deciding without clicking that job again.
- Continue the authorized batch after one uncertain item while reporting delivered, unavailable and indeterminate jobs separately; never convert an observed click into `job_unavailable` or claim an uncertain submission as delivered.
- Preserve the original delivery preview, authorization, round and Discover result during reconciliation. The exact returned recovery command adds no cloud charge and does not recollect candidates.

### Compatibility

- Existing API Keys, account binding, managed Chrome profile, round, paid Discover, signed decision, preview, authorization and audits remain in place. Follow the exact top-level `next_suggested` command after upgrade; do not start another round, repeat Discover, delete `~/.jobagent` or re-click a pending 51Job item.

## [0.5.24] - 2026-08-15

### Fixed

- Wait for slow Zhilian detail pages to finish loading, and do not treat a generic navigation login entry as a logged-out session.
- Recover job title and company from trusted official detail-page sources, and normalize explicit negotiable or undisclosed salary labels without inventing compensation.
- Keep one irreparable candidate from blocking an already paid batch: safely exclude only that signed candidate, preserve repaired candidates, and regenerate the remaining preview with zero additional credits.

### Compatibility

- Existing API Keys, account binding, managed Chrome profile, round, session, Discover ID, signed candidate identity and audits remain in place. Resume with `jobagent zhilian apply review`; do not start another round or Discover. The repaired decision still stops at the final delivery confirmation gate.

## [0.5.23] - 2026-08-15

### Fixed

- Reject generic Zhilian link labels such as `查看更多信息`, `查看详情` and `更多` as job titles, and prefer stable title nodes from the same result card.
- Extract company and salary from the same result card when available, then use bounded read-only detail-page hydration for missing review fields.
- Keep Zhilian candidates without a reviewable title, company and salary out of the selected delivery preview even when their semantic match score is high.
- Repair an already paid, preserved Zhilian decision in place: read only its signed detail URLs, re-sign the same Discover with zero additional credits, and regenerate the full delivery preview before asking for confirmation.

### Compatibility

- Existing API Keys, account binding, managed Chrome profile, round, session, Discover ID, signed candidate identity and audits remain in place. The upgrade clears only the stale Zhilian preview and its unanswered interaction. Resume with `jobagent zhilian apply review`; do not start another round or Discover, and no additional Discover charge is created.

## [0.5.22] - 2026-08-15

### Fixed

- Treat cross-city fallback cards on an independently verified Zhilian query and city route as that query's safe empty result instead of a city-evidence conflict.
- Keep rejecting every cross-city fallback card while preserving valid candidates already collected by earlier signed SearchPlan queries.
- Keep visible selected-city conflicts and route/control code conflicts fail-closed, and stop the safe empty query without probing another page.
- Keep job-card text scoped to its explicit card surface so page-level city labels cannot contaminate candidate-city parsing.

### Compatibility

- Existing API Keys, account binding, managed Chrome profile, round, preserved `request_id`, signed SearchPlan, Discover ID, candidates and audits remain in place. Resume with `jobagent zhilian discover`; the exact preserved request continues without another Discover charge.

## [0.5.21] - 2026-08-14

### Fixed

- Finish a verified Zhilian query after its first result page when the page exposes no pagination control, so an upper page limit never causes an unsupported page-two probe.
- Parse current Zhilian result cards from stable card metadata even when the logged-in page omits a directly usable detail anchor, while retaining the adopted search target and rejecting unverified recommendation surfaces.
- Report missing next-page controls, unaccepted keywords and loaded result pages with unparsed cards as separate machine-readable failures instead of reusing the keyword-rejection message.
- Emit redacted query index, query count, page and job-detail progress during long browser collection without exposing keywords, routes, account data or page content.
- Distinguish a satisfied page budget from a truly exhausted site pagination boundary or an explicit empty result, so a one-page request completes without probing page two even when more pages exist.

### Compatibility

- Existing API Keys, account binding, managed Chrome profile, round, preserved `request_id`, signed SearchPlan, Discover ID, candidates and audits remain in place. Resume with `jobagent zhilian discover`; the exact preserved request continues without another Discover charge.

## [0.5.20] - 2026-08-14

### Fixed

- Activate Zhilian's current official search anchor with a bounded native-pointer, DOM-click and official-destination fallback sequence, verifying a real route or result-state transition after every attempt.
- Capture and adopt the single verified Zhilian search target when the site opens results in a new tab, persist it as the platform target, and close only the superseded page.
- Keep same-tab navigation supported while refusing ambiguous or non-official targets and destinations.
- Return a redacted search-action receipt with control, target and transition evidence without exposing cookies, account content, page text or opaque route tokens.

### Compatibility

- Existing API Keys, account binding, managed Chrome profile, round, preserved `request_id`, signed SearchPlan, Discover ID, candidates and audits remain in place. Resume with `jobagent zhilian discover`; the same preserved request continues without another Discover charge.

## [0.5.19] - 2026-08-14

### Fixed

- Verify Zhilian's controlled search input and try the visible search button, input Enter and form submission once each, stopping as soon as a real route, history, navigation or result-state change is observed.
- Return stable `zhilian_search_input_not_committed`, `zhilian_search_submit_control_not_activated` and `zhilian_search_transition_not_observed` failures when the live page accepts no verifiable search action.
- Include a redacted action receipt with control types, readable input value, attempted submit method and before/after state without exposing cookies, account content or page text.
- Keep search-navigation recovery separate from city-evidence recovery, so a stalled search can no longer be rewritten as a city-resolution failure or repeat forever.

### Compatibility

- Existing API Keys, account binding, managed Chrome profile, round, preserved `request_id`, signed SearchPlan, Discover ID, candidates and audits remain in place. Resume with `jobagent zhilian discover`; no cache clearing, repeated login, replacement round or duplicate Discover charge is required.

## [0.5.15] - 2026-08-14

### Fixed

- Treat each signed Zhilian `page_limit` as an upper bound: explicit no-results or independently verified final-page evidence now ends that query without requesting a nonexistent next page.
- Continue later signed SearchPlan queries when an earlier query is empty, while preserving the human-readable query and never interpreting Zhilian's opaque `kw...` route state as a job keyword.
- Correct the generated no-results matcher and expose bounded pagination evidence from the live page to the SearchPlan scheduler.
- Return a terminal, no-charge `no_candidates` outcome after every signed query is exhausted, and stop non-retryable page failures from suggesting the same Discover command forever.

### Compatibility

- Existing API Keys, account binding, managed Chrome profile, round, preserved `request_id`, signed SearchPlan, Discover ID, candidates and audits remain in place. Resume with `jobagent zhilian discover`; the preserved request is reused and no additional Discover charge is created.

## [0.5.14] - 2026-08-14

### Fixed

- Actively discover a requested Zhilian city's current opaque identifier from visible city controls and their real navigation metadata when the results page remains on the public entry route.
- Require the discovered identifier to agree with independent readable title, selected-city or job-card evidence before caching or returning candidates; URL or numeric metadata alone remains insufficient.
- Bound city-evidence recovery to the exact preserved request. After three non-converging attempts, return a read-only browser diagnostic action instead of repeating the same Discover command forever.

### Compatibility

- Existing API Keys, account binding, profile, round, preserved request and Discover IDs, managed Chrome profile, city cache and audits remain in place. Resume the same request with `jobagent zhilian discover`; no cache clearing, replacement round, repeated login or duplicate Discover charge is required.

## [0.5.13] - 2026-08-14

### Fixed

- Wait for Zhilian's post-search navigation to reach a verified results page before resolving the requested city, including pages that take more than 50 seconds to finish loading.
- Discover candidate city codes from the current visible city controls while requiring independent URL, page-title, selected-city or job-card evidence before a changed mapping is cached.
- Distinguish incomplete city evidence, changed city-control structure and genuine city conflicts with stable machine-readable errors and safe diagnostics.
- Keep weak login controls non-authoritative during a slow page transition; authentication routes and visible credential challenges still stop the workflow immediately.

### Compatibility

- Existing API Keys, account binding, profiles, rounds, preserved request and Discover IDs, managed Chrome profile, city cache and audits remain in place. Resume with the original `jobagent zhilian discover`; no cache clearing, replacement round, repeated login or duplicate Discover charge is required.

## [0.5.12] - 2026-08-13

### Fixed

- Carry a short-lived Zhilian login verification across the same round and managed Chrome session when the search-results page omits the account header.
- Keep visible credential forms and login challenges authoritative, so previous login evidence cannot bypass a real sign-in requirement.
- Recognize additional current Zhilian job-card surfaces and return `zhilian_job_cards_not_found` with safe selector diagnostics when a fully loaded search page cannot be parsed.
- Preserve the bound login receipt when a browser collection attempt fails, allowing the exact saved Discover request to resume without another login or charge.

### Compatibility

- Existing API Keys, account binding, profiles, rounds, preserved request and Discover IDs, browser profiles, city cache and audits remain in place. No cache clearing, replacement round, repeated login or duplicate Discover charge is required.

## [0.5.11] - 2026-08-13

### Fixed

- Renew an expired, validly signed SearchPlan against the same preserved request and Discover IDs instead of returning a bare protocol error.
- Reuse already collected candidates and the original billing idempotency relationship after renewal; renewal itself has zero charge.
- Return complete machine-readable recovery fields when renewal is temporarily unavailable, while signature, account and context mismatches remain non-recoverable.

### Compatibility

- Existing API Keys, account binding, profiles, rounds, pending candidates, browser sessions and audits are preserved. No cache clearing, repeated login, replacement round, recollection or duplicate Discover charge is required.

## [0.5.6] - 2026-08-04

### Fixed

- Use Liepin's current Guangzhou city code instead of sending raw city text that produces an empty result page.
- Resolve unbundled Liepin city codes from the platform's own page metadata before collecting jobs.
- Stop with an explicit city-resolution error when a requested Liepin city cannot be verified, instead of misreporting a valid search as `no_candidates`.

### Compatibility

- Existing API Keys, profiles, rounds, signed decisions, delivery previews, browser sessions and audits are preserved. No cache clearing, state migration, repeated login or additional Discover charge is required.

## [0.5.5] - 2026-07-29

### Fixed

- Recognize the official Zhilian account home as authenticated during read-only browser diagnosis, avoiding inconclusive login guidance after a successful sign-in.
- Normalize official Zhilian job links from HTTP or protocol-relative forms to HTTPS before Discover results are saved or delivered.
- Use process-specific temporary files for client-upgrade state so simultaneous CLI starts cannot overwrite each other's atomic write.

### Compatibility

- Existing API Keys, profiles, rounds, signed decisions, delivery previews, browser sessions and audits are preserved. No cache clearing, state migration or additional Discover charge is required.

## [0.5.4] - 2026-07-28

### Fixed

- Recognize the current Boss greeting confirmation dialog instead of waiting for an editor on the covered job page.
- Preserve the job-bound, same-origin Boss chat destination before the first click and continue to the correct conversation after the platform sends its default introduction.
- Keep the signed chat query out of delivery results and audits while still requiring the reviewed personalized greeting itself to be visible with delivery evidence.

### Compatibility

- Existing API Keys, profiles, rounds, signed decisions, delivery previews, browser sessions and audits are preserved. No cache clearing, state migration or new Discover charge is required.

## [0.5.3] - 2026-07-27

### Added

- Show the complete pending-delivery list before any real action on Boss, Liepin, Zhilian or 51Job.
- Bind each send to the exact displayed platform, Discover result and ordered candidate list with a short-lived `preview_id`.

### Compatibility

- Existing review files can regenerate a bound preview without recollecting jobs or charging another Discover. Empty delivery lists close safely with zero platform actions.

## [0.5.2] - 2026-07-27

### Fixed

- Canonical release archive verification now fixes Git archive permissions and ignores machine-level Git configuration, global attributes and replacement objects.
- Official macOS/Linux and Windows installers configure the managed checkout with the same canonical archive permissions.
- A genuine archive verification failure now returns machine-readable official-installer recovery commands while preserving account state, browser sessions, profiles, rounds and audits.

## [0.5.1] - 2026-07-27

### Fixed

- Target-role confirmation now exposes three complete choices that map directly to native host cards: accept suggestions, append roles, or replace suggestions.
- Add `jobagent interaction respond` as the single continuation path for card and text answers, including a follow-up role-input interaction for append/replace choices.
- Include ready-to-call Codex `request_user_input` arguments and a stable display-label-to-option-ID map when the interaction can use a native choice card.
- Bind pending interactions to the current account and resume profile, reject stale or conflicting answers, and make repeated accepted responses idempotent.
- Host instructions now distinguish a host's card capability from whether the current surface or mode exposes the card interface.

### Compatibility

- Existing direct `round start --accept-suggested` and `round start --target-role` commands remain valid when the user's intent is already explicit.
- Existing rounds, profiles, browser sessions, decisions and audits are preserved. The new pending-interaction file is account-bound and removed after a successful response.

## [0.5.0] - 2026-07-27

### Added

- Add AgentMesh360 `interaction_required` v1 target-role confirmation before creating a new round.
- Persist confirmed target roles in the round and bind them to signed cloud search plans and decisions.

## [0.4.6] - 2026-07-24

### Fixed

- Zhilian discovery now enters the readable role keyword through the visible search form instead of rebuilding a legacy query URL.
- Treat Zhilian `kw...` route segments as opaque platform state and verify the readable keyword from the visible search input before collecting candidates.
- Dismiss and report a platform keyword alert as a no-candidate, no-charge failure instead of allowing the host Agent to misdiagnose the route token or skip the platform.
- Reject signed search plans whose keyword resembles an opaque platform identifier while preserving normal Chinese and English role titles.

### Compatibility

- Existing API Keys, profiles, rounds, browser login state, signed decisions and audits are preserved. No local state migration or cache clearing is required.

## [0.4.5] - 2026-07-22

### Fixed

- Retry idempotent Discover start/decision requests after bounded transient TLS, connection and gateway failures.
- Preserve a pending signed Discover and its local candidate set so the next identical platform command resumes cloud decision without reopening the recruiting platform or recollecting jobs.
- Return machine-readable `retryable`, `attempts`, `request_preserved` and `next_suggested` recovery fields for Agents.

### Compatibility

- Existing credentials, browser login state, profiles, rounds, decisions and audits are preserved. The additive account-bound pending file is created only after a new Discover collects candidates and is removed after a verified decision.

## [0.4.4] - 2026-07-17

### Fixed

- Synchronize Claude Code and OpenClaw Skill frontmatter versions with the CLI release.
- Add a release-contract test that prevents Skill metadata from drifting behind the Python package version.

## [0.4.3] - 2026-07-17

### Changed

- New AgentMesh360 accounts now start with zero cloud credits. Public Agent instructions no longer promise a signup grant and direct users to the monthly pass only after `doctor env` reports `insufficient_credits`.
- Registration, universal API Key creation, and the open-source client remain free; AgentMesh360 cloud capabilities use shared credits.

### Compatibility

- Previously issued signup-trial credits remain usable until their original expiry. When Core returns `signup_trial_active`, the Agent reports the returned balance and expiry and continues without asking the user to buy a pass.

## [0.4.2] - 2026-07-17

### Added

- Managed clients now emit machine-readable `client_update_detected`, `client_update_started`, `client_update_completed`, and `client_command_resumed` events while applying a signed release and continuing the original command.
- The first upgrade from a client that predates these events emits a compatibility completion/resume receipt after the new client starts.

### Changed

- Agent guides now require one brief update report, automatic continuation after success, and user intervention only when `client_update_failed` is returned.

## [0.4.1] - 2026-07-17

### Fixed

- The macOS/Linux and Windows installers now start the post-install workflow with `jobagent round start`, followed by the current platform's login check. They no longer direct a new user to run Boss Discover before a round exists.

## [0.4.0] - 2026-07-17

### Changed

- Local profiles, rounds, decisions and audits are now bound to a stable opaque AgentMesh account reference. Legacy state requires one explicit ownership confirmation; switching accounts preserves and restores separate account-owned state.
- `jobagent round start` is the only operation that creates a round. Doctor and status commands report existing state without silently starting work.
- Doctor output now separates environment health, cloud access, local account state and workflow readiness, with one authoritative top-level `next_suggested`.
- Platform and round audits return compact summaries by default. `--failures-only` and `--details` expose bounded records when requested.
- Boss and Liepin enforce a signed personalized-message contract before preview or delivery. Both accept at most 100 characters; Zhilian and 51Job explicitly remain resume-submit-only.

### Added

- Timestamped progress stages and periodic heartbeats for long Discover and delivery operations.
- `jobagent round audit` for one compact four-platform result.
- `jobagent browser diagnose --platform <platform>` for read-only CDP, tab, page-readiness and login-evidence diagnostics.
- Zhilian city-code discovery from visible platform controls, multi-signal verification and a local verified cache, with fail-closed/no-charge behavior when the requested city cannot be proven.

### Upgrade Notes

- Existing pre-`0.4.0` state is not claimed automatically. When it belongs to the configured account, run `jobagent account bind --confirm-legacy` once.
- When a new API Key belongs to another account, run `jobagent account switch --new-state`; do not delete `~/.jobagent` or the dedicated Chrome profile.

## [0.3.18] - 2026-07-17

### Fixed

- Zhilian Shenzhen searches now use the verified public `jl=489` route instead of the fragile visible-filter fallback.
- The post-release customer-validation fix is now included in the signed managed-client release.

## [0.3.17] - 2026-07-16

### Fixed

- `jobagent doctor env` now exposes a machine-readable cloud-access decision, remaining credits, entitlement source, expiry and the next runnable command.
- An active signup trial is treated as immediately usable without a paid pass; Agent guides must report the trial and continue instead of asking the user to purchase.
- The public onboarding, Claude Code and OpenClaw assets now reserve the purchase prompt for a real `insufficient_credits` result.

## [0.3.16] - 2026-07-13

### Fixed

- Boss Discover distinguishes environment rejection from login or verification requirements and uses a bounded visible-page recovery for `code=37`.

## [0.3.15] - 2026-07-13

### Fixed

- Hardened upgrades from older clients, preserved active round state and improved cross-environment platform delivery diagnostics.

## [0.3.14] - 2026-07-12

### Fixed

- Removed outdated per-platform confirmation wording from the public README and Claude Code distribution guide. Starting a round authorizes automatic delivery of signed `selected` jobs; `review` still requires explicit promotion and `rejected` is never sent.

## [0.3.13] - 2026-07-12

### Fixed

- 51Job retries a missing search-result card by searching for the reviewed job title while preserving the original area filter.
- 51Job requests resume intervention only when a visible resume-selection dialog is present, avoiding false positives from unrelated page text.

## [0.3.12] - 2026-07-11

### Fixed

- Liepin resume delivery now completes the live two-stage flow: open `发简历`, verify the default attachment selection, then click `立即投递`.
- Liepin uses trusted browser mouse events for the React resume controls and verifies the resulting resume message/card before reporting delivery.
- A delivered Liepin resume no longer also reports a stale resume-selection user action.

## [0.3.11] - 2026-07-11

### Changed

- Liepin selected jobs now require a signed personalized greeting in addition to the account resume.
- Liepin delivery is complete only after the active chat verifies both the resume card/message and the exact signed greeting.
- Resume-only audit history skips only the resume action; it no longer suppresses a missing personalized greeting.

### Fixed

- Liepin uses its live chat textarea and send button to deliver the personalized greeting after resume delivery.
- Liepin delivery history uses canonical job URLs so changing search query parameters cannot cause duplicate composite delivery.

## [0.3.10] - 2026-07-11

### Fixed

- Liepin chat-only jobs now recognize and click the live `.action-resume` container used by the `发简历` action.

## [0.3.9] - 2026-07-11

### Fixed

- Active rounds created by older clients automatically remove retired send-confirmation flags from persisted `next_suggested` commands.

## [0.3.8] - 2026-07-11

### Changed

- Starting a four-platform round now authorizes automatic delivery of every signed `selected` job; Agents no longer interrupt the user for per-platform send confirmation.
- Send commands no longer accept `--confirm-send` or `--confirm-submit`.
- Round status publishes a machine-readable delivery policy for `selected`, `review`, and `rejected` jobs.

### Fixed

- Liepin no longer treats a platform-owned default chat message or unread marker as resume delivery.
- For Liepin jobs that expose only a chat entry, the client continues to the explicit `发简历` action and requires resume-specific success evidence.
- Liepin's “请登录猎聘 APP 查看消息” prompt is no longer misreported as a logged-out web session.

## [0.3.7] - 2026-07-11

### Fixed

- Liepin uses the current Shenzhen city code instead of the former nationwide code.
- Liepin card locations such as `深圳-南山区` are split into city and district fields before city filtering.

## [0.3.6] - 2026-07-11

### Added

- `jobagent round status` persists the Boss -> Liepin -> Zhilian -> 51Job workflow and returns one machine-readable next action.
- `jobagent round skip --platform <platform> --confirm-skip` records an explicit, round-local user decision.

### Changed

- Platform commands now return `workflow.continue_required`, `workflow.workflow_complete`, remaining platforms and `next_suggested`.
- The CLI rejects out-of-order platform browser actions before opening a page.
- A confirmed send covers the complete reviewed list by default, up to 100 jobs.

### Fixed

- Browser startup and navigation failures are no longer misreported as login-required user actions.
- The official workflow no longer silently switches from the dedicated Job Agent browser to another Chrome profile.

## [0.3.5] - 2026-07-11

### Fixed

- Boss no longer treats the platform's automatic default introduction as delivery of the reviewed personalized greeting.
- After the default introduction establishes a conversation, the send flow continues into the editor and verifies the exact reviewed greeting.

## [0.3.4] - 2026-07-11

### Fixed

- Boss review now excludes jobs with verified delivery in the local audit log.
- Boss send rechecks the same audit history so stale or edited review files cannot trigger duplicate outreach.

### Changed

- Agent distribution assets now describe the current unlimited, 0-credit free-open policy.

## [0.3.3] - 2026-07-11

### Fixed

- `jobagent update check` now bypasses the local manifest cache and immediately verifies the latest signed release policy.
- Automatic checks use a five-minute cache instead of delaying release discovery for up to six hours.

## [0.3.2] - 2026-07-11

### Fixed

- Boss Discover now reads rendered search-result cards instead of relying on a direct search request that may be rejected by the upstream page.
- Boss salary glyphs are decoded to complete `0-9` values before cloud classification.
- Boss login and visible security-verification states now return the required user-intervention prompt instead of a misleading empty result.

### Changed

- Current free-open accounts are reported as unlimited and completed Discover calls deduct 0 credits. The signed cloud response remains authoritative for future policy changes.

## [0.3.1] - 2026-07-11

### Fixed

- Official installers now clone the public `AgentMesh-JobAgent` repository.
- Installer guidance consistently uses AgentMesh API Key and current platform commands.

## [0.3.0] - 2026-07-11

### Added

- One `discover` workflow for Boss Zhipin, Liepin, Zhilian, and 51Job.
- Signed cloud search plans and complete `selected`, `review`, and `rejected` decisions.
- Explicit review and confirmation gates before any greeting or resume submission.
- Boss greeting delivery and resume submission flows for Liepin, Zhilian, and 51Job.
- Signed release checks and guarded automatic updates for official managed installs.

### Changed

- `jobagent <platform> discover` is the only supported job discovery entry point.
- A completed platform Discover evaluates up to 100 jobs and consumes 10 credits.
- `API Key` is the single public credential term.

### Removed

- The former multi-step job processing command surface.
- Legacy client behavior and compatibility commands.

[0.3.14]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.3.14
[0.3.13]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.3.13
[0.3.12]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.3.12
[0.3.11]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.3.11
[0.3.10]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.3.10
[0.3.9]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.3.9
[0.3.8]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.3.8
[0.3.7]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.3.7
[0.3.6]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.3.6
[0.3.5]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.3.5
[0.3.4]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.3.4
[0.3.3]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.3.3
[0.3.2]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.3.2
[0.3.1]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.3.1
[0.3.0]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.3.0
