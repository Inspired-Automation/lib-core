"""The email components from email-design-spec.md section 6, and the renderer.

An ``Email`` holds content only. ``Email.render()`` lays it out in the order
the spec fixes (header, status banner, summary, KPIs, sections, files, about)
inside the shell from ``email-base-template.html``, escaping every value on
the way in. A bot author never writes HTML, so the rules a hand-written email
gets wrong sooner or later (escaping, separators, right-aligned numbers, the
15-row cap, the footer wording) are applied the same way every time.

Two kinds of rule are checked, differently:

- Rules that depend only on how the email is built (an Alert needs an Error
  or Warning banner, at most four KPIs) raise ``ValueError``. They fail on
  the first run, in development, where they belong.
- Rules that depend on the data (a subject that runs past 80 characters
  because a process name is long, a table with 2,000 rows) never fail a run.
  Long text is logged as a warning; long tables are cut to 15 rows with a
  note saying how many are not shown.
"""

from __future__ import annotations

import html
import logging
import numbers
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from importlib import resources
from pathlib import Path
from typing import Sequence, Union

from .formatting import file_url, format_date, format_datetime, format_number, format_size

TEMPLATE_NAME = "email-base-template.html"
_CONTENT_START = "<!-- ======================= CONTENT START ======================= -->"
_CONTENT_END = "<!-- ======================== CONTENT END ======================== -->"

MAX_TABLE_ROWS = 15
MAX_LIST_ITEMS = 6
MAX_SUBJECT_LENGTH = 80
PREHEADER_RANGE = (50, 110)
MAX_HTML_BYTES = 100 * 1024

# Brand tokens (spec 2). Kept as constants so every component uses the same
# values and a colour cannot drift between two snippets.
MIDNIGHT = "#18073a"
MIDNIGHT_80 = "#29215a"
PURPLE_60 = "#4d4777"
BLACK = "#000000"
LIGHT_GREY = "#dededd"
SURFACE = "#f6f6f5"
WHITE = "#ffffff"

CONTENT_WIDTH = 576
_MONO = "font-family:Consolas, 'Courier New', monospace;"

log = logging.getLogger(__name__)


class Status(Enum):
    """Status colours (spec 2): text colour, background, default status word."""

    SUCCESS = ("#29372a", "#e4efdf", "Completed")
    WARNING = ("#7a4420", "#f8eddb", "Needs attention")
    ERROR = ("#590006", "#f2d7d0", "Failed")
    INFO = ("#013c4e", "#deebf3", "For information")

    @property
    def text_colour(self) -> str:
        return self.value[0]

    @property
    def background(self) -> str:
        return self.value[1]

    @property
    def word(self) -> str:
        return self.value[2]


class EmailType(Enum):
    """The four email types (spec 4): the label in the header band and the noun used in "About this ..."."""

    REPORT = ("Automated report", "report")
    ALERT = ("Automated alert", "alert")
    NOTIFICATION = ("Automated notification", "notification")
    REQUEST = ("Action needed", "request")

    @property
    def label(self) -> str:
        return self.value[0]

    @property
    def noun(self) -> str:
        return self.value[1]


# The banner statuses each type allows (spec 4). A Report may have none.
_ALLOWED_STATUS = {
    EmailType.REPORT: {Status.WARNING, Status.ERROR, Status.INFO, Status.SUCCESS},
    EmailType.ALERT: {Status.ERROR, Status.WARNING},
    EmailType.NOTIFICATION: {Status.SUCCESS, Status.INFO},
    EmailType.REQUEST: {Status.WARNING},
}


@dataclass(frozen=True)
class Banner:
    """Status banner (spec 6.2): the status word in bold, then one sentence."""

    status: Status
    sentence: str
    word: str | None = None


@dataclass(frozen=True)
class Kpi:
    """One KPI tile (spec 6.4). An int value gets thousands separators."""

    value: str | int | float
    label: str
    context: str | None = None


@dataclass(frozen=True)
class Column:
    """A table column. ``numeric`` right-aligns it (spec 3).

    A plain string can be given instead; the column is then numeric when every
    value in it is a number or formatted number text ("27,674", "55.8%",
    "£1,234.56"). Ints in a numeric column get thousands separators.

    Declare identifier columns explicitly, ``Column("MPAN")``: a column of
    ints would otherwise be detected as numeric and an MPAN shown with commas.
    The first column is always left-aligned, being the row label.
    """

    name: str
    numeric: bool = False


Cell = Union[str, int, float, None]


@dataclass(frozen=True)
class Group:
    """A group row with the rows under it (spec 6.5), instead of a second table."""

    name: str
    rows: Sequence[Sequence[Cell]]


@dataclass(frozen=True)
class Table:
    """A data table (spec 6.5).

    ``rows`` holds lists of cells and ``Group``s. More than 15 body rows are
    cut to 15 with a note. When the caller has already cut the data (the first
    15 rows of a 2,341-row DataFrame), pass ``total_rows=2341`` so the note
    gives the real count. ``more_note`` follows the "Showing 15 of N rows."
    sentence and should say where the rest are.
    """

    columns: Sequence[str | Column]
    rows: Sequence[Sequence[Cell] | Group]
    total: Sequence[Cell] | None = None
    total_rows: int | None = None
    more_note: str = "The full list is in the attached report."


@dataclass(frozen=True)
class Section:
    """A section heading and its content (spec 4: "sections (tables or lists)").

    Content renders in this order: paragraphs, table, steps, code. ``steps`` is
    a numbered list unless ``ordered=False``. ``code`` is preformatted text
    such as a traceback, shown in the monospace style of the path block.
    """

    heading: str
    paragraphs: Sequence[str] = ()
    table: Table | None = None
    steps: Sequence[str] = ()
    ordered: bool = True
    code: str | None = None


@dataclass(frozen=True)
class Button:
    """The one button an email may have (spec 6.7). ``label`` is a verb phrase."""

    label: str
    url: str


@dataclass(frozen=True)
class AttachedFile:
    """A file attached to the email, listed as "name (size), attached" (spec 6.8)."""

    name: str
    size_bytes: int

    @classmethod
    def from_path(cls, path: Path) -> "AttachedFile":
        return cls(path.name, path.stat().st_size)


@dataclass(frozen=True)
class FolderLink:
    """A network location: a short link plus the full path in a copyable block (spec 6.8)."""

    path: str | Path
    label: str = "Open the report folder"


@dataclass(frozen=True)
class WebLink:
    """A SharePoint or web link. Prefer one of these over a ``FolderLink`` when it exists."""

    url: str
    label: str


FileItem = Union[AttachedFile, FolderLink, WebLink]


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _is_number(value: object) -> bool:
    return isinstance(value, numbers.Number) and not isinstance(value, bool)


# Text the formatting helpers produce: "27,674", "55.8%", "£1,234.56", "-£3.00".
_NUMERIC_TEXT = re.compile(r"-?£?\d[\d,]*(\.\d+)?%?")


def _looks_numeric(value: object) -> bool:
    return _is_number(value) or (isinstance(value, str) and bool(_NUMERIC_TEXT.fullmatch(value.strip())))


def _cell_text(value: Cell, numeric: bool) -> str:
    if value is None:
        return ""
    # Separators only in a numeric column: an MPAN or a job id in a text
    # column must come out exactly as given, not as "1,234,567,890,123".
    if numeric and _is_number(value):
        return format_number(value) if isinstance(value, int) else f"{value:,}"
    return str(value)


def _spacer(height: int) -> str:
    return (
        f'<table role="presentation" width="{CONTENT_WIDTH}" cellpadding="0" cellspacing="0" '
        f'border="0" style="width:{CONTENT_WIDTH}px;"><tr><td height="{height}" '
        f'style="height:{height}px; line-height:{height}px; font-size:0;">&nbsp;</td></tr></table>'
    )


def _block(inner_rows: str) -> str:
    return (
        f'<table role="presentation" width="{CONTENT_WIDTH}" cellpadding="0" cellspacing="0" '
        f'border="0" style="width:{CONTENT_WIDTH}px;">{inner_rows}</table>'
    )


@dataclass(frozen=True)
class Email:
    """A whole email. Build one, then call ``render()`` for the HTML body.

    ``process_name`` fills the footer and should be the human-readable name a
    reader would quote in a ticket. ``meta`` is the line under the title, for
    example ``"Generated Tue 27/10/2026 14:30 GMT · All-time read rows"``;
    ``formatting.format_datetime(..., weekday=True)`` gives the date part.
    """

    type: EmailType
    subject: str
    preheader: str
    title: str
    meta: str
    summary: Sequence[str] | str
    process_name: str
    status: Banner | None = None
    kpis: Sequence[Kpi] = ()
    sections: Sequence[Section] = ()
    button: Button | None = None
    files: Sequence[FileItem] = ()
    about: str | None = None

    def __post_init__(self) -> None:
        if self.status is None and self.type is not EmailType.REPORT:
            raise ValueError(f"A {self.type.noun} email needs a status banner (spec 4).")
        if self.status is not None and self.status.status not in _ALLOWED_STATUS[self.type]:
            allowed = ", ".join(sorted(s.name for s in _ALLOWED_STATUS[self.type]))
            raise ValueError(
                f"A {self.type.noun} email takes a {allowed} banner, not {self.status.status.name} (spec 4)."
            )
        if self.type is EmailType.REQUEST and self.button is None:
            raise ValueError("A request email needs a button (spec 4).")
        if self.type is EmailType.ALERT and not any(s.steps for s in self.sections):
            raise ValueError('An alert email needs a "What to do" list (spec 4).')
        if self.kpis and not 2 <= len(self.kpis) <= 4:
            raise ValueError(f"Use two to four KPIs, not {len(self.kpis)} (spec 6.4).")
        for section in self.sections:
            if len(section.steps) > MAX_LIST_ITEMS:
                raise ValueError(
                    f'"{section.heading}" has {len(section.steps)} list items; '
                    f"the most is {MAX_LIST_ITEMS} (spec 6.6)."
                )
        if not self.summary:
            raise ValueError("Every email needs a summary (spec 6.3).")

    def render(self) -> str:
        """Return the complete HTML document for the Graph ``body.content``."""
        self._warn_on_lengths()
        parts = [self._header()]
        if self.status is not None:
            parts.append(self._banner(self.status))
        parts.append(self._summary())
        if self.kpis:
            parts.append(self._kpis())
        parts.extend(self._section(section) for section in self.sections)
        if self.button is not None:
            parts.append(self._button(self.button))
        if self.files:
            parts.append(self._files())
        if self.about:
            parts.append(self._about())

        head, tail = _shell_parts()
        document = (
            head.replace("{{SUBJECT}}", _esc(self.subject))
            .replace("{{PREHEADER}}", _esc(self.preheader))
            .replace("{{EMAIL_TYPE}}", _esc(self.type.label))
            + "\n"
            + "\n".join(parts)
            + "\n"
            + tail.replace("{{PROCESS_NAME}}", _esc(self.process_name))
        )
        size = len(document.encode("utf-8"))
        if size > MAX_HTML_BYTES:
            log.warning(
                "automation_core.email: HTML body is %s bytes, over the %s byte limit (spec 5)",
                format_number(size),
                format_number(MAX_HTML_BYTES),
            )
        return document

    def _warn_on_lengths(self) -> None:
        if len(self.subject) > MAX_SUBJECT_LENGTH:
            log.warning(
                "automation_core.email: subject is %d characters, over %d (spec 3): %s",
                len(self.subject),
                MAX_SUBJECT_LENGTH,
                self.subject,
            )
        low, high = PREHEADER_RANGE
        if not low <= len(self.preheader) <= high:
            log.warning(
                "automation_core.email: preheader is %d characters, outside %d to %d (spec 3)",
                len(self.preheader),
                low,
                high,
            )

    def _header(self) -> str:
        # The email type is not repeated here: it is in the template's header band.
        return _block(
            f'<tr><td style="padding:0 0 8px 0; font-size:24px; line-height:32px; font-weight:300; '
            f'color:{MIDNIGHT};">{_esc(self.title)}</td></tr>'
            f'<tr><td style="padding:0 0 24px 0; font-size:13px; line-height:20px; '
            f'color:{PURPLE_60};">{_esc(self.meta)}</td></tr>'
        )

    @staticmethod
    def _banner(banner: Banner) -> str:
        status = banner.status
        word = banner.word or status.word
        return _block(
            f'<tr><td bgcolor="{status.background}" style="padding:12px 16px; '
            f"background-color:{status.background}; font-size:14px; line-height:22px; "
            f'color:{status.text_colour};"><strong style="font-weight:600;">{_esc(word)}</strong>'
            f"&nbsp;&nbsp;{_esc(banner.sentence)}</td></tr>"
            f'<tr><td height="24" style="height:24px; line-height:24px; font-size:0;">&nbsp;</td></tr>'
        )

    def _summary(self) -> str:
        paragraphs = [self.summary] if isinstance(self.summary, str) else list(self.summary)
        rows = []
        for index, paragraph in enumerate(paragraphs):
            bottom = 24 if index == len(paragraphs) - 1 else 16
            rows.append(
                f'<tr><td style="padding:0 0 {bottom}px 0; font-size:14px; line-height:22px; '
                f'color:{BLACK};">{_esc(paragraph)}</td></tr>'
            )
        return _block("".join(rows))

    def _kpis(self) -> str:
        count = len(self.kpis)
        per_row = 2 if count in (2, 4) else 3
        width = 284 if per_row == 2 else 186
        rows = []
        for start in range(0, count, per_row):
            if start:
                rows.append(
                    f'<tr><td colspan="{per_row * 2 - 1}" height="8" '
                    f'style="height:8px; line-height:8px; font-size:0;">&nbsp;</td></tr>'
                )
            cells = []
            for offset, kpi in enumerate(self.kpis[start : start + per_row]):
                if offset:
                    cells.append('<td width="8" style="width:8px; font-size:0;">&nbsp;</td>')
                value = format_number(kpi.value) if isinstance(kpi.value, int) else str(kpi.value)
                context = (
                    f'<div style="font-size:12px; line-height:18px; color:{PURPLE_60};">'
                    f"{_esc(kpi.context)}</div>"
                    if kpi.context
                    else ""
                )
                cells.append(
                    f'<td width="{width}" valign="top" bgcolor="{SURFACE}" style="width:{width}px; '
                    f'padding:16px; background-color:{SURFACE};">'
                    f'<div style="font-size:28px; line-height:34px; font-weight:600; '
                    f'color:{MIDNIGHT};">{_esc(value)}</div>'
                    f'<div style="font-size:13px; line-height:18px; color:{PURPLE_60};">'
                    f"{_esc(kpi.label)}</div>{context}</td>"
                )
            rows.append(f"<tr>{''.join(cells)}</tr>")
        rows.append(
            f'<tr><td colspan="{per_row * 2 - 1}" height="32" '
            f'style="height:32px; line-height:32px; font-size:0;">&nbsp;</td></tr>'
        )
        return _block("".join(rows))

    def _section(self, section: Section) -> str:
        parts = [
            _block(
                f'<tr><td style="padding:0 0 12px 0; font-size:16px; line-height:24px; '
                f'font-weight:600; color:{MIDNIGHT};">{_esc(section.heading)}</td></tr>'
            )
        ]
        for paragraph in section.paragraphs:
            parts.append(
                _block(
                    f'<tr><td style="padding:0 0 16px 0; font-size:14px; line-height:22px; '
                    f'color:{BLACK};">{_esc(paragraph)}</td></tr>'
                )
            )
        if section.table is not None:
            parts.append(_table(section.table))
        if section.steps:
            tag = "ol" if section.ordered else "ul"
            items = "".join(
                f'<li style="margin:0 0 8px 0;">{_esc(step)}</li>' for step in section.steps
            )
            parts.append(
                _block(
                    f'<tr><td style="padding:0 0 8px 0; font-size:14px; line-height:22px; '
                    f'color:{BLACK};"><{tag} style="margin:0; padding:0 0 0 20px;">{items}'
                    f"</{tag}></td></tr>"
                )
            )
        if section.code:
            parts.append(_block(f"<tr>{_code_cell(section.code)}</tr>"))
            parts.append(_spacer(16))
        parts.append(_spacer(16))
        return "\n".join(parts)

    @staticmethod
    def _button(button: Button) -> str:
        return (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0">'
            f'<tr><td bgcolor="{MIDNIGHT}" style="background-color:{MIDNIGHT}; padding:12px 24px;">'
            f'<a href="{_esc(button.url)}" style="font-size:14px; line-height:20px; font-weight:600; '
            f'color:{WHITE}; text-decoration:none; display:inline-block;">{_esc(button.label)}</a>'
            f'</td></tr><tr><td height="32" style="height:32px; line-height:32px; font-size:0;">'
            "&nbsp;</td></tr></table>"
        )

    def _files(self) -> str:
        rows = [
            f'<tr><td style="padding:0 0 12px 0; font-size:16px; line-height:24px; '
            f'font-weight:600; color:{MIDNIGHT};">Files</td></tr>'
        ]
        for item in self.files:
            if isinstance(item, AttachedFile):
                rows.append(
                    f'<tr><td style="padding:0 0 8px 0; font-size:14px; line-height:22px; '
                    f'color:{BLACK};">{_esc(item.name)} ({_esc(format_size(item.size_bytes))}), '
                    "attached</td></tr>"
                )
            elif isinstance(item, FolderLink):
                rows.append(_link_row(file_url(item.path), item.label))
                rows.append(f"<tr>{_code_cell(str(item.path))}</tr>")
                rows.append('<tr><td height="8" style="height:8px; line-height:8px; font-size:0;">&nbsp;</td></tr>')
            else:
                rows.append(_link_row(item.url, item.label))
        rows.append(
            '<tr><td height="24" style="height:24px; line-height:24px; font-size:0;">&nbsp;</td></tr>'
        )
        return _block("".join(rows))

    def _about(self) -> str:
        return _block(
            f'<tr><td style="padding:0 0 4px 0; font-size:12px; line-height:18px; font-weight:600; '
            f'color:{PURPLE_60};">About this {_esc(self.type.noun)}</td></tr>'
            f'<tr><td style="padding:0 0 32px 0; font-size:12px; line-height:18px; '
            f'color:{PURPLE_60};">{_esc(self.about)}</td></tr>'
        )


def _link_row(url: str, label: str) -> str:
    return (
        f'<tr><td style="padding:0 0 8px 0; font-size:14px; line-height:22px; color:{BLACK};">'
        f'<a href="{_esc(url)}" style="color:{MIDNIGHT_80}; text-decoration:underline;">'
        f"{_esc(label)}</a></td></tr>"
    )


def _code_cell(text: str) -> str:
    # Line breaks and leading spaces are written out rather than left to
    # white-space:pre-wrap, which Outlook desktop's Word engine ignores.
    lines = []
    for line in text.splitlines() or [""]:
        stripped = line.lstrip(" ")
        lines.append("&nbsp;" * (len(line) - len(stripped)) + _esc(stripped))
    return (
        f'<td bgcolor="{SURFACE}" style="padding:8px 12px; background-color:{SURFACE}; {_MONO} '
        f'font-size:12px; line-height:18px; color:{BLACK}; word-break:break-all;">'
        + "<br>".join(lines)
        + "</td>"
    )


def _get(row: Sequence[Cell], index: int) -> Cell:
    return row[index] if index < len(row) else None


def _table(table: Table) -> str:
    body_rows = [row for item in table.rows for row in (item.rows if isinstance(item, Group) else [item])]
    columns = [
        column
        if isinstance(column, Column)
        else Column(
            column,
            numeric=any(_looks_numeric(_get(row, i)) for row in body_rows)
            and all(_looks_numeric(_get(row, i)) or _get(row, i) is None for row in body_rows),
        )
        for i, column in enumerate(table.columns)
    ]
    # The first column is the row label, so it stays left-aligned even when numeric.
    aligns = ["right" if column.numeric and i else "left" for i, column in enumerate(columns)]
    last = len(columns) - 1

    def padding(index: int, vertical: str) -> str:
        top, bottom = vertical.split()
        return f"padding:{top} {0 if index == last else 8}px {bottom} {0 if index == 0 else 8}px;"

    header = []
    for index, column in enumerate(columns):
        align = aligns[index]
        width = ' width="110"' if align == "right" else ""
        width_css = "width:110px; " if align == "right" else ""
        header.append(
            f'<th scope="col" align="{align}"{width} style="{width_css}{padding(index, "0 8px")} '
            f"border-bottom:2px solid {MIDNIGHT}; font-size:12px; line-height:16px; "
            f'font-weight:600; color:{PURPLE_60}; text-align:{align};">{_esc(column.name)}</th>'
        )

    def body_row(cells: Sequence[Cell], border: str, bold: bool = False) -> str:
        weight = " font-weight:600;" if bold else ""
        out = []
        for index, align in enumerate(aligns):
            style = f"{padding(index, '10px 10px')} {border}{weight} text-align:{align};"
            out.append(
                f'<td align="{align}" style="{style}">'
                f"{_esc(_cell_text(_get(cells, index), columns[index].numeric))}</td>"
            )
        return f"<tr>{''.join(out)}</tr>"

    shown = 0
    rows_html = []
    for item in table.rows:
        if shown >= MAX_TABLE_ROWS:
            break
        if isinstance(item, Group):
            group_rows = list(item.rows)[: MAX_TABLE_ROWS - shown]
            if not group_rows:
                continue
            rows_html.append(
                f'<tr><td colspan="{len(columns)}" style="padding:12px 0 4px 0; '
                f"border-bottom:1px solid {LIGHT_GREY}; font-size:13px; line-height:18px; "
                f'font-weight:600; color:{PURPLE_60};">{_esc(item.name)}</td></tr>'
            )
            for row in group_rows:
                rows_html.append(body_row(row, f"border-bottom:1px solid {LIGHT_GREY};"))
            shown += len(group_rows)
        else:
            rows_html.append(body_row(item, f"border-bottom:1px solid {LIGHT_GREY};"))
            shown += 1
    if table.total is not None:
        rows_html.append(body_row(table.total, f"border-top:2px solid {MIDNIGHT};", bold=True))

    total_rows = max(table.total_rows or 0, len(body_rows))
    note = ""
    if total_rows > shown:
        note_text = (
            f"Showing {format_number(shown)} of {format_number(total_rows)} rows. {table.more_note}"
        )
        note = (
            f'<tr><td style="padding:8px 0 0 0; font-size:12px; line-height:18px; '
            f'color:{PURPLE_60};">{_esc(note_text.strip())}</td></tr>'
        )

    return (
        f'<table width="{CONTENT_WIDTH}" cellpadding="0" cellspacing="0" border="0" '
        f"style=\"width:{CONTENT_WIDTH}px; font-size:14px; line-height:20px; color:{BLACK};\">"
        f"<thead><tr>{''.join(header)}</tr></thead><tbody>{''.join(rows_html)}</tbody></table>"
        + (_block(note) if note else "")
        + _spacer(16)
    )


def subject_line(
    topic: str, when: date | datetime | str, *, prefix: str | None = None
) -> str:
    """Build a subject in the spec 3 pattern: ``{prefix: }{topic} | {when}``.

    ``topic`` is "process or data source: what this is", for example
    ``"Thames Water reads: data imports MI report"``. A ``date`` gives
    ``DD/MM/YYYY``; a ``datetime`` adds the UK time and zone. ``prefix`` is
    only ``"Action needed"`` or ``"Failed"``; routine reports have none.
    """
    if prefix not in (None, "Action needed", "Failed"):
        raise ValueError('prefix must be None, "Action needed" or "Failed" (spec 3).')
    if isinstance(when, datetime):
        when_text = format_datetime(when)
    elif isinstance(when, date):
        when_text = format_date(when)
    else:
        when_text = when
    lead = f"{prefix}: " if prefix else ""
    return f"{lead}{topic} | {when_text}"


_shell_cache: tuple[str, str] | None = None


def _shell_parts() -> tuple[str, str]:
    """The base template split around its content markers: (head, tail).

    The template file is the single source of the shell and footer, so a
    change to it changes every email without touching this code.
    """
    global _shell_cache
    if _shell_cache is None:
        text = resources.files(__package__).joinpath(TEMPLATE_NAME).read_text(encoding="utf-8")
        start = text.index(_CONTENT_START) + len(_CONTENT_START)
        end = text.index(_CONTENT_END)
        _shell_cache = (_strip_comments(text[:start]), _strip_comments(text[end:]))
    return _shell_cache


# The template's comments are instructions for whoever edits it, and one of
# them names the {{...}} placeholders, which would otherwise be filled in too.
# Outlook's conditional comments (<!--[if mso]>) are markup and are kept.
_COMMENT = re.compile(r"<!--(?!\[if ).*?-->\n?", re.DOTALL)


def _strip_comments(text: str) -> str:
    return _COMMENT.sub("", text)
