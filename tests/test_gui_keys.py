"""
Tests for keyboard shortcut translation.

Pure logic, no GUI and no pywinauto, so these run everywhere including CI on
a non-Windows box: `automation_core.gui.keys` deliberately imports nothing
from the `gui` extra.

The values here are real ones observed from UI Automation on Energy Manager,
not invented examples.
"""

from __future__ import annotations

import pytest

from automation_core.gui.keys import is_shortcut, to_send_keys


class TestRealWorldShortcuts:
    def test_energy_manager_web_extensions(self):
        """The one that matters. Matches the button's tooltip, and matches
        the send_keys spec the Automation Anywhere bot already used.
        """
        assert to_send_keys("Ctrl+Alt+1") == "^%1"

    @pytest.mark.parametrize(
        "hotkey,expected",
        [
            ("Alt+Space", "%{SPACE}"),
            ("Space", "{SPACE}"),
            ("Alt+Hyphen", "%-"),
            ("Alt+Down Arrow", "%{DOWN}"),
            ("Alt+Down", "%{DOWN}"),
        ],
    )
    def test_observed_on_energy_manager(self, hotkey, expected):
        assert to_send_keys(hotkey) == expected


class TestModifiers:
    @pytest.mark.parametrize(
        "hotkey,expected",
        [
            ("Ctrl+S", "^s"),
            ("Control+S", "^s"),
            ("Alt+F", "%f"),
            ("Shift+A", "+a"),
            ("Ctrl+Alt+2", "^%2"),
            ("Ctrl+Shift+F4", "^+{F4}"),
        ],
    )
    def test_modifier_combinations(self, hotkey, expected):
        assert to_send_keys(hotkey) == expected

    def test_letters_are_lowercased(self):
        """pywinauto reads a bare uppercase letter as shift-implied, so
        "Ctrl+S" rendered as "^S" would send Ctrl+Shift+S. Shift must come
        only from an explicit modifier.
        """
        assert to_send_keys("Ctrl+S") == "^s"
        assert to_send_keys("Ctrl+Shift+S") == "^+s"

    def test_digits_are_untouched(self):
        assert to_send_keys("Ctrl+Alt+1") == "^%1"
        assert to_send_keys("Alt+9") == "%9"


class TestFunctionKeys:
    @pytest.mark.parametrize(
        "hotkey,expected",
        [("F1", "{F1}"), ("Ctrl+F5", "^{F5}"), ("Alt+F12", "%{F12}")],
    )
    def test_function_keys(self, hotkey, expected):
        assert to_send_keys(hotkey) == expected


class TestRefusals:
    """Anything not confidently translatable must come back as None.
    Sending a guessed key combination to a live business application is
    worse than not using a shortcut at all.
    """

    @pytest.mark.parametrize(
        "value",
        ["none", "None", "NONE", "  none  ", "", "   ", None],
    )
    def test_devexpress_none_is_not_a_shortcut(self, value):
        """DevExpress writes the literal string "none" into AccessKey for
        controls that have no shortcut.
        """
        assert to_send_keys(value) is None

    @pytest.mark.parametrize(
        "value",
        ["Meta+X", "Hyper+A", "Ctrl+Some Unknown Key", "-", "Windows+R"],
    )
    def test_unrecognised_input_is_refused(self, value):
        assert to_send_keys(value) is None


class TestIsShortcut:
    def test_real_shortcuts(self):
        assert is_shortcut("Ctrl+Alt+1")
        assert is_shortcut("F1")

    def test_absent_shortcuts(self):
        assert not is_shortcut(None)
        assert not is_shortcut("")
        assert not is_shortcut("none")
        assert not is_shortcut("  NONE ")
