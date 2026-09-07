"""CDP Boss Driver — cross-platform browser control via Chrome DevTools Protocol.

Replaces AppleScript on Windows/Linux and serves as a fallback on macOS.
All DOM operations are performed via CDP Runtime.evaluate inside a real Chrome instance.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from jobagent.infra.platform_tabs import (
    adopt_platform_tab_target,
    close_platform_target,
    default_url_for_platform,
    ensure_platform_tab,
    list_targets,
    platform_for_url,
)

from .base import BossActionDriver
from .chrome_manager import ChromeInstanceManager
from .cdp_client import CDPClient


class CDPBossDriver(BossActionDriver):
    """Boss driver implementation using Chrome CDP.

    Launches a dedicated Chrome instance (visible window, independent profile)
    and controls it via WebSocket CDP commands.
    """

    def __init__(
        self,
        manager: ChromeInstanceManager | None = None,
        platform: str = "boss",
        *,
        track_round: bool = True,
    ):
        self.manager = manager or ChromeInstanceManager()
        self.platform = platform
        self.track_round = track_round
        self.current_platform = ""
        self.current_target_id = ""
        self._boss_expected_job_id = ""
        self._boss_bound_chat_job_id = ""
        self.cdp = CDPClient()
        self._ensure_connected()

    def _ensure_connected(
        self,
        platform: str | None = None,
        initial_url: str | None = None,
        *,
        force: bool = False,
    ) -> None:
        """Ensure Chrome is running and CDP WebSocket is connected."""
        current_platform = getattr(self, "current_platform", "")
        default_platform = getattr(self, "platform", "boss")
        target_platform = platform or current_platform or default_platform or "boss"
        if self.cdp.connected and not hasattr(self, "manager") and not force:
            return
        if self.cdp.connected and current_platform == target_platform and not force:
            return
        self.manager.ensure_running()
        tab_options: dict[str, Any] = {
            "platform": target_platform,
            "port": self.manager.port,
            "initial_url": initial_url or default_url_for_platform(target_platform),
        }
        if not getattr(self, "track_round", True):
            tab_options["track_round"] = False
        target = ensure_platform_tab(
            **tab_options,
        )
        ws_url = target["webSocketDebuggerUrl"]
        self.cdp.connect(ws_url)
        self.current_platform = target_platform
        self.current_target_id = str(target.get("id") or target.get("targetId") or "")

    @staticmethod
    def _trusted_platform_search_target(target: dict[str, Any], platform: str) -> bool:
        if (
            target.get("type") != "page"
            or platform_for_url(str(target.get("url") or "")) != platform
        ):
            return False
        try:
            parsed = urlsplit(str(target.get("url") or ""))
        except ValueError:
            return False
        if (
            parsed.scheme != "https"
            or parsed.port is not None
            or parsed.username is not None
            or parsed.password is not None
        ):
            return False
        if platform == "zhilian":
            return (
                parsed.path == "/sou"
                or parsed.path.startswith("/sou/")
                or parsed.path in {"/jobs", "/jobs/"}
            )
        return False

    @staticmethod
    def _target_state_fingerprint(target: dict[str, Any]) -> str:
        """Return an opaque fingerprint for target transition comparison."""

        raw = "\n".join(
            (
                str(target.get("url") or ""),
                str(target.get("title") or ""),
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _target_title_contains(
        target: dict[str, Any],
        expected_title_text: str,
    ) -> bool:
        """Require one readable destination hint before committing a target."""

        expected = " ".join(str(expected_title_text or "").split()).casefold()
        if not expected:
            return True
        title = " ".join(str(target.get("title") or "").split()).casefold()
        return bool(title and expected in title)

    def _target_info_map(self) -> dict[str, dict[str, Any]]:
        """Return CDP target metadata when the connected Chrome exposes it."""

        if getattr(self, "_target_info_supported", None) is False:
            return {}
        try:
            result = self.cdp.send("Target.getTargets", timeout=2)
        except Exception:
            self._target_info_supported = False
            return {}
        infos = result.get("targetInfos") if isinstance(result, dict) else None
        if not isinstance(infos, list):
            self._target_info_supported = False
            return {}
        self._target_info_supported = True
        return {
            str(info.get("targetId") or ""): info
            for info in infos
            if isinstance(info, dict) and info.get("targetId")
        }

    @staticmethod
    def _target_opener_id(
        target: dict[str, Any],
        target_infos: dict[str, dict[str, Any]],
    ) -> str:
        target_id = str(target.get("id") or target.get("targetId") or "")
        info = target_infos.get(target_id, {})
        return str(
            target.get("openerId")
            or target.get("opener_id")
            or info.get("openerId")
            or ""
        )

    @classmethod
    def _trusted_changed_platform_target(
        cls,
        target: dict[str, Any],
        platform: str,
    ) -> bool:
        """Allow one changed official page to be adopted before DOM verification."""

        if target.get("type") != "page" or platform_for_url(str(target.get("url") or "")) != platform:
            return False
        try:
            parsed = urlsplit(str(target.get("url") or ""))
        except ValueError:
            return False
        if (
            parsed.scheme != "https"
            or parsed.port is not None
            or parsed.username is not None
            or parsed.password is not None
        ):
            return False
        if platform != "zhilian":
            return cls._trusted_platform_search_target(target, platform)
        host = (parsed.hostname or "").lower()
        if host not in {"www.zhaopin.com", "sou.zhaopin.com"}:
            return False
        path = parsed.path.lower()
        return not any(token in path for token in ("login", "passport", "verify"))

    def capture_platform_target_state(self, platform: str) -> dict[str, Any]:
        """Capture opaque target identities before an action may open a new tab."""
        self._ensure_connected(platform=platform)
        targets = list_targets(self.manager.port)
        current_target_id = str(getattr(self, "current_target_id", "") or "")
        current_ws_url = str(getattr(self.cdp, "ws_url", "") or "")
        current_target = next(
            (
                target
                for target in targets
                if current_ws_url
                and str(target.get("webSocketDebuggerUrl") or "")
                == current_ws_url
            ),
            None,
        )
        if current_target:
            current_target_id = str(
                current_target.get("id") or current_target.get("targetId") or ""
            )
            self.current_target_id = current_target_id
        target_ids = [
            str(target.get("id") or target.get("targetId") or "")
            for target in targets
            if target.get("type") == "page"
            and platform_for_url(str(target.get("url") or "")) == platform
        ]
        platform_targets = [
            target
            for target in targets
            if target.get("type") == "page"
            and platform_for_url(str(target.get("url") or "")) == platform
        ]
        return {
            "platform": platform,
            "target_ids": [target_id for target_id in target_ids if target_id],
            "current_target_id": current_target_id,
            "target_fingerprints": {
                str(
                    target.get("id") or target.get("targetId") or ""
                ): self._target_state_fingerprint(target)
                for target in platform_targets
                if str(target.get("id") or target.get("targetId") or "")
            },
        }

    def adopt_platform_target_transition(
        self,
        before: dict[str, Any],
        *,
        platform: str,
        wait_seconds: float = 2,
        allow_changed_platform_page: bool = False,
        expected_title_text: str = "",
    ) -> dict[str, Any]:
        """Adopt one unambiguous official target transition."""
        if before.get("platform") != platform:
            return {
                "ok": False,
                "outcome": "target_state_platform_mismatch",
                "new_target_count": 0,
                "previous_target_closed": False,
            }
        before_ids = {
            str(target_id)
            for target_id in before.get("target_ids") or []
            if target_id
        }
        previous_target_id = str(before.get("current_target_id") or "")
        before_fingerprints = {
            str(target_id): str(fingerprint)
            for target_id, fingerprint in (
                before.get("target_fingerprints") or {}
            ).items()
            if target_id and fingerprint
        }
        timeout = min(max(float(wait_seconds), 2.0), 90.0)
        deadline = time.monotonic() + timeout
        max_provisional_count = 0
        last_ambiguous_count = 0
        last_owned_action_provisional: list[dict[str, Any]] = []

        def discard_action_targets(
            candidates: list[dict[str, Any]],
            *,
            selected_id: str = "",
        ) -> tuple[int, bool]:
            discarded = 0
            cleanup_verified = True
            for candidate in candidates:
                target_id = str(
                    candidate.get("id") or candidate.get("targetId") or ""
                )
                if (
                    not target_id
                    or target_id == selected_id
                    or target_id in before_ids
                ):
                    continue
                try:
                    receipt = close_platform_target(
                        self.manager.port,
                        target_id,
                        expected_websocket_url=str(
                            candidate.get("webSocketDebuggerUrl") or ""
                        ),
                    )
                except Exception:
                    cleanup_verified = False
                    continue
                if receipt.get("closed"):
                    discarded += 1
                else:
                    cleanup_verified = False
            return discarded, cleanup_verified

        while True:
            targets = list_targets(self.manager.port)
            page_targets = [target for target in targets if target.get("type") == "page"]
            target_infos = self._target_info_map()
            provisional_targets = [
                target
                for target in page_targets
                if str(target.get("id") or target.get("targetId") or "")
                not in before_ids
            ]
            max_provisional_count = max(
                max_provisional_count,
                len(provisional_targets),
            )
            current = next(
                (
                    target
                    for target in page_targets
                    if str(target.get("id") or target.get("targetId") or "")
                    == previous_target_id
                ),
                None,
            )
            current_changed = bool(
                current
                and previous_target_id
                and before_fingerprints.get(previous_target_id)
                and before_fingerprints.get(previous_target_id)
                != self._target_state_fingerprint(current)
            )
            current_trusted = bool(
                current
                and (
                    self._trusted_platform_search_target(current, platform)
                    if not allow_changed_platform_page
                    else current_changed
                    and self._trusted_changed_platform_target(current, platform)
                )
                and self._target_title_contains(current, expected_title_text)
            )

            changed_candidates = [
                target
                for target in page_targets
                if (
                    (
                        str(target.get("id") or target.get("targetId") or "")
                        not in before_ids
                    )
                    or (
                        str(target.get("id") or target.get("targetId") or "")
                        in before_fingerprints
                        and before_fingerprints[
                            str(target.get("id") or target.get("targetId") or "")
                        ]
                        != self._target_state_fingerprint(target)
                    )
                )
                and (
                    self._trusted_platform_search_target(target, platform)
                    if not allow_changed_platform_page
                    else self._trusted_changed_platform_target(target, platform)
                )
                and self._target_title_contains(target, expected_title_text)
            ]
            action_candidates = [
                target
                for target in changed_candidates
                if previous_target_id
                and self._target_opener_id(target, target_infos)
                == previous_target_id
            ]
            action_provisional = [
                target
                for target in provisional_targets
                if previous_target_id
                and self._target_opener_id(target, target_infos)
                == previous_target_id
            ]
            platform_provisional = [
                target
                for target in provisional_targets
                if platform_for_url(str(target.get("url") or "")) == platform
            ]
            owned_action_provisional = (
                action_provisional
                if action_provisional
                else platform_provisional
                if len(platform_provisional) == 1
                else []
            )
            last_owned_action_provisional = owned_action_provisional

            selected: dict[str, Any] | None = None
            outcome = ""
            if current_trusted and action_candidates:
                last_ambiguous_count = 1 + len(action_candidates)
            elif len(action_candidates) == 1 and len(action_provisional) <= 1:
                selected = action_candidates[0]
                outcome = "action_linked_target_adopted"
            elif len(action_candidates) > 1 or len(action_provisional) > 1:
                last_ambiguous_count = max(
                    len(action_candidates),
                    len(action_provisional),
                )
            elif current_trusted:
                selected = current
                outcome = "current_target_reused"
            elif (
                len(action_provisional) == 1
                and not expected_title_text
                and platform_for_url(
                    str(action_provisional[0].get("url") or "")
                )
                == platform
            ):
                # Search controls can open an owned official child at the
                # platform root before the SPA commits its result route. Keep
                # the exact child as the controlled observation surface and
                # let the collector verify readable city/query/result state.
                # Without this handoff, slow pages are cleaned or leaked while
                # the driver remains attached to an unrelated historical tab.
                selected = action_provisional[0]
                outcome = "action_linked_provisional_target_adopted"
            elif action_provisional and not expected_title_text:
                # The action-created page may remain about:blank while Chrome
                # establishes the official navigation. Keep observing it, but
                # never connect until it reaches a trusted platform URL.
                last_ambiguous_count = len(action_provisional)
            else:
                new_candidates = [
                    target
                    for target in changed_candidates
                    if str(target.get("id") or target.get("targetId") or "")
                    not in before_ids
                ]
                existing_candidates = [
                    target
                    for target in changed_candidates
                    if str(target.get("id") or target.get("targetId") or "")
                    in before_ids
                ]
                if len(new_candidates) == 1:
                    selected = new_candidates[0]
                    outcome = "new_target_adopted"
                elif len(new_candidates) > 1:
                    last_ambiguous_count = len(new_candidates)
                elif expected_title_text and len(existing_candidates) == 1:
                    selected = existing_candidates[0]
                    outcome = "changed_existing_target_adopted"
                elif expected_title_text and len(existing_candidates) > 1:
                    last_ambiguous_count = len(existing_candidates)
                elif provisional_targets:
                    last_ambiguous_count = len(provisional_targets)
                elif len(existing_candidates) == 1:
                    selected = existing_candidates[0]
                    outcome = "changed_existing_target_adopted"
                elif len(existing_candidates) > 1:
                    last_ambiguous_count = len(existing_candidates)

            if selected:
                selected_id = str(
                    selected.get("id") or selected.get("targetId") or ""
                )
                selected_is_new = selected_id not in before_ids
                if outcome != "current_target_reused":
                    self.cdp.connect(str(selected["webSocketDebuggerUrl"]))
                adopt_platform_tab_target(
                    platform=platform,
                    port=self.manager.port,
                    target=selected,
                    track_round=getattr(self, "track_round", True),
                )
                self.current_platform = platform
                self.current_target_id = selected_id
                previous_closed = False
                previous_cleanup_verified = True
                if (
                    selected_is_new
                    and previous_target_id
                    and previous_target_id in before_ids
                ):
                    previous_target = next(
                        (
                            target
                            for target in page_targets
                            if str(
                                target.get("id") or target.get("targetId") or ""
                            )
                            == previous_target_id
                        ),
                        None,
                    )
                    try:
                        receipt = close_platform_target(
                            self.manager.port,
                            previous_target_id,
                            expected_websocket_url=str(
                                (previous_target or {}).get(
                                    "webSocketDebuggerUrl"
                                )
                                or ""
                            ),
                        )
                        previous_closed = bool(receipt.get("closed"))
                        previous_cleanup_verified = previous_closed
                    except Exception:
                        previous_closed = False
                        previous_cleanup_verified = False
                discarded_action_target_count = 0
                target_cleanup_verified = True
                if not selected_is_new:
                    (
                        discarded_action_target_count,
                        target_cleanup_verified,
                    ) = discard_action_targets(
                        owned_action_provisional,
                        selected_id=selected_id,
                    )
                if not previous_cleanup_verified or not target_cleanup_verified:
                    return {
                        "ok": False,
                        "outcome": "action_target_cleanup_unverified",
                        "new_target_count": 1 if selected_is_new else 0,
                        "previous_target_closed": previous_closed,
                        "discarded_action_target_count": (
                            discarded_action_target_count
                        ),
                        "target_cleanup_verified": False,
                    }
                result = {
                    "ok": True,
                    "outcome": outcome,
                    "new_target_count": 1 if selected_is_new else 0,
                    "previous_target_closed": previous_closed,
                }
                if owned_action_provisional and not selected_is_new:
                    result["target_cleanup_verified"] = True
                if discarded_action_target_count:
                    result["discarded_action_target_count"] = (
                        discarded_action_target_count
                    )
                if outcome == "action_linked_target_adopted":
                    result.update(
                        {
                            "matching_target_count": len(
                                [
                                    target
                                    for target in page_targets
                                    if platform_for_url(
                                        str(target.get("url") or "")
                                    )
                                    == platform
                                ]
                            ),
                            "action_candidate_count": 1,
                            "provisional_target_count": max_provisional_count,
                        }
                    )
                return result
            if time.monotonic() >= deadline:
                if last_ambiguous_count > 1:
                    return {
                        "ok": False,
                        "outcome": "ambiguous_search_targets",
                        "new_target_count": last_ambiguous_count,
                        "previous_target_closed": False,
                    }
                if len(last_owned_action_provisional) == 1:
                    (
                        discarded_action_target_count,
                        target_cleanup_verified,
                    ) = discard_action_targets(last_owned_action_provisional)
                    return {
                        "ok": False,
                        "outcome": (
                            "action_target_navigation_not_observed"
                            if target_cleanup_verified
                            else "action_target_cleanup_unverified"
                        ),
                        "new_target_count": 1,
                        "previous_target_closed": False,
                        "discarded_action_target_count": (
                            discarded_action_target_count
                        ),
                        "target_cleanup_verified": target_cleanup_verified,
                    }
                return {
                    "ok": False,
                    "outcome": (
                        "action_target_not_ready"
                        if max_provisional_count
                        else "no_search_target_observed"
                    ),
                    "new_target_count": 0,
                    "previous_target_closed": False,
                }
            time.sleep(0.1)

    def _ensure_connected_for_url(self, url: str) -> str:
        target_platform = platform_for_url(url) or self.platform
        if self.cdp.connected:
            try:
                result = self.cdp.evaluate("location.href")
                current_url = str(result.get("result", {}).get("value") or "")
            except Exception:
                current_url = ""
            if platform_for_url(current_url) == target_platform:
                self.current_platform = target_platform
                return current_url
        self._ensure_connected(
            platform=target_platform,
            initial_url=url,
            force=True,
        )
        # The platform registry may reconnect to an existing tab whose URL is
        # older than ``initial_url``. Return an unknown location so callers
        # perform one explicit navigation instead of assuming the target loaded.
        return ""

    @staticmethod
    def _same_search_url(current_url: str, target_url: str) -> bool:
        """Return whether the current page already represents the target search.

        Boss may append tracking parameters after navigation, so require the
        same origin/path and treat the requested query parameters as a subset.
        """
        if not current_url:
            return False
        current = urlsplit(current_url)
        target = urlsplit(target_url)
        if (
            current.scheme,
            current.netloc,
            current.path.rstrip("/"),
        ) != (
            target.scheme,
            target.netloc,
            target.path.rstrip("/"),
        ):
            return False
        current_query = dict(parse_qsl(current.query, keep_blank_values=True))
        target_query = dict(parse_qsl(target.query, keep_blank_values=True))
        return all(current_query.get(key) == value for key, value in target_query.items())

    @staticmethod
    def _decode_snapshot_result(result: dict[str, Any]) -> dict[str, Any]:
        value = result.get("result", {}).get("value")
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"ok": False}
        return {"ok": False, "error": "search_snapshot_unavailable"}

    @staticmethod
    def _search_snapshot_ready(snapshot: dict[str, Any]) -> bool:
        cards = snapshot.get("cards")
        return bool(
            (isinstance(cards, list) and cards)
            or snapshot.get("noResults")
            or snapshot.get("loginRequired")
            or snapshot.get("verificationRequired")
            or snapshot.get("environmentRejected")
        )

    def _exec_js(self, js_code: str, timeout: int = 30) -> dict[str, Any]:
        """AppleScript-driver compatible JavaScript evaluator.

        BossDataDriver historically called the AppleScript driver's private
        _exec_js helper. CDP is now the preferred driver, so keep that call path
        working while returning the same shapes: parsed JSON dicts when possible,
        otherwise {"ok": True, "raw": "..."}.
        """
        self._ensure_connected()
        try:
            result = self.cdp.evaluate(js_code, timeout=timeout)
            value = result.get("result", {}).get("value")
        except Exception as e:
            return {"ok": False, "error": str(e)}

        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
                return {"ok": True, "raw": value}
            except Exception:
                return {"ok": True, "raw": value}
        return {"ok": True, "raw": "" if value is None else str(value)}

    def _unwrap(self, result: dict[str, Any]) -> dict[str, Any]:
        """Unwrap _exec_js output into a JSON dict."""
        if "raw" not in result:
            return result
        try:
            parsed = json.loads(result["raw"])
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _click_at(self, x: int | float, y: int | float) -> None:
        """Click viewport coordinates using CDP native mouse events."""
        try:
            self.cdp.send("Page.bringToFront")
        except Exception:
            pass
        self.cdp.send(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": x, "y": y, "button": "none"},
        )
        self.cdp.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mousePressed",
                "x": x,
                "y": y,
                "button": "left",
                "clickCount": 1,
            },
        )
        self.cdp.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseReleased",
                "x": x,
                "y": y,
                "button": "left",
                "clickCount": 1,
            },
        )

    @staticmethod
    def _trusted_boss_chat_redirect(url: str) -> str:
        """Return a validated Boss chat URL without weakening the origin boundary."""
        try:
            target = urlsplit(url)
        except ValueError:
            return ""
        if (
            target.scheme != "https"
            or target.hostname != "www.zhipin.com"
            or target.port is not None
            or target.username is not None
            or target.password is not None
            or target.path != "/web/geek/chat"
        ):
            return ""
        return target.geturl()

    def dismiss_javascript_dialog(self) -> dict[str, Any]:
        """Dismiss a blocking page alert without treating its text as user input."""
        self._ensure_connected()
        try:
            self.cdp.send(
                "Page.handleJavaScriptDialog",
                {"accept": False},
                timeout=2,
            )
        except Exception as exc:
            message = str(exc)
            if "No dialog is showing" in message or "No JavaScript dialog" in message:
                return {"ok": True, "dismissed": False}
            return {"ok": False, "dismissed": False, "error": message}
        return {"ok": True, "dismissed": True}

    def reload_current_page(self, wait_seconds: float = 3) -> dict[str, Any]:
        """Reload the active platform tab and return its resulting location."""
        self._ensure_connected()
        try:
            self.cdp.send("Page.reload", {"ignoreCache": False})
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            result = self.cdp.evaluate(
                "JSON.stringify({url: location.href, title: document.title})"
            )
            info = json.loads(result.get("result", {}).get("value", "{}"))
            return {
                "ok": True,
                "url": str(info.get("url") or ""),
                "title": str(info.get("title") or ""),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── BossActionDriver interface ──────────────────────────

    def chrome_running(self) -> bool:
        return self.manager.is_running()

    def applescript_js_enabled(self) -> tuple[bool, str]:
        # CDP does not use AppleScript; always report ready.
        return True, "cdp"

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 5) -> dict[str, Any]:
        """Navigate the current CDP page to the given URL.

        Checks for verification redirects (verify / code=36) after navigation.
        """
        try:
            parsed_url = urlsplit(url)
        except ValueError:
            parsed_url = None
        if (
            parsed_url is not None
            and platform_for_url(url) == "boss"
            and "/job_detail/" in parsed_url.path
        ):
            self._boss_expected_job_id = (
                parsed_url.path.rsplit("/", 1)[-1].removesuffix(".html")
            )
            self._boss_bound_chat_job_id = ""
        current_url = self._ensure_connected_for_url(url)
        reused = self._same_search_url(current_url, url)
        try:
            if platform_for_url(url) == "zhilian":
                self.dismiss_javascript_dialog()
            if not reused:
                self.cdp.send("Page.navigate", {"url": url})
            is_boss_job = (
                parsed_url is not None
                and platform_for_url(url) == "boss"
                and "/job_detail/" in parsed_url.path
            )
            is_liepin_search = (
                platform_for_url(url) == "liepin"
                and "/zhaopin" in urlsplit(url).path
            )
            is_liepin_job = (
                platform_for_url(url) == "liepin"
                and urlsplit(url).path.startswith("/job/")
            )
            if is_boss_job:
                snapshot_js = r"""
                (function(){
                  function isVisible(el) {
                    if (!el) return false;
                    var style = window.getComputedStyle(el);
                    var rect = el.getBoundingClientRect();
                    return style.display !== 'none'
                      && style.visibility !== 'hidden'
                      && Number(style.opacity || 1) > 0
                      && rect.width > 0
                      && rect.height > 0;
                  }
                  var chatEntry = document.querySelector('a.btn-startchat, .btn-startchat');
                  return JSON.stringify({
                    url: location.href || '',
                    title: document.title || '',
                    readyState: document.readyState || '',
                    hasChatEntry: !!(chatEntry && isVisible(chatEntry))
                  });
                })()
                """
                info: dict[str, Any] = {}
                stable_hits = 0
                required_hits = 1 if reused else 2
                # 18 probes * (5s CDP timeout + 2s interval) bounds a fully
                # unresponsive page to roughly two minutes while normal pages
                # complete after one or two cheap observations.
                for attempt in range(18):
                    try:
                        result = self.cdp.evaluate(snapshot_js, timeout=5)
                        info = json.loads(
                            result.get("result", {}).get("value", "{}")
                        )
                        ready = (
                            info.get("readyState") == "complete"
                            and info.get("hasChatEntry")
                        )
                        stable_hits = stable_hits + 1 if ready else 0
                        if stable_hits >= required_hits:
                            break
                    except Exception:
                        stable_hits = 0
                    if attempt < 17:
                        time.sleep(2)
                if stable_hits < required_hits:
                    return {
                        "ok": False,
                        "error": "job_page_load_timeout",
                        "url": str(info.get("url") or url).split("?", 1)[0],
                        "title": str(info.get("title") or ""),
                        "readyState": str(info.get("readyState") or ""),
                        "hasChatEntry": bool(info.get("hasChatEntry")),
                    }
            elif is_liepin_search:
                snapshot_js = r"""
                (function(){
                  var body = document.body
                    ? (document.body.innerText || document.body.textContent || '')
                    : '';
                  return JSON.stringify({
                    url: location.href || '',
                    title: document.title || '',
                    readyState: document.readyState || '',
                    jobLinkCount: document.querySelectorAll('a[href*="/job/"]').length,
                    noResults: /非常抱歉[\s\S]{0,40}(暂时没有|暂无).*职位/.test(body),
                    loginRequired: /登录|注册/.test((document.title || '') + '\n' + body.slice(0, 160))
                  });
                })()
                """
                info = {}
                stable_hits = 0
                required_hits = 1 if reused else 2
                max_wait = max(30.0, float(wait_seconds))
                poll_interval = 2.0
                attempts = max(2, int(math.ceil(max_wait / poll_interval)) + 1)
                for attempt in range(attempts):
                    try:
                        result = self.cdp.evaluate(snapshot_js, timeout=5)
                        info = json.loads(result.get("result", {}).get("value", "{}"))
                        navigated = self._same_search_url(
                            str(info.get("url") or ""),
                            url,
                        )
                        observable = bool(
                            info.get("jobLinkCount")
                            or info.get("noResults")
                            or info.get("loginRequired")
                        )
                        ready = (
                            navigated
                            and info.get("readyState") in {"interactive", "complete"}
                            and observable
                        )
                        stable_hits = stable_hits + 1 if ready else 0
                        if stable_hits >= required_hits:
                            break
                    except Exception:
                        stable_hits = 0
                    if attempt < attempts - 1:
                        time.sleep(poll_interval)
                if stable_hits < required_hits:
                    return {
                        "ok": False,
                        "error": "search_page_load_timeout",
                        "url": str(info.get("url") or url).split("?", 1)[0],
                        "title": str(info.get("title") or ""),
                        "readyState": str(info.get("readyState") or ""),
                        "jobLinkCount": int(info.get("jobLinkCount") or 0),
                        "noResults": bool(info.get("noResults")),
                    }
            elif is_liepin_job:
                snapshot_js = r"""
                (function(){
                  function visible(el) {
                    if (!el) return false;
                    var style = window.getComputedStyle(el);
                    var rect = el.getBoundingClientRect();
                    return style.display !== 'none'
                      && style.visibility !== 'hidden'
                      && Number(style.opacity || 1) > 0
                      && rect.width > 1
                      && rect.height > 1;
                  }
                  var body = document.body
                    ? (document.body.innerText || document.body.textContent || '')
                    : '';
                  var header = (document.title || '') + '\n' + body.slice(0, 500);
                  var loginRequired = /登录\/注册|密码登录|获取验证码|扫码登录/.test(header);
                  var authenticated = !loginRequired
                    && (/我的投递|我的收藏/.test(body.slice(0, 500))
                      || /你好[，,]/.test(body.slice(0, 160)));
                  var actionLabels = ['投简历', '聊一聊', '继续聊', '继续沟通', '已投递'];
                  var hasAction = Array.prototype.slice.call(
                    document.querySelectorAll('a,button,[role="button"]')
                  ).some(function(el) {
                    var text = (el.innerText || el.textContent || '').trim();
                    return visible(el) && actionLabels.indexOf(text) >= 0;
                  });
                  return JSON.stringify({
                    url: location.href || '',
                    title: document.title || '',
                    readyState: document.readyState || '',
                    authenticated: authenticated,
                    hasAction: hasAction,
                    loginRequired: loginRequired
                  });
                })()
                """
                info = {}
                stable_hits = 0
                required_hits = 1 if reused else 2
                max_wait = max(30.0, float(wait_seconds))
                poll_interval = 2.0
                attempts = max(2, int(math.ceil(max_wait / poll_interval)) + 1)
                for attempt in range(attempts):
                    try:
                        result = self.cdp.evaluate(snapshot_js, timeout=5)
                        info = json.loads(result.get("result", {}).get("value", "{}"))
                        navigated = self._same_search_url(
                            str(info.get("url") or ""),
                            url,
                        )
                        ready = bool(
                            navigated
                            and info.get("readyState") in {"interactive", "complete"}
                            and info.get("authenticated")
                            and info.get("hasAction")
                        )
                        stable_hits = stable_hits + 1 if ready else 0
                        if stable_hits >= required_hits:
                            break
                    except Exception:
                        stable_hits = 0
                    if attempt < attempts - 1:
                        time.sleep(poll_interval)
                if stable_hits < required_hits and not info.get("loginRequired"):
                    return {
                        "ok": False,
                        "error": "job_page_load_timeout",
                        "url": str(info.get("url") or url).split("?", 1)[0],
                        "title": str(info.get("title") or ""),
                        "readyState": str(info.get("readyState") or ""),
                        "authenticated": bool(info.get("authenticated")),
                        "hasAction": bool(info.get("hasAction")),
                    }
            else:
                if not reused:
                    time.sleep(wait_seconds)
                result = self.cdp.evaluate(
                    "JSON.stringify({url: location.href, title: document.title})"
                )
                info = json.loads(result.get("result", {}).get("value", "{}"))
            current_url = info.get("url", "")
            # Verification detection: upstream may redirect to a verify page
            if "verify" in current_url or "code=36" in current_url:
                return {
                    "ok": False,
                    "error": "verification_required",
                    "url": current_url,
                    "title": info.get("title", ""),
                }
            return {
                "ok": True,
                "url": current_url,
                "title": info.get("title", ""),
                "reused": reused,
                "readyState": info.get("readyState", ""),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def inspect_page(self) -> dict[str, Any]:
        """Inspect the current page for login state and UI elements."""
        self._ensure_connected()
        js = r"""
        (function(){
          function isVisible(el) {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.display !== 'none'
              && style.visibility !== 'hidden'
              && Number(style.opacity || 1) > 0
              && rect.width > 0
              && rect.height > 0;
          }
          const txt = document.body ? (document.body.innerText || '') : '';
          const title = document.title || '';
          const href = location.href || '';
          const loginDialog = [...document.querySelectorAll('.sign-content, .login-dialog, .passport-login-container, .dialog-wrap .sign-form')].some(isVisible);
          const qrLoginDialog = !![...document.querySelectorAll('div,span,p')].find(x => isVisible(x) && /扫码登录|请在App端确认登录|发送验证码/.test((x.innerText||x.textContent||'').trim()));
          const userNav = !!document.querySelector('.user-nav');
          const geekNav = !![...document.querySelectorAll('a,span,div')].find(x => ['消息','简历','职位'].includes((x.innerText||x.textContent||'').trim()));
          const resumeActions = !![...document.querySelectorAll('a,button,div')].find(x => /完善在线简历|新增附件简历/.test((x.innerText||x.textContent||'').trim()));
          const hasChatEntry = [...document.querySelectorAll('a,button,div')].some(x=>{const s=(x.innerText||x.textContent||'').trim(); return isVisible(x) && (s==='立即沟通' || s==='继续沟通')});
          return JSON.stringify({
            ok:true,
            href,
            title,
            loginDialog,
            qrLoginDialog,
            userNav,
            geekNav,
            resumeActions,
            hasChatEntry,
            textSnippet: txt.slice(0,500)
          });
        })()
        """
        try:
            result = self.cdp.evaluate(js)
            raw = result.get("result", {}).get("value", "{}")
            return json.loads(raw)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def click_chat_entry(self) -> dict[str, Any]:
        """Click the '立即沟通' button and handle the popup dialog.

        After clicking 立即沟通, a popup "已向BOSS发送消息" may appear.
        We must click "继续沟通" in that popup to open the chat sidebar.
        If no popup appears, treat the state as ambiguous rather than sent.

        Matches boss-radar's verified 6-step flow (2026-05-07).
        """
        self._ensure_connected()
        expected_job_id = str(getattr(self, "_boss_expected_job_id", "") or "")
        # Step 1: click 立即沟通
        click_js = r"""
        (function(){
          function isVisible(el) {
            if (!el) return false;
            var style = window.getComputedStyle(el);
            var rect = el.getBoundingClientRect();
            return style.display !== 'none'
              && style.visibility !== 'hidden'
              && Number(style.opacity || 1) > 0
              && rect.width > 0
              && rect.height > 0;
          }
          function textOf(el) {
            return (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
          }
          function targetInfo(el, label) {
            try { el.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {}
            var rect = el.getBoundingClientRect();
            var jobMatch = (location.pathname || '').match(/\/job_detail\/([^/]+?)(?:\.html)?$/);
            var info = {
              ok: true,
              step: 'target_' + label,
              label: label,
              jobId: jobMatch ? jobMatch[1].replace(/\.html$/, '') : '',
              tag: el.tagName,
              className: String(el.className || ''),
              x: Math.round(rect.left + rect.width / 2),
              y: Math.round(rect.top + rect.height / 2)
            };
            var redirectLink = el.matches && el.matches('a[redirect-url]')
              ? el
              : (el.querySelector ? el.querySelector('a[redirect-url]') : null);
            var redirectPath = redirectLink
              ? (redirectLink.getAttribute('redirect-url') || '')
              : '';
            if (redirectPath) {
              try {
                var redirectTarget = new URL(redirectPath, location.origin);
                if (redirectTarget.origin === location.origin
                    && redirectTarget.pathname === '/web/geek/chat') {
                  info.redirectUrl = redirectTarget.href;
                }
              } catch (e) {}
            }
            return JSON.stringify(info);
          }
          var labels = ['立即沟通', '继续沟通', '继续聊', '开聊'];
          var selectorGroups = [
            '.btn-startchat',
            '.btn-startchat-wrap',
            'a,button,[role="button"]'
          ];
          for (var l = 0; l < labels.length; l++) {
            for (var g = 0; g < selectorGroups.length; g++) {
              var candidates = Array.prototype.slice.call(
                document.querySelectorAll(selectorGroups[g])
              );
              for (var i = 0; i < candidates.length; i++) {
                var el = candidates[i];
                if (isVisible(el) && textOf(el) === labels[l]) {
                  return targetInfo(el, labels[l]);
                }
              }
            }
          }
          // Fallback: href matching
          var links = document.querySelectorAll('a[href*="opchat"], a[href*="chat"]');
          for (var k = 0; k < links.length; k++) {
            if (isVisible(links[k])) {
              return targetInfo(links[k], 'chat_fallback');
            }
          }
          return JSON.stringify({ok: false, step: 'no_chat_entry'});
        })()
        """
        click_data: dict[str, Any] = {}
        try:
            result = None
            for target_attempt in range(3):
                try:
                    result = self.cdp.evaluate(click_js, timeout=5)
                    break
                except Exception:
                    if target_attempt == 2:
                        raise
                    # Target discovery is read-only, so retrying here cannot
                    # duplicate a click or a greeting. Boss often pauses its
                    # renderer briefly just after job-detail navigation.
                    time.sleep(2)
            assert result is not None
            click_data = json.loads(result.get("result", {}).get("value", "{}"))
            if not click_data.get("ok"):
                return click_data
            chat_redirect_url = self._trusted_boss_chat_redirect(
                str(click_data.pop("redirectUrl", "") or "")
            )
            clicked_job_id = str(click_data.get("jobId") or "")
            exact_target_job = bool(
                expected_job_id and clicked_job_id == expected_job_id
            )

            # An already-contacted job exposes its signed, same-origin chat
            # route on the visible continuation control. Follow that official
            # route before a coordinate click, because a busy renderer can
            # complete the click but time out before the exact binding is kept.
            if (
                chat_redirect_url
                and exact_target_job
                and click_data.get("label") in {"继续沟通", "继续聊"}
            ):
                self.cdp.send("Page.navigate", {"url": chat_redirect_url})
                self._boss_bound_chat_job_id = clicked_job_id
                click_data["clicked"] = False
                click_data["autoSent"] = False
                click_data["step"] = "navigated_chat_redirect"
                click_data["chatPath"] = "/web/geek/chat"
                return click_data

            if "x" in click_data and "y" in click_data:
                self._click_at(click_data["x"], click_data["y"])
                click_data["clicked"] = True
                click_data["step"] = "clicked_" + str(click_data.get("label", "chat"))
                if exact_target_job:
                    # This is the last mutating operation. Preserve its exact
                    # signed-detail lineage if a later DOM probe times out.
                    self._boss_bound_chat_job_id = clicked_job_id
                time.sleep(0.5)

            # Step 2: wait for popup and click 继续沟通 (up to 5 retries, 1s each)
            for attempt in range(5):
                time.sleep(1)
                popup_js = """
                (function(){
                  function isVisible(el) {
                    if (!el) return false;
                    var style = window.getComputedStyle(el);
                    var rect = el.getBoundingClientRect();
                    return style.display !== 'none'
                      && style.visibility !== 'hidden'
                      && Number(style.opacity || 1) > 0
                      && rect.width > 0
                      && rect.height > 0;
                  }
                  function targetInfo(el, label) {
                    try { el.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {}
                    var rect = el.getBoundingClientRect();
                    return JSON.stringify({
                      ok: true,
                      step: 'target_' + label,
                      label: label,
                      tag: el.tagName,
                      className: String(el.className || ''),
                      x: Math.round(rect.left + rect.width / 2),
                      y: Math.round(rect.top + rect.height / 2),
                      autoSent: false
                    });
                  }
                  var startDialogs = Array.prototype.slice.call(
                    document.querySelectorAll(
                      '.startchat-dialog, .dialog-wrap.startchat-dialog, '
                      + '.dialog-wrap.greet-pop, .greet-pop'
                    )
                  );
                  for (var d = 0; d < startDialogs.length; d++) {
                    var dialog = startDialogs[d];
                    if (!isVisible(dialog)) continue;
                    var dialogText = (dialog.innerText || dialog.textContent || '').trim();
                    var messageEl = dialog.querySelector('.message, .dialog-con');
                    var sentMessage = messageEl
                      ? (messageEl.innerText || messageEl.textContent || '').trim()
                      : dialogText;
                    if (/已向BOSS发送消息|已发送/.test(dialogText)) {
                      return JSON.stringify({
                        ok: true,
                        step: 'platform_default_sent',
                        autoSent: true,
                        platformDefaultSent: true,
                        sentMessage: sentMessage.slice(0, 240)
                      });
                    }
                  }
                  var dialogs = Array.prototype.slice.call(
                    document.querySelectorAll('.dialog-wrap, .dialog-container, [class*="dialog"], [class*="modal"], [class*="popup"]')
                  ).filter(isVisible);
                  for (var m = 0; m < dialogs.length; m++) {
                    var all = dialogs[m].querySelectorAll('a,button,[role="button"],div,span');
                    for (var i = 0; i < all.length; i++) {
                      var text = (all[i].innerText || all[i].textContent || '').trim();
                      if (text === '继续沟通' && isVisible(all[i])) {
                        return targetInfo(all[i], '继续沟通');
                      }
                    }
                  }
                  // Chat sidebar already open?
                  var editor = document.querySelector('.chat-input');
                  if (editor && isVisible(editor)) {
                    return JSON.stringify({
                      ok: true, step: 'chat_opened', autoSent: false
                    });
                  }
                  // Verification redirect?
                  var href = location.href || '';
                  if (href.indexOf('verify') >= 0 || href.indexOf('code=36') >= 0) {
                    return JSON.stringify({
                      ok: false, step: 'verification_required', url: href
                    });
                  }
                  return JSON.stringify({ok: false, step: 'no_popup_yet'});
                })()
                """
                popup_result = self.cdp.evaluate(popup_js, timeout=5)
                popup_data = json.loads(popup_result.get("result", {}).get("value", "{}"))
                if popup_data.get("ok"):
                    if (
                        popup_data.get("platformDefaultSent")
                        and chat_redirect_url
                    ):
                        try:
                            self.cdp.send(
                                "Page.navigate",
                                {"url": chat_redirect_url},
                            )
                            if exact_target_job:
                                self._boss_bound_chat_job_id = clicked_job_id
                            popup_data["step"] = "navigated_chat_redirect_after_default"
                            popup_data["chatPath"] = "/web/geek/chat"
                        except Exception:
                            popup_data["step"] = "platform_default_sent"
                            popup_data["chatRedirectFailed"] = True
                    if popup_data.get("step") == "target_继续沟通" and "x" in popup_data and "y" in popup_data:
                        self._click_at(popup_data["x"], popup_data["y"])
                        popup_data["step"] = "clicked_继续沟通"
                        time.sleep(0.5)
                    click_data.update(popup_data)
                    return click_data
                if popup_data.get("step") == "verification_required":
                    return popup_data

            # An already-contacted job exposes a trusted ``redirect-url`` on
            # the visible 继续沟通 anchor. Boss sometimes ignores the native
            # coordinate click, leaving us on the job detail page with no
            # editor. Follow only the same-origin chat path supplied by the
            # page itself; do not persist its signed query string in audit.
            if click_data.get("label") in {"立即沟通", "继续沟通", "继续聊"}:
                if chat_redirect_url:
                    self.cdp.send("Page.navigate", {"url": chat_redirect_url})
                    if exact_target_job:
                        self._boss_bound_chat_job_id = clicked_job_id
                    click_data["autoSent"] = False
                    click_data["step"] = "navigated_chat_redirect"
                    click_data["chatPath"] = "/web/geek/chat"
                    return click_data
                redirect_js = """
                (function(){
                  function isVisible(el) {
                    if (!el) return false;
                    var style = window.getComputedStyle(el);
                    var rect = el.getBoundingClientRect();
                    return style.display !== 'none'
                      && style.visibility !== 'hidden'
                      && Number(style.opacity || 1) > 0
                      && rect.width > 0
                      && rect.height > 0;
                  }
                  var links = document.querySelectorAll('a[redirect-url]');
                  for (var i = 0; i < links.length; i++) {
                    var text = (links[i].innerText || links[i].textContent || '').trim();
                    var path = links[i].getAttribute('redirect-url') || '';
                    if ((text === '立即沟通' || text === '继续沟通' || text === '继续聊')
                        && isVisible(links[i])
                        && path.indexOf('/web/geek/chat') === 0) {
                      var target = new URL(path, location.origin);
                      if (target.origin === location.origin
                          && target.pathname === '/web/geek/chat') {
                        return JSON.stringify({ok: true, url: target.href});
                      }
                    }
                  }
                  return JSON.stringify({ok: false});
                })()
                """
                redirect_result = self.cdp.evaluate(redirect_js, timeout=5)
                redirect_data = json.loads(
                    redirect_result.get("result", {}).get("value", "{}")
                )
                if redirect_data.get("ok") and redirect_data.get("url"):
                    self.cdp.send("Page.navigate", {"url": redirect_data["url"]})
                    if exact_target_job:
                        self._boss_bound_chat_job_id = clicked_job_id
                    click_data["autoSent"] = False
                    click_data["step"] = "navigated_chat_redirect"
                    click_data["chatPath"] = "/web/geek/chat"
                    return click_data

            # No popup found after 5s. Opening a chat page is not evidence that
            # Boss sent the greeting, so leave delivery to the explicit flow.
            click_data["autoSent"] = False
            click_data["step"] = "no_popup_after_click"
            return click_data
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "clicked": bool(click_data.get("clicked")),
                "label": str(click_data.get("label") or ""),
                "jobId": str(click_data.get("jobId") or ""),
            }

    def inspect_chat_editor(self) -> dict[str, Any]:
        """Inspect the chat editor and send button.

        Supports both the sidebar contenteditable and the start-chat modal
        textarea. If neither appears, opening chat alone is not delivery.
        """
        self._ensure_connected()
        # Poll for editor appearance while bounding each DOM probe. A hung Boss
        # renderer must not turn one job into many minutes of 30-second CDP
        # timeouts. Responsive pages still get ~24s of passive appearance wait.
        for _attempt in range(30):
            js = """
            (function(){
              function isVisible(el) {
                if (!el) return false;
                var style = window.getComputedStyle(el);
                var rect = el.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && Number(style.opacity || 1) > 0
                  && rect.width > 0
                  && rect.height > 0;
              }
              function findEditor() {
                var modalEditors = document.querySelectorAll(
                  '.startchat-dialog textarea, .dialog-wrap.startchat-dialog textarea'
                );
                for (var d = 0; d < modalEditors.length; d++) {
                  if (isVisible(modalEditors[d])) return modalEditors[d];
                }
                var all = document.querySelectorAll('*');
                for (var i = 0; i < all.length; i++) {
                  if (all[i].className && typeof all[i].className === 'string'
                      && all[i].className.indexOf('chat-input') >= 0
                      && isVisible(all[i])) return all[i];
                }
                var editables = document.querySelectorAll('[contenteditable="true"]');
                for (var e = 0; e < editables.length; e++) {
                  if (isVisible(editables[e])) return editables[e];
                }
                return null;
              }
              var editor = findEditor();
              var send = null;
              var scope = editor && editor.closest('.startchat-dialog, .dialog-container');
              var btns = (scope || document).querySelectorAll(
                'button, [role="button"], .btn-send, .send-message'
              );
              for (var j = 0; j < btns.length; j++) {
                var t = (btns[j].innerText || '').trim();
                if (t === '\u53d1\u9001' && isVisible(btns[j])) { send = btns[j]; break; }
              }
              if (!send) {
                var sendCandidates = document.querySelectorAll('.btn-send, .send-message');
                for (var s = 0; s < sendCandidates.length; s++) {
                  if (isVisible(sendCandidates[s])) { send = sendCandidates[s]; break; }
                }
              }
              var loginEls = Array.prototype.slice.call(
                document.querySelectorAll('.sign-content, .login-dialog, .passport-login-container, .dialog-wrap .sign-form')
              );
              var loginDialog = loginEls.some(isVisible);
              var href = location.href || '';
              var riskControl = href.indexOf('verify') >= 0 || href.indexOf('code=36') >= 0;
              var jobId = '';
              try { jobId = new URL(href).searchParams.get('jobId') || ''; } catch (e) {}
              return JSON.stringify({
                ok: true,
                editorFound: !!editor,
                editorTag: editor ? editor.tagName : '',
                editorClass: editor ? (editor.className || '') : '',
                sendFound: !!send,
                sendClass: send ? (send.className || '') : '',
                loginDialog: loginDialog,
                riskControl: riskControl,
                href: href,
                jobId: jobId
              });
            })()
            """
            try:
                result = self.cdp.evaluate(js, timeout=3)
                data = json.loads(result.get("result", {}).get("value", "{}"))
                href = str(data.get("href") or "")
                if href:
                    data["href"] = urlsplit(href)._replace(query="", fragment="").geturl()
                if data.get("riskControl"):
                    return {"ok": False, "error": "verification_required", "url": data.get("href", "")}
                if data.get("loginDialog"):
                    return data
                if data.get("editorFound"):
                    return data
            except Exception:
                pass
            time.sleep(0.8)
        # Editor not found after waiting. This can mean the page opened without a
        # writable chat editor, but it does not prove the greeting was sent.
        return {"ok": True, "autoSent": False, "editorFound": False,
                "step": "editor_not_found"}

    def fill_chat_message(self, message: str) -> dict[str, Any]:
        """Fill the chat input with the given message.

        Uses CDP-native text input so the page receives trusted keyboard/input
        events. Direct DOM text assignment can leave BOSS's frontend state stale:
        the text appears in the editor, but the send action does not fire.
        """
        self._ensure_connected()
        js = """
        (function(){
          function isVisible(el) {
            if (!el) return false;
            var style = window.getComputedStyle(el);
            var rect = el.getBoundingClientRect();
            return style.display !== 'none'
              && style.visibility !== 'hidden'
              && Number(style.opacity || 1) > 0
              && rect.width > 0
              && rect.height > 0;
          }
          function findEditor() {
            var modalEditors = document.querySelectorAll(
              '.startchat-dialog textarea, .dialog-wrap.startchat-dialog textarea'
            );
            for (var d = 0; d < modalEditors.length; d++) {
              if (isVisible(modalEditors[d])) return modalEditors[d];
            }
            var all = document.querySelectorAll('*');
            for (var i = 0; i < all.length; i++) {
              if (all[i].className && typeof all[i].className === 'string'
                  && all[i].className.indexOf('chat-input') >= 0
                  && isVisible(all[i])) return all[i];
            }
            var editables = document.querySelectorAll('[contenteditable="true"]');
            for (var e = 0; e < editables.length; e++) {
              if (isVisible(editables[e])) return editables[e];
            }
            return null;
          }
          var editor = findEditor();
          if (!editor) return JSON.stringify({ok: false, error: 'no_editor'});

          editor.focus();
          var formControl = editor.tagName === 'TEXTAREA' || editor.tagName === 'INPUT';
          if (formControl) {
            editor.select();
            return JSON.stringify({
              ok: true,
              step: 'editor_selected',
              editorTag: editor.tagName,
              formControl: true
            });
          }
          var range = document.createRange();
          range.selectNodeContents(editor);
          var sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(range);
          return JSON.stringify({
            ok: true,
            step: 'editor_selected',
            editorTag: editor.tagName,
            formControl: false
          });
        })()
        """
        try:
            result = self.cdp.evaluate(js)
            data = json.loads(result.get("result", {}).get("value", "{}"))
            if not data.get("ok"):
                return data

            self.cdp.send(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyDown",
                    "key": "Backspace",
                    "code": "Backspace",
                    "windowsVirtualKeyCode": 8,
                    "nativeVirtualKeyCode": 8,
                },
            )
            self.cdp.send(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyUp",
                    "key": "Backspace",
                    "code": "Backspace",
                    "windowsVirtualKeyCode": 8,
                    "nativeVirtualKeyCode": 8,
                },
            )
            time.sleep(0.5)
            self.cdp.send("Input.insertText", {"text": message})
            time.sleep(0.2)
            self.cdp.evaluate("""
            (function(){
              var editor = document.activeElement;
              if (!editor || !(
                  editor.matches('.startchat-dialog textarea')
                  || editor.matches('.dialog-wrap.startchat-dialog textarea')
                  || editor.matches('.chat-input')
                  || editor.matches('[contenteditable="true"]')
              )) {
                editor = document.querySelector(
                  '.startchat-dialog textarea, .dialog-wrap.startchat-dialog textarea, .chat-input, [contenteditable="true"]'
                );
              }
              if (!editor) return;
              editor.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: ''}));
              editor.dispatchEvent(new Event('change', {bubbles: true}));
            })()
            """)
            time.sleep(0.2)

            verify_js = """
            (function(){
              var editor = document.activeElement;
              if (!editor || !(
                  editor.matches('.startchat-dialog textarea')
                  || editor.matches('.dialog-wrap.startchat-dialog textarea')
                  || editor.matches('.chat-input')
                  || editor.matches('[contenteditable="true"]')
              )) {
                editor = document.querySelector(
                  '.startchat-dialog textarea, .dialog-wrap.startchat-dialog textarea, .chat-input, [contenteditable="true"]'
                );
              }
              var formControl = editor
                && (editor.tagName === 'TEXTAREA' || editor.tagName === 'INPUT');
              var text = editor
                ? (formControl ? editor.value : (editor.innerText || editor.textContent || ''))
                : '';
              return JSON.stringify({ok: true, step: 'filled', len: text.length, text: text});
            })()
            """
            verify_result = self.cdp.evaluate(verify_js)
            verify_data = json.loads(verify_result.get("result", {}).get("value", "{}"))
            if verify_data.get("text") != message:
                return {
                    "ok": False,
                    "error": "message_fill_mismatch",
                    "len": verify_data.get("len", 0),
                }
            verify_data.pop("text", None)
            return verify_data
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def click_send(self) -> dict[str, Any]:
        """Click the send button using native CDP mouse events."""
        self._ensure_connected()
        js = """
        (function(){
          function isVisible(el) {
            if (!el) return false;
            var style = window.getComputedStyle(el);
            var rect = el.getBoundingClientRect();
            return style.display !== 'none'
              && style.visibility !== 'hidden'
              && Number(style.opacity || 1) > 0
              && rect.width > 0
              && rect.height > 0;
          }
          var editor = document.querySelector(
            '.startchat-dialog textarea, .dialog-wrap.startchat-dialog textarea, .chat-input, [contenteditable="true"]'
          );
          if (editor && !isVisible(editor)) editor = null;
          var scope = editor && editor.closest('.startchat-dialog, .dialog-container');
          var sendBtn = null;
          var btns = (scope || document).querySelectorAll(
            'button, [role="button"], .btn-send, .send-message'
          );
          for (var j = 0; j < btns.length; j++) {
            var t = (btns[j].innerText || '').trim();
            if (t === '\u53d1\u9001' && isVisible(btns[j])) { sendBtn = btns[j]; break; }
          }
          if (!sendBtn) {
            var candidates = document.querySelectorAll('.btn-send, .send-message');
            for (var k = 0; k < candidates.length; k++) {
              if (isVisible(candidates[k])) { sendBtn = candidates[k]; break; }
            }
          }
          if (!sendBtn) return JSON.stringify({ok: false, error: 'no_send'});
          if (sendBtn.disabled || (sendBtn.className || '').indexOf('disabled') >= 0) {
            var formControl = editor
              && (editor.tagName === 'TEXTAREA' || editor.tagName === 'INPUT');
            var editorText = editor
              ? (formControl ? editor.value : (editor.innerText || editor.textContent || ''))
              : '';
            if (!editorText.trim()) {
              return JSON.stringify({ok: false, error: 'send_button_disabled_empty_editor'});
            }
            sendBtn.disabled = false;
            sendBtn.removeAttribute('disabled');
            if (typeof sendBtn.className === 'string') {
              sendBtn.className = sendBtn.className.replace(/\\bdisabled\\b/g, '').trim();
            }
          }

          var rect = sendBtn.getBoundingClientRect();
          return JSON.stringify({
            ok: true,
            step: 'send_button_found',
            disabled: !!sendBtn.disabled,
            x: rect.left + rect.width / 2,
            y: rect.top + rect.height / 2
          });
        })()
        """
        try:
            result = self.cdp.evaluate(js)
            data = json.loads(result.get("result", {}).get("value", "{}"))
            if not data.get("ok"):
                return data
            if data.get("disabled"):
                return {"ok": False, "error": "send_disabled"}

            try:
                self.cdp.send("Page.bringToFront")
            except Exception:
                pass
            focus_js = """
            (function(){
              var editor = document.querySelector(
                '.startchat-dialog textarea, .dialog-wrap.startchat-dialog textarea, .chat-input, [contenteditable="true"]'
              );
              if (!editor) return JSON.stringify({ok: false, error: 'no_editor'});
              editor.focus();
              var formControl = editor.tagName === 'TEXTAREA' || editor.tagName === 'INPUT';
              var text = formControl ? editor.value : (editor.innerText || editor.textContent || '');
              return JSON.stringify({ok: true, len: text.length, formControl: formControl});
            })()
            """
            focus_result = self.cdp.evaluate(focus_js)
            focus_data = json.loads(focus_result.get("result", {}).get("value", "{}"))
            if not focus_data.get("ok"):
                return focus_data

            if focus_data.get("formControl"):
                self._click_at(data["x"], data["y"])
                time.sleep(1.5)
                return {
                    "ok": True,
                    "step": "clicked_send_button",
                    "x": data["x"],
                    "y": data["y"],
                }

            self.cdp.send(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyDown",
                    "key": "Enter",
                    "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                    "nativeVirtualKeyCode": 13,
                    "unmodifiedText": "\r",
                    "text": "\r",
                },
            )
            self.cdp.send(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyUp",
                    "key": "Enter",
                    "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                    "nativeVirtualKeyCode": 13,
                },
            )
            time.sleep(1.0)
            after_enter_js = """
            (function(){
              var editor = document.querySelector('.chat-input, [contenteditable="true"]');
              var text = editor ? (editor.innerText || editor.textContent || '') : '';
              return JSON.stringify({ok: true, editorLen: text.length});
            })()
            """
            after_enter_result = self.cdp.evaluate(after_enter_js)
            after_enter = json.loads(after_enter_result.get("result", {}).get("value", "{}"))
            if after_enter.get("ok") and after_enter.get("editorLen", 0) == 0:
                return {"ok": True, "step": "pressed_enter_send"}

            x = data["x"]
            y = data["y"]
            self._click_at(x, y)
            time.sleep(1.5)
            return {"ok": True, "step": "enter_then_clicked_send", "x": x, "y": y}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def verify_delivery(self, message: str) -> dict[str, Any]:
        """Verify an exact personalized greeting in the active bound conversation."""
        self._ensure_connected()
        expected_job_id = str(getattr(self, "_boss_expected_job_id", "") or "")
        bound_chat_job_id = str(getattr(self, "_boss_bound_chat_job_id", "") or "")
        js = f"""
        (function(){{
          var fullMessage = {json.dumps(message)};
          var expectedJobId = {json.dumps(expected_job_id)};
          var trustedBoundJobId = {json.dumps(bound_chat_job_id)};
          function normalizeText(value) {{
            return String(value || '').replace(/\\s+/g, ' ').trim();
          }}
          function isVisible(el) {{
            if (!el) return false;
            var style = window.getComputedStyle(el);
            var rect = el.getBoundingClientRect();
            return style.display !== 'none'
              && style.visibility !== 'hidden'
              && Number(style.opacity || 1) > 0
              && rect.width > 0
              && rect.height > 0;
          }}
          function firstVisible(selector, root) {{
            var nodes = (root || document).querySelectorAll(selector);
            for (var i = 0; i < nodes.length; i++) {{
              if (isVisible(nodes[i])) return nodes[i];
            }}
            return null;
          }}
          function activeConversationScope(editor) {{
            var modal = firstVisible(
              '.startchat-dialog, .dialog-wrap.startchat-dialog'
            );
            if (modal && (!editor || modal.contains(editor))) return modal;
            var transcriptSelector = [
              '.message-list .message-item',
              '.chat-record .message-item',
              '.chat-message .message-item',
              '.im-list .message-item'
            ].join(', ');
            var conversation = editor && editor.closest
              ? editor.closest('.chat-conversation')
              : null;
            if (conversation
                && conversation.querySelector(transcriptSelector)) {{
              return conversation;
            }}
            var node = editor;
            while (node && node.tagName !== 'BODY') {{
              if (node.querySelector
                  && node.querySelector(transcriptSelector)) {{
                return node;
              }}
              node = node.parentElement;
            }}
            return null;
          }}

          var editor = firstVisible(
            '.startchat-dialog textarea, .dialog-wrap.startchat-dialog textarea, '
            + '.chat-input, [contenteditable="true"]'
          );
          var formControl = editor
            && (editor.tagName === 'TEXTAREA' || editor.tagName === 'INPUT');
          var editorText = editor
            ? (formControl ? editor.value : (editor.innerText || editor.textContent || ''))
            : '';
          var normalizedMessage = normalizeText(fullMessage);
          var stillInEditor = !!normalizedMessage
            && normalizeText(editorText) === normalizedMessage;
          var pathMatch = (location.pathname || '').match(
            /\\/job_detail\\/([^/]+?)(?:\\.html)?$/
          );
          var pathJobId = pathMatch
            ? pathMatch[1].replace(/\\.html$/, '')
            : '';
          var queryJobId = '';
          try {{
            queryJobId = new URL(location.href).searchParams.get('jobId') || '';
          }} catch (e) {{}}
          var activeJobId = pathJobId || queryJobId || trustedBoundJobId;
          var conversationBound = !!expectedJobId && activeJobId === expectedJobId;
          var scope = activeConversationScope(editor);
          var items = scope
            ? Array.prototype.slice.call(
                scope.querySelectorAll(
                  '.message-list .message-item, .chat-record .message-item, '
                  + '.chat-message .message-item, .im-list .message-item'
                )
              ).filter(isVisible)
            : [];
          var personalizedExact = false;
          var statusBound = false;
          for (var itemIndex = 0; itemIndex < items.length; itemIndex++) {{
            var item = items[itemIndex];
            var outgoing = item.matches(
              '.item-myself, [class*="item-myself"], '
              + '[class*="message-mine"], [class*="message-self"], '
              + '[class*="outgoing"]'
            );
            if (!outgoing) continue;
            // Prefer the leaf text node. The surrounding ``.text`` container
            // also contains Boss's status icon, whose accessible text (for
            // example "送达") is rendered before the greeting.
            var contentNode = item.querySelector(
              '.text-content, [class~="text-content"]'
            ) || item.querySelector(
              '.text, [class~="text"], [class*="message-content"], '
              + '[class*="messageContent"]'
            );
            var normalizedContent = contentNode
              ? normalizeText(contentNode.innerText || contentNode.textContent || '')
              : '';
            if (!normalizedContent) {{
              var clone = item.cloneNode(true);
              var metadata = clone.querySelectorAll(
                '.status, [class~="status"], [class*="status"], time, '
                + '[class*="time"]'
              );
              for (var metadataIndex = 0; metadataIndex < metadata.length; metadataIndex++) {{
                metadata[metadataIndex].remove();
              }}
              normalizedContent = normalizeText(
                clone.innerText || clone.textContent || ''
              );
            }}
            if (normalizedContent === normalizedMessage) {{
              personalizedExact = true;
              var statusNodes = Array.prototype.slice.call(
                item.querySelectorAll(
                  '.status, [class~="status"], [class*="status"]'
                )
              );
              var statusText = statusNodes.map(function(node) {{
                return node.innerText || node.textContent || '';
              }}).join(' ');
              statusBound = statusNodes.some(function(node) {{
                return /(^|\\s)status-(delivery|delivered|read|sent)(\\s|$)/.test(
                  String(node.className || '')
                );
              }}) || /\\[?送达\\]?|已送达|\\[?已读\\]?|已发送/.test(
                  normalizeText(statusText)
                );
              if (statusBound) break;
            }}
          }}
          var delivered = conversationBound
            && personalizedExact
            && statusBound
            && !stillInEditor;
          return JSON.stringify({{
            ok: true,
            delivered: delivered,
            stillInEditor,
            hasMsg: personalizedExact,
            hasDeliveredNearMsg: statusBound,
            personalizedExact,
            conversationBound,
            statusBound,
            scopeFound: !!scope,
            messageCount: items.length,
            editorLen: editorText.length
          }});
        }})()
        """
        try:
            result = self.cdp.evaluate(js)
            return json.loads(result.get("result", {}).get("value", "{}"))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def recover_draft_delivery(self, message: str) -> dict[str, Any]:
        """Send an exact draft already present in the active bound conversation."""
        self._ensure_connected(
            platform="boss",
            initial_url="https://www.zhipin.com/web/geek/chat",
        )
        expected_job_id = str(getattr(self, "_boss_expected_job_id", "") or "")
        bound_chat_job_id = str(getattr(self, "_boss_bound_chat_job_id", "") or "")
        try:
            editor_js = f"""
            (function(){{
              var fullMessage = {json.dumps(message)};
              var expectedJobId = {json.dumps(expected_job_id)};
              var trustedBoundJobId = {json.dumps(bound_chat_job_id)};
              function normalizeText(value) {{
                return String(value || '').replace(/\\s+/g, ' ').trim();
              }}
              function isVisible(el) {{
                if (!el) return false;
                var style = window.getComputedStyle(el);
                var rect = el.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && Number(style.opacity || 1) > 0
                  && rect.width > 0
                  && rect.height > 0;
              }}
              var editor = Array.prototype.slice.call(document.querySelectorAll(
                '.startchat-dialog textarea, .dialog-wrap.startchat-dialog textarea, '
                + '.chat-input, [contenteditable="true"]'
              )).find(isVisible);
              var loginDialog = Array.prototype.slice.call(document.querySelectorAll(
                '.sign-content, .login-dialog, .passport-login-container, '
                + '.dialog-wrap .sign-form'
              )).some(isVisible);
              var pathMatch = (location.pathname || '').match(
                /\\/job_detail\\/([^/]+?)(?:\\.html)?$/
              );
              var pathJobId = pathMatch
                ? pathMatch[1].replace(/\\.html$/, '')
                : '';
              var queryJobId = '';
              try {{
                queryJobId = new URL(location.href).searchParams.get('jobId') || '';
              }} catch (e) {{}}
              var activeJobId = pathJobId || queryJobId || trustedBoundJobId;
              var conversationBound = !!expectedJobId
                && activeJobId === expectedJobId;
              var editorText = editor
                ? ((editor.tagName === 'TEXTAREA' || editor.tagName === 'INPUT')
                    ? editor.value
                    : (editor.innerText || editor.textContent || ''))
                : '';
              var normalizedMessage = normalizeText(fullMessage);
              var matches = !!normalizedMessage
                && normalizeText(editorText) === normalizedMessage;
              if (editor) editor.focus();
              return JSON.stringify({{
                ok: !!editor && !loginDialog && conversationBound && matches,
                editorFound: !!editor,
                loginDialog: loginDialog,
                conversationBound: conversationBound,
                matches: matches,
                editorLen: editorText.length
              }});
            }})()
            """
            editor = json.loads(
                self.cdp.evaluate(editor_js).get("result", {}).get("value", "{}")
            )
            if editor.get("loginDialog"):
                return {
                    "ok": False,
                    "delivered": False,
                    "error": "login_required",
                    "editor": editor,
                }
            if not editor.get("editorFound"):
                return {
                    "ok": False,
                    "delivered": False,
                    "error": "draft_editor_not_found",
                    "editor": editor,
                }
            if not editor.get("conversationBound"):
                return {
                    "ok": False,
                    "delivered": False,
                    "error": "draft_conversation_not_bound",
                    "editor": editor,
                }
            if not editor.get("matches"):
                return {
                    "ok": False,
                    "delivered": False,
                    "error": "draft_editor_mismatch",
                    "editor": editor,
                }

            self.cdp.send("Input.dispatchKeyEvent", {
                "type": "keyDown",
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
                "nativeVirtualKeyCode": 13,
                "unmodifiedText": "\r",
                "text": "\r",
            })
            self.cdp.send("Input.dispatchKeyEvent", {
                "type": "keyUp",
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
                "nativeVirtualKeyCode": 13,
            })
            time.sleep(1.3)
            return self.verify_delivery(message)
        except Exception as e:
            return {"ok": False, "delivered": False, "error": str(e)}

    # ── Extra helpers ───────────────────────────────────────

    def navigate(self, url: str) -> None:
        """CDP-native page navigation."""
        self._ensure_connected_for_url(url)
        self.cdp.send("Page.navigate", {"url": url})

    def snapshot_search_page(
        self,
        url: str,
        script: str,
        *,
        wait_seconds: int = 4,
        timeout: int = 8,
        poll_interval: float = 1.0,
    ) -> dict[str, Any]:
        """Wait for a visible search outcome without repeatedly reloading the page."""
        current_url = self._ensure_connected_for_url(url)
        try:
            if not self._same_search_url(current_url, url):
                self.cdp.send("Page.navigate", {"url": url})

            max_wait = max(0.0, float(wait_seconds))
            interval = max(0.1, float(poll_interval))
            attempts = max(1, math.ceil(max_wait / interval) + 1)
            last_snapshot: dict[str, Any] = {}
            last_error = ""

            for attempt in range(attempts):
                try:
                    result = self.cdp.evaluate(script, timeout=max(1, timeout))
                    last_snapshot = self._decode_snapshot_result(result)
                    if self._search_snapshot_ready(last_snapshot):
                        return last_snapshot
                    last_error = str(last_snapshot.get("error") or "")
                except Exception as exc:
                    last_error = str(exc)
                if attempt + 1 < attempts:
                    time.sleep(interval)

            return {
                "ok": False,
                "error": "search_page_load_timeout",
                "url": str(last_snapshot.get("url") or url),
                "title": str(last_snapshot.get("title") or ""),
                "readyState": str(last_snapshot.get("readyState") or ""),
                "candidateCount": int(last_snapshot.get("candidateCount") or 0),
                "cardCount": int(last_snapshot.get("cardCount") or 0),
                "waitedSeconds": max_wait,
                "lastError": last_error,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def api_fetch(self, path: str, method: str = "GET", body: str | None = None) -> Any:
        """Execute a fetch() inside Chrome and return JSON.

        This is the CDP equivalent of AppleScript XHR — all requests
        carry the real Chrome TLS fingerprint and cookies automatically.
        """
        url = path if path.startswith("http") else f"https://www.zhipin.com{path}"
        target_platform = platform_for_url(url) or self.platform or "boss"
        # After Chrome restarts the first CDP target is often ``about:blank``.
        # Connect it to a normal same-origin page before running fetch(); using
        # the API URL as the initial tab can race navigation and produce a
        # misleading cross-origin ``Failed to fetch`` result.
        self._ensure_connected_for_url(default_url_for_platform(target_platform))
        fetch_opts: dict[str, Any] = {"credentials": "include"}
        if method != "GET":
            fetch_opts["method"] = method
            if body:
                fetch_opts["body"] = body

        url_json = json.dumps(url)
        opts_json = json.dumps(fetch_opts)
        expression = (
            f"(async()=>{{try{{const r=await fetch({url_json},{opts_json});"
            f"if(!r.ok)throw new Error('HTTP '+r.status);"
            f"const t=await r.text();return JSON.parse(t)}}"
            f"catch(e){{return{{__error:e.message}}}}}})()"
        )
        last_error = ""
        for attempt in range(3):
            try:
                result = self.cdp.evaluate(expression, await_promise=True)
                value = result.get("result", {}).get("value")
                if not value:
                    last_error = f"API 请求无返回: {path}"
                elif isinstance(value, dict) and value.get("__error"):
                    last_error = f"API 请求失败: {value['__error']} ({path})"
                    if "Failed to fetch" not in str(value["__error"]):
                        raise RuntimeError(last_error)
                else:
                    return value
            except RuntimeError:
                raise
            except Exception as exc:
                last_error = str(exc)
            if attempt < 2:
                time.sleep(0.5)
        raise RuntimeError(last_error or f"API 请求失败: {path}")

    # ── Passive login guide ─────────────────────────────────

    def check_login_status(self) -> bool:
        """Check if user is logged in to Boss直聘 via CDP.

        Prefer the user info API. If the request itself is unavailable during
        Chrome startup, accept the visible authenticated navigation as a
        fallback instead of sending an already-logged-in user back to login.
        """
        try:
            result = self.api_fetch("/wapi/zpuser/wap/getUserInfo.json")
            if isinstance(result, dict) and result.get("code") == 0:
                return True
            if isinstance(result, dict) and "code" in result:
                return False
        except Exception:
            pass
        page = self.inspect_page()
        return bool(
            page.get("ok")
            and page.get("userNav")
            and page.get("geekNav")
            and not page.get("loginDialog")
            and not page.get("qrLoginDialog")
        )

    def ensure_logged_in(
        self,
        timeout: int = 300,
        poll_interval: int = 3,
        on_waiting: Any = None,
    ) -> bool:
        """Passive login guide — auto-open Chrome → login page → poll → continue.

        Designed for agent-driven workflows: no terminal UI, silent polling.
        The caller (agent) is responsible for notifying the human user.

        Args:
            timeout: Max seconds to wait for login.
            poll_interval: Seconds between login checks.
            on_waiting: Optional callback called once when entering wait state.
                        Signature: on_waiting(chrome_visible: bool) -> None

        Returns:
            True if login succeeded (or was already active).
            False if timeout exceeded.
        """
        if self.check_login_status():
            return True

        self._ensure_connected(platform="boss", initial_url="https://www.zhipin.com/web/user/?ka=header-login")

        # Navigate to BOSS login page
        self.cdp.send("Page.navigate", {
            "url": "https://www.zhipin.com/web/user/?ka=header-login"
        })
        time.sleep(0.5)
        try:
            self.cdp.send("Page.bringToFront")
            self.cdp.evaluate(
                "(function(){if(!document.title.startsWith('[Job Agent] '))"
                "document.title='[Job Agent] '+document.title;return document.title})()"
            )
        except Exception:
            pass

        # Notify the caller (agent) that Chrome is open and waiting
        if callable(on_waiting):
            on_waiting(True)

        start = time.time()
        while time.time() - start < timeout:
            if self.check_login_status():
                return True
            time.sleep(poll_interval)

        return False
