"""
Discovery tooling: work out how to address an application's controls.

The driver in `automation_core.gui` acts on controls you already know about.
This package is how you come to know about them, and how you refresh that
knowledge when an application is upgraded.

It ships with the library on purpose. The selector maps in
`automation_core.gui.apps` are *generated* by these tools, and a map whose
regeneration instructions point at a tool nobody has is a map that quietly
rots. Keeping the instrument beside the artifact it produces is the whole
point.

Two tools, both headless:

**Snapshot** maps an entire window under both backends, proves a selector for
every worthwhile control by actually executing it, and can diff two snapshots
to show what an action changed.

```
python -m automation_core.gui.discover.snapshot --list
python -m automation_core.gui.discover.snapshot --title "SystemsLink" \\
        --backend both --verify --label main-shell
python -m automation_core.gui.discover.snapshot --diff before.json after.json
```

**Watch** snapshots every window a process opens, as it opens. That is the
only way to catch a dialog that appears and is dismissed, and the only way to
map a launch or login sequence.

```
python -m automation_core.gui.discover.watcher --process EM.exe --duration 300
python -m automation_core.gui.discover.watcher --process EM.exe \\
        --launch "\\\\server\\share\\App.exe" --kill-first
```

Both write JSON to a `captures/` folder under the current working directory.

`probe` holds the shared logic: dual-backend metadata, candidate selector
generation with real match counts, stability grading, control-type
normalisation across UIA, CLR and Win32 vocabularies, and tree walking.

A note on what is *not* here: the hover inspector, which draws a highlight
around whatever is under the cursor. It needs a human at the keyboard and it
needs `pynput` and `tkinter`, neither of which belongs in a runtime library.
It lives in the discovery project instead.

Windows only. Requires the `gui` extra: `pip install automation-core[gui]`.
"""

from __future__ import annotations

from typing import Any

from . import probe
from .probe import (
    ACTIONABLE_CONTROL_TYPES,
    INTERACTIVE_CONTROL_TYPES,
    STABILITY_CAPTION,
    STABILITY_NAMED,
    STABILITY_NUMERIC,
    STABILITY_POSITIONAL,
    capture_at_point,
    class_name_regex,
    flatten_tree,
    is_synthesised_auto_id,
    is_volatile_class_name,
    normalised_control_type,
    recommend,
    verify_selector,
    verify_tree_selectors,
    walk_tree,
)

# `snapshot` and `watcher` are loaded on demand rather than imported here.
# Both are runnable with `python -m`, and eagerly importing them means the
# module is already in sys.modules when runpy executes it, which produces a
# RuntimeWarning about unpredictable behaviour. Lazy access (PEP 562) keeps
# `discover.snapshot_window(...)` working without that.
_LAZY = {
    "snapshot": "snapshot",
    "watcher": "watcher",
    "snapshot_window": "snapshot",
    "diff_trees": "snapshot",
    "WindowWatcher": "watcher",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(f".{module_name}", __name__)
    return module if name == module_name else getattr(module, name)


def __dir__() -> list[str]:
    return sorted(set(__all__))


__all__ = [
    "probe",
    "snapshot",
    "watcher",
    "snapshot_window",
    "diff_trees",
    "WindowWatcher",
    "capture_at_point",
    "walk_tree",
    "flatten_tree",
    "verify_selector",
    "verify_tree_selectors",
    "recommend",
    "normalised_control_type",
    "is_volatile_class_name",
    "class_name_regex",
    "is_synthesised_auto_id",
    "STABILITY_NAMED",
    "STABILITY_NUMERIC",
    "STABILITY_CAPTION",
    "STABILITY_POSITIONAL",
    "INTERACTIVE_CONTROL_TYPES",
    "ACTIONABLE_CONTROL_TYPES",
]
