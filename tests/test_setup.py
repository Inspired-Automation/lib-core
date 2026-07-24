from __future__ import annotations

import copy
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from automation_core._setup import _log_filename, setup
from automation_core.config import ConfigurationError

# A fully-valid freshservice config. Individual tests delete pieces to assert
# that setup() fails fast (before any filesystem work) with a clear message.
VALID_CONFIG = {
    "notifications": {"method": "freshservice"},
    "freshservice": {
        "api_key": "key",
        "domain": "example.freshservice.com",
        "defaults": {
            "workspace_id": 2,
            "group_id": 123,
            "requester_email": "requester@example.com",
            "type": "Incident",
            "tags": ["automation", "bpi"],
        },
    },
    "paths": {"production_root": r"I:\BPI\Automation Team\Automated Processes"},
}


def test_setup_freshservice_requires_defaults_block():
    cfg = copy.deepcopy(VALID_CONFIG)
    del cfg["freshservice"]["defaults"]
    with patch("automation_core._setup.load_config", return_value=cfg):
        with pytest.raises(ConfigurationError, match="defaults"):
            setup("TestProcess")


def test_setup_freshservice_requires_each_default_field():
    cfg = copy.deepcopy(VALID_CONFIG)
    del cfg["freshservice"]["defaults"]["tags"]
    with patch("automation_core._setup.load_config", return_value=cfg):
        with pytest.raises(ConfigurationError, match="tags"):
            setup("TestProcess")


def test_setup_freshservice_rejects_empty_default_field():
    cfg = copy.deepcopy(VALID_CONFIG)
    cfg["freshservice"]["defaults"]["requester_email"] = ""
    with patch("automation_core._setup.load_config", return_value=cfg):
        with pytest.raises(ConfigurationError, match="requester_email"):
            setup("TestProcess")


class TestLogFilename:
    NOW = datetime(2026, 7, 24, 10, 15, 0, tzinfo=timezone.utc)

    def test_job_id_makes_filename_unique(self):
        # With a job id the name is globally unique, so concurrent runs of the
        # same bot never open and interleave into one file.
        assert _log_filename("Demo", self.NOW, 42) == "Demo_20260724_101500_job42.log"

    def test_no_job_id_falls_back_to_pid(self):
        # A hand run has no job id; the pid keeps two same-second runs apart.
        name = _log_filename("Demo", self.NOW, None)
        assert name == f"Demo_20260724_101500_p{os.getpid()}.log"

    def test_unsafe_job_id_is_sanitised(self):
        # job_id is an int today; guard anyway so nothing odd reaches a path.
        name = _log_filename("Demo", self.NOW, "a/b c")
        assert name == "Demo_20260724_101500_joba-b-c.log"


# A minimal happy-path config: email method (no freshservice validation),
# production_root pointed at a scratch dir so setup() can create the log tree.
def _email_config(production_root: Path) -> dict:
    return {
        "notifications": {"method": "email", "default_recipient": "t@example.com"},
        "paths": {"production_root": str(production_root)},
    }


def _close_log_handlers():
    root = logging.getLogger()
    for handler in root.handlers:
        handler.close()
    root.handlers.clear()


def test_setup_exposes_job_id_and_names_log_after_it(tmp_path: Path):
    job_file = tmp_path / "job.json"
    job_file.write_text(
        json.dumps({"job_id": 123, "bot": "demo", "params": {"region": "north"}}),
        encoding="utf-8",
    )
    cfg = _email_config(tmp_path)
    try:
        with patch("automation_core._setup.load_config", return_value=cfg):
            ctx = setup("Demo", argv=["--job-file", str(job_file)])
        assert ctx.job_id == 123
        assert ctx.params == {"region": "north"}
        assert ctx.log_file.name.endswith("_job123.log")
        assert ctx.log_file.exists()
    finally:
        _close_log_handlers()


def test_setup_hand_run_has_no_job_id(tmp_path: Path):
    cfg = _email_config(tmp_path)
    try:
        with patch("automation_core._setup.load_config", return_value=cfg):
            ctx = setup("Demo", argv=[])
        assert ctx.job_id is None
        assert ctx.params == {}
        assert ctx.log_file.name.endswith(f"_p{os.getpid()}.log")
    finally:
        _close_log_handlers()
