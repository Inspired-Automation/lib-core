from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


class ReopeningFileHandler(logging.FileHandler):
    """
    A FileHandler that survives its file's handle going stale.

    Log files live on a network share. When the share drops for a moment (SMB
    session reset, drive remapped, machine asleep), the open handle dies and a
    plain FileHandler fails every write for the rest of the run, printing a
    "--- Logging error ---" traceback per record. This handler closes the dead
    handle, reopens the file in append mode and writes the record again.

    While the file cannot be reopened, records are counted rather than written
    (the console handler still has them) and reopening is retried at most once
    every `retry_seconds`, so an unreachable share cannot stall the bot on
    every log call. Only the first failure of an outage prints a traceback.
    When the file is back, a WARNING line records how many lines it is missing.
    """

    def __init__(self, filename: Path, mode: str = "a", encoding: str | None = None,
                 retry_seconds: float = 30.0) -> None:
        super().__init__(filename, mode=mode, encoding=encoding)
        # Any reopen appends: reopening in "w" mode would wipe what was written.
        self.mode = "a"
        self.retry_seconds = retry_seconds
        self.clock = time.monotonic
        self._retry_after = 0.0
        self._missed = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            self.handleError(record)
            return

        if self._missed:
            if self.clock() < self._retry_after:
                self._missed += 1
                return
            msg = self._gap_notice() + self.terminator + msg

        for attempt in (1, 2):
            try:
                self._write(msg)
                self._missed = 0
                return
            except OSError:
                self._drop_stream()
                # handleError prints the exception being handled, so call it in here.
                if attempt == 2 and not self._missed:
                    self.handleError(record)
        self._missed += 1
        self._retry_after = self.clock() + self.retry_seconds

    def _gap_notice(self) -> str:
        return self.format(logging.makeLogRecord({
            "name": "automation_core",
            "levelno": logging.WARNING,
            "levelname": "WARNING",
            "msg": "Log file was unavailable; %d line(s) are missing from this file "
                   "(they were still written to the console)",
            "args": (self._missed,),
        }))

    def _write(self, msg: str) -> None:
        if self.stream is None:
            self.stream = self._open()
        self.stream.write(msg + self.terminator)
        self.stream.flush()

    def _drop_stream(self) -> None:
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


def configure(log_file: Path, process_name: str) -> None:
    root = logging.getLogger()
    # Clear any handlers left over from a previous configure() call (e.g. in tests).
    root.handlers.clear()
    root.setLevel(logging.INFO)

    formatter = logging.Formatter(_FORMAT)

    file_handler = ReopeningFileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)
