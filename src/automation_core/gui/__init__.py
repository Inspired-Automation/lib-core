"""
Desktop GUI automation.

An optional layer over pywinauto that encodes the failure modes which make
GUI bots flaky, so a bot author does not have to remember them. Every rule in
here was arrived at by measuring a real application, not by reading
documentation.

    from automation_core.gui import GuiApp, controls, windows
    from automation_core.gui.apps.energy_manager import EnergyManager

Needs the `gui` extra, which is Windows only:

    pip install automation-core[gui]

Importing this package without it raises ImportError with that instruction.
`automation_core` itself never imports this, so a bot that does not touch a
GUI carries none of the dependency.
"""

from __future__ import annotations

from . import controls, grid, monitor, windows
from .app import BACKENDS, GuiApp
from .controls import READY, ControlNotReady, ControlOffScreen
from .grid import read_grid, read_grid_column
from .monitor import ErrorDialog, MonitorResult, Outcome, wait_for_operation
from .keys import is_shortcut, to_send_keys
from .windows import WindowInfo, find_window, list_windows, process_ids, wait_for_window

__all__ = [
    "GuiApp",
    "BACKENDS",
    "controls",
    "windows",
    "monitor",
    "grid",
    "read_grid",
    "read_grid_column",
    "wait_for_operation",
    "MonitorResult",
    "Outcome",
    "ErrorDialog",
    "READY",
    "ControlNotReady",
    "ControlOffScreen",
    "to_send_keys",
    "is_shortcut",
    "WindowInfo",
    "find_window",
    "list_windows",
    "process_ids",
    "wait_for_window",
]
