from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Generator

from . import _internal_log as _ilog
from ._setup import _get_config

if TYPE_CHECKING:
    from .context import Context

_log = logging.getLogger(__name__)

# Must match agent/executor.py's ERROR_SIDECAR_FILENAME (Control Room repo,
# agent >= 0.29.0). Written next to the job file so the agent can find it
# without lib-core needing to know anything about the job's own directory
# layout beyond "next to job.json".
SIDECAR_FILENAME = "cr_errors.json"
_SIDECAR_SCHEMA_VERSION = 1


class ErrorCollector:
    """Collects non-fatal errors during a script run.
    
    Use via the `collect_errors` context manager. Errors added via `add()` are
    logged immediately and, if any are present when the block exits, a summary
    notification is dispatched.
    """

    def __init__(self) -> None:
        self._errors: list[dict] = []

    def add(
        self,
        message: str,
        *,
        exception: Exception | None = None,
        details: dict | None = None,
    ) -> None:
        """Record a non-fatal error. Logs immediately and queues for summary."""
        _log.error(message, exc_info=exception)
        self._errors.append(
            {"message": message, "exception": exception, "details": details}
        )

    @property
    def count(self) -> int:
        return len(self._errors)

    @property
    def has_errors(self) -> bool:
        return self.count > 0

    def all(self) -> list[dict]:
        return list(self._errors)


def serialize_errors(errors: ErrorCollector) -> list[dict]:
    """JSON-safe view of the collected errors: message/details verbatim,
    exception as 'TypeName: str(exc)' (never a live Exception object).
    Shared by the notification meta block and the Control Room sidecar, so
    both consumers see identical error text for the same run."""
    return [
        {
            "message": err["message"],
            "details": err.get("details"),
            "exception": (
                f"{type(err['exception']).__name__}: {err['exception']}"
                if err.get("exception") is not None
                else None
            ),
        }
        for err in errors.all()
    ]


@contextmanager
def collect_errors(ctx: "Context") -> Generator[ErrorCollector, None, None]:
    """Context manager that collects non-fatal errors and dispatches a summary.

    Behaviour:
    - Non-fatal errors added via `errors.add()` are logged immediately.
    - If the block exits cleanly with errors collected, a summary notification
      is dispatched (subject to the dispatch rules in `_maybe_dispatch`), and
      a `cr_errors.json` sidecar is written for the Control Room to read back.
    - If the block raises an unhandled exception, a critical notification is
      dispatched with the traceback (and the same sidecar written), then the
      exception is re-raised.
    """
    errors = ErrorCollector()
    try:
        yield errors
    except Exception:
        exc_info = sys.exc_info()
        _write_sidecar(ctx, errors, is_critical=True, exc_info=exc_info)
        _maybe_dispatch(ctx, errors, is_critical=True, exc_info=exc_info)
        raise
    else:
        if errors.has_errors:
            _write_sidecar(ctx, errors, is_critical=False)
            _maybe_dispatch(ctx, errors, is_critical=False)


def _write_sidecar(
    ctx: "Context",
    errors: ErrorCollector,
    *,
    is_critical: bool,
    exc_info: tuple | None = None,
) -> None:
    """Best-effort local artifact for the Control Room agent to read back
    after the bot process exits. Never raises: a sidecar problem must not
    affect the run or the existing notification dispatch. Written
    unconditionally (not gated by notifications.enabled) -- this is Control
    Room visibility, independent of the team's own notification routing
    choice. Absent ctx.job_file (a hand run, or no Control Room job) this is
    a no-op: there is no agent-owned directory to write into."""
    if ctx.job_file is None:
        return
    try:
        now_utc = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        payload = {
            "schema": _SIDECAR_SCHEMA_VERSION,
            "is_critical": is_critical,
            "error_count": errors.count,
            "timestamp_utc": now_utc,
            "errors": serialize_errors(errors),
            "traceback": (
                "".join(traceback.format_exception(*exc_info)).rstrip()
                if is_critical and exc_info and exc_info[0] is not None
                else None
            ),
        }
        path = ctx.job_file.parent / SIDECAR_FILENAME
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, default=str)
        os.replace(tmp, path)  # atomic on the same volume
    except Exception:
        _ilog.logger.warning(
            "could not write Control Room error sidecar", exc_info=True
        )


def _maybe_dispatch(
    ctx: "Context",
    errors: ErrorCollector,
    *,
    is_critical: bool,
    exc_info: tuple | None = None,
) -> None:
    """Decide whether to dispatch a notification, then dispatch it.
    
    Dispatch decision is governed by `notifications.enabled` in the merged
    config:
      "never"  - never dispatch, even with AUTOMATION_FORCE_NOTIFY set.
      "always" - always dispatch, regardless of path or env var.
      "auto"   - path-based detection: dispatch if running from the production
                 root, OR if AUTOMATION_FORCE_NOTIFY=1 is set (dev override).
                 This is the default for absent or unrecognised values.
    """
    enabled = str(
        (_get_config().get("notifications") or {}).get("enabled") or "auto"
    ).lower()

    if enabled == "never":
        should_notify = False
    elif enabled == "always":
        should_notify = True
    else:
        should_notify = (
            ctx.is_production
            or os.environ.get("AUTOMATION_FORCE_NOTIFY") == "1"
        )

    if not should_notify:
        logging.info("automation_core: notification suppressed (enabled=%s)", enabled)
        return

    # Lazy import to avoid circular dependency: notifications imports
    # ErrorCollector for type hints, so we cannot import at module level here.
    from .notifications import dispatch_notification  # noqa: PLC0415

    dispatch_notification(ctx, errors, is_critical=is_critical, exc_info=exc_info)
    logging.info(
        "automation_core: notification dispatched via %s for %s",
        ctx.notification_method,
        ctx.process_name,
    )