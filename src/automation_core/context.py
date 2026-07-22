from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Context:
    process_name: str
    log_file: Path
    is_production: bool
    notification_method: str
    notification_recipient: str
    # Run parameters handed to the bot by the Control Room. Populated from the
    # --job-file the agent passes on the command line (job.json's "params"
    # object); an empty dict when the bot is run by hand or scheduled without
    # any params. Read values with ctx.params.get("name", default).
    params: dict = field(default_factory=dict)
