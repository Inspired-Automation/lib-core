# CLAUDE.md

## Purpose
`automation-core` is the shared Python library used by every project in the Inspired-Automation team. It provides standard logging setup, configuration loading from `team.yaml` and `config.yaml`, error collection, and notification dispatch (email via Microsoft Graph or tickets via Freshservice).

The goal is to remove ~500 lines of boilerplate from every project. All projects behave identically when something goes wrong because they all go through this library.

## Tech Stack
- Python 3.13+
- `PyYAML` for config loading (`yaml.safe_load` only)
- `requests` for HTTP calls to Graph and Freshservice
- `msal` for Microsoft Graph token acquisition
- Pure standard library `logging` for logging configuration

## Databases
- None. This library does not touch databases. Database access is the responsibility of the projects that use it.

## External Integrations
- **Microsoft Graph** for sending error notification emails
- **Freshservice** for raising error notification tickets

## Configuration
- Reads `team.yaml` from `\\inspiredenergysolutions.local\DFS\Public\!IE\BPI\Automation Team\Tools\Scripts\yaml\team.yaml` (required)
- Reads `config/config.yaml` from the consuming project's root (optional)
- The library itself has no config file

## Migrations
| File | DEV applied | PROD applied |
|------|-------------|--------------|
| _no migrations - library does not touch databases_ | | |

## Key Business Logic
- **Critical errors:** uncaught exceptions in `collect_errors` context manager → immediate notification with traceback → re-raise.
- **Non-fatal errors:** `errors.add(...)` → written to log immediately + held for end-of-run summary.
- **Notification rules:**
  - Zero errors → no notification.
  - Non-fatal errors → summary email/ticket at end of run.
  - Critical error → immediate email/ticket with traceback.
- **Dev vs prod detection:** based on `Path.cwd()` vs `paths.production_root` in `team.yaml`. Override via `AUTOMATION_FORCE_NOTIFY=1` env var.

## Known Gotchas
- `team.yaml` path is hardcoded by design. If the location ever changes, this is a breaking change for every project.
- If Graph or Freshservice API calls fail when dispatching a notification, the library logs loudly but does not crash the consuming project.

## Change Log
- 2026-07-15: `TEAM_YAML_PATH` switched from mapped `I:` drive to UNC path under `\\inspiredenergysolutions.local\DFS\Public\!IE\...`.
- 2026-07-08: Releases now include a built wheel as a GitHub release asset, so projects can pin by wheel URL; RELEASING.md updated.
- 2026-07-08: Lowered minimum Python from 3.14 to 3.13 (`requires-python = ">=3.13"`); fixed stale `__version__`; released v1.2.2.
- 2026-06-02: Added dispatch method to logging and removed debug statements; released v1.2.1
- 2026-06-02: Added required `freshservice.defaults` block (validated at setup) and normalised Freshservice URL handling; released v1.2.0.
- 2026-06-01: Added `notifications.enabled` (auto/always/never) to control dispatch; released v1.1.0.
- 2026-06-01: Implemented v1.0.0 per `lib-core-spec.md`.
- 2026-06-01: Initial scaffold from spec.

## Outstanding TODOs
- Tag and release v1.0.0 on GitHub (`git tag v1.0.0 && git push --tags`).