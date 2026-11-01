from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .assist import DEFAULT_POLL_S, DEFAULT_TIMEOUT_S, Assist
from .decide import DEFAULT_TIMEOUT_S as DECIDE_TIMEOUT_S
from .decide import Decision, decide


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
    # Path to the --job-file/CR_JOB_FILE job.json this run was invoked with.
    # None for a hand run. Used only to locate the cr_errors.json sidecar the
    # Control Room agent reads back after the bot exits (see errors.py) and
    # the assist marker files (see assist.py); not meant for bots to read
    # directly.
    job_file: Path | None = None

    def request_assist(
        self,
        reason: str,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        wait: bool = True,
        poll_s: float = DEFAULT_POLL_S,
    ) -> Assist:
        """Ask the Control Room for a person to help in this run's session.

        Use as a context manager. Entering writes ``assist.request`` with
        ``reason`` and, when ``wait`` is true, blocks up to ``timeout_s``
        for the agent to make the session live (``assist.url``); a timeout
        logs a warning and never raises. Leaving, including on an
        exception, writes ``assist.release``. A no-op with a logged warning
        when there is no job file (a hand run). See assist.py.
        """
        job_dir = self.job_file.parent if self.job_file is not None else None
        return Assist(job_dir, reason, timeout_s=timeout_s, wait=wait,
                      poll_s=poll_s)

    def decide(
        self,
        agent: str,
        inputs: Mapping[str, Any] | None = None,
        /,
        *,
        timeout_s: float = DECIDE_TIMEOUT_S,
        **fields: Any,
    ) -> Decision:
        """Ask one of the Control Room's decision agents and get its answer.

        ``ctx.decide("failure_triage", error_text=text)`` sends the keyword
        arguments as the agent's inputs (``inputs`` may carry them as a dict
        instead, for a name that clashes with a parameter). Returns a
        ``Decision``: ``decision["cause"]`` is the decided choice or None
        when the Control Room did not stand behind one. Raises
        ``DecisionUnavailable`` when there is no answer to be had: a hand
        run, an agent older than 0.72.0, the kill switch off, an unknown
        agent, a refused token, or the Control Room unreachable. See
        decide.py.
        """
        merged: dict[str, Any] = dict(inputs or {})
        merged.update(fields)
        return decide(self.job_file, agent, merged, timeout_s=timeout_s)
