# Changelog

All notable public Job Agent client changes are documented here.

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

[0.3.1]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.3.1
[0.3.0]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.3.0
