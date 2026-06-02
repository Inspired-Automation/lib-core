from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from automation_core.config import ConfigurationError, load_config


def _yaml(data: dict) -> str:
    return yaml.dump(data)


TEAM_DATA = {
    "graph": {
        "client_id": "cid",
        "client_secret": "sec",
        "tenant_id": "tid",
        "sender_address": "bot@example.com",
    },
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
    "notifications": {"default_recipient": "team@example.com"},
    "paths": {"production_root": r"I:\BPI\Automation Team\Automated Processes"},
}


def _patch_team(tmp_path: Path, data: dict | None = None):
    team_file = tmp_path / "team.yaml"
    team_file.write_text(_yaml(data or TEAM_DATA), encoding="utf-8")
    return team_file


def test_missing_team_yaml_raises(tmp_path: Path):
    missing = tmp_path / "no_such_file.yaml"
    with patch("automation_core.config.TEAM_YAML_PATH", missing):
        with pytest.raises(ConfigurationError, match="team.yaml"):
            load_config()


def test_loads_team_yaml(tmp_path: Path):
    team_file = _patch_team(tmp_path)
    with patch("automation_core.config.TEAM_YAML_PATH", team_file):
        config = load_config()
    assert config["notifications"]["default_recipient"] == "team@example.com"


def test_absent_project_config_uses_team_defaults(tmp_path: Path):
    team_file = _patch_team(tmp_path)
    with (
        patch("automation_core.config.TEAM_YAML_PATH", team_file),
        patch("pathlib.Path.cwd", return_value=tmp_path),
    ):
        config = load_config()
    assert config["notifications"]["default_recipient"] == "team@example.com"


def test_project_config_deep_merges(tmp_path: Path):
    team_file = _patch_team(tmp_path)
    project_cfg_dir = tmp_path / "config"
    project_cfg_dir.mkdir()
    (project_cfg_dir / "config.yaml").write_text(
        _yaml({"notifications": {"method": "freshservice", "recipient": "other@example.com"}}),
        encoding="utf-8",
    )
    with (
        patch("automation_core.config.TEAM_YAML_PATH", team_file),
        patch("pathlib.Path.cwd", return_value=tmp_path),
    ):
        config = load_config()
    # Overridden keys
    assert config["notifications"]["method"] == "freshservice"
    assert config["notifications"]["recipient"] == "other@example.com"
    # Non-overridden key survives
    assert config["notifications"]["default_recipient"] == "team@example.com"


def test_project_config_does_not_clobber_sibling_keys(tmp_path: Path):
    team_file = _patch_team(tmp_path)
    project_cfg_dir = tmp_path / "config"
    project_cfg_dir.mkdir()
    (project_cfg_dir / "config.yaml").write_text(
        _yaml({"notifications": {"method": "email"}}),
        encoding="utf-8",
    )
    with (
        patch("automation_core.config.TEAM_YAML_PATH", team_file),
        patch("pathlib.Path.cwd", return_value=tmp_path),
    ):
        config = load_config()
    assert config["notifications"]["default_recipient"] == "team@example.com"
    assert config["graph"]["client_id"] == "cid"


def test_project_config_overrides_nested_freshservice_default(tmp_path: Path):
    team_file = _patch_team(tmp_path)
    project_cfg_dir = tmp_path / "config"
    project_cfg_dir.mkdir()
    (project_cfg_dir / "config.yaml").write_text(
        _yaml({"freshservice": {"defaults": {"group_id": 999}}}),
        encoding="utf-8",
    )
    with (
        patch("automation_core.config.TEAM_YAML_PATH", team_file),
        patch("pathlib.Path.cwd", return_value=tmp_path),
    ):
        config = load_config()
    defaults = config["freshservice"]["defaults"]
    # Overridden nested value
    assert defaults["group_id"] == 999
    # Sibling defaults survive the deep-merge
    assert defaults["workspace_id"] == 2
    assert defaults["tags"] == ["automation", "bpi"]
    # Sibling freshservice keys survive
    assert config["freshservice"]["api_key"] == "key"
    assert config["freshservice"]["domain"] == "example.freshservice.com"
