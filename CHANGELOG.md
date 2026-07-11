# Changelog

All notable public Job Agent client changes are documented here.

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
