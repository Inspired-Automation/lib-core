"""
Tests for reading a WinForms / DevExpress grid.

The cell-name parsing is pure logic. `read_grid` is tested against a fake
wrapper, because its job is turning a flat bag of cells into ordered rows and
that is where the mistakes live, not in the COM calls.

The fixtures are real values measured from Energy Manager's `gcDataSets` and
`gcSites`.
"""

from __future__ import annotations

import pytest

from automation_core.gui.grid import (
    cell_value,
    parse_cell_name,
    read_grid,
    read_grid_column,
)


class TestParseCellName:
    """A DataItem's name is the cell's ADDRESS, not its content."""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("Utility row 0", ("Utility", 0)),
            ("Data Set Account Number row 0", ("Data Set Account Number", 0)),
            ("Total Meters row 3", ("Total Meters", 3)),
            ("I row 0", ("I", 0)),
            ("Name row 12", ("Name", 12)),
            ("Virtual For Data Mode row 1", ("Virtual For Data Mode", 1)),
        ],
    )
    def test_real_cell_names(self, name, expected):
        assert parse_cell_name(name) == expected

    @pytest.mark.parametrize(
        "name",
        ["Header Panel", "Data Panel", "lciFind", "lciCloseButton", "", None,
         "row 0", "Utility row", "Utility row x"],
    )
    def test_non_cells_are_rejected(self, name):
        """The grid subtree also holds panels and find-box items. Treating one
        as a cell would invent a row.
        """
        assert parse_cell_name(name) is None

    def test_a_column_containing_the_word_row(self):
        """Anchored at the end, so only the trailing ' row <N>' is the split."""
        assert parse_cell_name("Rows per row row 4") == ("Rows per row", 4)

    def test_row_index_is_an_int(self):
        column, row = parse_cell_name("Name row 007")
        assert row == 7 and isinstance(row, int)


class FakeCell:
    """A grid cell, as pywinauto would present one."""

    def __init__(self, name, legacy_value=None, pattern_value=None,
                 legacy_raises=False):
        self.element_info = type("Info", (), {"name": name})()
        self._legacy_value = legacy_value
        self._legacy_raises = legacy_raises
        if pattern_value is not None:
            self.iface_value = type("V", (), {"CurrentValue": pattern_value})()

    def legacy_properties(self):
        if self._legacy_raises:
            raise RuntimeError("no legacy pattern")
        props = {"ChildId": 0, "Name": self.element_info.name, "Role": 29}
        if self._legacy_value is not None:
            props["Value"] = self._legacy_value
        return props


class FakeGrid:
    def __init__(self, cells):
        self._cells = cells

    def descendants(self, control_type=None):
        return self._cells


@pytest.fixture(autouse=True)
def _resolve_to_the_fake(monkeypatch):
    """`read_grid` resolves its spec through controls.resolve; hand it back
    whatever the test passed in.
    """
    monkeypatch.setattr("automation_core.gui.controls.resolve",
                        lambda spec, **kw: spec)


class TestCellValue:
    def test_legacy_value_is_preferred(self):
        assert cell_value(FakeCell("I row 0", legacy_value="Y")) == "Y"

    def test_falls_back_to_the_value_pattern(self):
        cell = FakeCell("Account row 0", legacy_raises=True,
                        pattern_value="10194627")
        assert cell_value(cell) == "10194627"

    def test_an_empty_cell_is_none_not_its_own_name(self):
        """Returning "Location row 0" as if it were a value would be worse
        than returning nothing: it looks like data.
        """
        assert cell_value(FakeCell("Location row 0")) is None

    def test_empty_string_counts_as_no_value(self):
        assert cell_value(FakeCell("X row 0", legacy_value="")) is None


class TestReadGrid:
    def test_reads_the_energy_manager_datasets_grid(self):
        """The shape actually measured: 2 rows, 9 columns, several blank."""
        cells = []
        for row in (0, 1):
            cells += [
                FakeCell(f"Utility row {row}", legacy_value="Electricity"),
                FakeCell(f"Data Set Account Number row {row}",
                         legacy_value=f"1019462{7 + row}"),
                FakeCell(f"Location row {row}"),
                FakeCell(f"I row {row}", legacy_value="Y"),
                FakeCell(f"D row {row}", legacy_value="Y"),
                FakeCell(f"P row {row}", legacy_value="N"),
                FakeCell(f"Submeter row {row}"),
                FakeCell(f"Closed row {row}", legacy_value="X"),
                FakeCell(f"Virtual For Data Mode row {row}"),
            ]
        cells += [FakeCell("Header Panel"), FakeCell("Data Panel"),
                  FakeCell("lciFind")]

        rows = read_grid(FakeGrid(cells))
        assert len(rows) == 2
        assert rows[0]["Utility"] == "Electricity"
        assert rows[0]["Data Set Account Number"] == "10194627"
        assert rows[1]["Data Set Account Number"] == "10194628"
        assert rows[0]["Closed"] == "X"

    def test_every_row_has_every_column(self):
        """A caller writing to a database must not have to guess whether a
        missing key means empty or unread.
        """
        cells = [
            FakeCell("A row 0", legacy_value="1"),
            FakeCell("B row 0", legacy_value="2"),
            FakeCell("A row 1", legacy_value="3"),      # no B on row 1
        ]
        rows = read_grid(FakeGrid(cells))
        assert rows[0] == {"A": "1", "B": "2"}
        assert rows[1] == {"A": "3", "B": None}
        assert set(rows[0]) == set(rows[1])

    def test_rows_come_back_in_index_order(self):
        cells = [FakeCell(f"N row {i}", legacy_value=str(i)) for i in (3, 0, 2, 1)]
        rows = read_grid(FakeGrid(cells))
        assert [r["N"] for r in rows] == ["0", "1", "2", "3"]

    def test_column_order_follows_first_appearance(self):
        cells = [
            FakeCell("Utility row 0", legacy_value="Electricity"),
            FakeCell("Location row 0", legacy_value="Site"),
        ]
        rows = read_grid(FakeGrid(cells))
        assert list(rows[0]) == ["Utility", "Location"]

    def test_an_empty_grid_is_an_empty_list_not_an_error(self):
        """"No unmatched meters" is a legitimate answer and must be
        distinguishable from a failure to read.
        """
        assert read_grid(FakeGrid([])) == []
        assert read_grid(FakeGrid([FakeCell("Header Panel")])) == []

    def test_enumeration_failure_returns_empty_rather_than_raising(self):
        class Broken:
            def descendants(self, control_type=None):
                raise RuntimeError("grid went away")

        assert read_grid(Broken()) == []

    def test_the_cell_cap_is_respected(self):
        cells = [FakeCell(f"N row {i}", legacy_value="x") for i in range(50)]
        rows = read_grid(FakeGrid(cells), max_cells=10)
        assert len(rows) == 10


class TestReadGridColumn:
    def test_one_column_in_row_order(self):
        cells = [
            FakeCell("Name row 0", legacy_value="( Old WSR site )"),
            FakeCell("Code row 0"),
            FakeCell("Name row 1", legacy_value="(Glamorous) Unit 4"),
            FakeCell("Code row 1"),
        ]
        assert read_grid_column(FakeGrid(cells), "Name") == [
            "( Old WSR site )", "(Glamorous) Unit 4"]

    def test_a_missing_column_is_all_none(self):
        cells = [FakeCell("Name row 0", legacy_value="x")]
        assert read_grid_column(FakeGrid(cells), "Nope") == [None]
