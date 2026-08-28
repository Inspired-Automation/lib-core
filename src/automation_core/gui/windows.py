"""
Finding top-level windows reliably.

This is harder than pywinauto makes it look, and getting it wrong produces
bots that pass testing and fail in production. Two traps, both hit while
mapping Energy Manager, both of which make a window that is plainly on
screen invisible to the obvious code.

**`title=` depends on the application answering `WM_GETTEXT`.**
pywinauto matches `title` / `title_re` against `rich_text`, which it obtains
by sending `WM_GETTEXT` to the window. An application that is busy, showing
a modal dialog, or wedged does not answer, `rich_text` comes back empty, and
no title match can ever succeed. Energy Manager's login form answered
perfectly on a clean launch and then returned empty for several minutes
after a rejected login. That is the worst possible failure shape: it works
while you are testing and stops working at the exact moment something has
already gone wrong and you most need the bot to keep going.

`GetWindowText` reads the caption cached in the window structure when called
across processes, so it keeps working regardless. That is what this module
uses.

**Searching by `auto_id` at the desktop level raises `AccessDenied`.**
Reading a WinForms designer name means sending `WM_GETCONTROLNAME` and
having the target process write the answer into memory allocated inside it
with `VirtualAllocEx`. At the desktop level pywinauto must try that against
every top-level window to find a match, and the first protected or elevated
process refuses:

    pywinauto.remote_memory_block.AccessDenied:
        ('[WinError 5] Access is denied.process: %d', 20748)

So: find the window here, by handle, then use `auto_id` *inside* it.

Windows only. Requires the `gui` extra: `pip install automation-core[gui]`.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Iterable

try:
    import win32gui
    import win32process
except ImportError as exc:  # pragma: no cover - import guard
    raise ImportError(
        "automation_core.gui needs the 'gui' extra. "
        "Install it with: pip install automation-core[gui]"
    ) from exc


@dataclass(frozen=True)
class WindowInfo:
    """A top-level window, described without any cross-process messaging."""

    handle: int
    title: str
    class_name: str
    process_id: int | None
    rectangle: tuple[int, int, int, int] = field(default=(0, 0, 0, 0))

    @property
    def is_on_screen(self) -> bool:
        """Whether the window sits within the visible desktop.

        A window can report itself visible and still be nowhere a user could
        see, which matters because any action driven by real mouse input will
        then click empty space. Energy Manager parks MDI child forms at
        coordinates like (-31950, -31803).
        """
        left, top, right, bottom = self.rectangle
        return right > 0 and bottom > 0 and left < 32000 and top < 32000


def process_ids(image_name: str) -> set[int]:
    """Process ids for a running image name, e.g. "EM.exe".

    Shells out to tasklist rather than taking a psutil dependency: this runs
    at most once per poll and the parsing is trivial.
    """
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()

    pids: set[int] = set()
    for line in (result.stdout or "").splitlines():
        fields = [part.strip('" ') for part in line.split('","')]
        if len(fields) >= 2 and fields[1].isdigit():
            pids.add(int(fields[1]))
    return pids


def list_windows(
    *,
    pids: Iterable[int] | None = None,
    visible_only: bool = True,
    titled_only: bool = True,
) -> list[WindowInfo]:
    """Every top-level window, optionally restricted to some processes.

    Uses EnumWindows and GetWindowText only, so nothing here depends on the
    target application servicing a message.
    """
    wanted = set(pids) if pids is not None else None
    found: list[WindowInfo] = []

    def callback(handle: int, _extra: object) -> None:
        try:
            if visible_only and not win32gui.IsWindowVisible(handle):
                return
            title = win32gui.GetWindowText(handle) or ""
            if titled_only and not title.strip():
                return
            try:
                _thread_id, pid = win32process.GetWindowThreadProcessId(handle)
            except Exception:
                pid = None
            if wanted is not None and pid not in wanted:
                return
            try:
                rect = win32gui.GetWindowRect(handle)
            except Exception:
                rect = (0, 0, 0, 0)
            found.append(
                WindowInfo(
                    handle=handle,
                    title=title,
                    class_name=win32gui.GetClassName(handle) or "",
                    process_id=pid,
                    rectangle=rect,
                )
            )
        except Exception:
            return

    win32gui.EnumWindows(callback, None)
    return found


def find_window(
    caption: str,
    *,
    pids: Iterable[int] | None = None,
    exact: bool = False,
    visible_only: bool = True,
) -> WindowInfo | None:
    """First window whose caption matches, or None.

    Always pass `pids` when you know the process. Without it, another
    application's window called "Error" will satisfy the match, and a bot
    that mistakes someone's Outlook dialog for its own is worse than one that
    finds nothing.
    """
    needle = caption if exact else caption.lower()
    for window in list_windows(pids=pids, visible_only=visible_only):
        title = window.title if exact else window.title.lower()
        if (title == needle) if exact else (needle in title):
            return window
    return None


def wait_for_window(
    caption: str,
    *,
    pids: Iterable[int] | None = None,
    process_name: str | None = None,
    timeout: float = 120.0,
    poll: float = 0.5,
    exact: bool = False,
) -> WindowInfo:
    """Wait for a window to appear, and return it.

    Pass `process_name` instead of `pids` when the process may not have
    started yet, or may restart during the wait: the process list is
    re-read on every poll, so a window belonging to a newly spawned instance
    is still found.

    Raises TimeoutError, which is the right outcome: a bot that carries on
    without the window it was waiting for does something unpredictable.
    """
    deadline = time.monotonic() + timeout
    resolved_pids = set(pids) if pids is not None else None

    while time.monotonic() < deadline:
        if process_name is not None:
            resolved_pids = process_ids(process_name)
        window = find_window(caption, pids=resolved_pids, exact=exact)
        if window is not None:
            return window
        time.sleep(poll)

    where = f" for {process_name}" if process_name else ""
    raise TimeoutError(
        f"No window with caption {caption!r}{where} appeared within {timeout}s"
    )


def wait_for_window_to_close(
    handle: int,
    *,
    timeout: float = 60.0,
    poll: float = 0.25,
) -> bool:
    """Wait until a window handle is gone. True if it closed in time.

    Useful as the completion signal for a modal dialog: the dialog closing is
    a far more reliable "the action finished" than any caption.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not win32gui.IsWindow(handle):
            return True
        time.sleep(poll)
    return not win32gui.IsWindow(handle)


def window_title(handle: int) -> str:
    """The cached caption of a window, without messaging the application."""
    try:
        return win32gui.GetWindowText(handle) or ""
    except Exception:
        return ""
