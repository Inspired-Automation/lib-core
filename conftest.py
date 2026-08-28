"""
Make the tests import the working tree, not whatever is installed.

Without this, `import automation_core` resolves to the released wheel in
site-packages, so the suite silently tests the last release instead of the
code being changed. That is fine until you add a module: the tests then fail
with ModuleNotFoundError for code that is plainly there.

Prepending `src` keeps `pip install -e .` optional and makes a plain
`pytest` from a fresh clone do the obvious thing.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
