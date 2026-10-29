"""Branded HTML email for automated processes, built to email-design-spec.md.

The spec and the base template ship in this package, beside this file. Build
an ``Email`` from the components, render it, and send it::

    from datetime import datetime
    from pathlib import Path

    from automation_core.email import (
        AttachedFile, Email, EmailType, Group, Kpi, Section, Table, send_email, subject_line,
    )
    from automation_core.email.formatting import format_datetime, percent_of

    now = datetime.now()
    report = Path("output/ThamesWater_MI.xlsx")
    total = 49565
    message = Email(
        type=EmailType.REPORT,
        subject=subject_line("Thames Water reads: data imports MI report", now.date()),
        preheader="11,832 reads imported to date. 37,733 rows failed and are in the report.",
        title="Data imports MI report",
        meta=f"Generated {format_datetime(now, weekday=True)} · All-time read rows",
        summary="11,832 Thames Water reads have been imported to date. "
                "37,733 rows failed and are listed in the attached report.",
        process_name="Data imports MI report (Thames Water reads)",
        kpis=[
            Kpi(11832, "Imported", f"{percent_of(11832, total)} of all rows"),
            Kpi(37733, "Failed", f"{percent_of(37733, total)} of all rows"),
        ],
        sections=[
            Section("Results by category", table=Table(
                ["Category", "Rows", "% of rows"],
                [
                    ["Imported", 11832, percent_of(11832, total)],
                    Group("Failed matching", [
                        ["Multiple matches", 20112, percent_of(20112, total)],
                        ["No match", 17621, percent_of(17621, total)],
                    ]),
                ],
                total=["Total", total, "100.0%"],
            )),
        ],
        files=[AttachedFile.from_path(report)],
        about="Counts include all read rows imported since go-live.",
    )
    send_email(message.subject, message.render(), "team@inspiredenergy.co.uk",
               attachments=[report])

``Table`` right-aligns a column whose values are all numbers, and gives ints
thousands separators. Format anything else with ``automation_core.email.formatting``.

Every value is HTML-escaped by the renderer, so pass plain text, never HTML.
"""

from .components import (
    AttachedFile,
    Banner,
    Button,
    Column,
    Email,
    EmailType,
    FolderLink,
    Group,
    Kpi,
    Section,
    Status,
    Table,
    WebLink,
    subject_line,
)
from .graph import send_email, split_recipients

__all__ = [
    "AttachedFile",
    "Banner",
    "Button",
    "Column",
    "Email",
    "EmailType",
    "FolderLink",
    "Group",
    "Kpi",
    "Section",
    "Status",
    "Table",
    "WebLink",
    "send_email",
    "split_recipients",
    "subject_line",
]
