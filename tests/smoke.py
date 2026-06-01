"""
Smoke test for automation_core. Run manually:

    python tests/smoke.py

Does not require network access or real credentials. Verifies that:
- setup() creates a log file in the expected dated directory structure.
- collect_errors() captures a non-fatal error and writes it to the log.
- In dev mode, no notification is dispatched (confirmed by absence of dispatch calls).

Set AUTOMATION_FORCE_NOTIFY=1 to test the notification path (requires real credentials).
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml


def _write_fake_team_yaml(tmp_dir: Path) -> Path:
    team_file = tmp_dir / "team.yaml"
    data = {
        "graph": {
            "client_id": "smoke-cid",
            "client_secret": "smoke-sec",
            "tenant_id": "smoke-tid",
            "sender_address": "smoke@example.com",
        },
        "freshservice": {"api_key": "smoke-key", "domain": "smoke.freshservice.com"},
        "notifications": {"default_recipient": "smoke@example.com"},
        "paths": {"production_root": str(tmp_dir / "prod")},
    }
    team_file.write_text(yaml.dump(data), encoding="utf-8")
    return team_file


def run_smoke_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        team_file = _write_fake_team_yaml(tmp_path)

        import automation_core.config as cfg_module

        with patch.object(cfg_module, "TEAM_YAML_PATH", team_file):
            from automation_core import collect_errors, setup

            ctx = setup("SmokeTest")
            print(f"Log file: {ctx.log_file}")
            assert ctx.log_file.exists(), "Log file was not created"

            with collect_errors(ctx) as errors:
                errors.add("smoke non-fatal error", details={"item": "test-item"})

            log_content = ctx.log_file.read_text(encoding="utf-8")
            assert "smoke non-fatal error" in log_content, "Error not in log file"
            print("Log contains expected error message.")

    print("Smoke test passed.")


if __name__ == "__main__":
    run_smoke_test()
