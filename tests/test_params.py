from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation_core._setup import _read_job_file
from automation_core.config import ConfigurationError
from automation_core.params import (
    PARAMS_FILE,
    load_param_definitions,
    validate_params,
)


def _write_job_file(tmp_path: Path, payload: object) -> str:
    job_file = tmp_path / "job.json"
    job_file.write_text(json.dumps(payload), encoding="utf-8")
    return str(job_file)


def test_no_job_file_gives_no_job_id_or_params():
    # A hand run in dev has no --job-file argument at all.
    assert _read_job_file([]) == (None, {})


def test_reads_job_id_and_params_object(tmp_path: Path):
    job_file = _write_job_file(
        tmp_path, {"job_id": 42, "bot": "demo", "params": {"region": "north"}}
    )
    assert _read_job_file(["--job-file", job_file]) == (42, {"region": "north"})


def test_job_file_without_params_key_gives_empty(tmp_path: Path):
    job_file = _write_job_file(tmp_path, {"job_id": 7, "bot": "demo"})
    assert _read_job_file(["--job-file", job_file]) == (7, {})


def test_job_file_without_job_id_gives_none(tmp_path: Path):
    # A hand-crafted or older job file may omit job_id; params still read.
    job_file = _write_job_file(tmp_path, {"bot": "demo", "params": {"k": "v"}})
    assert _read_job_file(["--job-file", job_file]) == (None, {"k": "v"})


def test_missing_job_file_does_not_raise(tmp_path: Path):
    missing = str(tmp_path / "no_such_file.json")
    assert _read_job_file(["--job-file", missing]) == (None, {})


def test_malformed_job_file_does_not_raise(tmp_path: Path):
    job_file = tmp_path / "job.json"
    job_file.write_text("{not valid json", encoding="utf-8")
    assert _read_job_file(["--job-file", str(job_file)]) == (None, {})


def test_non_object_params_treated_as_none(tmp_path: Path):
    job_file = _write_job_file(
        tmp_path, {"job_id": 1, "bot": "demo", "params": ["north"]}
    )
    assert _read_job_file(["--job-file", job_file]) == (None, {})


def test_bot_own_arguments_are_left_alone(tmp_path: Path):
    # parse_known_args must ignore arguments the bot defines for itself and
    # still pick --job-file out of the mix.
    job_file = _write_job_file(
        tmp_path, {"job_id": 9, "bot": "demo", "params": {"k": "v"}}
    )
    argv = ["--dry-run", "--job-file", job_file, "--count", "5"]
    assert _read_job_file(argv) == (9, {"k": "v"})


def test_cr_job_file_env_used_when_no_arg(tmp_path, monkeypatch):
    # A run.bat that does not forward its args to Python leaves --job-file off
    # the command line; the agent's CR_JOB_FILE env var is the fallback.
    job_file = _write_job_file(
        tmp_path, {"job_id": 77, "bot": "demo", "params": {"k": "v"}}
    )
    monkeypatch.setenv("CR_JOB_FILE", job_file)
    assert _read_job_file([]) == (77, {"k": "v"})


def test_job_file_arg_takes_precedence_over_env(tmp_path, monkeypatch):
    arg_file = tmp_path / "arg.json"
    arg_file.write_text(
        json.dumps({"job_id": 1, "bot": "demo", "params": {"src": "arg"}}),
        encoding="utf-8",
    )
    env_file = tmp_path / "env.json"
    env_file.write_text(
        json.dumps({"job_id": 2, "bot": "demo", "params": {"src": "env"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CR_JOB_FILE", str(env_file))
    assert _read_job_file(["--job-file", str(arg_file)]) == (1, {"src": "arg"})


def _write_params_file(root: Path, payload: object) -> None:
    (root / PARAMS_FILE).write_text(json.dumps(payload), encoding="utf-8")


class TestLoadParamDefinitions:
    def test_no_file_gives_empty_list(self, tmp_path: Path):
        # A bot that takes no run params simply ships no params.json.
        assert load_param_definitions(tmp_path) == []

    def test_loads_and_normalises_declarations(self, tmp_path: Path):
        _write_params_file(
            tmp_path,
            {
                "params": [
                    {
                        "name": "region",
                        "type": "string",
                        "required": True,
                        "description": "Region to process",
                    },
                    # required/description omitted -> normalised defaults.
                    {"name": "dry_run", "type": "boolean"},
                ]
            },
        )
        defs = load_param_definitions(tmp_path)
        assert defs == [
            {
                "name": "region",
                "type": "string",
                "required": True,
                "description": "Region to process",
                "choices": None,
            },
            {
                "name": "dry_run",
                "type": "boolean",
                "required": False,
                "description": "",
                "choices": None,
            },
        ]

    def test_malformed_json_raises(self, tmp_path: Path):
        (tmp_path / PARAMS_FILE).write_text("{not json", encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_param_definitions(tmp_path)

    def test_missing_params_array_raises(self, tmp_path: Path):
        _write_params_file(tmp_path, {"nope": []})
        with pytest.raises(ConfigurationError):
            load_param_definitions(tmp_path)

    def test_unknown_type_raises(self, tmp_path: Path):
        _write_params_file(tmp_path, {"params": [{"name": "x", "type": "date"}]})
        with pytest.raises(ConfigurationError):
            load_param_definitions(tmp_path)

    def test_missing_name_raises(self, tmp_path: Path):
        _write_params_file(tmp_path, {"params": [{"type": "string"}]})
        with pytest.raises(ConfigurationError):
            load_param_definitions(tmp_path)

    def test_duplicate_name_raises(self, tmp_path: Path):
        _write_params_file(
            tmp_path,
            {"params": [{"name": "x", "type": "string"}, {"name": "x", "type": "integer"}]},
        )
        with pytest.raises(ConfigurationError):
            load_param_definitions(tmp_path)

    def test_non_bool_required_raises(self, tmp_path: Path):
        _write_params_file(
            tmp_path, {"params": [{"name": "x", "type": "string", "required": "yes"}]}
        )
        with pytest.raises(ConfigurationError):
            load_param_definitions(tmp_path)


class TestValidateParams:
    DEFS = [
        {"name": "region", "type": "string", "required": True, "description": ""},
        {"name": "limit", "type": "integer", "required": False, "description": ""},
        {"name": "dry_run", "type": "boolean", "required": False, "description": ""},
    ]

    def test_all_good_no_problems(self):
        provided = {"region": "north", "limit": 5, "dry_run": True}
        assert validate_params(self.DEFS, provided) == []

    def test_missing_required_is_reported(self):
        problems = validate_params(self.DEFS, {"limit": 5})
        assert any("region" in p and "not supplied" in p for p in problems)

    def test_optional_missing_is_fine(self):
        assert validate_params(self.DEFS, {"region": "north"}) == []

    def test_type_mismatch_is_reported(self):
        problems = validate_params(self.DEFS, {"region": 5})
        assert any("region" in p and "string" in p for p in problems)

    def test_bool_is_not_accepted_as_integer(self):
        # bool is a subclass of int; an integer param must reject True/False.
        problems = validate_params(self.DEFS, {"region": "n", "limit": True})
        assert any("limit" in p for p in problems)

    def test_undeclared_param_is_reported(self):
        problems = validate_params(self.DEFS, {"region": "north", "surprise": 1})
        assert any("surprise" in p and "not declared" in p for p in problems)
