# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed — `boss greet send` pipeline (PR #1)

The `jobagent boss greet send` command was failing 100% on real Boss直聘
traffic. Seven root causes were identified and fixed; all are verified
end-to-end against live HR inboxes (5/5 delivered on a real batch).

#### 1. `loginDialog` false positive blocked every send
- **Symptom**: every page reported `loginDialog: true`, aborting send as
  `login_required` even when no popup was visible.
- **Cause**: `inspect_page` / `inspect_chat_editor` checked for element
  existence via `querySelector`, not visibility. Boss keeps hidden
  `.sign-content` templates in the DOM (e.g., preloaded for the login
  flow), so the selector always matched.
- **Fix**: selectors now require `getComputedStyle` (display/visibility/
  opacity) AND `getBoundingClientRect` (width > 10, height > 10).

#### 2. Synthetic mouse clicks silently dropped by Boss anti-automation
- **Symptom**: clicking 立即沟通 did nothing; send fell through to
  `chat_editor_not_found`.
- **Cause**: `click_chat_entry` used `dispatchEvent(new MouseEvent(...))`
  which produces `isTrusted=false` events. Boss's anti-automation silently
  ignores non-trusted clicks.
- **Fix**: new `_cdp_click_at(x, y)` helper uses
  `Input.dispatchMouseEvent` (CDP-native, `isTrusted=true`). All
  chat-entry clicks (立即沟通 / 继续沟通 / fallback) go through this.

#### 3. Wrong element picked when multiple text matches exist
- **Symptom**: occasionally clicked recommendation-sidebar badges
  instead of the main button.
- **Cause**: `_find_clickable_by_text` picked smallest-area match,
  which selected inner spans or sidebar "继续沟通" links.
- **Fix**: `click_chat_entry` now prefers Boss's canonical `.btn-startchat`
  class via direct selector. Text fallback picks **largest** area.

#### 4. Single editor selector missed Boss DOM variations
- **Symptom**: editor not found even when the chat sidebar was open.
- **Cause**: only `.chat-input` was tried. Boss's editor class varies
  across versions.
- **Fix**: tries `.chat-input` / `.edit-input` / `.message-input` /
  `[contenteditable="true"]` / `[contenteditable]` in order.

#### 5. `send_flow` abandoned established conversations on stale selector
- **Symptom**: chat opened, conversation established server-side, but
  send aborted as `chat_editor_not_found` and the caller closed the tab.
- **Cause**: `send_flow.py` short-circuited on `editorFound=false`.
- **Fix**: if `chatContainer` OR `sendFound` is true, fall through to
  `fill_chat_message` (which has its own editor-finding fallback).

#### 6. `verify_delivery` too strict for freshly-sent messages
- **Symptom**: greetings stuck in `delivery_not_verified` limbo.
- **Cause**: required an explicit `送达` / `已读` marker near the message.
  Boss only shows these after the recipient reads — never for fresh sends.
- **Fix**: `delivered = hasMsg && !stillInEditor` (message text in chat
  transcript AND editor cleared). 送达/已读 markers remain as bonus signal.

#### 7. CDP stalls under rapid sequential operations
- **Symptom**: occasional `CDP timeout: Input.dispatchMouseEvent`.
- **Cause**: under rapid sequential operations Chrome's CDP channel can
  briefly stall past the 30s timeout, even though the browser is healthy.
- **Fix**: `_cdp_click_at` retries 3× with exponential backoff (2s / 4s / 6s).

#### 8. Modal dialog DOM differed from sidebar DOM (stress test discovery)
- **Symptom**: stress test on fresh 立即沟通 clicks (杭州 数据产品负责人) —
  all 5 send attempts failed as `chat_editor_not_found` even though the
  sidebar was visually open.
- **Cause**: Boss has TWO distinct chat UIs:
  - **Sidebar** (existing conversation): `.chat-input` contenteditable +
    `<button>发送</button>` — this is what the original code targeted.
  - **Modal dialog** (fresh 立即沟通 first click): `<textarea.input-area>`
    + `<div.send-message>` inside `.dialog-wrap.startchat-dialog`.
  The original selectors only matched the sidebar variant.
- **Fix**: `inspect_chat_editor` / `click_chat_entry` polling now try both
  selector sets (sidebar + modal). `chatContainer` detection expanded to
  include `.startchat-dialog`.

#### 9. fill_chat_message / click_send needed textarea-aware paths
- **Symptom**: even with the modal dialog detected, fill+send logic was
  hardcoded for contenteditable.
- **Cause**: `fill_chat_message` used Range API + `Input.insertText`
  (contenteditable-only); `click_send` used `<button>` lookups and Enter
  key (Enter inserts newline in `<textarea>`, doesn't submit).
- **Fix**:
  - `fill_chat_message`: detects editor type; for `<textarea>` sets `.value`
    via JS + dispatches `input`/`change` events (React/Vue-compatible).
  - `click_send`: detects editor type; for textarea mode skips Enter and
    uses real CDP mouse click on `.send-message` div via `_cdp_click_at`.
  - Send-button lookup now includes `.send-message` div (not just `<button>`).

### Changed

- `.gitignore`: ignore `boss.raw.json` / `boss.ranked.json` /
  `boss.ready.json` / `boss_greetings_*.md` — these runtime artifacts
  contain personal resume data and should never be committed.

### Tests

- `test_click_chat_entry_no_popup_is_not_auto_sent` → renamed to
  `test_click_chat_entry_no_sidebar_is_not_auto_sent`, updated for the
  new CDP-click flow:
  - FakeCDP now records `send()` calls in `sent_methods`, so tests can
    assert real `Input.dispatchMouseEvent` was emitted.
  - New evaluate sequence: risk-check + locate + 3 polling evaluates.

## [0.2.1] — 2026-06-15

### Changed

- `chore: bump cli version to 0.2.1`

## [0.1.1]

### Added

- `feat: publish liepin and zhilian beta workflows`
- `feat: make boss platform commands canonical`
- `feat: add ClawHub Job Agent skill`
- `feat: add voluntary first-delivery star prompt`

[Unreleased]: https://github.com/jiyangnan/AgentMesh-JobAgent/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.2.1
[0.1.1]: https://github.com/jiyangnan/AgentMesh-JobAgent/releases/tag/v0.1.1
