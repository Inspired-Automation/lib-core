"""
Monitoring an operation that runs for hours.

A GUI operation that takes minutes or hours is not the same problem as a
control that takes a moment to appear, and the difference is not just a bigger
timeout. A long operation needs to be watched: it can fail halfway with a
dialog, it can hang, and something has to say it is still alive so a human
watching the logs does not assume it has died.

The shape here is taken from the Automation Anywhere bots this library
replaces, because that logic is the product of years of running against the
real application. Decoded from their profile-data bot, it is:

    counter = 1
    loop:
        if the operation's window is GONE   -> success
        counter += 1
        if counter > limit                  -> screenshot, give up
        sleep 60 seconds
        read the progress window's caption:
            "Internet Connection Error" -> screenshot, log, dismiss, give up
            "Unexpected Error"          -> screenshot, log, dismiss, give up

Four details in that are easy to get wrong, and all four are preserved:

  1. **Completion is checked before the sleep.** An operation that finishes in
     ten seconds must not be made to wait a full minute.
  2. **The limit is per operation.** Their config carries separate
     `vDownloadWaitMin` and `vUploadWaitMin` values, because an upload and a
     download do not take the same time. One global timeout is wrong.
  3. **Errors are looked for on every poll**, by reading the progress window,
     rather than waiting for something to throw.
  4. **Every failure path captures evidence** before acting: a screenshot, and
     a log line. When a bot fails at 3am the screenshot is the only witness.

What this module deliberately does NOT do: it never sends the application a
message to ask how far along it is. Reading a progress bar's position means
`PBM_GETPOS`, and its neighbour `PBM_GETRANGE` takes a *pointer* that the
receiving window dereferences inside the target process. Getting that wrong
crashes the application being automated, which has happened. Existence and
visibility are read with `IsWindow` and `IsWindowVisible`, which ask the
window manager, not the application.

Windows only. Requires the `gui` extra.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Sequence

from . import windows

logger = logging.getLogger(__name__)


class Outcome(str, Enum):
    """How an operation ended."""

    COMPLETED = "completed"
    FAILED = "failed"          # the application reported an error
    TIMED_OUT = "timed_out"    # still running when the limit was reached
    ABANDONED = "abandoned"    # a caller's callback asked to stop


@dataclass
class ErrorDialog:
    """A dialog that means the operation has failed.

    `caption` is matched against the window's cached caption. `contains` is
    matched against the concatenated static text inside it, which is how a
    plain Win32 MessageBox says what went wrong.

    `dismiss` names the button to press. Dismissing matters: an undismissed
    modal dialog blocks the application, so the next run finds it wedged.
    """

    caption: str
    contains: str | None = None
    dismiss: str | None = "OK"
    exact_caption: bool = False


@dataclass
class MonitorResult:
    """What happened, in enough detail to log and to act on."""

    outcome: Outcome
    elapsed_seconds: float
    polls: int
    detail: str = ""
    error_text: str = ""
    screenshots: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.COMPLETED


def poll_count(timeout_minutes: float, poll_seconds: float) -> int:
    """How many polls fit in the timeout. At least one.

    Named rather than inlined because it is the arithmetic that decides when a
    bot gives up on a real operation, and it should be testable without
    waiting for the clock.

    Raises ValueError for a non-positive interval. Silently accepting zero
    would turn a caller's typo into a loop that spins a core for hours, which
    is worse than failing immediately. Tests that want no delay inject a
    no-op `sleep` into `wait_for_operation` instead.
    """
    if poll_seconds <= 0:
        raise ValueError(
            f"poll_seconds must be positive, got {poll_seconds!r}. "
            "To run without delay, pass sleep=lambda _: None instead."
        )
    return max(1, int(round((timeout_minutes * 60.0) / poll_seconds)))


def _message_box_text(handle: int) -> str:
    """The static text inside a Win32 dialog, concatenated."""
    try:
        import win32gui
    except ImportError:  # pragma: no cover - guarded at package import
        return ""

    parts: list[str] = []

    def callback(child: int, _extra: object) -> None:
        try:
            if win32gui.GetClassName(child) == "Static":
                text = (win32gui.GetWindowText(child) or "").strip()
                if text:
                    parts.append(text)
        except Exception:
            return

    try:
        win32gui.EnumChildWindows(handle, callback, None)
    except Exception:
        return ""
    return " ".join(parts)


def find_error_dialog(
    dialogs: Sequence[ErrorDialog],
    pids: Iterable[int],
) -> tuple[ErrorDialog, int, str] | None:
    """The first matching error dialog belonging to one of `pids`.

    Scoping to the process matters. An unrelated application's window called
    "Error" is not our failure, and a bot that treats it as one will abandon
    a perfectly healthy run.
    """
    pid_set = set(pids)
    for spec in dialogs:
        window = windows.find_window(
            spec.caption, pids=pid_set, exact=spec.exact_caption
        )
        if window is None:
            continue
        text = _message_box_text(window.handle)
        if spec.contains and spec.contains.lower() not in text.lower():
            continue
        return spec, window.handle, text
    return None


def wait_for_operation(
    *,
    is_running: Callable[[], bool],
    process_ids: Callable[[], set[int]],
    timeout_minutes: float,
    poll_seconds: float = 60.0,
    error_dialogs: Sequence[ErrorDialog] = (),
    dismiss: Callable[[int, str], None] | None = None,
    on_poll: Callable[[int, float], None] | None = None,
    capture_screenshot: Callable[[str], str | None] | None = None,
    description: str = "operation",
    sleep: Callable[[float], None] = time.sleep,
) -> MonitorResult:
    """Watch a long-running GUI operation until it ends.

    `is_running` is the only definition of "still going". Pass something that
    reads window state rather than asking the application: for Energy Manager
    that is whether the progress window still exists.

    `timeout_minutes` is per operation, not global. A download and an upload
    should be given different limits, from config.

    `error_dialogs` are checked on every poll. `dismiss(handle, button)` is
    called to clear one; without it the dialog is left on screen and the next
    run will find the application wedged.

    `on_poll(poll_number, elapsed_seconds)` is where to log a heartbeat. For
    an operation that runs for hours, a log line every minute is the
    difference between "working" and "apparently hung".

    `capture_screenshot(label)` is called before every failure and on timeout,
    and its return value is collected into the result. When something fails
    unattended, the screenshot is the only witness.

    `sleep` exists so tests can run the real control flow without waiting for
    it. Production callers leave it alone.

    Returns a MonitorResult. Never raises for an application-level failure:
    a failed operation is an outcome to be logged and reported, not an
    exception to be caught somewhere else. It *does* raise ValueError for a
    non-positive `poll_seconds`, because that is a caller bug rather than an
    application outcome.
    """
    started = time.monotonic()
    deadline_polls = poll_count(timeout_minutes, poll_seconds)
    screenshots: list[str] = []
    polls = 0

    logger.info(
        "Monitoring %s: up to %.0f minutes, polling every %.0fs (%d polls)",
        description, timeout_minutes, poll_seconds, deadline_polls,
    )

    def shoot(label: str) -> None:
        if capture_screenshot is None:
            return
        try:
            path = capture_screenshot(label)
        except Exception:
            logger.exception("Screenshot failed for %s", label)
            return
        if path:
            screenshots.append(str(path))

    while True:
        # Completion is checked BEFORE sleeping, so an operation that finishes
        # quickly is not held for a whole poll interval.
        try:
            still_running = is_running()
        except Exception:
            logger.exception("is_running() raised; treating %s as still running",
                             description)
            still_running = True

        elapsed = time.monotonic() - started

        if not still_running:
            logger.info("%s completed after %.0fs (%d polls)",
                        description, elapsed, polls)
            return MonitorResult(Outcome.COMPLETED, elapsed, polls,
                                 detail="the operation's window closed",
                                 screenshots=screenshots)

        if polls >= deadline_polls:
            logger.error("%s still running after %.0f minutes; giving up",
                         description, timeout_minutes)
            shoot(f"{description}-timeout")
            return MonitorResult(
                Outcome.TIMED_OUT, elapsed, polls,
                detail=(f"still running after {timeout_minutes} minutes "
                        f"({polls} polls)"),
                screenshots=screenshots,
            )

        # Look for a failure the application is reporting, scoped to its own
        # process so another app's "Error" window cannot end our run.
        if error_dialogs:
            try:
                pids = process_ids()
            except Exception:
                pids = set()
            hit = find_error_dialog(error_dialogs, pids) if pids else None
            if hit is not None:
                spec, handle, text = hit
                message = text or spec.caption
                logger.error("%s failed: %s", description, message)
                shoot(f"{description}-error")
                if dismiss is not None and spec.dismiss:
                    try:
                        dismiss(handle, spec.dismiss)
                    except Exception:
                        logger.exception(
                            "Could not dismiss %r; the application may be left "
                            "blocked by a modal dialog", spec.caption)
                return MonitorResult(
                    Outcome.FAILED, elapsed, polls,
                    detail=f"{spec.caption} dialog",
                    error_text=message,
                    screenshots=screenshots,
                )

        polls += 1
        if on_poll is not None:
            try:
                on_poll(polls, elapsed)
            except StopIteration:
                logger.warning("%s abandoned by on_poll at poll %d",
                               description, polls)
                return MonitorResult(Outcome.ABANDONED, elapsed, polls,
                                     detail="on_poll requested a stop",
                                     screenshots=screenshots)
            except Exception:
                logger.exception("on_poll raised; continuing to monitor")
        else:
            logger.info("%s still running: %.0f minutes elapsed (poll %d/%d)",
                        description, elapsed / 60.0, polls, deadline_polls)

        sleep(poll_seconds)


def window_gone(caption: str, pids: Callable[[], set[int]],
                exact: bool = False) -> Callable[[], bool]:
    """An `is_running` for an operation that shows a top-level window.

    Returns a callable that is True while the window exists. Use it when the
    application signals progress with a window of its own, as Energy Manager
    does with `Downloading` and `Upload in Progress`.
    """
    def still_there() -> bool:
        return windows.find_window(caption, pids=pids(), exact=exact) is not None

    return still_there


def control_gone(handle: int, require_visible: bool = True) -> Callable[[], bool]:
    """An `is_running` for an operation that shows an in-window progress bar.

    Some operations open no window at all. Energy Manager's Import New Data
    puts a ProgressBar inside a form's toolbar and opens nothing on the
    desktop, so a monitor watching for dialogs would wait forever.

    Reads only `IsWindow` and `IsWindowVisible`: no message is sent to the
    application, which is deliberate. See the module docstring.
    """
    try:
        import win32gui
    except ImportError:  # pragma: no cover - guarded at package import
        raise

    def still_there() -> bool:
        if not win32gui.IsWindow(handle):
            return False
        if require_visible and not win32gui.IsWindowVisible(handle):
            return False
        return True

    return still_there


def any_of(*predicates: Callable[[], bool]) -> Callable[[], bool]:
    """True while ANY predicate is true.

    For an operation whose progress indicator might be either a window or an
    in-form control, and you would rather not guess which.
    """
    def combined() -> bool:
        return any(p() for p in predicates)

    return combined
