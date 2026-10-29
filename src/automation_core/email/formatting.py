"""Value formatting rules from email-design-spec.md section 3.

Each helper returns display text, never HTML: escaping is the renderer's job.

UK local time is worked out here from the fixed UK daylight saving rule rather
than with ``zoneinfo``. Windows has no system time zone database, so
``ZoneInfo("Europe/London")`` needs the ``tzdata`` package, and a bot on a
server without it would fail to send its alert. The rule (BST from 01:00 UTC
on the last Sunday in March to 01:00 UTC on the last Sunday in October) is
set in law and has not changed since 2002.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from pathlib import PureWindowsPath
from urllib.parse import quote

# Characters left unencoded in a file: URL path. RFC 3986 sub-delimiters plus
# ":" and "@", so a share such as "!IES" or "C$" stays readable.
_URL_SAFE = "/!$&'()*+,;=:@-._~"


def format_number(value: float, decimals: int = 0) -> str:
    """``27674`` -> ``"27,674"``. Thousands separators always (spec 3)."""
    return f"{value:,.{decimals}f}"


def format_percent(value: float) -> str:
    """A value already in percent, to one decimal place: ``55.83`` -> ``"55.8%"``."""
    return f"{value:,.1f}%"


def percent_of(part: float, whole: float) -> str:
    """``part`` as a percentage of ``whole``: ``percent_of(1, 8)`` -> ``"12.5%"``.

    A zero ``whole`` gives ``"0.0%"`` rather than raising, because an empty
    run is a normal thing for a report to describe.
    """
    if not whole:
        return format_percent(0)
    return format_percent(part / whole * 100)


def format_money(value: float) -> str:
    """``1234.5`` -> ``"£1,234.50"``; negatives as ``"-£1,234.50"``."""
    sign = "-" if value < 0 else ""
    return f"{sign}£{abs(value):,.2f}"


def format_size(num_bytes: int) -> str:
    """A file size for the files list: ``"820 KB"``, ``"5 MB"``, ``"4.2 MB"``."""
    if num_bytes < 1024 * 1024:
        return f"{max(1, round(num_bytes / 1024)):,} KB"
    megabytes = round(num_bytes / (1024 * 1024), 1)
    if megabytes == int(megabytes):
        return f"{int(megabytes):,} MB"
    return f"{megabytes:,} MB"


def _last_sunday(year: int, month: int) -> date:
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    return last_day - timedelta(days=(last_day.weekday() - 6) % 7)


def to_uk_time(moment: datetime) -> tuple[datetime, str]:
    """Convert to UK local time and return it with its zone, ``"GMT"`` or ``"BST"``.

    A naive datetime is taken to be in the machine's local time, as Python's
    own ``astimezone()`` does, so ``datetime.now()`` works as expected.
    """
    utc = moment.astimezone(timezone.utc)
    bst_start = datetime.combine(_last_sunday(utc.year, 3), datetime.min.time(), timezone.utc)
    bst_end = datetime.combine(_last_sunday(utc.year, 10), datetime.min.time(), timezone.utc)
    one_am = timedelta(hours=1)
    if bst_start + one_am <= utc < bst_end + one_am:
        return (utc + timedelta(hours=1)).replace(tzinfo=None), "BST"
    return utc.replace(tzinfo=None), "GMT"


def format_date(value: date | datetime) -> str:
    """``DD/MM/YYYY``. A datetime is converted to UK local time first."""
    if isinstance(value, datetime):
        value = to_uk_time(value)[0]
    return value.strftime("%d/%m/%Y")


def format_time(moment: datetime) -> str:
    """24-hour UK local time with its zone: ``"14:30 GMT"``."""
    local, zone = to_uk_time(moment)
    return f"{local:%H:%M} {zone}"


def format_datetime(moment: datetime, *, weekday: bool = False) -> str:
    """``"27/10/2026 14:30 GMT"``, or ``"Tue 27/10/2026 14:30 GMT"`` with ``weekday``."""
    local, zone = to_uk_time(moment)
    prefix = f"{local:%a} " if weekday else ""
    return f"{prefix}{local:%d/%m/%Y %H:%M} {zone}"


def file_url(path: str | PureWindowsPath) -> str:
    """A ``file:`` URL for a UNC or drive path, with spaces encoded as ``%20``.

    ``\\\\server\\share\\a b`` -> ``file://///server/share/a%20b`` (spec 6.8),
    ``C:\\Reports\\x.xlsx`` -> ``file:///C:/Reports/x.xlsx``.
    """
    text = str(path).replace("\\", "/")
    if text.startswith("//"):
        return "file:///" + quote(text, safe=_URL_SAFE)
    return "file:///" + quote(text.lstrip("/"), safe=_URL_SAFE)
