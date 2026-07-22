from __future__ import annotations

from pathlib import Path

import yaml

TEAM_YAML_PATH = Path(
    r"\\inspiredenergysolutions.local\DFS\Public\!IES\BPI\Automation Team\Tools\Scripts\yaml\team.yaml"
)


class ConfigurationError(RuntimeError):
    pass


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict:
    if not TEAM_YAML_PATH.exists():
        raise ConfigurationError(
            f"Required team config not found: {TEAM_YAML_PATH}\n"
            "Ensure the BPI Automation Team network share is accessible."
        )
    with TEAM_YAML_PATH.open(encoding="utf-8") as fh:
        config: dict = yaml.safe_load(fh) or {}

    project_config_path = Path.cwd() / "config" / "config.yaml"
    if project_config_path.exists():
        with project_config_path.open(encoding="utf-8") as fh:
            project_config: dict = yaml.safe_load(fh) or {}
        config = _deep_merge(config, project_config)

    return config
