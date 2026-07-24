from __future__ import annotations

import json
from pathlib import Path

from .config import ConfigurationError

# The declarations file a bot ships at its repo root when it consumes run params.
# The Control Room orchestrator reads this file to render a GUI that prompts the
# user for each param before a run; lib-core reads it to validate the params
# actually supplied. Keep this filename stable — the orchestrator looks for
# exactly this name at the project root.
PARAMS_FILE = "params.json"

# The "type" values a param declaration may use, mapped to the Python type(s) a
# supplied value is allowed to be. `bool` is handled specially (see _type_ok):
# in Python `bool` is a subclass of `int`, so an integer/number param must not
# silently accept True/False.
_ALLOWED_TYPES = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
}


def load_param_definitions(root: Path | None = None) -> list[dict]:
    """Load and validate a bot's ``params.json`` parameter declarations.

    Returns the list of declared params (each a dict with ``name``, ``type``,
    ``required`` and ``description``), or an empty list when the bot ships no
    ``params.json`` — a bot that takes no run params simply omits the file.

    Unlike reading the *supplied* params (which never fails a run), a malformed
    ``params.json`` is a developer error in the bot's own repo: it means the
    orchestrator cannot render the parameter GUI. So this raises
    ``ConfigurationError`` — mirroring how ``setup()`` treats a bad
    ``freshservice.defaults`` block — rather than being swallowed.
    """
    params_path = (root or Path.cwd()) / PARAMS_FILE
    if not params_path.exists():
        return []

    try:
        with params_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ConfigurationError(
            f"{PARAMS_FILE} could not be read as JSON: {exc}"
        ) from exc

    if not isinstance(data, dict) or not isinstance(data.get("params"), list):
        raise ConfigurationError(
            f'{PARAMS_FILE} must be a JSON object with a "params" array; '
            "see lib-core-spec.md §3.4."
        )

    definitions: list[dict] = []
    seen: set[str] = set()
    for index, entry in enumerate(data["params"]):
        definitions.append(_validate_definition(entry, index, seen))
    return definitions


def _validate_definition(entry: object, index: int, seen: set[str]) -> dict:
    where = f"{PARAMS_FILE} params[{index}]"
    if not isinstance(entry, dict):
        raise ConfigurationError(f"{where} must be a JSON object.")

    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigurationError(f"{where} is missing a non-empty string 'name'.")
    if name in seen:
        raise ConfigurationError(f"{PARAMS_FILE} declares param {name!r} more than once.")
    seen.add(name)

    param_type = entry.get("type")
    if param_type not in _ALLOWED_TYPES:
        raise ConfigurationError(
            f"{where} ({name!r}) has type {param_type!r}; "
            f"allowed types are {sorted(_ALLOWED_TYPES)}."
        )

    # `required` and `description` are optional in the file for authoring
    # convenience but always present (normalised) in what we return, so callers
    # and the GUI can rely on the shape. `required` defaults to False.
    required = entry.get("required", False)
    if not isinstance(required, bool):
        raise ConfigurationError(f"{where} ({name!r}) 'required' must be true or false.")

    description = entry.get("description", "")
    if not isinstance(description, str):
        raise ConfigurationError(f"{where} ({name!r}) 'description' must be a string.")

    choices = entry.get("choices")
    if choices is not None and not isinstance(choices, list):
        raise ConfigurationError(f"{where} ({name!r}) 'choices' must be a list.")

    return {
        "name": name,
        "type": param_type,
        "required": required,
        "description": description,
        "choices": choices,
    }


def _type_ok(value: object, param_type: str) -> bool:
    allowed = _ALLOWED_TYPES[param_type]
    # bool is a subclass of int: keep booleans out of integer/number and vice versa.
    if param_type in ("integer", "number") and isinstance(value, bool):
        return False
    if param_type == "boolean":
        return isinstance(value, bool)
    return isinstance(value, allowed)


def validate_params(definitions: list[dict], provided: dict) -> list[str]:
    """Check supplied params against their declarations.

    Returns a list of human-readable problem strings (empty when everything
    checks out): required params that were not supplied, supplied values whose
    type does not match the declaration, and supplied params not declared at
    all. This returns problems rather than raising so the caller can decide how
    loudly to react — ``setup()`` logs them as warnings and carries on, since
    the orchestrator is the primary gate on required params.
    """
    problems: list[str] = []
    declared_by_name = {d["name"]: d for d in definitions}

    for definition in definitions:
        name = definition["name"]
        if definition["required"] and name not in provided:
            problems.append(f"required param {name!r} was not supplied")

    for name, value in provided.items():
        definition = declared_by_name.get(name)
        if definition is None:
            problems.append(f"param {name!r} was supplied but is not declared in {PARAMS_FILE}")
            continue
        if not _type_ok(value, definition["type"]):
            problems.append(
                f"param {name!r} should be {definition['type']} "
                f"but got {type(value).__name__}"
            )
            continue
        choices = definition.get("choices")
        if choices and value not in choices:
            problems.append(
                f"param {name!r} value {value!r} is not one of the "
                f"declared choices {choices}"
            )

    return problems
