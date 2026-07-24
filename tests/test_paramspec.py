from __future__ import annotations

import pytest

from automation_core import param
from automation_core.config import ConfigurationError
from automation_core.params import validate_params
from automation_core.paramspec import _reset_registry, declared_params


@pytest.fixture(autouse=True)
def _clean_registry():
    _reset_registry()
    yield
    _reset_registry()


class TestParamDeclaration:
    def test_builtin_types_map_to_json_type_names(self):
        assert param("a", str).type == "string"
        assert param("b", int).type == "integer"
        assert param("c", float).type == "number"
        assert param("d", bool).type == "boolean"

    def test_type_may_be_given_as_json_name(self):
        assert param("a", "string").type == "string"
        assert param("b", "integer").type == "integer"

    def test_defaults(self):
        p = param("region", str)
        assert p.required is False
        assert p.description == ""
        assert p.choices is None
        assert p.default is None

    def test_all_fields(self):
        p = param("region", str, required=True, description="Region",
                  choices=["north", "south"], default="north")
        assert (p.name, p.type, p.required, p.description) == (
            "region", "string", True, "Region")
        assert p.choices == ["north", "south"]
        assert p.default == "north"

    def test_unsupported_type_raises(self):
        with pytest.raises(ConfigurationError, match="unsupported type"):
            param("x", dict)

    def test_empty_name_raises(self):
        with pytest.raises(ConfigurationError, match="non-empty"):
            param("  ", str)

    def test_registry_accumulates_in_order(self):
        param("a", str)
        param("b", int)
        assert [p.name for p in declared_params()] == ["a", "b"]


class TestParamRead:
    def test_returns_supplied_value(self):
        p = param("message", str)
        assert p.read({"message": "hi"}) == "hi"

    def test_absent_returns_default(self):
        p = param("message", str, default="fallback")
        assert p.read({}) == "fallback"

    def test_blank_and_null_treated_as_absent(self):
        p = param("message", str, default="fallback")
        assert p.read({"message": ""}) == "fallback"
        assert p.read({"message": "   "}) == "fallback"
        assert p.read({"message": None}) == "fallback"

    def test_non_dict_returns_default(self):
        p = param("message", str, default="fallback")
        assert p.read(None) == "fallback"


class TestRegistryValidation:
    def test_choices_violation_is_reported(self):
        defs = [param("region", str, choices=["north", "south"]).to_definition()]
        problems = validate_params(defs, {"region": "east"})
        assert any("region" in p and "choices" in p for p in problems)

    def test_valid_choice_passes(self):
        defs = [param("region", str, choices=["north", "south"]).to_definition()]
        assert validate_params(defs, {"region": "north"}) == []

    def test_required_missing_reported(self):
        defs = [param("region", str, required=True).to_definition()]
        problems = validate_params(defs, {})
        assert any("region" in p and "not supplied" in p for p in problems)
