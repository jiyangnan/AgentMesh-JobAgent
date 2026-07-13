# Project Instructions for Codex

## What This Project Is

This is the public Job Agent CLI repository in the AgentMesh ecosystem. It contains the local client, installer scripts, public docs, and agent onboarding. Cloud AI features use the public AgentMesh360 API with a user-provided API Key.

## Start Here

1. `README.md` for public product scope and user setup.
2. `docs/agent-onboarding.md` for the canonical agent-driven workflow.
3. `skills/claude-code/SKILL.md` and `skills/openclaw-job-agent/SKILL.md` when updating skill distribution assets.
4. `pyproject.toml` for package metadata and CLI entry points.

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

- Follow the persisted workflow and `next_suggested`; never invent a parallel or batch-login workflow.
- Platforms run as complete vertical chains in this order: Boss -> Liepin -> Zhilian -> 51Job. Complete the current platform through audit before logging in to the next platform.
- Starting a job-search round authorizes automatic delivery of cloud-signed `selected` jobs. Do not request another confirmation before each platform or send command.
- `review` jobs require explicit user-selected IDs and `--confirm-promote`. Never auto-promote `rejected` jobs.
- Stop and relay the exact prompt whenever the CLI returns `requires_user_action=true`.
- Never delete `~/.jobagent` or the Job Agent Chrome profile as a general upgrade fix. Follow `client_upgrade_required`, `conflicts`, and `next_suggested`.

## Safety Rules

- Do not add internal-only strategy, progress, launch reports, admin runbooks, server operations, private prompts, infrastructure details, or secrets to this public repo.
- Use public-safe wording. Avoid exposing anti-abuse internals, platform evasion language, or private operational tactics.
- Do not weaken signed-decision verification, platform order, user-intervention prompts, review overrides, delays, audit logging, or privacy boundaries without a clear product decision.
- User resumes, cookies, local profiles, API Keys, and audit logs are sensitive.
- Real browser actions are serial. Never run shared Chrome sessions or shared audit/state writes concurrently.

## Current Focus

This repository should remain a clean public distribution surface: installable CLI, public docs, public skills, tests, and user-safe onboarding. Internal R&D decisions belong elsewhere.

## Done Means

- Public wording and links are safe for GitHub.
- `pytest` or a focused CLI smoke check was run, or the final answer explains why not.
- README, onboarding, Skills, and CLI output agree on platform order, automatic signed-selected delivery, review overrides, upgrade recovery, and user-intervention points.
- The final handoff lists changed files, verification, and whether any real platform/session data was touched.
