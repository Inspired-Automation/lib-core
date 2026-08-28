"""
Acting on controls safely.

Every function here exists because the obvious pywinauto call has a failure
mode that is silent, intermittent, or both. The point of this module is that
a bot author never has to remember any of them.

The rules encoded here:

  - Keep specifications, resolve late. `child_window()` is a method on
    `WindowSpecification`, not on a resolved wrapper. Calling it on a wrapper
    raises `AttributeError: 'UIAWrapper' object has no attribute
    'child_window'`, which is what killed every run of an earlier spike.

  - Wait for `visible`, not `exists`. On the win32 backend a hidden control
    is still in the tree, so an exists-check passes instantly and the bot
    acts on something invisible. Under UIA the element genuinely does not
    exist until shown. Waiting on visibility is correct under both.

  - Click by posting, not sending. `wrapper.click()` sends `BM_CLICK` with
    `SendMessage`, which does not return until the application has finished
    handling it. On a button that opens a modal dialog, that is never: the
    bot hangs forever with no error. `PostMessage` returns immediately.

  - Set text atomically. `type_keys()` sends one character at a time into
    whatever currently has focus, so it is slow and it will spray a password
    into another window if focus moves mid-sequence.

  - Check the control is on screen before any real-mouse action. A control
    can report itself visible while sitting at (-31950, -31803).

Windows only. Requires the `gui` extra.
"""

from __future__ import annotations

import time
from typing import Any

try:
    import win32con
    import win32gui
except ImportError as exc:  # pragma: no cover - import guard
    raise ImportError(
        "automation_core.gui needs the 'gui' extra. "
        "Install it with: pip install automation-core[gui]"
    ) from exc

# Visible and enabled, not merely existing. See the module docstring.
READY = "visible enabled ready"

DEFAULT_TIMEOUT = 30.0


class ControlNotReady(RuntimeError):
    """A control did not become usable within its timeout."""


class ControlOffScreen(RuntimeError):
    """A control resolved but sits outside the visible desktop."""


def resolve(spec: Any, timeout: float = DEFAULT_TIMEOUT, condition: str = READY) -> Any:
    """Resolve a specification into a wrapper, waiting for it to be usable.

    Raises ControlNotReady rather than pywinauto's bare TimeoutError, so a
    caller can tell "the control never appeared" apart from any other timeout
    in the same try block.
    """
    try:
        return spec.wait(condition, timeout=timeout)
    except Exception as exc:
        raise ControlNotReady(
            f"Control did not become '{condition}' within {timeout}s: {exc}"
        ) from exc


def rectangle(wrapper: Any) -> tuple[int, int, int, int]:
    try:
        rect = wrapper.rectangle()
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return (0, 0, 0, 0)


def is_on_screen(wrapper: Any) -> bool:
    """Whether a resolved control is somewhere a user could actually see."""
    left, top, right, bottom = rectangle(wrapper)
    if right <= 0 and bottom <= 0:
        return False
    return left < 32000 and top < 32000


def require_on_screen(wrapper: Any) -> Any:
    if not is_on_screen(wrapper):
        raise ControlOffScreen(
            f"Control resolved at {rectangle(wrapper)}, which is off screen. "
            "Its parent form is probably parked outside the MDI client area. "
            "Restore or activate the form first, or use a message-based "
            "action that does not need the real mouse."
        )
    return wrapper


def click(
    spec: Any,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    opens_dialog: bool = True,
    settle: float = 0.0,
) -> Any:
    """Click a control without risking a hang.

    `opens_dialog` defaults to True because that is the safe assumption: a
    posted click behaves identically to a sent one for buttons that do not
    open a dialog, whereas a *sent* click to one that does will block the bot
    indefinitely. Only set it False if you specifically need the call to
    block until the application has finished handling the click.
    """
    wrapper = resolve(spec, timeout=timeout)
    if opens_dialog:
        win32gui.PostMessage(wrapper.handle, win32con.BM_CLICK, 0, 0)
    else:
        wrapper.click()
    if settle:
        time.sleep(settle)
    return wrapper


def invoke(spec: Any, *, timeout: float = DEFAULT_TIMEOUT, settle: float = 0.0) -> Any:
    """Invoke a UIA element through its InvokePattern.

    This is the right way to press a *drawn* item: a WinForms ToolStrip
    button or a DevExpress ribbon button is not a child window, so it has no
    handle to post BM_CLICK to and `controls.click` cannot reach it. Invoking
    the UIA pattern presses it without moving the mouse, so it does not
    depend on the item being on screen or on nothing else stealing focus.

    Verified against Energy Manager's "Import New Data" toolbar button, which
    win32 cannot see at all.

    Falls back to a real mouse click if the element does not support the
    pattern, which some owner-drawn controls do not.
    """
    wrapper = resolve(spec, timeout=timeout)
    try:
        wrapper.invoke()
    except Exception:
        require_on_screen(wrapper).click_input()
    if settle:
        time.sleep(settle)
    return wrapper


def click_with_mouse(spec: Any, *, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """Click by moving the real mouse. Use only when messages are ignored.

    Some owner-drawn and third-party controls do not respond to BM_CLICK.
    This checks the control is actually on screen first, because otherwise
    the pointer is sent to nowhere and nothing happens, silently.
    """
    wrapper = require_on_screen(resolve(spec, timeout=timeout))
    wrapper.click_input()
    return wrapper


def set_text(spec: Any, text: str, *, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """Set an edit control's value atomically.

    Never use `type_keys` for this. It is slow, it depends on focus staying
    put for the whole sequence, and a password typed that way lands wherever
    focus happens to move to.
    """
    wrapper = resolve(spec, timeout=timeout)
    wrapper.set_edit_text(text)
    return wrapper


def select(spec: Any, item: str | int, *, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """Choose an item in a combo or list, by text or index."""
    wrapper = resolve(spec, timeout=timeout)
    wrapper.select(item)
    return wrapper


def read_text(spec: Any, *, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Read a control's text, for verifying an action actually took."""
    wrapper = resolve(spec, timeout=timeout)
    try:
        return wrapper.window_text() or ""
    except Exception:
        return ""


def verify_text(
    spec: Any,
    expected: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> bool:
    """Read a value back and confirm it.

    Any blind or keyboard-driven action should be followed by one of these.
    An action you have not verified has not happened.
    """
    return read_text(spec, timeout=timeout) == expected


def wait_while_present(
    handle: int,
    *,
    timeout: float = 600.0,
    poll: float = 1.0,
    require_visible: bool = True,
) -> bool:
    """Wait until a control is destroyed or hidden. True if it went away.

    This is how to monitor a long operation that shows a progress bar rather
    than a dialog. Energy Manager's import puts a ProgressBar inside a
    ToolStrip and opens no window at all, so a monitor watching for dialogs
    sees nothing happen, and the control being destroyed is the completion
    signal.

    Deliberately reads only `IsWindow` and `IsWindowVisible`, which query
    window state without sending the application anything.

    Do NOT be tempted to read the bar's actual position with PBM_GETRANGE to
    show a percentage. That message takes a POINTER in lParam, which the
    receiving window procedure dereferences inside the target process: passing
    a plain integer crashed Energy Manager with an access violation in
    Comctl32. `PBM_GETPOS` is safe (lParam is 0), but existence is usually all
    a bot needs.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not win32gui.IsWindow(handle):
            return True
        if require_visible and not win32gui.IsWindowVisible(handle):
            return True
        time.sleep(poll)
    return False


def wait_until(
    predicate,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    poll: float = 0.25,
    description: str = "condition",
) -> bool:
    """Poll a predicate until it is true. Never sleep a fixed duration.

    A fixed sleep is simultaneously too slow on a good day and too short on a
    bad one. Energy Manager's launch was measured at anywhere from 42 to 174
    seconds depending on how warm the file share was.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(poll)
    return False
