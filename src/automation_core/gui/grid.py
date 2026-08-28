"""
Reading a WinForms or DevExpress data grid.

A grid is the one thing a desktop application shows that a bot most often
needs to *read* rather than click, and it is the hardest, because the cells
are drawn rather than created as windows. The win32 backend sees a grid as a
pair of scrollbars. UIA does better, but not in the obvious way:

  - The grid itself exposes **no usable Table or Grid pattern**. Asking for
    one raises `NoPatternInterfaceError`, and pywinauto's own `column_count()`
    refuses with "not work properly for WinForms DataGrid, use cell".
  - Individual cells *are* exposed, as `DataItem` elements whose **name is the
    cell's address**, not its content: `"Location row 0"`,
    `"Total Meters row 3"`.
  - The **value** lives in the LegacyIAccessible `Value` property, with the
    ValuePattern as a second route. A cell with no value simply has neither.

So a grid is read by enumerating its cells, parsing the column and row out of
each name, and pulling the value separately. That is what this module does.

Measured against Energy Manager's `gcDataSets`: cells came back as
`'I row 0'` with `Value: 'Y'`, `'P row 0'` with `Value: 'N'`, and an account
number as `'10194627'`.

Two caveats that will bite:

  1. **Cells exist only while the grid is rendered.** They vanish when another
     MDI child form covers it. Activate the grid's form first.
  2. **Only realised rows are present.** A virtualised grid materialises the
     rows it is showing, so a long grid must be scrolled to be read in full.
     `read_grid` reports what it found rather than pretending otherwise;
     compare `len(rows)` against the application's own row count if it offers
     one.

Windows only. Requires the `gui` extra.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

#: A DataItem name is "<Column> row <N>". Anchored at the end so a column
#: whose own name contains " row " does not confuse the split.
_CELL_NAME = re.compile(r"^(?P<column>.+?) row (?P<row>\d+)$")


def parse_cell_name(name: str | None) -> tuple[str, int] | None:
    """Split a DataItem name into (column, row index), or None.

    >>> parse_cell_name("Total Meters row 3")
    ('Total Meters', 3)
    >>> parse_cell_name("Header Panel") is None
    True
    """
    if not name:
        return None
    match = _CELL_NAME.match(str(name).strip())
    if match is None:
        return None
    return match.group("column"), int(match.group("row"))


def cell_value(wrapper: Any) -> str | None:
    """The content of a grid cell, or None if it has none.

    Tries LegacyIAccessible first because it is the one that answered most
    reliably in practice, then the ValuePattern. Never falls back to the
    element's name: the name is the cell's *address*, and returning
    "Location row 0" as if it were a value would be worse than returning
    nothing.
    """
    try:
        legacy = wrapper.legacy_properties()
        if isinstance(legacy, dict):
            value = legacy.get("Value")
            if value not in (None, ""):
                return str(value)
    except Exception:
        pass

    try:
        value = wrapper.iface_value.CurrentValue
        if value not in (None, ""):
            return str(value)
    except Exception:
        pass

    return None


def read_grid(
    grid_spec: Any,
    *,
    timeout: float = 30.0,
    max_cells: int = 20000,
) -> list[dict[str, str | None]]:
    """Read a grid as a list of rows, each a {column: value} dict.

    `grid_spec` is a pywinauto specification for the grid, resolved on the
    **uia** backend: the cells do not exist under win32 at all.

    Rows come back ordered by their row index, and every row carries the same
    keys, so a missing cell is an explicit None rather than an absent key.
    That matters when the result is written to a database.

    Returns an empty list when the grid has no realised cells, which usually
    means it is empty or is not the rendered form. It does not raise for that:
    an empty grid is a legitimate state, and `UnmatchedMeters` depends on
    telling "no unmatched meters" apart from "could not read".
    """
    from .controls import resolve

    wrapper = resolve(grid_spec, timeout=timeout, condition="visible ready")

    try:
        descendants = wrapper.descendants(control_type="DataItem")
    except Exception:
        logger.exception("Could not enumerate grid cells")
        return []

    if len(descendants) > max_cells:
        logger.warning(
            "Grid exposed %d cells, above the %d cap; reading the first %d only",
            len(descendants), max_cells, max_cells,
        )
        descendants = descendants[:max_cells]

    by_row: dict[int, dict[str, str | None]] = {}
    columns: list[str] = []
    skipped = 0

    for cell in descendants:
        try:
            name = cell.element_info.name
        except Exception:
            skipped += 1
            continue
        parsed = parse_cell_name(name)
        if parsed is None:
            skipped += 1          # Header Panel, Data Panel, find-panel items
            continue
        column, row_index = parsed
        if column not in columns:
            columns.append(column)
        by_row.setdefault(row_index, {})[column] = cell_value(cell)

    if not by_row:
        logger.info(
            "Grid exposed no addressable cells (%d elements skipped). Either it "
            "is empty, or its form is not the rendered one.", skipped)
        return []

    # Every row gets every column, so a caller writing to a database does not
    # have to guess whether a missing key means empty or unread.
    rows: list[dict[str, str | None]] = []
    for row_index in sorted(by_row):
        row = by_row[row_index]
        rows.append({column: row.get(column) for column in columns})

    logger.info("Read %d row(s) x %d column(s) from the grid: %s",
                len(rows), len(columns), ", ".join(columns))
    return rows


def read_grid_column(grid_spec: Any, column: str, **kwargs: Any) -> list[str | None]:
    """One column's values, in row order. Convenience over `read_grid`."""
    return [row.get(column) for row in read_grid(grid_spec, **kwargs)]


def grid_row_count(grid_spec: Any, **kwargs: Any) -> int:
    """How many rows are currently realised in the grid.

    Not necessarily the grid's true row count: a virtualised grid only
    materialises what it is showing. Treat this as "rows readable right now".
    """
    return len(read_grid(grid_spec, **kwargs))
