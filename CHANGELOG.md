# Changelog

All notable changes to `automation-core` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.3.0] - 2026-07-22

### Added
- `Context.params`: run parameters handed to a bot by the Control Room.
  `setup()` reads a `--job-file <path>` argument off the command line (parsed
  with `parse_known_args`, so a bot's own arguments are left untouched) and
  exposes the job.json `params` object as `ctx.params`. Reading params never
  fails a run: a hand run with no `--job-file`, a missing/malformed file, or a
  non-object `params` value all yield an empty dict (the latter two are logged
  as a warning). Read values with `ctx.params.get("name", default)`.
- Machine-readable JSON meta block appended to every notification body, wrapped
  in `---AUTOMATION-META-BEGIN---` / `---AUTOMATION-META-END---` markers so
  downstream automations (e.g. a Power Automate flow) can extract and parse it
  without scraping the human-readable text. Includes schema version, process
  name, severity, error count, timestamp, host, user, log file path, and the
  list of errors (exceptions stringified). Marker strings and schema version
  (`META_SCHEMA_VERSION = 1`) are exported from `automation_core.notifications`.

### Changed
- `TEAM_YAML_PATH` now uses the UNC share
  (`\\inspiredenergysolutions.local\DFS\Public\!IE\...`) instead of the
  mapped `I:` drive letter, so config loading works without a drive mapping.

## [1.2.2] - 2026-07-08

### Changed
- Lowered the minimum supported Python version from 3.14 to 3.13
  (`requires-python = ">=3.13"`). No code changes were required; the library
  uses no 3.14-only features.

### Fixed
- `automation_core.__version__` was still `1.2.0`; it now matches the package
  version again.

## [1.2.1] - 2026-06-02
   
   ### Added
   - `automation_core: notification dispatched via <method> for <process_name>` log line after successful dispatch, so it's clear from logs whether notification was actually sent.
   
   ### Removed
   - Internal debug print statements left over from troubleshooting.

## [1.2.0] - 2026-06-02

### Added
- Required `freshservice.defaults` block in `team.yaml` (`workspace_id`, `group_id`,
  `requester_email`, `type`, `tags`), validated at setup when `notifications.method`
  is `freshservice`. Its values populate the ticket payload (`workspace_id`,
  `group_id`, `type`, `email` from `requester_email`, and `tags`), with a `critical`
  or `summary` tag appended per ticket. Missing or empty fields raise
  `ConfigurationError` at setup, naming the offending field.

### Fixed
- Freshservice API URL construction now normalises the configured `freshservice.domain`
  via `normalize_base_url()` (strips surrounding whitespace, a leading `http(s)://`
  scheme, trailing slashes, and a trailing `/api/v2`), so any reasonable input format
  produces a valid URL instead of a malformed one.

## [1.1.0] - 2026-06-01

### Added
- `notifications.enabled` config setting (`auto` | `always` | `never`), read from the merged
  config to control notification dispatch. `auto` (or absent) keeps the existing path-based
  behaviour (production notifies; development suppresses unless `AUTOMATION_FORCE_NOTIFY=1`);
  `always` forces dispatch regardless of path or env var; `never` suppresses dispatch
  unconditionally (the env var does not override it).

## [1.0.0] - 2026-06-01

### Added
- `setup(process_name)` -- loads team.yaml and config.yaml, configures logging, detects prod/dev mode, returns `Context`.
- `collect_errors(ctx)` context manager -- collects non-fatal errors via `errors.add()`, dispatches summary notification on clean exit with errors, dispatches critical notification with traceback on uncaught exception.
- `ErrorCollector` with `add()`, `count`, and `has_errors`.
- Email notifications via Microsoft Graph (`msal` + `requests`).
- Freshservice ticket notifications via Freshservice REST API.
- Dev/prod detection via `Path.cwd()` vs `paths.production_root`; override with `AUTOMATION_FORCE_NOTIFY=1`.
- Deep-merge of team.yaml and per-project config.yaml.

## [0.1.0] - 2026-06-01

Initial scaffold.