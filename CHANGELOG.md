# Changelog

All notable changes to `automation-core` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.12.0] - 2026-08-28

### Added
- `automation_core.gui.monitor`, for operations that run for hours rather than seconds. A bigger timeout is not the answer to a long operation: it has to be watched, because it can fail halfway with a dialog, it can hang, and something has to show it is still alive.
  - `wait_for_operation()` polls a caller-supplied "is it still running" test, watches for the application's error dialogs on every poll, dismisses them, captures a screenshot of any failure, and emits a heartbeat so a multi-hour run does not look hung. Returns a `MonitorResult` with an `Outcome` rather than raising: a failed operation is something to log and report, not an exception for someone else to catch.
  - The shape is taken from the Automation Anywhere bots this library replaces, and preserves four details that a naive implementation gets wrong. Completion is checked **before** sleeping, so a fast operation is not held for a full interval. The timeout is **per operation** and comes from config, because a download and an upload do not take the same time. Errors are looked for on **every poll** rather than waited for. Every failure path captures evidence before acting.
  - `window_gone()` and `control_gone()` cover the two ways an operation reports progress: a top-level window of its own, or a progress bar inside an existing form that opens no window at all. Both read only `IsWindow` and `IsWindowVisible`, so nothing is sent to the application. Reading a progress bar's actual position is deliberately not offered: the neighbouring `PBM_GETRANGE` takes a pointer that the target dereferences in its own process, and getting it wrong crashes the application being automated.
- `EnergyManager.wait_for_operation()`, wired to Energy Manager's real error dialogs (`Internet Connection Error`, `Unexpected Error`), with `progress_window_running()` and `progress_control_running()` for the two cases above.
- `run_parameters` in `energy_manager.yaml`: what the Automation Anywhere bots' `strType`, `intMONLY`, `intCalculateVirtual` and `intDuration` actually meant, and why `strUid` is not carried over.

### Fixed
- `poll_count()` rejects a non-positive poll interval instead of dividing by it. The first implementation raised `ZeroDivisionError`; the obvious patch, treating zero as "poll every second", would have turned a caller's typo into a loop that spins a core for hours. Tests that want no delay inject a no-op `sleep` into `wait_for_operation` instead, so they exercise the real control flow at realistic intervals: a simulated two-hour download at 60-second polling runs all 120 polls instantly.


## [1.11.0] - 2026-08-28

### Added
- `automation_core.gui.discover`, the discovery tooling that generates the selector maps in `automation_core.gui.apps`. It ships with the library on purpose: v1.10.0 shipped a knowledge base whose regeneration instructions pointed at a script that was not in the package, and a map nobody can regenerate is a map that quietly rots.
  - `python -m automation_core.gui.discover.snapshot` maps a whole window under both backends, proves a selector for every worthwhile control by executing it, and diffs two snapshots to show what an action changed.
  - `python -m automation_core.gui.discover.watcher` snapshots every window a process opens as it opens, which is the only way to catch a dialog that appears and is dismissed, or to map a launch and login sequence.
  - `discover.probe` holds the shared logic: dual-backend metadata, candidate selectors graded for how stable their anchor is, control-type normalisation across the UIA, CLR and Win32 vocabularies, and tree walking that filters `EnumChildWindows` down to real children.
  - Only the headless tools ship. The hover inspector needs a human at the keyboard plus `pynput` and `tkinter`, so it stays in the discovery project and adds no dependency here.
- `automation_core/gui/PLAYBOOK.md` ships as package data, beside the driver it describes, so a consuming project has the reasoning without needing this repository checked out.

### Changed
- `energy_manager.yaml`'s regeneration instructions now name a command that exists, and explain that `build_marker` is the staleness check: when it stops matching the application's WinForms class-name suffix, every selector needs reproving.
- Hotkey translation is no longer duplicated. `discover.probe` re-exports `to_send_keys` from `automation_core.gui.keys` rather than carrying a second copy of the table.

### Fixed
- 43 tests covering the probe logic (stability grading, tree flattening, control-type normalisation, volatile class-name detection) moved into this repository with the code they test. They previously lived in the discovery project, so the logic had shipped in v1.10.0 with no coverage here.


## [1.10.0] - 2026-08-28

### Added
- `automation_core.gui`, an optional layer over `pywinauto` for driving Windows desktop applications. Install with `pip install automation-core[gui]`; it is Windows only, so it is an extra rather than a dependency and `automation_core` itself never imports it.
  - `gui.windows` finds top-level windows by enumerating handles and reading cached captions, then addresses them by handle. pywinauto's `title=` matching compares against `WM_GETTEXT`, which a busy or wedged UI thread does not answer: Energy Manager's login form answered on a clean launch and then returned empty for minutes after a rejected login, which is the worst failure shape there is.
  - `gui.controls` wraps the actions whose obvious form fails silently. Clicks are posted rather than sent, because `wrapper.click()` uses `SendMessage` and will not return until the application finishes handling it, so a button that opens a modal dialog hangs the bot forever. Text is set atomically rather than typed. Controls are waited for on `visible enabled ready`, never `exists`, because a hidden win32 control is still in the tree. `ControlOffScreen` catches the case where a control reports itself visible while sitting at coordinates like (-31950, -31803).
  - `gui.keys.to_send_keys` translates a UIA shortcut display string ("Ctrl+Alt+1") into a `send_keys` spec ("^%1"), refusing anything it cannot translate confidently rather than sending a guessed key combination to a live application.
  - `gui.app.GuiApp` is the base for an application driver: launch or attach, find windows safely, and get specifications per backend. An application may need more than one backend.
- `automation_core.gui.apps.energy_manager`, a driver for Energy Manager, with its knowledge base in `energy_manager.yaml` beside it. Covers the launch and login sequence (`Enter Password`, then `Client Group`, then the main shell), opening Web Extensions by its Ctrl+Alt+1 shortcut, and scoping control lookups to the owning MDI child form. The YAML records which backend suits which region of the UI, the two distinct credential pairs, the selectors that must never be used, and an explicit list of what has not been mapped yet.

### Changed
- `conftest.py` puts `src` on `sys.path`, so the test suite exercises the working tree rather than whichever release happens to be installed in site-packages.


## [1.9.0] - 2026-08-04

### Added
- `load_config` is now exported from the top-level package, so a bot that
  needs its own settings can `from automation_core import load_config`
  instead of reaching into `automation_core.config`. It returns the
  merged configuration (`team.yaml` with the project's
  `config/config.yaml` layered over it), which is what `setup()` reads
  internally but does not return: `Context` carries only the derived
  values the library itself needs. There was no supported way to read an
  arbitrary config key, so every bot either re-implemented the loader
  and the share path or imported a submodule the docs had to call out as
  an exception.

Backward compatible in both directions: `from automation_core.config
import load_config` keeps working and stays the form to use on 1.8.1 and
earlier. `ConfigurationError` is deliberately still submodule-only, in
line with the guidance that a bot which cannot read `team.yaml` should
stop rather than handle it.

## [1.8.1] - 2026-07-29

Documentation only — **no functional change**. The installed package is
identical to 1.8.0; released so the guidance below has a version to point at.

### Documentation
- Documented the team-wide convention for persisting the Control Room job id:
  when a bot writes `ctx.job_id` to a database, `None` is stored as `0`,
  meaning "not started by the Control Room". `NULL` is the more faithful
  representation, but not every target column can hold it — some are
  `NOT NULL`, and some are part of a primary key or unique constraint — and a
  mixed `NULL`/`0` scheme silently breaks reporting queries. Coalesce at the
  insert with `0 if ctx.job_id is None else ctx.job_id` (not `or 0`, which is
  correct only while real job ids are never `0`), document the sentinel beside
  the column, and use `WHERE job_id > 0` for "real Control Room runs only".
- Recorded that `ctx.job_id` stays `None` by design and that the library
  applies no sentinel of its own: the substitution belongs at the boundary
  that needs it, not in shared library state. `lib-core` touches no databases.
- Noted that `job_id` is not a run parameter — it is a sibling top-level key
  of `params` in `job.json`, read as `ctx.job_id`, and must never be declared
  with `param()` or in `params.json`.

See `CLAUDE.md` ("Storing the Job ID") and `lib-core-spec.md` §3.4.

## [1.8.0] - 2026-07-26

### Added
- `Context.job_file`: the path to the `--job-file`/`CR_JOB_FILE` job.json
  this run was invoked with, `None` for a hand run. Used to locate the
  new error sidecar below; not otherwise meant for bots to read directly.
- `collect_errors` now writes a `cr_errors.json` sidecar next to the job
  file (schema `{schema, is_critical, error_count, timestamp_utc, errors,
  traceback}`) whenever it has anything to report: on a clean exit with
  non-fatal errors collected, or on an unhandled exception. This lets the
  Control Room agent read a run's collected/critical errors back into the
  job's terminal report after the bot exits, so the same errors already
  sent to Freshservice or email also show up in the console. Written
  unconditionally (independent of `notifications.enabled`) and is a
  no-op when `ctx.job_file` is `None`; a write failure only logs a
  warning and never affects the run or the existing notification.
  Requires Control Room agent >= 0.29.0 to be read; older agents, plain
  script bots, and any bot not using `collect_errors` are unaffected.

### Changed
- The notification meta block's error serialization now shares
  `errors.serialize_errors` instead of duplicating the logic; no
  observable change to the notification body.

## [1.7.0] - 2026-07-24

### Added
- Code-first run-parameter declarations. A bot declares the parameters it reads
  directly in code with `param(name, type, *, required=False, description="",
  choices=None, default=None)` (exported from `automation_core`), instead of a
  hand-written `params.json`. `param()` returns a `Param`; read the supplied
  value at runtime with `.read(ctx.params)`, which returns the declared default
  when the param was not supplied. Declare at module scope with literal
  arguments: the Control Room reads these declarations straight from the
  deployed source to build its parameter-entry form (it parses the calls, it
  does not run the bot), so a declaration can never drift from the code that
  uses it. Type may be a Python builtin (`str`/`int`/`float`/`bool`) or its
  JSON-type name. `choices` restricts the value to a fixed set, which the
  Control Room renders as a dropdown.
- `setup()` validates supplied params against the code declarations when any
  `param()` is declared, falling back to `params.json` otherwise; mismatches
  (missing required, wrong type, value not in `choices`) are logged as warnings
  without failing the run.
- `params.json` entries and validation accept an optional `choices` list, kept
  consistent with the code-first path.

## [1.6.0] - 2026-07-24

### Added
- `setup()` now falls back to the `CR_JOB_FILE` environment variable to locate
  the job file when `--job-file` is not on the command line. The Control Room
  agent's project-bot wrapper exports this variable, so a project launched
  through a `run.bat` that does not forward its arguments to Python still gets
  `ctx.job_id` and `ctx.params` populated (previously a project bot only
  received them if the job-file argument reached the interpreter). The explicit
  `--job-file` argument still wins when both are present. Reading remains
  never-fail: with neither source, `job_id` is `None` and `params` is `{}`.

## [1.5.0] - 2026-07-24

### Added
- `Context.job_id`: the Control Room job id a run belongs to, read from the
  same `--job-file` as `ctx.params` (job.json's `job_id`). `None` for a hand
  run or any run the Control Room did not start. Read it with `ctx.job_id`.
- The job id is now folded into the log filename:
  `<ProcessName>_<YYYYMMDD>_<HHMMSS>_job<job_id>.log` when a job id is present,
  so concurrent runs of the same bot (an `allow_overlap` bot, a multi-session
  node, or a dev hand run overlapping a production run) each get their own log
  file instead of interleaving into one. A run with no job id falls back to a
  process-id suffix (`_p<pid>.log`) so two same-second hand runs on a host also
  stay separate.
- `job_id` added to the machine-readable notification meta block so a failure
  email or ticket links straight back to the exact Control Room job (`null` on
  a hand run). The human-readable body gains a `Job ID:` line.

### Changed
- Notification meta `META_SCHEMA_VERSION` bumped `1` -> `2` for the additive
  `job_id` field. The change is backward compatible: a v1 consumer that ignores
  unknown keys keeps working.

## [1.4.1] - 2026-07-22

### Fixed
- `TEAM_YAML_PATH` pointed at a non-existent share segment `\Public\!IE\`
  (missing trailing `S`), introduced during the 2026-07-15 `I:` → UNC
  conversion. Corrected to `\Public\!IES\`. Every `setup()` call on 1.3.0 and
  1.4.0 failed with `ConfigurationError` ("Required team config not found")
  because the path did not resolve; projects on those versions must upgrade to
  1.4.1. Also corrected the path in the README, spec, and CLAUDE.md.

## [1.4.0] - 2026-07-22

### Added
- `params.json` run-parameter declarations contract. A bot that consumes run
  params (`ctx.params`) ships a `params.json` at its repo root declaring each
  param (`name`, `type`, `required`, `description`) so the Control Room
  orchestrator can render a parameter-entry GUI before a run.
- `automation_core.load_param_definitions(root=None)`: reads and validates
  `params.json`, returning normalised declarations (`[]` when absent, raises
  `ConfigurationError` when malformed).
- `setup()` now validates the supplied `ctx.params` against `params.json` when
  present, logging any mismatch (missing required param, wrong type, undeclared
  key) as a warning without failing the run.

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
  (`\\inspiredenergysolutions.local\DFS\Public\!IES\...`) instead of the
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