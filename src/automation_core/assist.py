"""Human-in-the-loop assist: asking the Control Room for a person.

Some runs cannot be finished by a bot alone. A captcha on a supplier's
portal is the standing example: the bot drives everything either side of
it, but the challenge itself needs a person to look at the screen and
click. The Control Room agent (0.45.0 or later) can put a person into the
bot's own RDP session through noVNC in a browser, and a bot asks for that
with three files in its job directory:

  - the bot writes ``assist.request`` (its content is a reason, shown on
    the job page as "This bot is waiting for a person");
  - the agent picks it up on its next progress tick (every 10 seconds by
    default), brings the session up for viewing and writes ``assist.json``
    back with the URL;
  - the bot writes ``assist.release`` when it can carry on, and the agent
    ends the session and deletes ``assist.json``.

Request and release are two files rather than one edited in place because
the bot runs as the runner account and cannot overwrite or delete a file
LocalSystem wrote. The agent deletes the bot's markers as it consumes them.

Every bot used to hand-roll this. ``Context.request_assist()`` is the one
implementation:

    with ctx.request_assist("Solve the captcha on the login page",
                            timeout_s=900) as assist:
        wait_for_login_page_to_change()
    # leaving the block writes assist.release

Behaviour, and the reasons for it:

  - The job directory is the directory of the job file (``ctx.job_file``),
    the same place ``collect_errors`` writes ``cr_errors.json``. With no job
    file (a hand run on a developer's machine) the whole thing is a no-op
    with one logged warning, so the same code runs locally unchanged.
  - Not getting a URL in time never raises. It logs a warning and the block
    runs anyway: the agent may still be starting, the person can still be
    reached from the job page, and a bot that fails because help was slow
    to arrive is worse than one that waits on its own page.
  - The URL is never logged. It is the credential (a per-assist token that
    the agent drops when the assist ends), and a log file on a share is
    read by more people than the one being asked to help. The job page in
    the Control Room shows it to whoever opens the run.
  - Leaving the block always writes ``assist.release``, on an exception
    too, so a person is never left holding a session for a bot that has
    already given up. Any ``assist.request`` the agent has not consumed yet
    is deleted first (the bot owns that file), so a request the agent never
    saw cannot start a session after the bot has moved on.
  - A release left over from an earlier block in the same run is dealt
    with before a new request is written. While that earlier assist is
    still live the agent consumes the release on its next tick, so this
    waits for that; otherwise the agent only reads a release while an
    assist is live, so a leftover one would end the new assist on its
    first tick, and it is deleted instead.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from types import TracebackType
from typing import Callable

from . import _internal_log as _ilog

_log = logging.getLogger(__name__)

# Must match agent/vncops.py's REQUEST_MARKER, RELEASE_MARKER and
# STATE_FILE (Control Room repo, agent >= 0.45.0).
REQUEST_FILENAME = "assist.request"
RELEASE_FILENAME = "assist.release"
STATE_FILENAME = "assist.json"

# How long entering the block waits for assist.json by default. The agent
# notices a request within one progress tick (10s by default), and starting
# TightVNC in the session waits up to 30s for it to listen, so two minutes
# covers a healthy node with room to spare.
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_POLL_S = 2.0

# How long to wait for the agent to consume a release left by an earlier
# block while that assist is still live: three progress ticks.
LEFTOVER_RELEASE_WAIT_S = 30.0

# The agent reads the first 4096 bytes of a marker and parses them as JSON.
# json.dumps escapes non-ASCII as \uXXXX (up to 12 bytes a character for a
# surrogate pair), so 300 characters always fits whole.
MAX_REASON_CHARS = 300


class Assist:
    """One request for a person, used as a context manager.

    Get one from ``Context.request_assist()`` rather than building it. The
    request is written on entering the ``with`` block and the release on
    leaving it.
    """

    def __init__(
        self,
        job_dir: Path | None,
        reason: str,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        wait: bool = True,
        poll_s: float = DEFAULT_POLL_S,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if poll_s <= 0:
            # Zero would spin a core for the whole wait; reject the typo.
            raise ValueError("poll_s must be greater than zero")
        self.job_dir = job_dir
        self.reason = " ".join(str(reason).split())[:MAX_REASON_CHARS]
        self.timeout_s = timeout_s
        self.wait = wait
        self.poll_s = poll_s
        self._sleep = sleep
        self._clock = clock
        self._requested = False
        self._released = False

    # -- state -----------------------------------------------------

    @property
    def active(self) -> bool:
        """True when running under the Control Room, so a request can be
        made at all. False on a hand run, where everything is a no-op."""
        return self.job_dir is not None

    @property
    def state(self) -> dict | None:
        """The agent's ``assist.json`` as it stands now, or None when there
        is none (not live yet, already ended, or not under the Control
        Room). Carries ``phase``, ``url``, ``reason`` and ``expires_in_s``.
        Read fresh on every access; never raises."""
        if self.job_dir is None:
            return None
        try:
            with open(self.job_dir / STATE_FILENAME, encoding="utf-8") as fh:
                body = json.load(fh)
        except (OSError, ValueError):
            # Absent is the ordinary case. The agent writes it whole via a
            # temp file, so a parse failure is a passing oddity: treat it
            # as not there and let the next read try again.
            return None
        return body if isinstance(body, dict) else None

    @property
    def url(self) -> str | None:
        """The noVNC URL while the assist is live, otherwise None.
        Read fresh on every access, so a bot can poll it."""
        state = self.state
        if not state or state.get("phase") != "live":
            return None
        url = state.get("url")
        return url if isinstance(url, str) and url else None

    # -- lifecycle -------------------------------------------------

    def wait_for_url(self, timeout_s: float | None = None) -> str | None:
        """Block until the assist is live or ``timeout_s`` passes (the
        instance's ``timeout_s`` when None). Returns the URL, or None on
        timeout, after logging a warning. Never raises for a timeout."""
        if not self._requested:
            return None
        limit = self.timeout_s if timeout_s is None else timeout_s
        deadline = self._clock() + max(0.0, limit)
        while True:
            url = self.url
            if url:
                _log.info(
                    "assist is live: a person can now open this run's "
                    "session from its job page in the Control Room"
                )
                return url
            remaining = deadline - self._clock()
            if remaining <= 0:
                _log.warning(
                    "assist was requested but the Control Room agent had not "
                    "made it live within %ss; carrying on. A person can still "
                    "open the session from the job page once it starts. If it "
                    "never does, check that this is an interactive bot and "
                    "that the host has Human assist installed",
                    limit,
                )
                return None
            self._sleep(min(self.poll_s, remaining))

    def request(self) -> bool:
        """Write ``assist.request``. Called on entering the block; returns
        True when the request was written. Calling it again while a request
        is outstanding does nothing."""
        if self.job_dir is None:
            _log.warning(
                "assist requested (%s) but this run has no Control Room job "
                "file, so nobody will be asked to help; carrying on without",
                self.reason or "no reason given",
            )
            return False
        if self._requested and not self._released:
            return True
        self._clear_leftover_release()
        body = {"reason": self.reason} if self.reason else {}
        if not self._write_marker(REQUEST_FILENAME, body):
            return False
        self._requested = True
        self._released = False
        _log.info(
            "assist requested from the Control Room: %s",
            self.reason or "no reason given",
        )
        return True

    def release(self, reason: str | None = None) -> None:
        """Write ``assist.release``, ending the session. Called on leaving
        the block; safe to call earlier (for example the moment the page
        changes) and safe to call twice."""
        if not self._requested or self._released:
            return
        self._released = True
        # A request the agent has not consumed yet would otherwise start a
        # session after the bot has moved on. The bot wrote it, so it can
        # delete it.
        try:
            (self.job_dir / REQUEST_FILENAME).unlink(missing_ok=True)
        except OSError:
            _ilog.logger.warning(
                "could not withdraw an unconsumed %s", REQUEST_FILENAME,
                exc_info=True,
            )
        why = " ".join(str(reason).split())[:MAX_REASON_CHARS] if reason else ""
        self._write_marker(RELEASE_FILENAME, {"reason": why} if why else {})
        _log.info("assist released%s", f": {why}" if why else "")

    def __enter__(self) -> Assist:
        if self.request() and self.wait:
            self.wait_for_url()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if exc_type is None:
            self.release()
        else:
            self.release(f"released by the bot after {exc_type.__name__}")
        return False

    # -- files -----------------------------------------------------

    def _write_marker(self, name: str, body: dict) -> bool:
        """Write a marker whole, via a temp file, so the agent can never
        read half of one. Never raises: a failed write is logged and the
        run carries on."""
        path = self.job_dir / name
        tmp = path.with_name(path.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(body, fh)
            os.replace(tmp, path)
            return True
        except OSError:
            _ilog.logger.warning(
                "could not write %s for the Control Room agent", name,
                exc_info=True,
            )
            return False

    def _clear_leftover_release(self) -> None:
        """Deal with an ``assist.release`` still on disk from an earlier
        block, before a new request can be undone by it (see the module
        docstring)."""
        release = self.job_dir / RELEASE_FILENAME
        if not release.exists():
            return
        if (self.job_dir / STATE_FILENAME).exists():
            # The earlier assist is still live: the agent consumes the
            # release on its next tick, and deletes assist.json with it.
            deadline = self._clock() + LEFTOVER_RELEASE_WAIT_S
            while release.exists() and self._clock() < deadline:
                self._sleep(min(self.poll_s, 1.0))
            if not release.exists():
                return
        try:
            release.unlink(missing_ok=True)
        except OSError:
            _ilog.logger.warning(
                "could not remove a leftover %s; the agent may end this "
                "assist on its first tick", RELEASE_FILENAME, exc_info=True,
            )
