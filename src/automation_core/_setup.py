from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from . import _internal_log as _ilog
from .config import load_config
from .context import Context
from .logging_setup import configure

# Single config store per process. A second call to setup() overwrites the first,
# which is intentional for the single-script-per-process use case. Test suites
# must patch or reset this between test runs.
_config: dict | None = None


def _get_config() -> dict:
    if _config is None:
        raise RuntimeError("automation_core.setup() has not been called yet.")
    return _config


def setup(process_name: str) -> Context:
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

    log_root_override: str | None = _config.get("logging", {}).get("log_root")
    if log_root_override:
        log_root = Path(log_root_override)
    else:
        production_root = _config.get("paths", {}).get("production_root", "")
        log_root = Path(production_root) / process_name

    now = datetime.now(tz=timezone.utc)
    dated_dir = (
        log_root
        / "logs"
        / now.strftime("%Y")
        / now.strftime("%B")
        / now.strftime("%d")
    )
    dated_dir.mkdir(parents=True, exist_ok=True)

    log_file = dated_dir / f"{process_name}_{now.strftime('%Y%m%d_%H%M%S')}.log"

    configure(log_file, process_name)
    logging.info("automation_core: setup complete for '%s'", process_name)

    return Context(
        process_name=process_name,
        log_file=log_file,
        is_production=is_production,
        notification_method=notification_method,
        notification_recipient=notification_recipient,
    )


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
