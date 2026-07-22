from __future__ import annotations

import json
from pathlib import Path

from automation_core._setup import _read_job_params


def _write_job_file(tmp_path: Path, payload: object) -> str:
    job_file = tmp_path / "job.json"
    job_file.write_text(json.dumps(payload), encoding="utf-8")
    return str(job_file)


def test_no_job_file_gives_empty_params():
    # A hand run in dev has no --job-file argument at all.
    assert _read_job_params([]) == {}


def test_reads_params_object(tmp_path: Path):
    job_file = _write_job_file(
        tmp_path, {"job_id": 1, "bot": "demo", "params": {"region": "north"}}
    )
    params = _read_job_params(["--job-file", job_file])
    assert params == {"region": "north"}


def test_job_file_without_params_key_gives_empty(tmp_path: Path):
    job_file = _write_job_file(tmp_path, {"job_id": 1, "bot": "demo"})
    assert _read_job_params(["--job-file", job_file]) == {}


def test_missing_job_file_does_not_raise(tmp_path: Path):
    missing = str(tmp_path / "no_such_file.json")
    assert _read_job_params(["--job-file", missing]) == {}


def test_malformed_job_file_does_not_raise(tmp_path: Path):
    job_file = tmp_path / "job.json"
    job_file.write_text("{not valid json", encoding="utf-8")
    assert _read_job_params(["--job-file", str(job_file)]) == {}


def test_non_object_params_treated_as_none(tmp_path: Path):
    job_file = _write_job_file(
        tmp_path, {"job_id": 1, "bot": "demo", "params": ["north"]}
    )
    assert _read_job_params(["--job-file", job_file]) == {}


def test_bot_own_arguments_are_left_alone(tmp_path: Path):
    # parse_known_args must ignore arguments the bot defines for itself and
    # still pick --job-file out of the mix.
    job_file = _write_job_file(
        tmp_path, {"job_id": 1, "bot": "demo", "params": {"k": "v"}}
    )
    argv = ["--dry-run", "--job-file", job_file, "--count", "5"]
    assert _read_job_params(argv) == {"k": "v"}
