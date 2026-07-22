from __future__ import annotations

import getpass
import json
import logging
import os
import socket
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING

# Delimiters wrapping the machine-readable JSON block in the notification body.
# Downstream automations (e.g. a Power Automate flow that posts to Teams) extract
# the text between these markers and run it through a Parse JSON action. Keep
# these exact strings stable — changing them breaks every consuming flow.
META_BEGIN = "---AUTOMATION-META-BEGIN---"
META_END = "---AUTOMATION-META-END---"
# Bump when the JSON structure changes so flows can branch on it.
META_SCHEMA_VERSION = 1

from .. import _internal_log as _ilog
from .._setup import _get_config

if TYPE_CHECKING:
    from ..context import Context
    from ..errors import ErrorCollector


def dispatch_notification(
    ctx: "Context",
    errors: "ErrorCollector",
    *,
    is_critical: bool,
    exc_info: tuple | None = None,
) -> None:
    subject = _build_subject(ctx.process_name, errors.count, is_critical)
    body = _build_body(ctx, errors, is_critical, exc_info)

    config = _get_config()

    try:
        if ctx.notification_method == "freshservice":
            from . import freshservice

            freshservice.create_ticket(
                config,
                subject,
                f"<pre>{body}</pre>",
                is_critical=is_critical,
            )
        else:
            from . import email_graph

            email_graph.send(config, ctx.notification_recipient, subject, body)
    except Exception:
        _fail_msg = "automation_core: notification dispatch failed"
        logging.error(_fail_msg, exc_info=True)
        _ilog.logger.error(_fail_msg, exc_info=True)


def _build_subject(process_name: str, error_count: int, is_critical: bool) -> str:
    if is_critical:
        return f"[CRITICAL] {process_name} failed"
    return f"[{process_name}] {error_count} issue(s) during run"


def _build_body(
    ctx: "Context",
    errors: "ErrorCollector",
    is_critical: bool,
    exc_info: tuple | None,
) -> str:
    now_utc = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    hostname = socket.gethostname()
    username = _get_username()

    lines: list[str] = [
        f"Process:    {ctx.process_name}",
        f"Timestamp:  {now_utc}",
        f"Host:       {hostname}",
        f"User:       {username}",
        f"Log file:   {ctx.log_file}",
        f"Errors:     {errors.count}",
        "",
    ]

    if is_critical and exc_info and exc_info[0] is not None:
        lines.append("--- Traceback ---")
        lines.append("".join(traceback.format_exception(*exc_info)).rstrip())
        lines.append("")

    if errors.has_errors:
        lines.append("--- Error List ---")
        for i, err in enumerate(errors.all(), start=1):
            lines.append(f"{i}. {err['message']}")
            if err.get("details"):
                lines.append(f"   Details: {err['details']}")
            if err.get("exception") is not None:
                exc_lines = traceback.format_exception(
                    type(err["exception"]), err["exception"], err["exception"].__traceback__
                )
                for exc_line in "".join(exc_lines).splitlines():
                    lines.append(f"   {exc_line}")
        lines.append("")

    lines.append(_build_meta_block(ctx, errors, is_critical, now_utc, hostname, username))
    lines.append("")

    return "\n".join(lines)


def _build_meta_block(
    ctx: "Context",
    errors: "ErrorCollector",
    is_critical: bool,
    now_utc: str,
    hostname: str,
    username: str,
) -> str:
    """Machine-readable JSON block appended to the notification body.

    Wrapped in ``META_BEGIN`` / ``META_END`` so a downstream automation can
    extract and parse it without scraping the human-readable text above.
    """
    payload = {
        "schema": META_SCHEMA_VERSION,
        "process": ctx.process_name,
        "severity": "critical" if is_critical else "error",
        "is_critical": is_critical,
        "error_count": errors.count,
        "timestamp_utc": now_utc,
        "host": hostname,
        "user": username,
        "log_file": str(ctx.log_file),
        "errors": [
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
        ],
    }
    # default=str guards against any non-serialisable value in a details dict.
    body = json.dumps(payload, indent=2, default=str)
    return f"{META_BEGIN}\n{body}\n{META_END}"


def _get_username() -> str:
    try:
        return getpass.getuser()
    except Exception:
        pass
    try:
        return os.getlogin()
    except Exception:
        pass
    return "unknown"
