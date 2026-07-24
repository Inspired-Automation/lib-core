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
- Reads `team.yaml` from `\\inspiredenergysolutions.local\DFS\Public\!IES\BPI\Automation Team\Tools\Scripts\yaml\team.yaml` (required)
- Reads `config/config.yaml` from the consuming project's root (optional)
- The library itself has no config file

## Migrations
| File | DEV applied | PROD applied |
|------|-------------|--------------|
| _no migrations - library does not touch databases_ | | |

## Run Parameters (`params.json` contract)
Bots can receive run parameters from the Control Room orchestrator. The orchestrator invokes a bot as `python.exe <script> --job-file <path>`; that `job.json` carries a `params` object, which `setup()` exposes as `ctx.params` (read with `ctx.params.get("name", default)`).

**The contract:** so the orchestrator can render a parameter-entry GUI *before* it starts a run, every bot that consumes run params must ship a `params.json` at its repo root declaring those params. The orchestrator reads this file to build the form; `lib-core` reads it to validate what was actually supplied.

`params.json` — a JSON object with a `params` array; each entry has:
- `name` (string, required) — the key the bot reads via `ctx.params.get(name)`.
- `type` (string, required) — one of `string`, `integer`, `number`, `boolean`.
- `required` (bool, optional, default `false`) — whether the GUI must collect it.
- `description` (string, optional) — shown to the user in the GUI.

```json
{
  "params": [
    { "name": "region", "type": "string", "required": true, "description": "Region to process" },
    { "name": "dry_run", "type": "boolean", "required": false, "description": "Skip writes" }
  ]
}
```

`lib-core` support (in `automation_core.params`): `load_param_definitions()` reads and validates `params.json` (raising `ConfigurationError` on a malformed file — a bot developer error, like a bad `freshservice.defaults` block), and `setup()` validates the supplied `ctx.params` against the declarations, logging any mismatch (missing required, wrong type, undeclared key) as a **warning** without failing the run — the orchestrator is the primary gate on required params.

**Enforcing rule (propagate into each bot's CLAUDE.md via the Watchdog scaffold):** *When you add or change code in this bot that reads `ctx.params.get("...")`, you MUST create or update `params.json` at the repo root so every consumed param is declared with its `type` and `required` flag. A param the code reads but does not declare is a bug — the orchestrator will not prompt for it.*

## Key Business Logic
- **Run params:** `ctx.params` populated from the `--job-file` job.json; each consumed param must be declared in the repo-root `params.json` (see Run Parameters). Reading params never fails a run; a malformed `params.json` does raise at setup.
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
- Cross-project deployment note (not a lib-core dependency): consuming projects that pull `numpy` (usually transitively via pandas) should pin `numpy<2.4`. numpy 2.4.0 raised the x86-64 build baseline to x86-64-v2, so `import numpy` aborts with `RuntimeError: NumPy was built with baseline optimizations: (X86_V2) but your machine doesn't support: (X86_V2)` on generic/virtualised CPU models (seen on an RDS server). 2.3.x keeps the v1 baseline and has Python 3.14 wheels. Durable fix: have infra set the VM's CPU compatibility mode to a v2-capable model. First hit in `automation-lseg-data-refresh` (2026-07-22).

## Change Log
- 2026-07-24: Added code-first run-parameter declarations: `param(name, type, *, required, description, choices, default)` and `Param` (exported from `automation_core`). Bots declare params in code (module scope) instead of a hand-written `params.json`; read values with `Param.read(ctx.params)`. The Control Room reads the declarations from source (parses the `param()` calls, does not run the bot). `choices` gives a dropdown. `setup()` validates against the code declarations (params.json is the fallback). Documented in lib-core-spec.md §3.4; released v1.7.0.
- 2026-07-24: `setup()` falls back to the `CR_JOB_FILE` env var for the job file when `--job-file` is absent (the agent's project-bot wrapper exports it), so `run.bat` project bots that do not forward args to Python still get `ctx.job_id`/`ctx.params`; the argument wins when both are present. Documented in lib-core-spec.md §3.4; released v1.6.0.
- 2026-07-24: Added `Context.job_id` (read from the same `--job-file` as `ctx.params`) and folded the job id into the log filename (`<ProcessName>_<ts>_job<id>.log`, or `_p<pid>.log` with no job id) so concurrent runs of the same bot no longer interleave into one log file; added `job_id` to the notification meta block (schema bumped 1→2) and body. Documented in lib-core-spec.md §3.4/§5; released v1.5.0.
- 2026-07-22: Fixed broken `TEAM_YAML_PATH` (`\Public\!IE\` → `\Public\!IES\`, a missing `S` from the 2026-07-15 UNC conversion) that made every `setup()` on 1.3.0/1.4.0 raise `ConfigurationError`; released v1.4.1. `!IES` confirmed as the real share (Control Room uses it too).
- 2026-07-22: Added the `params.json` run-parameter declarations contract (repo-root file declaring each consumed param for the orchestrator's GUI), plus `automation_core.load_param_definitions()` and setup-time validation of supplied params. Documented in lib-core-spec.md §3.4; released v1.4.0.
- 2026-07-22: Added `Context.params` (run params from a `--job-file` job.json) and a machine-readable JSON meta block in notification bodies (wrapped in `---AUTOMATION-META-BEGIN/END---` markers for downstream flows); folded in the UNC `TEAM_YAML_PATH` change; released v1.3.0.
- 2026-07-22: Documented a cross-project deployment gotcha (numpy 2.4 x86-64-v2 baseline crash on virtualised/RDS CPUs; pin `numpy<2.4`). Not a lib-core dependency — guidance for consuming projects. See Known Gotchas.
- 2026-07-15: `TEAM_YAML_PATH` switched from mapped `I:` drive to UNC path under `\\inspiredenergysolutions.local\DFS\Public\!IES\...`.
- 2026-07-08: Releases now include a built wheel as a GitHub release asset, so projects can pin by wheel URL; RELEASING.md updated.
- 2026-07-08: Lowered minimum Python from 3.14 to 3.13 (`requires-python = ">=3.13"`); fixed stale `__version__`; released v1.2.2.
- 2026-06-02: Added dispatch method to logging and removed debug statements; released v1.2.1
- 2026-06-02: Added required `freshservice.defaults` block (validated at setup) and normalised Freshservice URL handling; released v1.2.0.
- 2026-06-01: Added `notifications.enabled` (auto/always/never) to control dispatch; released v1.1.0.
- 2026-06-01: Implemented v1.0.0 per `lib-core-spec.md`.
- 2026-06-01: Initial scaffold from spec.

## Outstanding TODOs
- Tag and release v1.0.0 on GitHub (`git tag v1.0.0 && git push --tags`).