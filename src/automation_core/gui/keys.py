"""
Keyboard shortcut translation.

UI Automation advertises a shortcut as a display string ("Ctrl+Alt+1").
pywinauto's `send_keys` wants a spec ("^%1"). This converts between them.

Why it matters: a hotkey is the most robust way to invoke a command there
is. It needs no selector, no backend, no traversal of a ribbon that is drawn
rather than built from windows, and it does not care which tab is active or
where the window sits on screen. Energy Manager's Web Extensions button
advertises Ctrl+Alt+1, and the Automation Anywhere bot it replaces sent
exactly `^%1`.

Shortcuts are patchy, though. Most Energy Manager ribbon buttons advertise
nothing at all, and DevExpress writes the literal string "none" for controls
without one. So a hotkey supplements a selector map, it does not replace it.

Anything not confidently translatable comes back as None. Sending a guessed
key combination to a live business application is worse than not using a
shortcut at all.
"""

from __future__ import annotations

# UIA display name -> send_keys modifier prefix.
_MODIFIERS = {
    "ctrl": "^",
    "control": "^",
    "alt": "%",
    "shift": "+",
}

# UIA display name -> send_keys token, for keys that are not a bare character.
_NAMED_KEYS = {
    "space": "{SPACE}",
    "enter": "{ENTER}",
    "return": "{ENTER}",
    "tab": "{TAB}",
    "esc": "{ESC}",
    "escape": "{ESC}",
    "del": "{DEL}",
    "delete": "{DEL}",
    "ins": "{INS}",
    "insert": "{INS}",
    "home": "{HOME}",
    "end": "{END}",
    "backspace": "{BACKSPACE}",
    "pgup": "{PGUP}",
    "pgdn": "{PGDN}",
    "page up": "{PGUP}",
    "page down": "{PGDN}",
    # Both spellings occur in the wild: UIA reports "Alt+Down Arrow" in some
    # places and "Alt+Down" in others, for the same key.
    "up": "{UP}",
    "down": "{DOWN}",
    "left": "{LEFT}",
    "right": "{RIGHT}",
    "up arrow": "{UP}",
    "down arrow": "{DOWN}",
    "left arrow": "{LEFT}",
    "right arrow": "{RIGHT}",
    "hyphen": "-",
    "plus": "+",
}

# Values that mean "this control has no shortcut". DevExpress writes "none"
# into AccessKey rather than leaving it empty.
_NOT_A_SHORTCUT = {"", "none"}


def to_send_keys(hotkey: str | None) -> str | None:
    """Translate a UIA shortcut display string into a send_keys spec.

    Returns None when the input is absent, means "no shortcut", or contains
    anything this function cannot translate with confidence.

    >>> to_send_keys("Ctrl+Alt+1")
    '^%1'
    >>> to_send_keys("Alt+Down Arrow")
    '%{DOWN}'
    >>> to_send_keys("none") is None
    True
    """
    if hotkey is None:
        return None
    text = str(hotkey).strip()
    if text.lower() in _NOT_A_SHORTCUT:
        return None

    parts = [part.strip() for part in text.split("+") if part.strip()]
    if not parts:
        return None

    prefix = ""
    for part in parts[:-1]:
        modifier = _MODIFIERS.get(part.lower())
        if modifier is None:
            return None  # an unrecognised modifier: refuse rather than guess
        prefix += modifier

    key = parts[-1]
    lowered = key.lower()

    if lowered in _NAMED_KEYS:
        return prefix + _NAMED_KEYS[lowered]

    if len(key) == 1 and key.isalnum():
        # Lower-case letters deliberately. pywinauto reads a bare uppercase
        # letter as shift-implied, so "Ctrl+S" rendered as "^S" would send
        # Ctrl+Shift+S. Shift must come only from an explicit modifier.
        return prefix + (key.lower() if key.isalpha() else key)

    if lowered.startswith("f") and lowered[1:].isdigit():
        return prefix + "{" + key.upper() + "}"

    return None


def is_shortcut(hotkey: str | None) -> bool:
    """Whether a UIA-advertised value represents a real shortcut."""
    if hotkey is None:
        return False
    return str(hotkey).strip().lower() not in _NOT_A_SHORTCUT
