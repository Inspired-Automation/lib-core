"""The error notification email, built as an Alert to email-design-spec.md.

Freshservice tickets are not built here: they keep the plain-text body and
meta block from ``notifications._build_body``, because the spec covers email
only.
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime
from typing import TYPE_CHECKING

from ..email import Banner, Column, Email, EmailType, FolderLink, Section, Status, Table, subject_line
from ..email.formatting import format_datetime, format_time
from ..errors import ErrorCollector, serialize_errors

if TYPE_CHECKING:
    from ..context import Context

# A traceback longer than this is cut from the top: the last frames are the
# ones that say where it failed, and the log file has the whole thing.
MAX_TRACEBACK_LINES = 60
# An exception message quoted in the summary is cut to this, with the full
# text in the traceback block.
MAX_QUOTED_MESSAGE = 200


def _plural(count: int, noun: str) -> str:
    return f"{count:,} {noun}" if count == 1 else f"{count:,} {noun}s"


def _traceback_text(exc_info: tuple) -> str:
    lines = "".join(traceback.format_exception(*exc_info)).rstrip().splitlines()
    if len(lines) > MAX_TRACEBACK_LINES:
        cut = len(lines) - MAX_TRACEBACK_LINES
        lines = [f"({cut:,} earlier lines not shown; the log file has the full traceback)"] + lines[-MAX_TRACEBACK_LINES:]
    return "\n".join(lines)


def _details_text(error: dict) -> str:
    parts = []
    if error.get("details") is not None:
        details = error["details"]
        parts.append(
            details if isinstance(details, str) else json.dumps(details, default=str)
        )
    if error.get("exception"):
        parts.append(error["exception"])
    return " | ".join(parts)


def build_alert_email(
    ctx: "Context",
    errors: ErrorCollector,
    *,
    is_critical: bool,
    exc_info: tuple | None,
    now: datetime,
    hostname: str,
    username: str,
) -> Email:
    process = ctx.process_name
    run = f"Control Room job {ctx.job_id}" if ctx.job_id is not None else "Hand run"
    has_traceback = bool(is_critical and exc_info and exc_info[0] is not None)

    if is_critical:
        exc_name = exc_info[0].__name__ if has_traceback else "error"
        message = str(exc_info[1]) if has_traceback else ""
        if len(message) > MAX_QUOTED_MESSAGE:
            message = message[: MAX_QUOTED_MESSAGE - 3].rstrip() + "..."
        subject = subject_line(process, now, prefix="Failed")
        preheader = f"{process} stopped on an unhandled {exc_name}. The traceback and log file are inside."
        title = f"{process} failed"
        banner = Banner(Status.ERROR, f"The run stopped on an unhandled {exc_name}.")
        stopped = f"{process} stopped on {hostname} at {format_time(now)}"
        if message:
            first = f"{stopped} with {exc_name}: {message}"
            first += "" if first.endswith(".") else "."
        else:
            first = f"{stopped} with an unhandled {exc_name}."
        summary = [
            first,
            "Work after that point did not run. The traceback below shows where it stopped.",
        ]
        if errors.has_errors:
            summary[1] += f" It had also recorded {_plural(errors.count, 'issue')} before stopping."
        steps = [
            "Read the traceback below to find where the run stopped.",
            "Open the log file to see what happened before it.",
            "Fix the cause, then run the process again.",
        ]
    else:
        subject = subject_line(
            f"{process}: {_plural(errors.count, 'issue')} during run", now, prefix="Action needed"
        )
        preheader = (
            f"{process} finished with {_plural(errors.count, 'issue')} that need checking. "
            "Details and the log file are inside."
        )
        title = f"{process} finished with {_plural(errors.count, 'issue')}"
        verb = "needs" if errors.count == 1 else "need"
        banner = Banner(
            Status.WARNING,
            f"The run finished, but {_plural(errors.count, 'issue')} {verb} checking.",
        )
        summary = [
            f"{process} finished its run on {hostname} at {format_time(now)} and recorded "
            f"{_plural(errors.count, 'issue')}. The run did not stop for them.",
        ]
        steps = [
            "Check each issue listed below.",
            "Open the log file for what happened around each one.",
            "Correct or rerun the affected items if they need it.",
        ]

    sections = [Section("What to do", steps=steps)]
    if has_traceback:
        sections.append(Section("Traceback", code=_traceback_text(exc_info)))
    if errors.has_errors:
        sections.append(
            Section(
                "Issues",
                table=Table(
                    [Column("#"), Column("Issue"), Column("Details")],
                    [
                        [str(i), error["message"], _details_text(error)]
                        for i, error in enumerate(serialize_errors(errors), start=1)
                    ],
                    more_note="The full list is in the log file.",
                ),
            )
        )
    sections.append(
        Section(
            "Run details",
            table=Table(
                [Column("Detail"), Column("Value")],
                [
                    ["Process", process],
                    ["Run", run],
                    ["Host", hostname],
                    ["User", username],
                    ["Issues recorded", f"{errors.count:,}"],
                ],
            ),
        )
    )

    return Email(
        type=EmailType.ALERT,
        subject=subject,
        preheader=preheader,
        title=title,
        meta=f"Generated {format_datetime(now, weekday=True)} · {run}",
        summary=summary,
        process_name=process,
        status=banner,
        sections=sections,
        files=[FolderLink(ctx.log_file, "Open the log file")],
        about=(
            "This alert is sent when a production run records issues or stops on an "
            "unhandled error. A run with no issues sends nothing."
        ),
    )
