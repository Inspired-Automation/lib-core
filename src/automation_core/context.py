from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Context:
    process_name: str
    log_file: Path
    is_production: bool
    notification_method: str
    notification_recipient: str
