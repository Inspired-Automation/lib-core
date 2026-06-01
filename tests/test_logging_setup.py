from __future__ import annotations

import logging
from pathlib import Path

from automation_core.logging_setup import configure


def test_configure_attaches_two_handlers(tmp_path: Path):
    log_file = tmp_path / "test.log"
    configure(log_file, "TestProcess")
    root = logging.getLogger()
    assert len(root.handlers) == 2
    handler_types = {type(h).__name__ for h in root.handlers}
    assert "FileHandler" in handler_types
    assert "StreamHandler" in handler_types


def test_configure_twice_does_not_accumulate_handlers(tmp_path: Path):
    log_file = tmp_path / "test.log"
    configure(log_file, "TestProcess")
    configure(log_file, "TestProcess")
    root = logging.getLogger()
    assert len(root.handlers) == 2


def test_log_file_is_created(tmp_path: Path):
    log_file = tmp_path / "test.log"
    configure(log_file, "TestProcess")
    logging.info("hello from test")
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "hello from test" in content


def test_root_logger_level_is_info(tmp_path: Path):
    log_file = tmp_path / "test.log"
    configure(log_file, "TestProcess")
    assert logging.getLogger().level == logging.INFO
