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

Liepin delivery opens the approved conversation before submitting a resume.
Follow the CLI's subsequent read-only conversation inspection: a platform default
greeting and an actual resume receipt are separate facts. Never submit a resume
again when the conversation already proves it was sent, and never count the
default greeting as the signed personalized message.

An older Liepin task may offer `continuation.kind=liepin_unattempted_prerequisite`.
Use its exact `work continue` template and receipt schema only when the preserved
observations explicitly establish that no external action was attempted and the
conversation prerequisite was missing. Unknown or attempted actions cannot use
this path. It preserves the list and authorization, records `not_attempted`, then
offers the missing step; it does not itself permit a browser action. Preserve and
replay an identical receipt after a lost response. Never reset the old nonce or
observation count, cancel delivery, or start a replacement round to unblock it.

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
  Discovery reads the bound resume's verified cloud material. A new installation
  with a valid workbench binding does not need a separate local resume analysis.
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
