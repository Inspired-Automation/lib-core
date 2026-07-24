"""Code-first run-parameter declarations.

A bot declares the parameters it reads from ``ctx.params`` in code, at module
scope, with :func:`param`::

    from automation_core import param

    MESSAGE = param("message", str, required=False,
                    description="Header line written at the top of the log")

    def main():
        ctx = setup("MyBot")
        message = MESSAGE.read(ctx.params)   # coerced value, or the default

This replaces the hand-written ``params.json`` declarations file: the code is
the single source of truth. The Control Room reads these declarations straight
from the deployed source (it parses the ``param(...)`` calls; it does not run
the bot) to build its parameter-entry form, so a declaration can never drift
from or lag the code that uses it.

Keep the calls declarative: literal arguments at module scope. The Control
Room's reader understands literals, not values computed at runtime, so a
``param()`` built from a variable or inside a function would not be seen.

The accepted types mirror the widgets the Control Room renders. Pass the
Python builtin (``str``/``int``/``float``/``bool``) or its JSON-type name
(``"string"``/``"integer"``/``"number"``/``"boolean"``). ``choices`` restricts
the value to a fixed set, which the Control Room shows as a dropdown.
"""
from __future__ import annotations

from typing import Any

from .config import ConfigurationError

# Python builtin to JSON-type name. bool before int matters conceptually (bool
# is an int subclass) but here the keys are the types themselves, so the map is
# exact and unambiguous.
_TYPE_FROM_BUILTIN = {str: "string", int: "integer", float: "number",
                      bool: "boolean"}
_ALLOWED_TYPES = ("string", "integer", "number", "boolean")

# Every param() call in the running process registers here so setup() can
# validate the supplied params against what the bot declares. One bot runs per
# process, so a module-level list is the right scope; tests reset it.
_REGISTRY: list["Param"] = []


def _reset_registry() -> None:
    """Clear the process registry. For tests only."""
    _REGISTRY.clear()


def _type_name(type_: Any, name: str) -> str:
    if isinstance(type_, str):
        candidate = type_
    else:
        candidate = _TYPE_FROM_BUILTIN.get(type_)
    if candidate not in _ALLOWED_TYPES:
        raise ConfigurationError(
            f"param {name!r} has an unsupported type {type_!r}; use one of "
            f"str, int, float, bool (or {', '.join(_ALLOWED_TYPES)})."
        )
    return candidate


class Param:
    """One declared run parameter. Created via :func:`param`."""

    def __init__(self, name: str, type: Any = str, *, required: bool = False,
                 description: str = "", choices: list | None = None,
                 default: Any = None) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError("param name must be a non-empty string.")
        self.name = name.strip()
        self.type = _type_name(type, self.name)
        self.required = bool(required)
        self.description = description or ""
        self.choices = list(choices) if choices is not None else None
        self.default = default
        _REGISTRY.append(self)

    def read(self, params: dict) -> Any:
        """Return this param's value from a supplied params dict, or the
        declared default when it was not supplied. Absent keys, JSON null and
        blank strings all count as not supplied, so an optional left blank in
        the Control Room falls through to the default. The Control Room has
        already coerced the value to this param's type, so no coercion happens
        here."""
        if not isinstance(params, dict) or self.name not in params:
            return self.default
        value = params[self.name]
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return self.default
        return value

    def to_definition(self) -> dict:
        """The normalised declaration dict, matching what
        ``params.load_param_definitions`` returns from a params.json entry so
        the same validator serves both."""
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "description": self.description,
            "choices": self.choices,
        }


def param(name: str, type: Any = str, *, required: bool = False,
          description: str = "", choices: list | None = None,
          default: Any = None) -> Param:
    """Declare a run parameter the bot reads from ``ctx.params``.

    Call at module scope and keep the arguments literal so the Control Room can
    read the declaration from source. Returns a :class:`Param`; read its value
    at runtime with ``.read(ctx.params)``.
    """
    return Param(name, type, required=required, description=description,
                 choices=choices, default=default)


def declared_params() -> list[Param]:
    """Every param() declared in this process, in declaration order."""
    return list(_REGISTRY)
