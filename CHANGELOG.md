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

## [Unreleased — Liepin beta pipeline] — 2026-06-22

The Liepin vertical chain was returning 0 jobs on `liepin collect` and
silently mis-attribute cities. Six issues fixed, all verified end-to-end
with a real 2/2 delivered batch against 京东物流 HR.

### Fixed

#### 1. Wrong search URL path (0 jobs returned)
- **Symptom**: `liepin collect` always returned 0 jobs.
- **Cause**: `build_liepin_search_url` used `/zhaopin/` path. On logged-in
  sessions, Liepin serves an SEO landing page there showing "非常抱歉！暂时
  没有合适的职位". The real search endpoint is `/sojob/`.
- **Fix**: switched to `/sojob/` path with `city=` param (the older `dq=`
  param is silently dropped by Liepin's redirect).

#### 2. Parser overrode real city with requested city
- **Symptom**: every parsed Job had `city="北京"` even when the card text
  clearly said "上海-黄浦区" or "沈阳-浑南区".
- **Cause**: `parse_liepin_job` used `city_name or _first(raw, "city", ...)`
  — the user-requested city won over the card's actual city.
- **Fix**: reversed to `_first(raw, ...) or city_name`. Card's own city
  field wins; requested city is only a fallback.

#### 3. Snapshot selector rejected real `<a>` job cards as "missingIdentity"
- **Symptom**: 109 of 190 candidate elements rejected, only 8-10 cards
  parsed per page.
- **Cause**: selector looked for `el.querySelector('a[href*="/job/"]')`
  (descendant `<a>`). Liepin wraps job titles in `<a href="/job/XXX.shtml">`
  directly — the candidate IS the `<a>`, no descendant `<a>` exists.
- **Fix**: when the candidate element is itself an `<a>` with `/job/` href,
  accept it as the link.

#### 4. City filter not actually applied
- **Symptom**: `--city 北京` returned jobs from 沈阳, 上海, 乌鲁木齐.
- **Cause**: Liepin's sojob endpoint ignores URL city params server-side
  (all of `city=`, `cityCode=`, `dq=` return the same mixed-city results).
- **Fix**: added `_city_matches` post-filter in Python on the parsed
  `Job.city` field. To compensate for filtered-out cards, the snapshot
  fetch limit is doubled when a city filter is active.

#### 5. Pagination dedup treated same job on different pages as different
- **Symptom**: `--pages 3` returned 16 jobs where 8 were duplicates of
  the other 8.
- **Cause**: `_job_dedupe_key` used the full URL as key. Liepin decorates
  the same job's URL with different per-page query params (`d_posi=`,
  `skId=`, `ckId=`, `curPage=`, `index=`, …), so the same `/job/1983458373.shtml`
  had a different URL string on page 1 vs page 2.
- **Fix**: extract `/job/<id>.shtml` from the URL and use `job:<id>` as
  the dedup key. Falls back to full URL only if regex doesn't match.

#### 6. Snapshot city extraction missed non-whitelisted cities
- **Symptom**: jobs in 沈阳, 乌鲁木齐, 南京 (any city not in the hardcoded
  whitelist) were extracted with empty `cityName`, which then fell back
  to the user-requested city (Bug #2 above), making the city filter
  incorrectly include them.
- **Cause**: selector used `/北京|上海|深圳|.../.test(line)` — whitelist
  of ~20 cities.
- **Fix**: prefer the `【城市-区域】` bracketed pattern Liepin uses on
  every card (catches any Chinese city). Fall back to an expanded
  whitelist (added 沈阳, 乌鲁木齐, 济南, 哈尔滨, 长春, 昆明, 南宁, 福州,
  石家庄, 太原, 贵阳, 兰州, 海口, 南昌, 无锡, 温州, 珠海, 中山, 惠州).

### Documentation

- `LiepinApplySender` docstring now explicitly documents that:
  - Liepin has no Boss-style greeting chat flow.
  - The only contact mechanism is "立即投递" (submit resume).
  - Generated greetings are handoff-only (used by `liepin apply open`
    for human copy-paste), NOT auto-sent by `apply send`.
  - The `fill_liepin_message` step reporting `editor_not_found` on
    every healthy Liepin job page is **expected behavior, not a bug**.
- `_liepin_apply_click_entry_script` docstring documents why 立即投递
  variants come first in the label list.

### Tests

- Updated `test_liepin_search_url_encodes_query_and_city` for new URL
  contract (`/sojob/`, `city=`, `curPage=`).
- Updated `test_liepin_live_read_only_collector_*` and
  `test_liepin_session_check_reports_login_required` to assert the new
  `/sojob/` URL.

## [Unreleased — Zhilian beta pipeline] — 2026-06-22

The Zhilian vertical chain worked end-to-end out of the box (20 jobs →
5/5 delivered), but `--city` filter was silently ignored: 20 jobs came
back spread across 12 cities (only 4 in 北京). Two issues fixed.

### Fixed

#### 1. City filter not actually applied
- **Symptom**: `zhilian collect --city 北京` returned jobs from 上海/合肥/
  青岛/厦门/石家庄/重庆/保定/长春/福州/佛山/广州 — only 4 of 20 were in 北京.
- **Cause**: Zhilian's `sou.zhaopin.com` endpoint accepts the `jl=` URL
  param but doesn't filter results server-side. Same pattern as Liepin.
- **Fix**: added `_city_matches` Python post-filter on parsed `Job.city`.
  Handles Zhilian's `·` separator ("北京·朝阳区") in addition to `-`.
  Doubled snapshot fetch limit when city filter is active.

#### 2. Pagination dedup used full URL string
- **Symptom (latent)**: same job on different pages would have different
  URLs (Zhilian decorates with `refcode=`, `srccode=`, `preactionid=`),
  so URL-based dedup would treat them as different.
- **Fix**: extract `/jobdetail/<ALNUM>.htm` from URL, use `job:<id>` as
  dedup key. Falls back to full URL only if regex doesn't match.

### Documentation

- `ZhilianApplySender` docstring explicitly documents that:
  - Zhilian has no Boss-style greeting chat flow.
  - The only contact mechanism is "立即投递" (submit resume).
  - Generated greetings are handoff-only (used by `zhilian apply open`
    for human copy-paste), NOT auto-sent by `apply send`.
  - The `zhilian_greeting_not_supported` step reporting
    `zhilian_resume_submit_has_no_message_editor` on every healthy
    Zhilian job page is **expected behavior, not a bug**.

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
