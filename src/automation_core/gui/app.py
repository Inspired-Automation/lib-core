"""
Driving a desktop application.

`GuiApp` is the generic layer: launch or attach to a process, find its
windows safely, and get specification objects scoped to them. Application
specific knowledge (which forms exist, what the login sequence is, which
commands have hotkeys) belongs in a subclass under `apps/`.

The design point is that a bot author writes what they want to happen, and
this layer decides how. In particular it holds the two rules that a caller
should never have to remember:

  - Top-level windows are found by handle, via EnumWindows and the cached
    caption, never by `title=` matching that depends on the application
    answering a message.
  - `auto_id` is only ever used *inside* a window already found, never at
    the desktop level where it raises AccessDenied against protected
    processes.

An application may need more than one backend. Energy Manager needs win32
for its WinForms MDI child forms and UIA for its DevExpress ribbon, which
win32 cannot see into at all because DevExpress draws ribbon items instead
of creating a window per button. `GuiApp` therefore keeps a specification
per backend rather than assuming one.

Windows only. Requires the `gui` extra.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Any

try:
    from pywinauto import Desktop
except ImportError as exc:  # pragma: no cover - import guard
    raise ImportError(
        "automation_core.gui needs the 'gui' extra. "
        "Install it with: pip install automation-core[gui]"
    ) from exc

from . import controls, windows
from .keys import to_send_keys

logger = logging.getLogger(__name__)

BACKENDS = ("win32", "uia")


class GuiApp:
    """A running desktop application, addressed safely."""

    #: Image name used to find the process, e.g. "EM.exe".
    process_name: str = ""

    #: Substring of the main window caption, e.g. "SystemsLink".
    main_window_caption: str = ""

    #: Backend used for most controls. Override per application after
    #: measuring both, never by assumption.
    default_backend: str = "win32"

    def __init__(self, process_name: str | None = None) -> None:
        if process_name:
            self.process_name = process_name
        if not self.process_name:
            raise ValueError("process_name is required")
        self._launched: subprocess.Popen[bytes] | None = None

    # -- process ----------------------------------------------------------
    @property
    def pids(self) -> set[int]:
        """Process ids currently running under this image name."""
        return windows.process_ids(self.process_name)

    @property
    def is_running(self) -> bool:
        return bool(self.pids)

    def launch(self, executable: str) -> None:
        """Start the application. Does not wait for any window."""
        logger.info("Launching %s", executable)
        self._launched = subprocess.Popen([executable])

    def kill(self) -> None:
        """Terminate every instance of the image. Use before a clean launch."""
        logger.info("Terminating %s", self.process_name)
        subprocess.run(
            ["taskkill", "/IM", self.process_name, "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        time.sleep(1.0)

    # -- windows ----------------------------------------------------------
    def find_window(self, caption: str, *, exact: bool = False):
        """A window of this application, or None."""
        return windows.find_window(caption, pids=self.pids, exact=exact)

    def wait_for_window(
        self,
        caption: str,
        *,
        timeout: float = 180.0,
        exact: bool = False,
    ):
        """Wait for one of this application's windows to appear.

        Re-reads the process list each poll, so this still works across a
        launch, and across the application restarting itself.
        """
        return windows.wait_for_window(
            caption,
            process_name=self.process_name,
            timeout=timeout,
            exact=exact,
        )

    def window(self, handle: int, *, backend: str | None = None) -> Any:
        """A pywinauto specification for a window, addressed by handle.

        By handle deliberately: it needs no cross-process message, so it
        cannot be defeated by a busy or wedged UI thread the way `title=`
        matching can.
        """
        chosen = backend or self.default_backend
        if chosen not in BACKENDS:
            raise ValueError(f"backend must be one of {BACKENDS}, got {chosen!r}")
        return Desktop(backend=chosen).window(handle=handle)

    def main_window(self, *, backend: str | None = None, timeout: float = 180.0) -> Any:
        """Specification for the application's main window."""
        info = self.wait_for_window(self.main_window_caption, timeout=timeout)
        return self.window(info.handle, backend=backend)

    # -- actions ----------------------------------------------------------
    def send_hotkey(self, hotkey: str, *, window_handle: int | None = None) -> bool:
        """Invoke a command by its keyboard shortcut.

        Prefer this to clicking whenever a shortcut exists. It needs no
        selector, does not care which ribbon tab is active, and is unaffected
        by the window's position on screen.

        Accepts either a UIA display string ("Ctrl+Alt+1") or a send_keys
        spec ("^%1"). Returns False if the shortcut could not be translated,
        rather than sending a guessed key combination to a live application.
        """
        spec = to_send_keys(hotkey) or (hotkey if hotkey.startswith(("^", "%", "+", "{")) else None)
        if spec is None:
            logger.warning("Not sending unrecognised shortcut %r", hotkey)
            return False

        if window_handle is not None:
            # The shortcut goes to whichever window has focus, so make sure
            # it is the right one before sending.
            self.window(window_handle).set_focus()
            time.sleep(0.2)

        from pywinauto import keyboard

        logger.info("Sending shortcut %s (%s)", hotkey, spec)
        keyboard.send_keys(spec, with_spaces=True, pause=0.05)
        return True

    # -- convenience re-exports so a bot needs one import ------------------
    resolve = staticmethod(controls.resolve)
    invoke = staticmethod(controls.invoke)
    click = staticmethod(controls.click)
    set_text = staticmethod(controls.set_text)
    select = staticmethod(controls.select)
    read_text = staticmethod(controls.read_text)
    wait_until = staticmethod(controls.wait_until)
    wait_while_present = staticmethod(controls.wait_while_present)
