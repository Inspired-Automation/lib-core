from __future__ import annotations

import logging
import sys
from pathlib import Path

_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def configure(log_file: Path, process_name: str) -> None:
    root = logging.getLogger()
    # Clear any handlers left over from a previous configure() call (e.g. in tests).
    root.handlers.clear()
    root.setLevel(logging.INFO)

    formatter = logging.Formatter(_FORMAT)

    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)
