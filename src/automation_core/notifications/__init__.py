from __future__ import annotations

import getpass
import logging
import os
import socket
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING

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

    return "\n".join(lines)


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
