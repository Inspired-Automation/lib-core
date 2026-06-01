# Changelog

All notable changes to `automation-core` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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