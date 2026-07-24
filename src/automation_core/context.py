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
    # The Control Room job id this run belongs to, read from the same
    # --job-file (job.json's "job_id"). None for a hand run or any run the
    # Control Room did not start. It is folded into the log filename so
    # concurrent runs of the same bot never share a log file, and included in
    # failure notifications so an alert links back to the exact job.
    job_id: int | None = None
