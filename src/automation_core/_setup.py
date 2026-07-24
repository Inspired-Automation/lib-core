from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import _internal_log as _ilog
from .config import load_config
from .context import Context
from .logging_setup import configure
from .params import load_param_definitions, validate_params

# Single config store per process. A second call to setup() overwrites the first,
# which is intentional for the single-script-per-process use case. Test suites
# must patch or reset this between test runs.
_config: dict | None = None


def _get_config() -> dict:
    if _config is None:
        raise RuntimeError("automation_core.setup() has not been called yet.")
    return _config


def setup(process_name: str, argv: list[str] | None = None) -> Context:
    global _config
    _config = load_config()

    is_production = _detect_production(_config)

    notification_method: str = (
        _config.get("notifications", {}).get("method") or "email"
    )
    notification_recipient: str = (
        _config.get("notifications", {}).get("recipient")
        or _config.get("notifications", {}).get("default_recipient", "")
    )

    if notification_method == "freshservice":
        # Lazy import avoids the _setup <-> notifications import cycle.
        from .notifications import freshservice

        freshservice.validate_defaults(_config)

    log_root_override: str | None = _config.get("logging", {}).get("log_root")
    if log_root_override:
        log_root = Path(log_root_override)
    else:
        production_root = _config.get("paths", {}).get("production_root", "")
        log_root = Path(production_root) / process_name

    # Read the job file once, before naming the log, so the job id can go
    # into the filename. Never fails the run (see _read_job_file).
    job_id, params = _read_job_file(argv)

    now = datetime.now(tz=timezone.utc)
    dated_dir = (
        log_root
        / "logs"
        / now.strftime("%Y")
        / now.strftime("%B")
        / now.strftime("%d")
    )
    dated_dir.mkdir(parents=True, exist_ok=True)

    log_file = dated_dir / _log_filename(process_name, now, job_id)

    configure(log_file, process_name)

    # If the bot declares its params in params.json, validate what was actually
    # supplied against those declarations. A malformed params.json is a bot
    # developer error and propagates; validation mismatches are logged loudly
    # but do not fail the run (the orchestrator is the primary gate on params).
    definitions = load_param_definitions()
    for problem in validate_params(definitions, params):
        _ilog.logger.warning("run param validation: %s", problem)

    logging.info(
        "automation_core: setup complete for '%s' (%d run param(s))",
        process_name,
        len(params),
    )

    return Context(
        process_name=process_name,
        log_file=log_file,
        is_production=is_production,
        notification_method=notification_method,
        notification_recipient=notification_recipient,
        params=params,
        job_id=job_id,
    )


# Characters that must not reach a Windows filename. job_id is an int today,
# so this never fires in practice; it is a belt-and-braces guard against a
# hand-crafted job file carrying something odd.
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\s]+')


def _log_filename(process_name: str, now: datetime, job_id: object) -> str:
    """Build the log filename, folding in the Control Room job id.

    With a job id the name is globally unique (one file per job), so
    concurrent runs of the same bot (an ``allow_overlap`` bot, a multi-session
    node, or a dev hand run overlapping a production run) never open and
    interleave into the same file. Without one, the process id keeps two
    same-second hand runs on a host in separate files.
    """
    stamp = now.strftime("%Y%m%d_%H%M%S")
    if job_id is not None:
        token = _UNSAFE_FILENAME.sub("-", str(job_id)).strip("-") or "x"
        suffix = f"job{token}"
    else:
        suffix = f"p{os.getpid()}"
    return f"{process_name}_{stamp}_{suffix}.log"


def _read_job_file(argv: list[str] | None) -> tuple[int | None, dict]:
    """Return ``(job_id, params)`` the Control Room handed this bot.

    The agent invokes bots as `python.exe <script> --job-file <path>`; that
    job.json carries a "job_id" and a "params" object set on the schedule,
    trigger or API call. We parse only --job-file (parse_known_args leaves any
    arguments the bot defines for itself untouched) and never fail the bot over
    it: a hand run has no --job-file, and a missing or malformed file is logged
    and treated as no job id and no params rather than killing an otherwise
    healthy run.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--job-file")
    args, _ = parser.parse_known_args(sys.argv[1:] if argv is None else argv)

    if not args.job_file:
        return None, {}

    try:
        with open(args.job_file, encoding="utf-8") as fh:
            job = json.load(fh)
        job_id = job.get("job_id")
        params = job.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("job params is not a JSON object")
        return job_id, params
    except Exception:
        _ilog.logger.warning(
            "could not read run params from --job-file %r; continuing with none",
            args.job_file,
            exc_info=True,
        )
        return None, {}


def _detect_production(config: dict) -> bool:
    try:
        cwd = Path.cwd().resolve()
        prod_root = Path(config["paths"]["production_root"]).resolve()
        return cwd == prod_root or cwd.is_relative_to(prod_root)
    except Exception:
        _ilog.logger.warning(
            "prod-root detection failed; assuming development mode", exc_info=True
        )
        return False
