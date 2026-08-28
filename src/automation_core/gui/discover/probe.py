"""
Element probing for the discovery tools.

`ui_element_inspector.py` (hover, one control at a time) and
`ui_tree_snapshot.py` (headless, whole window at once) both need the same
four things, so they live here rather than in either tool:

  1. UIA metadata for an element, JSON-serialisable.
  2. The Win32 view of the same screen point, so a control is never
     described through one backend's assumptions alone.
  3. Candidate selectors, each one actually executed so we know how many
     elements it really matches, and each graded for how stable its
     anchor is. A selector that resolves to 0 or 7 elements is not a
     selector; nor is one that resolves to 1 by luck.
  4. A recommendation, with its reasoning, of how to address the control.

Point 3 is the one that matters most. The April 2026 spike failed three
times on `AttributeError: 'UIAWrapper' object has no attribute
'child_window'` and never resolved a single control, so every selector it
recorded below the login dialog came from the Automation Anywhere bot's
logic and was never tested. Proving a selector at capture time is how
that does not happen again.

What measurement showed about Energy Manager
--------------------------------------------
The expectation from the April spike's `cmbNames`/`cmdOk` naming was a VB6
application best driven through Win32 class and control id. Measuring it
says otherwise, and the difference matters enough to record here:

  - EM is .NET WinForms (`framework_id` "WinForm"), and its controls carry
    real designer names: `frmMain`, `frmWeb`, `cmdUpload`, `txtEmail`.
  - The win32 backend reads those names through WM_GETCONTROLNAME, and the
    full CLR type through WM_GETCONTROLTYPE, so a plain Win32 probe reports
    `System.Windows.Forms.Button` named `cmdUpload`. That is richer than
    what UIA exposes for the same control, and far faster: 77 controls in
    42ms against UIA's 6.7 seconds, with 71.4% carrying a designer name
    against UIA's 24.2%.
  - So for this application win32 is both faster and more complete. That
    is the opposite of the usual "UIA for anything modern" advice, which
    is exactly why the tool measures instead of assuming.
  - Three things must never be used as selectors here. WinForms class names
    end in a per-process suffix (`...app.0.1a0e24_r7_ad1`) that changes on
    rebuild. WinForms `control_id` values are derived from the window
    handle, so `tsMain` reports 2297934 on one run and something else on
    the next. And a designer name is only unique within its own form: EM
    has three separate `tsMain` toolbars across its MDI child forms, so a
    selector has to be scoped to the owning form rather than to the main
    window. All of these resolve uniquely in the right circumstances and
    are still wrong. The stability grading below exists to stop any of them
    being written down.
  - Visible does not mean on screen. EM's `frmWeb` reports
    IsWindowVisible=True and IsIconic=False while its rectangle sits at
    (-31950, -31803), because an MDI child can be scrolled or parked
    outside the client area. Its controls resolve normally and report
    themselves visible. A bot that calls `click_input()` on one of them
    moves the physical mouse to nowhere and clicks nothing, with no error.
    Check the rectangle intersects the desktop, or use `click()`, which
    posts a message instead of moving the mouse.

Windows only. Needs pywinauto and pywin32.
"""

from __future__ import annotations

import ctypes
import json
import re
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import win32con
import win32gui
import win32process
from pywinauto import findwindows

from ..keys import to_send_keys  # noqa: F401  (re-exported for callers)
from pywinauto.uia_element_info import UIAElementInfo
from pywinauto.win32_element_info import HwndElementInfo

# A window as the pickers deal with it: handle, title, owning process id.
WindowRow = tuple[int, str, "int | None"]

# Captures land under the working directory. Deliberately not derived from
# this file's location: inside site-packages that would be wrong and
# probably unwritable.
CAPTURE_DIR = Path.cwd() / "captures"

# GetAncestor(hwnd, GA_ROOT) walks up to the top-level window that owns a
# control. That window is the anchor a bot re-finds the control from, so it
# is the parent every selector below is verified against.
_GA_ROOT = 2

MAX_ANCESTOR_DEPTH = 25

# WinForms class names carry a per-process suffix: the Energy Manager email
# field is a "WindowsForms10.EDIT.app.0.1a0e24_r7_ad1". The tail encodes the
# assembly version and an instance counter, so it changes when the app is
# rebuilt and sometimes between runs. Selecting on the literal string is a
# trap; matching the stable head with class_name_re is not.
_VOLATILE_CLASS_RE = re.compile(
    r"^(?P<stable>WindowsForms\d+\..*?\.app\.0\.)[0-9A-Fa-f]+(_r\d+)?(_ad\d+)?$"
)


def is_volatile_class_name(class_name: Any) -> bool:
    """True for a class name with a per-process suffix, as WinForms uses."""
    if not class_name:
        return False
    return bool(_VOLATILE_CLASS_RE.match(str(class_name)))


def class_name_regex(class_name: Any) -> str | None:
    """A regex matching a volatile class name's stable head, or None.

    'WindowsForms10.BUTTON.app.0.1a0e24_r7_ad1' becomes
    '^WindowsForms10\\.BUTTON\\.app\\.0\\..*$', which survives a rebuild.
    """
    if not class_name:
        return None
    match = _VOLATILE_CLASS_RE.match(str(class_name))
    if match is None:
        return None
    return f"^{re.escape(match.group('stable'))}.*$"


# ---------------------------------------------------------------------------
# DPI awareness. Must run before any windowing call so that the coordinates
# UI Automation reports line up with what a GUI toolkit thinks the screen
# looks like. Without it a highlight sits offset from the real control on any
# scaled display, which is most of our estate.
# ---------------------------------------------------------------------------
def set_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def safe(source: Callable[[], Any] | Any, default: Any = None) -> Any:
    """Read a property that may throw, returning default if it does.

    Broad by design: the underlying COM calls raise a variety of errors for
    a control that has just been destroyed or repainted, and one missing
    property is never a reason to abandon a capture.
    """
    try:
        return source() if callable(source) else source
    except Exception:
        return default


def _rect_dict(rect: Any) -> dict[str, Any] | None:
    if rect is None:
        return None
    try:
        return {
            "left": rect.left,
            "top": rect.top,
            "right": rect.right,
            "bottom": rect.bottom,
            "width": rect.width(),
            "height": rect.height(),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Window enumeration and selection. Shared by both discovery tools.
#
# EnumWindows rather than pywinauto's Desktop(...).windows(): it needs no COM,
# runs in about a millisecond instead of a second, and reliably includes modal
# dialogs, which matters because Energy Manager shows its "Enter Password" box
# before the main shell exists at all.
# ---------------------------------------------------------------------------
def list_windows(visible_only: bool = True) -> list[WindowRow]:
    """Visible top-level windows that have a title."""
    rows: list[WindowRow] = []

    def callback(hwnd: int, _extra: object) -> None:
        try:
            if visible_only and not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd) or ""
            if not title.strip():
                return
            try:
                _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                pid = None
            rows.append((hwnd, title, pid))
        except Exception:
            return

    try:
        win32gui.EnumWindows(callback, None)
    except Exception as exc:
        print(f"  (Window enumeration failed: {exc})")
    return rows


def find_window_by_title(rows: list[WindowRow], needle: str) -> WindowRow | None:
    """First window whose title contains needle, case-insensitively."""
    lowered = needle.lower()
    for row in rows:
        if lowered in row[1].lower():
            return row
    return None


def choose_window(rows: list[WindowRow]) -> WindowRow:
    """Print the windows and read a choice from stdin. Exits on q."""
    print("\nOpen windows:")
    print("-" * 78)
    for index, (handle, title, pid) in enumerate(rows, 1):
        shown = (title[:56] + "...") if len(title) > 59 else title
        print(f"  [{index:>2}] {shown:<60} pid={pid} hwnd={handle}")
    print("-" * 78)
    while True:
        choice = input(f"\nSelect a window [1-{len(rows)}] (q to quit): ").strip()
        if choice.lower() == "q":
            raise SystemExit(0)
        if choice.isdigit() and 1 <= int(choice) <= len(rows):
            return rows[int(choice) - 1]
        print("Invalid choice, try again.")


def bring_to_front(handle: int) -> None:
    """Restore and foreground a window.

    The stray Alt press is the documented workaround for SetForegroundWindow
    refusing to work when the calling process does not own the foreground
    window, which is our situation every single time.
    """
    try:
        if win32gui.IsIconic(handle):
            win32gui.ShowWindow(handle, win32con.SW_RESTORE)
        try:
            ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)  # Alt down
            ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)  # Alt up
        except (AttributeError, OSError):
            pass
        win32gui.SetForegroundWindow(handle)
    except Exception as exc:
        print(f"  (Warning: could not bring the window to the front: {exc})")
    time.sleep(0.4)


# ---------------------------------------------------------------------------
# Win32 side of the probe
# ---------------------------------------------------------------------------
class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _real_child_window_from_point(parent: int, client_x: int, client_y: int) -> int:
    """RealChildWindowFromPoint, which pywin32 does not wrap.

    Unlike ChildWindowFromPointEx this reports the control a user would say
    is at that point: it does not descend into a group box just because the
    group box happens to sit underneath.
    """
    user32 = ctypes.windll.user32
    user32.RealChildWindowFromPoint.argtypes = [wintypes.HWND, _POINT]
    user32.RealChildWindowFromPoint.restype = wintypes.HWND
    return user32.RealChildWindowFromPoint(parent, _POINT(client_x, client_y)) or 0


def deepest_hwnd_at_point(screen_x: int, screen_y: int, max_depth: int = 12) -> int:
    """The innermost real child window at a screen point.

    WindowFromPoint alone stops at the top-level window for owner-drawn and
    container controls, which is exactly the case in an old VB app, so we
    drill down until the answer stops changing.
    """
    try:
        hwnd = win32gui.WindowFromPoint((screen_x, screen_y))
    except Exception:
        return 0
    if not hwnd:
        return 0

    for _ in range(max_depth):
        try:
            client_x, client_y = win32gui.ScreenToClient(hwnd, (screen_x, screen_y))
        except Exception:
            break
        child = _real_child_window_from_point(hwnd, client_x, client_y)
        if not child or child == hwnd:
            break
        hwnd = child
    return hwnd


def top_level_handle(hwnd: int) -> int:
    """The top-level window owning hwnd, or hwnd itself."""
    if not hwnd:
        return 0
    try:
        root = ctypes.windll.user32.GetAncestor(hwnd, _GA_ROOT)
        return int(root) if root else hwnd
    except (AttributeError, OSError):
        return hwnd


def win32_metadata(hwnd: int) -> dict[str, Any]:
    """The Win32 view of a control: class, text, control id, ancestry."""
    if not hwnd:
        return {"handle": None, "available": False}

    meta: dict[str, Any] = {
        "handle": hwnd,
        "available": True,
        "class_name": safe(lambda: win32gui.GetClassName(hwnd)),
        "window_text": safe(lambda: win32gui.GetWindowText(hwnd)),
        # The dialog control id. Stable across runs in a VB or WinForms
        # dialog, and the single most useful Win32 selector when UIA
        # reports no automation_id.
        "control_id": safe(lambda: win32gui.GetDlgCtrlID(hwnd)),
        "visible": safe(lambda: bool(win32gui.IsWindowVisible(hwnd))),
        "enabled": safe(lambda: bool(win32gui.IsWindowEnabled(hwnd))),
        "rectangle": safe(lambda: dict(zip(("left", "top", "right", "bottom"),
                                           win32gui.GetWindowRect(hwnd)))),
    }

    # pywinauto's win32 backend asks a WinForms control for its designer name
    # with WM_GETCONTROLNAME, which is how `cmdUpload` and `txtEmail` come
    # back from a plain Win32 probe. That name is the closest thing an old
    # .NET app has to a deliberate automation id, so it belongs in the probe
    # rather than only in a tree walk.
    hwnd_info = safe(lambda: HwndElementInfo(hwnd))
    if hwnd_info is not None:
        meta["automation_id"] = safe(lambda: hwnd_info.automation_id) or None
        meta["framework_id"] = safe(lambda: hwnd_info.framework_id) or None
        meta["control_type"] = safe(lambda: hwnd_info.control_type) or None

    meta["class_name_is_volatile"] = is_volatile_class_name(meta.get("class_name"))
    meta["class_name_re"] = class_name_regex(meta.get("class_name"))

    ancestors: list[dict[str, Any]] = []
    parent = safe(lambda: win32gui.GetParent(hwnd)) or 0
    depth = 0
    while parent and depth < MAX_ANCESTOR_DEPTH:
        ancestors.append(
            {
                "handle": parent,
                "class_name": safe(lambda p=parent: win32gui.GetClassName(p)),
                "window_text": safe(lambda p=parent: win32gui.GetWindowText(p)),
                "control_id": safe(lambda p=parent: win32gui.GetDlgCtrlID(p)),
            }
        )
        parent = safe(lambda p=parent: win32gui.GetParent(p)) or 0
        depth += 1

    meta["ancestor_path"] = list(reversed(ancestors))
    meta["top_level_handle"] = top_level_handle(hwnd)
    return meta


# ---------------------------------------------------------------------------
# UIA side of the probe
# ---------------------------------------------------------------------------
def hotkeys_for(info: Any) -> dict[str, Any]:
    """Any keyboard shortcut UI Automation advertises for an element.

    UIA carries two separate properties and applications use them
    inconsistently, so read both:

      AccessKey       what Energy Manager puts its ribbon shortcuts in.
                      The Web Extensions button reports 'Ctrl+Alt+1', which
                      is exactly what its tooltip shows.
      AcceleratorKey  the more conventional home for a shortcut. Empty
                      throughout EM, but populated in plenty of other apps.

    A hotkey is the most robust way to invoke a command there is. It needs no
    selector, no backend, no traversal of a drawn DevExpress ribbon, and it
    does not care which tab is currently active or where the window sits on
    screen. Where one exists, prefer it to clicking. They are patchy though:
    most EM ribbon buttons have none, so this never replaces selectors, it
    just beats them when available.

    Win32 elements have no equivalent, so this returns empty for them.
    """
    element = safe(lambda: info.element)
    if element is None:
        return {"access_key": None, "accelerator_key": None, "help_text": None}
    return {
        "access_key": safe(lambda: element.CurrentAccessKey) or None,
        "accelerator_key": safe(lambda: element.CurrentAcceleratorKey) or None,
        "help_text": safe(lambda: element.CurrentHelpText) or None,
    }


def element_metadata(element: Any) -> dict[str, Any]:
    """A JSON-serialisable dict of UIA metadata for a wrapper or ElementInfo."""
    info = element.element_info if hasattr(element, "element_info") else element

    meta: dict[str, Any] = {
        "name": safe(lambda: info.name),
        "control_type": safe(lambda: info.control_type),
        "automation_id": safe(lambda: info.automation_id),
        "class_name": safe(lambda: info.class_name),
        "framework_id": safe(lambda: getattr(info, "framework_id", None)),
        "control_id": safe(lambda: getattr(info, "control_id", None)),
        "process_id": safe(lambda: info.process_id),
        "handle": safe(lambda: info.handle),
        "runtime_id": safe(lambda: list(info.runtime_id) if info.runtime_id else None),
        "rectangle": _rect_dict(safe(lambda: info.rectangle)),
        "enabled": safe(lambda: info.enabled),
        "visible": safe(lambda: info.visible),
        "rich_text": safe(lambda: getattr(info, "rich_text", None)),
    }
    meta["automation_id_is_synthesised"] = is_synthesised_auto_id(
        meta["automation_id"]
    )
    meta["hotkeys"] = hotkeys_for(info)
    meta["send_keys"] = to_send_keys(
        meta["hotkeys"]["access_key"] or meta["hotkeys"]["accelerator_key"]
    )
    meta["ancestor_path"] = ancestor_path(info)
    return meta


def ancestor_path(info: Any) -> list[dict[str, Any]]:
    """Chain of ancestors from the desktop down to the element.

    This is the part of a capture that is reusable rather than merely
    descriptive: it is the path a bot walks to find the same control again.
    """
    ancestors: list[dict[str, Any]] = []
    parent = safe(lambda: info.parent)
    depth = 0
    while parent is not None and depth < MAX_ANCESTOR_DEPTH:
        ancestors.append(
            {
                "control_type": safe(lambda p=parent: p.control_type),
                "name": safe(lambda p=parent: p.name),
                "automation_id": safe(lambda p=parent: p.automation_id),
                "class_name": safe(lambda p=parent: p.class_name),
                "handle": safe(lambda p=parent: p.handle),
            }
        )
        nxt = safe(lambda p=parent: p.parent)
        if nxt is None:
            break
        parent = nxt
        depth += 1
    return list(reversed(ancestors))


# ---------------------------------------------------------------------------
# Candidate selectors, and proving them
# ---------------------------------------------------------------------------
# How much trust a selector's anchor deserves. Uniqueness is necessary but
# not sufficient: a selector can match exactly one element today and still be
# the wrong thing to write down, because what it matches on is not stable.
STABILITY_NAMED = 3      # a name the application's author chose
STABILITY_NUMERIC = 2    # a numeric id that is stable for this framework
STABILITY_CAPTION = 1    # visible text, or a class-name pattern
STABILITY_POSITIONAL = 0 # control type alone: one layout change from wrong

STABILITY_LABELS = {
    STABILITY_NAMED: "application-assigned name",
    STABILITY_NUMERIC: "framework-stable numeric id",
    STABILITY_CAPTION: "visible caption or class pattern",
    STABILITY_POSITIONAL: "control type only",
}


def _candidate(
    criteria: dict[str, Any],
    stability: int,
    basis: str,
) -> dict[str, Any]:
    return {"criteria": criteria, "stability": stability, "basis": basis}


def _uia_candidates(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """UIA criteria, most trustworthy first."""
    auto_id = meta.get("automation_id") or None
    name = meta.get("name") or None
    ctype = meta.get("control_type") or None
    cls = meta.get("class_name") or None
    cls_re = class_name_regex(cls)
    synthesised = is_synthesised_auto_id(auto_id)

    out: list[dict[str, Any]] = []
    if auto_id:
        stability = STABILITY_NUMERIC if synthesised else STABILITY_NAMED
        basis = (
            "automation_id synthesised from the win32 control id"
            if synthesised
            else "application-assigned automation_id"
        )
        if ctype:
            out.append(
                _candidate({"auto_id": auto_id, "control_type": ctype},
                           stability, basis)
            )
        out.append(_candidate({"auto_id": auto_id}, stability, basis))
    if name:
        if ctype:
            out.append(
                _candidate({"title": name, "control_type": ctype},
                           STABILITY_CAPTION, "visible name")
            )
        out.append(_candidate({"title": name}, STABILITY_CAPTION, "visible name"))

    # Only ever offer the volatile class name as a pattern, never literally.
    if cls_re and ctype:
        out.append(
            _candidate({"class_name_re": cls_re, "control_type": ctype},
                       STABILITY_CAPTION, "class-name pattern")
        )
    elif cls and ctype and not is_volatile_class_name(cls):
        out.append(
            _candidate({"class_name": cls, "control_type": ctype},
                       STABILITY_CAPTION, "class name")
        )

    if ctype:
        out.append(
            _candidate({"control_type": ctype}, STABILITY_POSITIONAL,
                       "control type only")
        )
    return out


def _win32_candidates(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Win32 criteria, most trustworthy first.

    The WinForms designer name goes first where there is one. The control id
    is demoted hard for WinForms: `tsMain` reports control_id 2297934, which
    is derived from the window handle and therefore different on every run.
    Writing that into a bot produces something that passes once and then
    fails silently, which is worse than not resolving at all.
    """
    auto_id = meta.get("automation_id") or None
    ctrl_id = meta.get("control_id") or None
    cls = meta.get("class_name") or None
    text = meta.get("window_text") or None
    cls_re = meta.get("class_name_re") or class_name_regex(cls)
    volatile = meta.get("class_name_is_volatile")
    if volatile is None:
        volatile = is_volatile_class_name(cls)

    out: list[dict[str, Any]] = []

    if auto_id and not is_synthesised_auto_id(auto_id):
        out.append(
            _candidate({"auto_id": auto_id}, STABILITY_NAMED,
                       "WinForms designer control name")
        )
        if cls_re:
            out.append(
                _candidate({"auto_id": auto_id, "class_name_re": cls_re},
                           STABILITY_NAMED,
                           "WinForms designer control name, class pattern")
            )

    if ctrl_id:
        # A handle-derived id on a WinForms control is not an identifier.
        stability = STABILITY_POSITIONAL if volatile else STABILITY_NUMERIC
        basis = (
            "control id derived from the window handle, changes between runs"
            if volatile
            else "dialog control id"
        )
        if cls and not volatile:
            out.append(
                _candidate({"control_id": ctrl_id, "class_name": cls},
                           stability, basis)
            )
        out.append(_candidate({"control_id": ctrl_id}, stability, basis))

    if text:
        if cls_re:
            out.append(
                _candidate({"title": text, "class_name_re": cls_re},
                           STABILITY_CAPTION, "window text, class pattern")
            )
        elif cls and not volatile:
            out.append(
                _candidate({"title": text, "class_name": cls},
                           STABILITY_CAPTION, "window text and class name")
            )
        out.append(_candidate({"title": text}, STABILITY_CAPTION, "window text"))

    if cls_re:
        out.append(
            _candidate({"class_name_re": cls_re}, STABILITY_POSITIONAL,
                       "class-name pattern only")
        )
    elif cls:
        out.append(
            _candidate({"class_name": cls}, STABILITY_POSITIONAL,
                       "class name only")
        )
    return out


def _root_element_info(root_handle: int, backend: str) -> Any:
    if backend == "uia":
        return UIAElementInfo(root_handle)
    return HwndElementInfo(root_handle)


def verify_selector(
    root_handle: int,
    criteria: dict[str, Any],
    backend: str,
    visible_only: bool = True,
) -> dict[str, Any]:
    """Run a selector for real and report how many elements it matched.

    A count of 1 is the only usable answer. 0 means the criteria are wrong
    or the control is on an inactive tab; more than 1 means a bot would need
    found_index and would be one layout change away from clicking the wrong
    thing.
    """
    result: dict[str, Any] = {
        "criteria": dict(criteria),
        "backend": backend,
        "match_count": None,
        "elapsed_ms": None,
        "error": None,
    }
    started = time.perf_counter()
    try:
        parent = _root_element_info(root_handle, backend)
        found = findwindows.find_elements(
            parent=parent,
            top_level_only=False,
            visible_only=visible_only,
            backend=backend,
            **criteria,
        )
        result["match_count"] = len(found)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result


def verify_candidate(
    root_handle: int,
    candidate: dict[str, Any],
    backend: str,
    visible_only: bool = True,
) -> dict[str, Any]:
    """Verify one candidate, carrying its stability grading through."""
    proof = verify_selector(
        root_handle, candidate["criteria"], backend, visible_only=visible_only
    )
    proof["stability"] = candidate["stability"]
    proof["basis"] = candidate["basis"]
    return proof


def _criteria_kwargs(criteria: dict[str, Any]) -> str:
    parts = []
    for key, value in criteria.items():
        parts.append(f"{key}={value!r}")
    return ", ".join(parts)


def pywinauto_snippet(
    criteria: dict[str, Any],
    backend: str,
    root_title: str | None,
) -> str:
    """Copy-paste code that finds this control correctly.

    Deliberately shows the specification pattern. `child_window()` is a
    method on WindowSpecification, not on a resolved wrapper, and calling it
    on a wrapper is what killed every run of the April spike. Resolving late
    (`.wait(...)` at the point of use) is also what makes the timeouts work.

    The window title is regex-escaped, because a real title routinely
    contains characters a regex would eat: "Download (3)" and "Import [new]"
    both silently match the wrong thing, or nothing, if pasted in raw.
    """
    if root_title:
        title_arg = f"title_re={re.escape(root_title)!r}"
        hint = (
            "  # title_re is escaped for an exact match. Loosen it to a\n"
            "  # substring if the caption varies between runs.\n"
        )
    else:
        title_arg = "handle=<hwnd>"
        hint = ""
    return (
        f"{hint}"
        f'window = Desktop(backend="{backend}").window({title_arg})\n'
        f"control = window.child_window({_criteria_kwargs(criteria)})\n"
        f'control.wait("exists ready", timeout=30)'
    )


def is_synthesised_auto_id(auto_id: Any) -> bool:
    """True when an automation_id is just the Win32 dialog control id.

    UI Automation manufactures an automation_id for a classic Win32 control
    by stringifying its dialog control id, so charmap's Select button comes
    back as automation_id "103". That is stable, but it is not an
    application-assigned name like `cmdOk`, and it carries no more
    information than the win32 control_id does while costing far more to
    query. Telling the two apart is the difference between "this app was
    built to be automated" and "UIA is papering over a Win32 dialog".
    """
    if auto_id is None:
        return False
    text = str(auto_id).strip()
    return bool(text) and text.isdigit()


def recommend(
    uia_meta: dict[str, Any],
    win32_meta: dict[str, Any],
    uia_proof: list[dict[str, Any]],
    win32_proof: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pick the selector to write down, and say why.

    Two rules, in order. First, only a selector that resolved to exactly one
    element is eligible: uniqueness is the entry requirement. Second, among
    those, prefer the most stable anchor rather than the first that worked.
    That second rule is what stops the tool recommending
    `control_id=2297934` for a WinForms control, which resolves uniquely
    today and is a different number tomorrow.

    Ties between backends go to win32, which measured roughly two orders of
    magnitude faster per lookup on both applications tested and, for
    WinForms, also reports the designer control name.
    """
    eligible: list[tuple[str, dict[str, Any]]] = []
    for backend, proofs in (("uia", uia_proof), ("win32", win32_proof)):
        for proof in proofs:
            if proof.get("match_count") == 1:
                eligible.append((backend, proof))

    if not eligible:
        checked = len(uia_proof) + len(win32_proof)
        return {
            "backend": None,
            "selector": None,
            "confidence": "none",
            "stability": None,
            "basis": None,
            "reason": (
                f"None of the {checked} candidate selectors resolved to exactly "
                "one element under either backend. This control needs a "
                "different anchor: find a nearer stable ancestor in a tree "
                "snapshot and search within it, or fall back to coordinates. "
                "Do not write down a selector that matched 0 or several."
            ),
            "alternatives": [],
        }

    best_stability = max(proof["stability"] for _backend, proof in eligible)
    finalists = [
        (backend, proof)
        for backend, proof in eligible
        if proof["stability"] == best_stability
    ]
    # Prefer win32 on a tie: measurably faster, and no worse identified.
    win32_finalists = [f for f in finalists if f[0] == "win32"]
    backend, proof = (win32_finalists or finalists)[0]

    confidence = {
        STABILITY_NAMED: "high",
        STABILITY_NUMERIC: "medium",
        STABILITY_CAPTION: "low",
        STABILITY_POSITIONAL: "low",
    }[best_stability]

    reason = f"Resolves to exactly one element on {proof['basis']}."
    if best_stability == STABILITY_NAMED:
        reason += " This is the most stable anchor available."
    elif best_stability == STABILITY_NUMERIC:
        reason += (
            " Stable for this framework, but carries no meaning, so check it "
            "again after any application upgrade."
        )
    elif best_stability == STABILITY_CAPTION:
        reason += (
            " No stable identifier was available, so this depends on text or "
            "class that a UI change can alter. Expect to revisit it."
        )
    else:
        reason += (
            " This is positional only and will click the wrong control after "
            "any layout change. Treat it as a last resort and prefer anchoring "
            "on a parent container."
        )

    if len(finalists) > 1 and backend == "win32":
        reason += (
            " UIA resolves this equally well; win32 was chosen because it is"
            " far faster per lookup."
        )

    alternatives = [
        {
            "backend": alt_backend,
            "criteria": alt["criteria"],
            "stability": alt["stability"],
            "basis": alt["basis"],
            "elapsed_ms": alt["elapsed_ms"],
        }
        for alt_backend, alt in eligible
        if not (alt_backend == backend and alt["criteria"] == proof["criteria"])
    ]

    return {
        "backend": backend,
        "selector": proof["criteria"],
        "confidence": confidence,
        "stability": best_stability,
        "stability_label": STABILITY_LABELS[best_stability],
        "basis": proof["basis"],
        "elapsed_ms": proof["elapsed_ms"],
        "reason": reason,
        "alternatives": alternatives,
    }


def capture_at_point(
    screen_x: int,
    screen_y: int,
    uia_element: Any,
    root_title: str | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    """Full dual-backend capture of whatever sits at a screen point."""
    uia_meta = element_metadata(uia_element)
    hwnd = deepest_hwnd_at_point(screen_x, screen_y)
    w32_meta = win32_metadata(hwnd)

    root_handle = (
        w32_meta.get("top_level_handle")
        or top_level_handle(uia_meta.get("handle") or 0)
        or 0
    )
    if root_title is None and root_handle:
        root_title = safe(lambda: win32gui.GetWindowText(root_handle))

    capture: dict[str, Any] = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "cursor": {"x": screen_x, "y": screen_y},
        "root_window": {
            "handle": root_handle or None,
            "title": root_title,
            "class_name": safe(lambda: win32gui.GetClassName(root_handle))
            if root_handle
            else None,
        },
        "uia": uia_meta,
        "win32": w32_meta,
    }

    if not verify or not root_handle:
        capture["uia_selectors"] = []
        capture["win32_selectors"] = []
        capture["recommendation"] = {
            "backend": None,
            "selector": None,
            "confidence": "not_checked",
            "reason": "Selector verification was skipped.",
        }
        return capture

    uia_proof = [
        verify_candidate(root_handle, c, "uia") for c in _uia_candidates(uia_meta)
    ]
    win32_proof = [
        verify_candidate(root_handle, c, "win32") for c in _win32_candidates(w32_meta)
    ]
    capture["uia_selectors"] = uia_proof
    capture["win32_selectors"] = win32_proof

    rec = recommend(uia_meta, w32_meta, uia_proof, win32_proof)
    if rec["selector"]:
        rec["snippet"] = pywinauto_snippet(
            rec["selector"], rec["backend"], root_title
        )
    capture["recommendation"] = rec
    return capture


# ---------------------------------------------------------------------------
# Verifying selectors for a node found by a tree walk, with no cursor
# involved. This is what lets a whole window be mapped headlessly: the hover
# tool needs a human to point at each control, which is fine for one
# ambiguous case and hopeless for 1151 of them.
# ---------------------------------------------------------------------------

# Control types worth proving a selector for. Everything else in a window is
# structure or decoration: a bot never clicks a Pane, and verifying all of
# them on the UIA backend costs about 50ms each for no benefit.
INTERACTIVE_CONTROL_TYPES = frozenset(
    {
        "Button", "CheckBox", "ComboBox", "Edit", "Hyperlink", "List",
        "ListItem", "MenuItem", "RadioButton", "Slider", "Spinner", "Tab",
        "TabItem", "Table", "Text", "Tree", "TreeItem", "DataGrid",
        "DataItem", "Document", "Custom", "SplitButton", "ToolBar",
        "Window", "Pane",
    }
)

# The subset that is genuinely clickable or typeable. Used when a caller asks
# to keep the work small.
ACTIONABLE_CONTROL_TYPES = frozenset(
    {
        "Button", "CheckBox", "ComboBox", "Edit", "Hyperlink", "MenuItem",
        "RadioButton", "Slider", "Spinner", "TabItem", "TreeItem",
        "ListItem", "DataItem", "SplitButton",
    }
)


# The win32 backend has no control_type at all: HwndElementInfo returns ''
# for every node, because control type is a UI Automation concept. All it has
# is the window class, so a win32 node's kind has to be derived from that.
_WINFORMS_PREFIX_RE = re.compile(r"^WindowsForms\d+\.(?P<kind>[^.]+)\.", re.I)

_WIN32_CLASS_KINDS = {
    "button": "Button",
    "edit": "Edit",
    "richedit20w": "Edit",
    "richedit50w": "Edit",
    "combobox": "ComboBox",
    "syscombobox": "ComboBox",
    "listbox": "List",
    "syslistview32": "List",
    "systreeview32": "Tree",
    "systabcontrol32": "Tab",
    "static": "Text",
    "msctls_trackbar32": "Slider",
    "msctls_updown32": "Spinner",
    "toolbarwindow32": "ToolBar",
    "scrollbar": "ScrollBar",
    # EM's download date range uses one of these, so it must not be skipped.
    # The real Win32 class is SysDateTimePick32; the others are variants seen
    # on older common-controls versions.
    "sysdatetimepick32": "Edit",
    "datetimepick32": "Edit",
    "sysmonthcal32": "Calendar",
    "#32770": "Window",
    "window": "Pane",  # a WinForms container form or panel
}


def win32_control_kind(class_name: Any) -> str:
    """Map a Win32 window class to a UIA-style control type, or "".

    'WindowsForms10.BUTTON.app.0.1a0e24_r7_ad1' and a plain 'Button' both
    come back as 'Button', so one interactive-control set serves both
    backends.
    """
    if not class_name:
        return ""
    text = str(class_name)
    match = _WINFORMS_PREFIX_RE.match(text)
    if match:
        text = match.group("kind")
    return _WIN32_CLASS_KINDS.get(text.lower(), "")


# For a .NET application, pywinauto's win32 backend answers control_type with
# the full CLR type name (WM_GETCONTROLTYPE): Energy Manager reports
# 'System.Windows.Forms.Button' and 'DevExpress.XtraEditors.SimpleButton'.
# That is richer than UIA's vocabulary but it is not UIA's vocabulary, so it
# has to be mapped before any control-type filter can mean anything.
_DOTNET_TYPE_KINDS = {
    # Buttons
    "button": "Button", "simplebutton": "Button", "closebutton": "Button",
    "buttonedit": "Button", "splitbutton": "SplitButton",
    # Text entry
    "textbox": "Edit", "textboxmaskbox": "Edit", "mruedit": "Edit",
    "textedit": "Edit", "memoedit": "Edit", "maskedtextbox": "Edit",
    "spinedit": "Spinner", "dateedit": "Edit", "datetimepicker": "Edit",
    # Choice
    "checkbox": "CheckBox", "checkedit": "CheckBox",
    "radiobutton": "RadioButton", "radiogroup": "RadioButton",
    "combobox": "ComboBox", "comboboxedit": "ComboBox",
    "toolstripcomboboxcontrol": "ComboBox",
    "datasetgroupdropdownedit": "ComboBox",
    "lookupedit": "ComboBox",
    # Structure and display
    "label": "Text", "labelcontrol": "Text",
    "groupbox": "Pane", "panel": "Pane", "tablelayoutpanel": "Pane",
    "layoutcontrol": "Pane", "splitcontainer": "Pane",
    "tabcontrol": "Tab", "tabpage": "TabItem", "xtratabcontrol": "Tab",
    "toolstrip": "ToolBar", "menustrip": "ToolBar", "ribboncontrol": "ToolBar",
    "statusstrip": "StatusBar",
    # Data
    "gridcontrol": "DataGrid", "slgrid": "DataGrid", "datagridview": "DataGrid",
    "treelist": "Tree", "treeview": "Tree", "listview": "List",
    "listbox": "List", "checkedlistbox": "List",
}


def dotnet_control_kind(control_type: Any) -> str:
    """Map a CLR type name to a UIA-style control type, or "".

    Takes the last dotted segment, so 'System.Windows.Forms.Button' and
    'DevExpress.XtraEditors.SimpleButton' both reduce to 'Button'. A nested
    type ('ToolStripComboBox+ToolStripComboBoxControl') keeps the inner name,
    which is the one that says what the control actually is. A SystemsLink
    form ('SystemsLink.frmWeb') is reported as a Window, because that is what
    an MDI child form is to a bot searching for an anchor.
    """
    if not control_type:
        return ""
    text = str(control_type)
    if "." not in text and "+" not in text:
        return ""  # already a UIA control type, or meaningless

    segment = text.rsplit(".", 1)[-1]
    if "+" in segment:
        segment = segment.rsplit("+", 1)[-1]

    kind = _DOTNET_TYPE_KINDS.get(segment.lower())
    if kind:
        return kind
    # SystemsLink.frmMain, SystemsLink.frmWeb: an MDI child form is a Window.
    if segment.lower().startswith("frm"):
        return "Window"
    return ""


def normalised_control_type(node: dict[str, Any], backend: str = "uia") -> str:
    """A UIA-style control type for a node from either backend.

    Three sources, in order: a real UIA control type; a CLR type name that the
    win32 backend reported for a .NET control; the Win32 window class. The
    first that yields something recognisable wins.
    """
    control_type = node.get("control_type") or ""
    if control_type and "." not in control_type and "+" not in control_type:
        return control_type

    kind = dotnet_control_kind(control_type)
    if kind:
        return kind
    if backend == "win32":
        return win32_control_kind(node.get("class_name"))
    return ""


def node_is_worth_verifying(
    node: dict[str, Any],
    control_types: frozenset[str] | None = None,
    backend: str = "uia",
) -> bool:
    """Whether a tree node deserves a verified selector.

    A node qualifies if it is an interactive control, or if it carries an
    application-assigned name. That second clause matters: EM's `frmWeb` and
    `grpUserDetails` are a Window and a Group, not controls, but they are the
    stable anchors a bot searches within, so their selectors need proving too.
    """
    wanted = control_types if control_types is not None else INTERACTIVE_CONTROL_TYPES

    control_type = normalised_control_type(node, backend)
    if control_type in wanted:
        return True

    auto_id = node.get("automation_id")
    return bool(auto_id and not is_synthesised_auto_id(auto_id))


def _node_as_uia_meta(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "automation_id": node.get("automation_id"),
        "name": node.get("name"),
        "control_type": node.get("control_type"),
        "class_name": node.get("class_name"),
    }


def _node_as_win32_meta(node: dict[str, Any]) -> dict[str, Any]:
    # A win32 tree walk puts the caption in `name`; the point probe calls the
    # same thing `window_text`. Normalise here so one candidate builder serves
    # both paths.
    class_name = node.get("class_name")
    return {
        "automation_id": node.get("automation_id"),
        "window_text": node.get("name"),
        "class_name": class_name,
        "control_id": node.get("control_id"),
        "class_name_is_volatile": is_volatile_class_name(class_name),
        "class_name_re": class_name_regex(class_name),
    }


def verify_node(
    root_handle: int,
    node: dict[str, Any],
    backend: str,
    visible_only: bool = True,
) -> dict[str, Any]:
    """Prove selectors for one node of a tree walk, on one backend."""
    if backend == "uia":
        meta = _node_as_uia_meta(node)
        candidates = _uia_candidates(meta)
        proofs = [
            verify_candidate(root_handle, c, "uia", visible_only) for c in candidates
        ]
        verdict = recommend(meta, {}, proofs, [])
    else:
        meta = _node_as_win32_meta(node)
        candidates = _win32_candidates(meta)
        proofs = [
            verify_candidate(root_handle, c, "win32", visible_only)
            for c in candidates
        ]
        verdict = recommend({}, meta, [], proofs)
    return {"proofs": proofs, "recommendation": verdict}


def verify_tree_selectors(
    root_handle: int,
    tree: dict[str, Any],
    backend: str,
    control_types: frozenset[str] | None = None,
    limit: int | None = None,
    visible_only: bool = True,
) -> dict[str, Any]:
    """Prove a selector for every worthwhile control in a window.

    Returns a mapping of tree path to verdict, plus counts. Paths come from
    flatten_tree, so a verified selector map and a tree diff describe the same
    controls by the same names.
    """
    flat = flatten_tree(tree)
    selected = [
        (path, node)
        for path, node in flat.items()
        # Skip the root: every selector is verified by searching inside it, so
        # it can never match itself, and counting it as a failure is noise.
        if node.get("depth", 0) > 0
        and node_is_worth_verifying(node, control_types, backend)
    ]
    truncated = False
    if limit is not None and len(selected) > limit:
        selected = selected[:limit]
        truncated = True

    results: dict[str, Any] = {}
    started = time.perf_counter()
    for path, node in selected:
        control_type = normalised_control_type(node, backend)

        # A hidden control cannot resolve while visible_only is on, and
        # reporting that as "no selector found" is misleading: the selector may
        # be perfectly good, the control just is not on screen yet. The win32
        # backend keeps hidden controls in the tree (UIA drops them), so this
        # is common and worth naming.
        if visible_only and node.get("visible") is False:
            results[path] = {
                "control_type": control_type or None,
                "class_name": node.get("class_name"),
                "name": node.get("name"),
                "automation_id": node.get("automation_id"),
                "selector": None,
                "confidence": "hidden",
                "stability": None,
                "basis": None,
                "reason": (
                    "Control is present in the tree but not visible, so no "
                    "selector can resolve against it right now. Make it "
                    "visible first (open its tab, panel or dialog) and "
                    "snapshot again. A bot must wait for this control to "
                    "become visible, not merely to exist."
                ),
            }
            continue

        outcome = verify_node(root_handle, node, backend, visible_only)
        recommendation = outcome["recommendation"]
        control_type = normalised_control_type(node, backend)
        results[path] = {
            "control_type": control_type or None,
            "class_name": node.get("class_name"),
            "name": node.get("name"),
            "automation_id": node.get("automation_id"),
            "selector": recommendation.get("selector"),
            "confidence": recommendation.get("confidence"),
            "stability": recommendation.get("stability"),
            "basis": recommendation.get("basis"),
            "reason": recommendation.get("reason"),
        }
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

    resolved = [r for r in results.values() if r["selector"]]
    by_confidence: dict[str, int] = {}
    for entry in results.values():
        key = entry["confidence"] or "none"
        by_confidence[key] = by_confidence.get(key, 0) + 1

    return {
        "backend": backend,
        "nodes_in_tree": len(flat),
        "nodes_considered": len(selected),
        "nodes_truncated": truncated,
        "uniquely_addressable": len(resolved),
        "by_confidence": by_confidence,
        "elapsed_ms": elapsed_ms,
        "selectors": results,
    }


# ---------------------------------------------------------------------------
# Tree walking, for the snapshot tool
# ---------------------------------------------------------------------------
def child_elements(info: Any) -> list[Any]:
    """The immediate children of an element, for either backend.

    pywinauto's HwndElementInfo.children() is documented as returning
    "immediate children" but implements that with EnumChildWindows, which the
    Win32 API defines as enumerating *all* descendants. On Energy Manager's
    main form that is 76 elements where only 4 are actual children.

    Recursing over that produces one path per subset of the intervening
    containers: `txtPassword` showed up 8 times, once for each combination of
    MdiClient, frmWeb and grpUserDetails being present or skipped. It inflates
    every node count, every addressability percentage, and makes a diff
    unreadable. Filtering on the real parent is the fix.
    """
    children = safe(lambda: list(info.children()), default=[]) or []
    handle = safe(lambda: info.handle)
    if not isinstance(info, HwndElementInfo) or not handle:
        return children

    direct: list[Any] = []
    for child in children:
        child_handle = safe(lambda c=child: c.handle)
        if not child_handle:
            continue
        parent = safe(lambda h=child_handle: win32gui.GetParent(h))
        if parent == handle:
            direct.append(child)
    return direct


def walk_tree(
    info: Any,
    max_depth: int = 30,
    max_nodes: int = 20000,
    _depth: int = 0,
    _counter: list[int] | None = None,
) -> dict[str, Any]:
    """Recursively describe an element and its immediate children.

    Node identity is positional (`path`), assigned by the caller through
    flatten_tree, because a tree is only useful for diffing if the same
    control gets the same key in two snapshots.
    """
    if _counter is None:
        _counter = [0]
    _counter[0] += 1

    node: dict[str, Any] = {
        "name": safe(lambda: info.name),
        "control_type": safe(lambda: info.control_type),
        "automation_id": safe(lambda: info.automation_id),
        "class_name": safe(lambda: info.class_name),
        "control_id": safe(lambda: getattr(info, "control_id", None)),
        "framework_id": safe(lambda: getattr(info, "framework_id", None)),
        "handle": safe(lambda: info.handle),
        "rectangle": _rect_dict(safe(lambda: info.rectangle)),
        "enabled": safe(lambda: info.enabled),
        "visible": safe(lambda: info.visible),
        "depth": _depth,
        "children": [],
    }
    node["automation_id_is_synthesised"] = is_synthesised_auto_id(
        node["automation_id"]
    )
    if isinstance(info, UIAElementInfo):
        keys = hotkeys_for(info)
        if keys["access_key"] or keys["accelerator_key"]:
            node["hotkeys"] = keys
            node["send_keys"] = to_send_keys(
                keys["access_key"] or keys["accelerator_key"]
            )

    if _depth >= max_depth or _counter[0] >= max_nodes:
        node["truncated"] = True
        return node

    for child in child_elements(info):
        if _counter[0] >= max_nodes:
            node["truncated"] = True
            break
        node["children"].append(
            walk_tree(child, max_depth, max_nodes, _depth + 1, _counter)
        )
    return node


def _node_label(node: dict[str, Any]) -> str:
    """Positional label for a node, stable between snapshots.

    An application-assigned automation_id wins, because it is both stable
    and meaningful. A name comes next: less stable, but it is what makes a
    tree readable. A purely numeric automation_id or control id is last,
    marked with a hash so a reader can see at a glance that this node has no
    name of its own.
    """
    ctype = node.get("control_type") or node.get("class_name") or "?"
    auto_id = node.get("automation_id")
    if auto_id and not is_synthesised_auto_id(auto_id):
        return f"{ctype}[{auto_id}]"
    name = node.get("name")
    if name:
        return f"{ctype}[{name}]"
    numeric = auto_id or node.get("control_id")
    if numeric:
        return f"{ctype}#{numeric}"
    return str(ctype)


def flatten_tree(
    node: dict[str, Any],
    prefix: str = "",
    occurrence: int = 0,
) -> dict[str, dict[str, Any]]:
    """Map path string to node, for diffing two snapshots.

    Siblings sharing a label get an occurrence index, so paths stay unique.
    That index is a useful signal in itself: a path ending in `(2)` marks a
    control a bot can only reach positionally, which is a control one layout
    change away from being the wrong control.

    The first occurrence deliberately carries no suffix, so a control that
    gains a duplicate sibling between two snapshots keeps its original path
    and shows up as a stable node with one addition beside it, rather than
    as two unrelated changes.
    """
    label = _node_label(node)
    if occurrence:
        label = f"{label}({occurrence})"
    path = f"{prefix}/{label}"

    flat: dict[str, dict[str, Any]] = {path: node}

    seen: dict[str, int] = {}
    for child in node.get("children", []):
        child_label = _node_label(child)
        seen[child_label] = seen.get(child_label, 0) + 1
        count = seen[child_label]
        flat.update(flatten_tree(child, path, count if count > 1 else 0))
    return flat


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def write_json(payload: Any, out_path: Path) -> Path | None:
    """Write JSON, creating the parent directory. Returns None on failure."""
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, default=str)
    except OSError as exc:
        print(f"  (Could not write {out_path}: {exc})")
        return None
    return out_path


def timestamped_path(prefix: str, suffix: str = ".json") -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return CAPTURE_DIR / f"{prefix}_{stamp}{suffix}"


def summarise_capture(capture: dict[str, Any]) -> str:
    """One-line human summary of a capture, for the console."""
    uia = capture.get("uia", {})
    w32 = capture.get("win32", {})
    rec = capture.get("recommendation", {})
    name = uia.get("name") or w32.get("window_text") or "(no name)"
    ctype = uia.get("control_type") or w32.get("class_name") or "?"
    auto_id = uia.get("automation_id") or ""
    ctrl_id = w32.get("control_id") or ""
    ids = []
    if auto_id:
        ids.append(f"auto_id={auto_id}")
    if ctrl_id:
        ids.append(f"ctrl_id={ctrl_id}")
    id_part = f" {' '.join(ids)}" if ids else " no stable id"
    backend = rec.get("backend") or "none"
    return f"{ctype} | {name}{id_part} -> {backend} ({rec.get('confidence')})"
