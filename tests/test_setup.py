from __future__ import annotations

import copy
from unittest.mock import patch

import pytest

from automation_core._setup import setup
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
