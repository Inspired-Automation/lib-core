from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING, Generator

from ._setup import _get_config

if TYPE_CHECKING:
    from .context import Context

_log = logging.getLogger(__name__)


class ErrorCollector:
    def __init__(self) -> None:
        self._errors: list[dict] = []

    def add(
        self,
        message: str,
        *,
        exception: Exception | None = None,
        details: dict | None = None,
    ) -> None:
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


@contextmanager
def collect_errors(ctx: "Context") -> Generator[ErrorCollector, None, None]:
    errors = ErrorCollector()
    try:
        yield errors
    except Exception:
        _maybe_dispatch(ctx, errors, is_critical=True, exc_info=sys.exc_info())
        raise
    else:
        if errors.has_errors:
            _maybe_dispatch(ctx, errors, is_critical=False)


def _maybe_dispatch(
    ctx: "Context",
    errors: ErrorCollector,
    *,
    is_critical: bool,
    exc_info: tuple | None = None,
) -> None:
    # notifications.enabled from the merged config controls dispatch:
    #   "never"  -> never dispatch, even with AUTOMATION_FORCE_NOTIFY set.
    #   "always" -> always dispatch, regardless of path or env var.
    #   "auto" (or absent / unrecognised) -> path-based detection, with the
    #            AUTOMATION_FORCE_NOTIFY=1 override for development locations.
    enabled = str((_get_config().get("notifications") or {}).get("enabled") or "auto").lower()

    if enabled == "never":
        should_notify = False
    elif enabled == "always":
        should_notify = True
    else:
        should_notify = ctx.is_production or os.environ.get("AUTOMATION_FORCE_NOTIFY") == "1"

    if not should_notify:
        logging.info("automation_core: notification suppressed (enabled=%s)", enabled)
        return

    # Lazy import to avoid circular dependency: notifications imports ErrorCollector
    # for type hints, so we cannot import at module level here.
    from .notifications import dispatch_notification  # noqa: PLC0415

    dispatch_notification(ctx, errors, is_critical=is_critical, exc_info=exc_info)
