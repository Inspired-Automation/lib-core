from __future__ import annotations

import logging
from pathlib import Path

import pytest

from automation_core.logging_setup import ReopeningFileHandler, configure


class _DeadStream:
    """Stands in for a file handle on a share that has dropped."""

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        raise OSError(22, "Invalid argument")

    def close(self) -> None:
        raise OSError(22, "Invalid argument")


def _file_handler() -> ReopeningFileHandler:
    return next(h for h in logging.getLogger().handlers if isinstance(h, ReopeningFileHandler))


def _kill_stream(handler: ReopeningFileHandler) -> None:
    handler.stream.close()
    handler.stream = _DeadStream()


@pytest.fixture
def clock() -> list[float]:
    return [1000.0]


def test_configure_attaches_two_handlers(tmp_path: Path):
    log_file = tmp_path / "test.log"
    configure(log_file, "TestProcess")
    root = logging.getLogger()
    assert len(root.handlers) == 2
    assert any(isinstance(h, ReopeningFileHandler) for h in root.handlers)
    assert any(type(h) is logging.StreamHandler for h in root.handlers)


def test_stale_handle_is_reopened_without_losing_the_line(tmp_path: Path, capsys):
    log_file = tmp_path / "test.log"
    configure(log_file, "TestProcess")
    logging.info("before the share dropped")
    _kill_stream(_file_handler())

    logging.info("after the share dropped")

    content = log_file.read_text(encoding="utf-8")
    assert "before the share dropped" in content
    assert "after the share dropped" in content
    assert "missing" not in content
    assert "Logging error" not in capsys.readouterr().err


def test_outage_counts_lines_and_notes_the_gap(tmp_path: Path, capsys, clock):
    log_file = tmp_path / "test.log"
    configure(log_file, "TestProcess")
    handler = _file_handler()
    handler.clock = lambda: clock[0]
    reopen = handler._open

    def unreachable():
        raise FileNotFoundError(2, "No such file or directory")

    handler._open = unreachable
    _kill_stream(handler)
    logging.info("lost one")          # fails: one traceback, retry blocked for 30s
    clock[0] += 5
    logging.info("lost two")          # inside the retry window: counted, no attempt
    clock[0] += 30
    logging.info("lost three")        # retried, still unreachable: counted quietly
    clock[0] += 31
    handler._open = reopen
    logging.info("share is back")

    content = log_file.read_text(encoding="utf-8")
    assert "lost" not in content
    assert "3 line(s) are missing" in content
    assert content.index("3 line(s) are missing") < content.index("share is back")
    assert capsys.readouterr().err.count("--- Logging error ---") == 1


def test_reopen_appends_even_in_write_mode(tmp_path: Path):
    log_file = tmp_path / "test.log"
    handler = ReopeningFileHandler(log_file, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    try:
        handler.handle(logging.makeLogRecord({"msg": "first"}))
        _kill_stream(handler)
        handler.handle(logging.makeLogRecord({"msg": "second"}))
    finally:
        handler.close()
    assert log_file.read_text(encoding="utf-8").split() == ["first", "second"]


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
