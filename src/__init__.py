"""
automation_core
================

Shared utilities for Inspired-Automation team projects.

Public API:

    setup(process_name) -> Context
        Initialise logging, load config (team.yaml + config.yaml),
        determine production/dev mode. Call at the start of main().

    collect_errors(ctx) -> ErrorCollector
        Context manager. Collects non-fatal errors via .add(), catches
        critical exceptions, and dispatches notifications on exit.

Typical usage:

    from automation_core import setup, collect_errors

    def main():
        ctx = setup("MyProcess")
        with collect_errors(ctx) as errors:
            # work here
            pass

    if __name__ == "__main__":
        main()
"""

# Public API re-exports. Concrete implementations live in submodules.
# These will be filled in as the library is built.

# from .context import Context
# from .errors import ErrorCollector, collect_errors
# from . import _setup
# setup = _setup.setup

__version__ = "0.1.0"

__all__ = [
    "setup",
    "collect_errors",
    "ErrorCollector",
    "Context",
    "__version__",
]