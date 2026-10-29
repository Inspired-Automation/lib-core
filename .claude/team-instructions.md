<!-- Copied from Inspired-Automation/config-cursor-rules at d6ddac2, the
same team instructions Cursor loads from
.cursor/rules/team-instructions.mdc in this repo. Two copies exist
because the two editors load rules differently: Cursor reads .mdc files
under .cursor/rules, and Claude Code's CLAUDE.md @import follows .md
only, so an .mdc import resolves to nothing at all rather than failing
loudly. Do not edit either copy here; change the instructions repo and
re-copy both. -->

# Team AI Instructions

These instructions apply to all AI-assisted work within the BPI Automation Team. Follow them on every task unless the user explicitly overrides a specific rule for that task.

## 1. Languages

- Python and SQL are the defaults. Use them unless the user explicitly requests another language.
- Target **Python 3.14**.
- Use the SQL dialect appropriate to the target engine (T-SQL for MSSQL, MySQL dialect for Sugar).
- If the user explicitly requests another language for a task (PowerShell, VBA, Node-RED, JavaScript, etc.), follow that request, but still apply the rest of these rules where they translate (no hardcoded credentials, structured logging, config-driven paths, CLAUDE.md maintained, branding respected).

## 2. Databases and Connections

### Connection method
- Always use **`pyodbc`** for database connections, for both MSSQL and MySQL targets.
- All connections are **trusted connections**. Do not include usernames, passwords, or credential prompts in connection strings.
- Always use **parameterised queries**. Never concatenate or f-string user-supplied values into SQL.
- Always wrap connections in context managers so they close cleanly.

### DSN reference

| System          | DSN                  | Engine | Purpose                            |
|-----------------|----------------------|--------|-------------------------------------|
| Sugar           | `DSN=Sugar Corp`     | MySQL  | SugarCRM (live)                     |
| Jupiter         | `DSN=Jupiter`        | MSSQL  | Jupiter on `emdb` (live)            |
| Titan (DEV)     | `DSN=Titan_INSE_DEV` | MSSQL  | Development environment for Titan; new tables are built and tested here first |
| Titan (PROD)    | `DSN=Titan_INSE`     | MSSQL  | Production environment for Titan    |

Everything other than Sugar is MSSQL by default. New tables and schema changes are always built first against `Titan_INSE_DEV`. They are only promoted to `Titan_INSE` (PROD) via a migration script, see Section 4 below. If a new database is introduced, ask the user for the DSN and engine type and record it in CLAUDE.md.

Example:
```python
import pyodbc

with pyodbc.connect("DSN=Jupiter") as conn:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT TOP 10 * FROM dbo.SomeTable WHERE col = ?",
        (value,),
    )
    rows = cursor.fetchall()
```

### Destructive statements
For any `UPDATE`, `DELETE`, `DROP`, `TRUNCATE`, or `ALTER` statement, display the full statement and provide an explicit warning to the user that the statement is destructive and to proceed with caution.

### Naming conventions
- Follow existing prefix patterns in the target schema (for example, `spTENDER_` for tender-related stored procedures).
- If unsure of the convention for a schema you have not touched before, ask the user.

## 3. Project Structure

Every project must follow this layout:

```
project_root/
├── src/                          # Python source code
├── config/
│   ├── config.yaml               # Local config, never committed
│   └── config.template.yaml      # Template with placeholder values
├── migrations/                   # SQL migration scripts (see Section 4)
├── .claude/
│   ├── settings.json             # Permission deny rules for AI agents
│   └── team-instructions.md      # These instructions, imported by CLAUDE.md
├── .cursor/rules/
│   └── team-instructions.mdc     # These instructions, loaded by Cursor
├── CLAUDE.md                     # AI agent context (required)
├── README.md                     # Human-readable project overview
├── requirements.txt              # Pinned dependencies
├── inspired-brand-guidelines.md  # Required only for user-facing work
└── .gitignore
```

- The Control Room writes this layout when it scaffolds a project, including both copies of these instructions. They are two copies of one file because the editors load rules differently: Cursor reads `.mdc` files under `.cursor/rules`, and Claude Code's `CLAUDE.md` `@import` follows `.md` only, so importing the `.mdc` would resolve to nothing without any error. Never edit either copy inside a project; change this repo and re-copy both.
- Older hand-built repos name the committed template `config.example.yaml` and may keep these instructions elsewhere or not at all. Follow whichever template name a project already has (see Section 6), and do not add a second one.
- Always create `requirements.txt` with exact pinned versions (`package==1.2.3`). Include `PyYAML` whenever config is read.
- Where `pandas` is used, pin both `pandas` and `numpy` to the team's known-good versions in `requirements.txt`: `pandas==2.3.3` and `numpy==2.3.5`. These versions are tested together and should not be changed without team-wide agreement.
- Always create or update `.gitignore` to exclude `config/config.yaml`, `__pycache__/`, `*.pyc`, and any local log output.
- **No virtual environments are used in this team.** Do not create `venv/` directories or suggest virtualenv setup.

## 4. Schema Changes and Migrations

All database schema work follows a DEV-first, migrate-to-PROD pattern.

### Workflow

- New tables, columns, indexes, constraints, and stored procedures are built and tested against the DEV environment (e.g. `Titan_INSE_DEV`).
- Once the design is settled, the canonical schema change is captured as a numbered SQL migration script in `migrations/`.
- The migration is then run against the PROD environment (e.g. `Titan_INSE`) when ready to promote.

### Migrations folder

Every project that touches database schema has a `migrations/` folder at the project root containing numbered SQL files.

```
migrations/
├── 001_create_initial_tables.sql
├── 002_add_status_column_to_orders.sql
├── 003_create_audit_log_table.sql
└── ...
```

Rules for migration scripts:

- File names follow the pattern `NNN_short_description.sql`, where `NNN` is a zero-padded sequence number starting at 001.
- Each migration is a single logical change (one new table, one column added, one index, etc.).
- Migrations must be **idempotent where possible**, so re-running them is safe. For MSSQL:
  - Use `IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = '...') CREATE TABLE ...`
  - Use `IF COL_LENGTH('schema.table', 'column') IS NULL ALTER TABLE ... ADD ...`
  - Use `IF OBJECT_ID('schema.proc') IS NULL ... ELSE ALTER` patterns for stored procedures
- Migrations are never edited after they have been applied to any environment. To fix or change something, add a new migration.
- DEV-only exploratory schema changes that are not intended to reach PROD should not be added as migrations. Once a change is finalised, write it up as a proper migration.

### Tracking in CLAUDE.md

The Migrations section of `CLAUDE.md` tracks which migrations have been applied to which environments. Example:

```markdown
## Migrations

| File                                | DEV applied  | PROD applied |
|-------------------------------------|--------------|--------------|
| 001_create_initial_tables.sql       | 2026-01-15   | 2026-01-22   |
| 002_add_status_column_to_orders.sql | 2026-02-10   | not yet      |
```

The AI agent updates this table whenever:
- A new migration script is added (initial row, both columns "not yet")
- The user reports applying a migration to an environment (update the relevant date)

### When the AI agent makes schema changes

Whenever the agent creates, modifies, or drops a schema object as part of a task:

1. Add the SQL to a new numbered migration file in `migrations/`.
2. Add a row to the Migrations table in `CLAUDE.md` with DEV/PROD "not yet".
3. Note in the response that the migration has been written and needs to be applied to DEV first, then PROD when ready.

## 5. CLAUDE.md (Required in Every Project)

Every project must contain a `CLAUDE.md` file at the project root.

- If it does not exist, **create it before doing any other work**.
- Read CLAUDE.md at the start of every task and treat it as the source of truth for project context.
- You (the AI agent) are responsible for keeping CLAUDE.md accurate. Update it whenever the project changes in any meaningful way, and add a Change Log entry.
- If CLAUDE.md contradicts what you observe in the code, ask the user which is correct rather than guessing.

### Required structure

```markdown
# CLAUDE.md

## Purpose
One or two sentences describing what this project does and the business problem it solves.

## Tech Stack
- Language(s) and version(s)
- Key libraries and why they are used

## Databases
- DSNs used and which schemas, tables, and stored procedures are read from or written to
- Any cross-database or linked server dependencies

## External Integrations
- APIs called (Graph, Freshservice, ECOES, etc.) and config keys required for each

## Configuration
- All keys expected in config.yaml
- Log root folder location

## Migrations
| File | DEV applied | PROD applied |
|------|-------------|--------------|
| _no migrations yet_ | | |

## Key Business Logic
- Domain rules, edge cases, and non-obvious decisions
- Anything a new developer or agent would need to know to avoid breaking things

## Known Gotchas
- Quirks, workarounds, and things that have caused issues before

## Change Log
- YYYY-MM-DD: Brief description of change

## Outstanding TODOs
- Open items, in priority order
```

## 6. Configuration

These rules apply to everything, both tiers below:

- All environment-specific values (paths, API keys, log folder, DSN overrides, recipient lists) must live in `config/config.yaml`.
- Never hardcode credentials, file paths, or API keys in source files.
- `config/config.yaml` is never committed. Commit the template next to it, with placeholder values, so the team knows which keys are required. Projects scaffolded by the Control Room use `config/config.template.yaml`, which is the name its Deploy and its Runtime config editor read; older hand-built repos use `config.example.yaml`. Follow whichever name the project already has, and never add a second one.
- Never log secret values, even at debug level.
- Use `yaml.safe_load`, never `yaml.load`, wherever YAML is read directly.

### Project bots (anything the Control Room runs)

`automation_core.setup()` loads configuration for you. It reads the team-wide `team.yaml` from the Automation Team share, then deep-merges the project's own `config/config.yaml` over the top, so a project key overrides the team default of the same name. Do not re-implement any of that: not the share path, not the merge, not the loader.

What `setup()` returns on the context is only the derived values the library needs (`ctx.process_name`, `ctx.log_file`, `ctx.is_production`, `ctx.notification_method`, `ctx.notification_recipient`, `ctx.params`, `ctx.job_id`). It does not hand back the merged config dictionary. When the bot needs its own settings, ask the library for the merged config rather than opening the file yourself:

```python
from automation_core import setup, collect_errors, load_config

def main() -> None:
    ctx = setup("MyProcess")
    with collect_errors(ctx) as errors:
        config = load_config()          # team.yaml merged with config/config.yaml
        batch_size = config.get("processing", {}).get("batch_size", 100)
```

`load_config` raises `ConfigurationError` when the team share is unreachable, which is the correct behaviour: a bot that cannot read team.yaml has no notification recipient and should stop rather than run half-configured. The top-level import needs `automation_core` 1.9.0 or later; on an older pin, import it from `automation_core.config`, which still works.

### Standalone and ad-hoc scripts

A script that does not run under the Control Room and does not use `automation_core` (a one-off, an investigation, a scheduled task outside the estate) loads its own config directly. That is the only case where the loader below is the right answer:

```python
from pathlib import Path
import yaml

config_path = Path("config/config.yaml")
with config_path.open("r", encoding="utf-8") as f:
    config = yaml.safe_load(f)
```

If such a script grows into something the team runs on a schedule, move it to `automation_core` rather than keeping the hand-rolled version.

## 7. Logging

These rules apply to everything, both tiers below:

- Use the standard `logging` module for all operational output, configured with **two handlers**: a `FileHandler` writing to a timestamped log file, and a `StreamHandler` writing to the console. This gives both a persistent record and visible output when running the script interactively.
- A single `logging.info(...)` call then writes to both file and console with no need for parallel `print()` calls.
- `print()` is permitted for casual ad-hoc output that does not need persisting (e.g. quick debugging or one-off scripts), but anything that represents a real operational event must go through `logging` so it lands in the log file.
- The log format is `%(asctime)s | %(levelname)s | %(name)s | %(message)s`.

### Project bots (anything the Control Room runs)

`automation_core.setup()` configures logging. Call it once at the top of `main()`, then just use `logging.info(...)`. Never call `logging.basicConfig()` in a bot, and never add handlers of your own: `setup()` clears the root handlers and installs its two, so anything you add beforehand is discarded and anything you add afterwards duplicates output.

What it does, so you can find a log without guessing:

- **Log root** is `logging.log_root` from the merged config when set, otherwise `paths.production_root` from `team.yaml` with the process name appended.
- **Dated folder** is `<log_root>\logs\<YYYY>\<MonthName>\<DD>\`, created if missing, from the **UTC** date. A run just after midnight UTC lands in the next day's folder even though local time still reads the previous evening.
- **Filename** is `<ProcessName>_YYYYMMDD_HHMMSS_job<JobId>.log` under the Control Room, or `<ProcessName>_YYYYMMDD_HHMMSS_p<Pid>.log` for a hand run. The job id or process id suffix is what keeps two concurrent runs of the same bot out of the same file, so do not rename logs to drop it.
- **The path used** is on the context as `ctx.log_file` if the bot needs to report it.

### Standalone and ad-hoc scripts

A script that does not use `automation_core` configures its own logging to the same convention: `<log_root>\logs\<YYYY>\<MonthName>\<DD>\<ProcessName>_YYYYMMDD_HHMMSS.log`, dated folders created at runtime, log root from `config.yaml` under a `logging` key. When prompting the user for a log location, suggest:

```
I:\BPI\Automation Team\Automated Processes\<ProcessName>
```

Example (standalone only: in a project bot, `setup()` has already done all of this):
```python
import logging
from datetime import datetime
from pathlib import Path
import yaml

PROCESS_NAME = "MyProcess"

with Path("config/config.yaml").open("r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

log_root = Path(config["logging"]["log_root"])

now = datetime.now()
log_dir = log_root / "logs" / f"{now.year:04d}" / now.strftime("%B") / f"{now.day:02d}"
log_dir.mkdir(parents=True, exist_ok=True)

log_file = log_dir / f"{PROCESS_NAME}_{now.strftime('%Y%m%d_%H%M%S')}.log"

log_format = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

file_handler = logging.FileHandler(log_file, encoding="utf-8")
file_handler.setFormatter(logging.Formatter(log_format))

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(log_format))

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler],
)

logging.info("Logging initialised. Log file: %s", log_file)
```

## 8. Email (Microsoft Graph)

### Building and sending

Every email an automated process sends (reports, alerts, notifications, requests) is built and sent with **`automation_core.email`**, which needs `automation_core` 1.14.0 or later. Do not write a Graph `sendMail` call or an HTML email body by hand.

- The rules are in the **email design spec**, which ships inside the package: `automation_core/email/email-design-spec.md` in the installed `automation_core` (find it with `python -c "import automation_core.email, pathlib; print(pathlib.Path(automation_core.email.__file__).parent)"`). Read it before writing an email. It covers which email type to use, the subject and preview text, wording, and number, date and time formats.
- Build an `Email` from the components (`Banner`, `Kpi`, `Section`, `Table`, `Group`, `Column`, `Button`, `AttachedFile`, `FolderLink`, `WebLink`), call `render()`, and pass the result to `send_email()`. The module docstring has a complete example.
- Pass plain text, never HTML: the renderer escapes every value. Format values with `automation_core.email.formatting` (`format_number`, `percent_of`, `format_money`, `format_datetime` and the rest) so they follow the spec.
- Declare identifier columns explicitly, `Column("MPAN")`. A column of ints is otherwise treated as numeric and shown with thousands separators.
- Never add a logo, signature or "do not reply" line. The template's Inspired Automation header band, its footer and the mail system provide them.

```python
from automation_core.email import Email, EmailType, Kpi, Section, Table, send_email, subject_line
```

### Credentials and sign-off

Graph credentials come from the `graph` block of `team.yaml`, which `load_config()` merges in and `send_email()` reads by default. Do not copy them into a project's `config.yaml`; add a `graph` block there only when the project must send from a different mailbox, and ask the user for the values. Expected structure:

```yaml
graph:
  client_id:
  client_secret:
  tenant_id:
  sender_address:
```

- Confirm external recipients with the user before sending.
- Do not send email automatically from any new script without explicit user sign-off on the first run.

## 9. Ticketing (Freshservice)

If a task involves raising tickets, you must request that the user adds Freshservice API credentials to `config.yaml` before proceeding. Expected structure:

```yaml
freshservice:
  api_key:
  domain:
```

## 10. User-Facing Output (Branding)

If a task involves anything a user or customer will see (dashboards, PDFs, reports, web UIs, etc.), reference `inspired-brand-guidelines.md` from the project folder. If that file is not present, stop and ask the user to provide it before producing any user-facing output.

Emails are the exception: the email design spec that ships with `automation_core.email` already applies the brand guidelines (colours, type, layout), so follow Section 8 and do not stop to ask for `inspired-brand-guidelines.md` for an email.

## 11. Code Style and Output

- Use type hints on function signatures.
- Use `pathlib.Path` for filesystem paths, not `os.path`.
- Wrap database, file I/O, and network calls in `try`/`except` with specific exception types. Never use bare `except:`.
- Log exceptions with `logging.exception(...)` so the stack trace is captured.
- All code must be **copy-paste ready**. No placeholder ellipses, no "rest of the function unchanged" shortcuts.
- Do not use em-dashes anywhere in code, comments, docstrings, log messages, or written output.

## 12. Agent Behaviour

- Ask clarifying questions before making assumptions, especially about data shape, business rules, recipient lists, and target environments.
- **Never execute terminal commands or SQL statements automatically.** Always show what you intend to run and wait for the user to confirm.
- For multi-step tasks, summarise the full plan before starting, and check in with the user at meaningful milestones.
- If the user provides a working query, script, or pattern, treat it as the canonical style for that project.
- If unsure which DSN, schema, or table to use, ask rather than guess.
- If setup.bat exists in the project root and automation_core is not importable, suggest the developer run setup.bat before proceeding with code changes.

## 13. Run Parameters (Control Room)

When a bot runs under the Control Room, the run can carry per-run input set on the schedule (the "with params" box), a trigger, a webhook body, or an API job. This input arrives on the run context returned by `automation_core.setup()` as `ctx.params`, a dict, and is the standard way to make one bot do different work on different runs (for example a `region` that differs between two schedules).

- Read only the keys the bot expects, always with a default so a manual run or a param-less schedule still works: `region = ctx.params.get("region", "all")`.
- Values keep their JSON type: `{"region": "north"}` gives a string, `{"count": 5}` gives an int, `{"enabled": true}` gives a bool, a JSON array gives a list.
- Event triggers also merge their own details under a `"trigger"` key (the file path, the mail sender and subject, or the upstream job id), so an event-fired bot can find what to process at `ctx.params.get("trigger", {})`.
- Never expect secrets in params: they are stored and shown in plain text on the Control Room. Secrets belong in `config/config.yaml` (see Section 6).
- `ctx.params` needs `automation_core` 1.3.0 or later; on older versions it is always empty. Under the Control Room, a project bot receives params only on agent 0.28.1 or later together with `automation_core` 1.6.0 or later: the agent hands the job file to the bot through an environment variable (never on the command line, so a bot that parses its own arguments is never disturbed), and `automation_core` reads that variable from 1.6.0.

#### Declaring params in code (`param()`)

So the Control Room can render a parameter-entry form before a run, every param a bot reads must be declared. Declare each one **in code, at module scope**, with `automation_core.param()`. The code is the single source of truth: the Control Room reads these declarations straight from the bot's source (it parses the `param()` calls, it does not run the bot) to build the form, so a declaration can never drift from what the bot actually reads. Do not hand-write a `params.json` for new code (see the legacy note below). `param()` needs `automation_core` 1.7.0 or later; new projects pin this automatically.

`param(name, type, *, required=False, description="", choices=None, default=None) -> Param`:

| Argument | Meaning |
|----------|---------|
| `name` | The key the param is supplied under (what the bot reads). |
| `type` | The Python builtin `str`, `int`, `float` or `bool` (or its JSON-type name `"string"`/`"integer"`/`"number"`/`"boolean"`). Drives the form widget. |
| `required` | Whether the form must collect it (default `False`). |
| `description` | Shown to the user in the form (default `""`). |
| `choices` | A list restricting the value to a fixed set, rendered as a dropdown (default `None`). |
| `default` | Returned by `.read()` when the param was not supplied (default `None`). |

Declare at module scope with literal arguments, and read the value inside `main()` with `Param.read(ctx.params)`, which returns the declared default when the param was not supplied:

```python
from automation_core import setup, collect_errors, param

REGION = param("region", str, required=True, description="Region to process")
DRY_RUN = param("dry_run", bool, default=False, description="Skip writes")
MODE = param("mode", str, choices=["fast", "full"], default="fast",
             description="Processing mode")


def main():
    ctx = setup("MyProcess")
    with collect_errors(ctx) as errors:
        region = REGION.read(ctx.params)
        dry_run = DRY_RUN.read(ctx.params)
        logging.info("Running for region %s (dry_run=%s)", region, dry_run)
```

**Enforcing rule:** whenever you add or change code that reads a run parameter, declare it with `param()` at module scope, with literal arguments so the Control Room can read it. A param the bot reads but does not declare is a bug: the Control Room will not prompt for it, so it always falls back to the default. Do not declare params the bot does not read. Declarations describe inputs only and never hold values, so they never contain secrets (see the plain-text warning above).

`automation_core.setup()` validates the supplied params against the declarations and logs a warning for a missing required param, a wrong type, a value outside `choices`, or an undeclared key; validation never fails the run (the Control Room is the primary gate).

#### Legacy: `params.json`

Older bots declared params in a `params.json` file at the repo root instead (a JSON object with a `params` array; each entry `name`, `type`, and optional `required`, `description`, `choices`). The Control Room still reads it as a fallback when a bot declares nothing in code, so existing bots keep working. Do not use it for new code; prefer `param()`. An empty or malformed `params.json` is worse than none (it blocks the form), so when migrating a bot to `param()`, delete its `params.json`.

## 14. GUI Automation (Windows desktop applications)

Some processes have no API and no database route, and the only way in is to
drive the application's own interface. Do not write raw `pywinauto` for this.
Use **`automation_core.gui`**, which exists because every obvious pywinauto
call has a failure mode that is silent, intermittent, or destructive, and
those are already handled there.

```
pip install automation-core[gui]
```

It is an **optional extra**, Windows only. `automation_core` never imports it,
so a bot that touches no GUI carries no `pywinauto`.

### Before writing a single selector

1. **Check whether the application is already mapped.** Look in
   `automation_core/gui/apps/`. A mapped application has a driver
   (`<application>.py`) and a knowledge base (`<application>.yaml`) holding
   verified selectors, keyboard shortcuts, login flows, the selectors that
   must **never** be used for that application, and an honest list of what has
   not been mapped yet.
2. **Read `automation_core/gui/PLAYBOOK.md`.** It ships inside the package,
   beside the driver. It is the reasoning behind every rule below.
3. **If the application is not mapped, map it** with the discovery tools that
   ship with the library:

```
python -m automation_core.gui.discover.snapshot --title "MyApp" --backend both --verify
python -m automation_core.gui.discover.watcher  --process MyApp.exe --duration 300
```

`snapshot` walks an entire window under both backends and **proves** every
candidate selector by executing it, reporting how many elements it really
matched. `watcher` snapshots each window a process opens as it opens, which is
the only way to catch a dialog that appears and is dismissed, or to map a
launch and login sequence.

### Rules

- **A selector that has not been executed is a guess.** Never write down a
  selector you have not seen resolve to exactly one element. The discovery
  tools report the match count; use it.
- **Uniqueness is not enough.** A selector can match one element today and
  still be wrong. Prefer, in order: an application-assigned name
  (`auto_id="cmdOk"`), a framework-stable numeric id, visible text, and only
  then position. Never select on a WinForms `class_name` (it carries a
  per-process suffix that changes when the application is rebuilt) or a
  WinForms `control_id` (derived from the window handle, different every run).
- **Choose the backend by measuring, not by reputation.** "win32 for old,
  uia for modern" is unreliable. Run the snapshot tool with `--backend both`
  and compare. One application often needs both: anything whose items are
  *drawn* rather than created as child windows (a WinForms `ToolStrip`, a
  DevExpress ribbon, grid cells) is invisible to win32 and needs UIA.
- **Wait for conditions, never sleep.** Use the library's waits. A fixed
  `time.sleep` is simultaneously too slow on a good day and too short on a
  bad one.
- **Verify every action by reading state back.** An action you have not
  confirmed has not happened.
- **Never send a Win32 message whose parameter is a pointer.** The receiving
  window procedure dereferences it inside the target process, so passing a
  plain integer crashes the application you are automating. Use the library's
  accessors, which allocate the buffer in the target process correctly.
- **Read data from the database, not from the screen.** GUI automation is for
  driving actions. If the numbers you need exist in a database, query them:
  scraping a grid is slower, far more fragile, and usually unnecessary.
- **Credentials never go in run parameters.** Control Room params are stored
  and displayed in plain text. Application logins belong in
  `config/config.yaml` or the Control Room's Runtime config panel, per
  Section 6. Note that an application may need more than one credential (for
  example a login for the application and a different one for a web module
  inside it); keep them as separate, clearly named config keys.

### Keeping a knowledge base honest

A selector map goes stale when the application is upgraded, and a stale map is
worse than none because it gets believed. Each map records a `build_marker`
identifying the application build it was harvested from. When that stops
matching the running application, re-harvest with the snapshot tool and reprove
every selector. Never hand-edit a generated map.

If you map a new application, or extend an existing map, contribute it back to
`lib-core` as `automation_core/gui/apps/<application>.yaml` rather than keeping
it in the bot. The next project to automate that application should not have to
rediscover it.
