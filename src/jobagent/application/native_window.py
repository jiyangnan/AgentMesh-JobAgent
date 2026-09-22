"""Window identity evidence for native hosts; never controls the host UI."""

from __future__ import annotations

from typing import Any

from jobagent.infra.browser_work import BrowserWorkError

APP_SCOPED = "app_scoped_window"
KINDS = ("native_window_id", "host_window_handle", APP_SCOPED)
REFERENCE = ("Actual host window ID/handle when exposed. For app_scoped_window, use the actual "
             "native app reference accepted by the host; this identifies an app, not a persistent window. "
             "Never use a page title or snapshot element index as a stable identifier.")
CONTEXT_SCHEMA = {
    "app_reference": "exact actual host app reference in window_reference",
    "window_title": "current observed window title; diagnostic only, may change between pages",
    "selection_verified": "boolean true only after native UI verification of the selected target window",
    "selection_evidence": "actual fresh native observation explaining which window is selected and why it is the intended profile/task context; do not infer uniqueness from missing window IDs",
}
BEFORE_ACTION = (
    "Before EACH native UI action, read fresh native app/window state and use fresh indices/coordinates. "
    "To identify the target, perform only task-permitted preparation: native window/tab selection or "
    "navigation to the task's declared official URL, then inspect the resulting state again. These "
    "preparation actions do not require an already-visible platform page/account. Before collecting, "
    "inspecting job details or receipts, or any recruiting action, verify the selected target window, "
    "bound profile, official task page and bound platform account when present. "
    "In app_scoped_window mode the app reference does not pin a physical window: "
    "use native window selection or the window menu to resolve multiple windows, then inspect the "
    "selected window again. Recheck after foreground changes or interruptions. Do not assume one "
    "window because the host omits window IDs or a window list. If the target remains ambiguous, "
    "pause without acting. A receipt checked after a click cannot replace these pre-action checks."
)
BIND_INSTRUCTION = (
    "Verify native Computer Use is callable and app access is allowed. Missing window IDs alone do "
    "not mean Computer Use is unavailable. Inspect existing Chrome windows and reuse the intended "
    "profile/task window when identifiable. Use native_window_id or host_window_handle if actually "
    "exposed; otherwise use app_scoped_window with the host's actual app reference and fresh "
    "window_context selection evidence. This latter mode binds a verified profile/account context, "
    "not a persistent physical window. A changed page title or foreground tab alone is inconclusive. "
    "Only create a window if native inspection proves none is reusable. Do not copy cookies, clear "
    "profiles, close unrelated tabs or ask the customer to diagnose native tools. " + BEFORE_ACTION
)


def context_example(reference: str) -> dict[str, Any]:
    return {"app_reference": reference, "window_title": "Replace with the currently observed window title",
            "selection_verified": True,
            "selection_evidence": "Replace with fresh native evidence identifying the selected target window/profile"}


def binding_task() -> dict[str, Any]:
    evidence = {"native_computer_use_available": "boolean true only for actual callable native UI capability",
                "browser": "chrome", "window_reference": REFERENCE,
                "window_reference_kind": "|".join(KINDS),
                "window_context": {"required_for": APP_SCOPED, **CONTEXT_SCHEMA},
                "profile_label": "actual observed Chrome profile label",
                "group_reference": "actual task tab/group reference; a native Chrome tab group is not required",
                "reuse_status": "reused|created_no_existing"}
    base = {"outcome": "success", "evidence": {
        "native_computer_use_available": True, "browser": "chrome", "reuse_status": "reused",
        "window_reference": "Replace with the actual host window ID or handle",
        "window_reference_kind": "native_window_id",
        "profile_label": "Replace with the actual observed profile",
        "group_reference": "Replace with the actual task tab/group reference"}}
    app_reference = "Replace with the actual host native app reference"
    app = {"outcome": "success", "evidence": {**base["evidence"],
           "window_reference": app_reference, "window_reference_kind": APP_SCOPED,
           "window_context": context_example(app_reference)}}
    return {"instruction": BIND_INSTRUCTION,
            "required_evidence": ["native_computer_use_available=true", "browser=chrome", "window_reference",
                                  "window_reference_kind", "profile_label", "group_reference", "observation",
                                  "reuse_status=reused|created_no_existing"],
            "result_schema": {"evidence": evidence}, "result_example": base,
            "result_examples": {"native_window": base, APP_SCOPED: app}}


def validate(evidence: dict[str, Any], *, expected_kind: str | None = None,
             expected_reference: str | None = None, require_kind: bool = False) -> None:
    kind = evidence.get("window_reference_kind")
    if kind not in KINDS and (kind is not None or require_kind or expected_kind == APP_SCOPED):
        raise BrowserWorkError("native_window_reference_invalid", "Use an actual supported native window reference kind.")
    if expected_kind == APP_SCOPED and kind != APP_SCOPED:
        raise BrowserWorkError("native_window_context_required", "App-scoped work requires fresh window selection evidence; do not downgrade it to a legacy reference.")
    if (expected_reference is not None and kind is not None and kind != expected_kind
            and (expected_kind is not None or kind == APP_SCOPED)):
        raise BrowserWorkError("native_window_reference_invalid", "The bound reference scope cannot change inside an ordinary task; preserve the session and use explicit recovery.")
    if kind != APP_SCOPED:
        return
    context = evidence.get("window_context")
    reference = evidence.get("window_reference")
    if (not isinstance(context, dict) or not isinstance(reference, str) or not reference.strip()
            or context.get("app_reference") != reference
            or (expected_reference is not None and reference != expected_reference)
            or context.get("selection_verified") is not True
            or not all(isinstance(context.get(key), str) and context[key].strip()
                       for key in ("window_title", "selection_evidence"))):
        raise BrowserWorkError("native_window_context_required",
                               "Verify the actual app reference and freshly selected native window before continuing; a title alone is not window identity.")
