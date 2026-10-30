# `lib-core` — Shared Utilities Library Specification

This document specifies the shared Python library used by every automation project in the Inspired-Automation team. The library lives in its own GitHub repo (`Inspired-Automation/lib-core`) and is installed into each project via pip.

The purpose of the library is to remove ~500 lines of boilerplate from every project: logging setup, configuration loading, error collection, and notification dispatch. All projects behave identically when something goes wrong because they all go through the same code.

---

## 1. Repo and Package Identity

- **GitHub repo:** `Inspired-Automation/lib-core`
- **Python package (import name):** `automation_core`
- **Distribution name (in requirements.txt):** `automation-core`
- **Install pattern:**
  ```
  pip install git+https://github.com/Inspired-Automation/lib-core.git@vX.Y.Z
  ```
- **Versioning:** Semantic versioning. Every release is tagged on GitHub. Projects pin exact versions in `requirements.txt`.

---

## 2. Configuration Architecture

The library reads two config files at startup:

### 2.1 Team-wide config (required)

Path: `\\inspiredenergysolutions.local\DFS\Public\!IES\BPI\Automation Team\Tools\Scripts\yaml\team.yaml`

Holds credentials and team-wide defaults. Same file for every project. Rotated once when credentials change.

```yaml
graph:
  client_id: "..."
  client_secret: "..."
  tenant_id: "..."
  sender_address: "bpiautomation@inspiredenergy.co.uk"

freshservice:
  api_key: "..."
  domain: "..."
  defaults:            # required when notifications.method is "freshservice"
    workspace_id: 2                 # int
    group_id: 123                   # int
    requester_email: "bpiautomation@inspiredenergy.co.uk"  # str
    type: "Incident"               # str
    tags: ["automation", "bpi"]    # list of str

notifications:
  default_recipient: "bpiautomation@inspiredenergy.co.uk"

paths:
  production_root: "I:\\BPI\\Automation Team\\Automated Processes"
```

The `freshservice.defaults` block is **required** whenever any project sets
`notifications.method: "freshservice"`. All five fields (`workspace_id`,
`group_id`, `requester_email`, `type`, `tags`) must be present and non-empty;
otherwise `setup()` raises `ConfigurationError`, naming the missing field.

### 2.2 Project config (optional)

Path: `config/config.yaml` in the project root.

Holds project-specific overrides. The whole file is optional. If present, its values override team.yaml defaults.

```yaml
# All sections optional - only include what you want to override.

notifications:
  method: "email"  # or "freshservice"
  recipient: "different-address@inspiredenergy.co.uk"  # overrides team default

logging:
  log_root: "C:\\custom\\path"  # overrides default
```

### 2.3 Merge rules

- `team.yaml` is the base. `config.yaml` values override on a key-by-key basis.
- `team.yaml` missing → library raises a clear configuration error at startup.
- `config.yaml` missing → fine, use team defaults for everything.
- Project's `notifications.method` defaults to `"email"` if not specified anywhere.

---

## 3. Public API

The library exposes a small surface area. Projects should not need to reach into internals.

### 3.1 `setup(process_name: str) -> Context`

Call this once at the start of every project's `main()` function. It:

1. Loads team.yaml and project config.yaml.
2. Configures logging (file handler + console handler) with the standard format.
3. Detects whether the script is running from production (under `paths.production_root`) or development.
4. Returns a `Context` object describing the resolved environment.

```python
from automation_core import setup

ctx = setup("TenderWatcher")
# ctx.log_file - Path to the active log file
# ctx.is_production - bool, True if running from the production root
# ctx.notification_method - "email" or "freshservice"
# ctx.notification_recipient - resolved recipient address
# ctx.params - dict of run parameters (see 3.4); {} when run without any
# ctx.job_id - the Control Room job id (see 3.4); None for a hand run
# ctx.job_file - Path to the --job-file/CR_JOB_FILE job.json; None for a hand run
# ctx.request_assist(reason, ...) - ask the Control Room for a person (see 3.7)
```

`setup()` also accepts an optional `argv` argument (defaulting to `sys.argv[1:]`) used only to locate `--job-file` when reading run params; tests pass it explicitly.

Note what `setup()` does **not** return: the merged configuration. `Context` carries only the derived values the library needs. A bot that wants its own settings calls `load_config()` (3.1.1).

### 3.1.1 `load_config() -> dict`

Returns the merged configuration: `team.yaml` as the base with the project's `config/config.yaml` layered over it, per the merge rules in 2.3. Exported from the top level since **1.9.0**; on earlier versions import it from `automation_core.config`, which also still works.

```python
from automation_core import setup, collect_errors, load_config

def main() -> None:
    ctx = setup("TenderWatcher")
    with collect_errors(ctx) as errors:
        config = load_config()
        batch_size = config.get("processing", {}).get("batch_size", 100)
```

It reads both files on each call rather than returning what `setup()` cached, so it does not depend on `setup()` having run. It raises `ConfigurationError` when the team share is unreachable, which is intentional: a bot that cannot read `team.yaml` has no notification recipient and should stop rather than run half-configured. That exception stays importable from `automation_core.config` only, since handling it is not the expected behaviour.

### 3.2 `collect_errors(ctx) -> ErrorCollector`

Context manager that collects non-fatal errors and dispatches notifications on exit.

```python
from automation_core import setup, collect_errors

def main():
    ctx = setup("TenderWatcher")
    with collect_errors(ctx) as errors:
        # ... project work ...
        for item in things_to_process:
            try:
                process(item)
            except SomeRecoverableError as exc:
                errors.add(f"Failed to process {item.id}", exception=exc)
        # ... more work ...
    # On normal exit:
    #   - If errors.count > 0, send summary notification
    #   - If errors.count == 0, send nothing
    # On uncaught exception:
    #   - Send critical-error notification with traceback
    #   - Re-raise so the script exits non-zero
```

### 3.3 `ErrorCollector` methods

- `errors.add(message: str, *, exception: Exception | None = None, details: dict | None = None)` — log the error immediately to the log file and add it to the in-memory collection.
- `errors.count` — number collected so far (read-only).
- `errors.has_errors` — boolean.

Every call to `add` writes to the log file immediately, so a subsequent crash does not lose errors collected so far.

### 3.4 Run parameters (`ctx.params` and `params.json`)

The Control Room orchestrator can pass run parameters to a bot. It invokes the
bot as `python.exe <script> --job-file <path>`, where `job.json` carries a
`job_id` and a `params` object. `setup()` reads that object and exposes it as
`ctx.params` (a `dict`; `{}` when the bot is run by hand or scheduled without
params). Read values with `ctx.params.get("name", default)`.

The same job file carries the job's `job_id`, exposed as `ctx.job_id` (`None`
for a hand run, or any run the Control Room did not start). It is read on the
same never-fail-the-run basis as `params`, folded into the log filename so
concurrent runs of one bot never share a log file (see §5), and included in
failure notifications so an alert links back to the exact job (see §4.4).

`ctx.job_id` is deliberately left as `None` rather than coalesced to a
placeholder: `None` is the truthful value, and any substitute belongs at the
boundary that needs it. Bots that persist the job id to a `NOT NULL` database
column substitute `0` there, at the insert — see the team-wide convention in
CLAUDE.md. The library itself stores nothing and applies no sentinel.

`ctx.job_file` is the path to that same job file (`None` for a hand run). It
is not meant for bots to read directly; it exists so `collect_errors` (§3.2)
knows where to write the `cr_errors.json` sidecar described in §4.6, and so
`ctx.request_assist()` (§3.7) knows where the assist marker files go.

Reading supplied params never fails a run: no `--job-file`, a missing or
malformed job file, or a non-object `params` value all resolve to `{}` (the
last two are logged as a warning). Only `--job-file` is consumed from the
command line (via `parse_known_args`), so a bot's own CLI arguments are left
untouched.

When `--job-file` is not on the command line, `setup()` falls back to the
`CR_JOB_FILE` environment variable. The Control Room agent's project-bot
wrapper exports this variable, so a project launched via a `run.bat` that does
not forward its arguments to Python still receives `ctx.job_id` and
`ctx.params`. The explicit `--job-file` argument wins when both are present.

So the orchestrator can render a parameter-entry GUI **before** starting a run,
every bot that consumes run params must declare them. The preferred way is
code-first with `param()`; a `params.json` file is the older, still-supported
alternative.

#### Code-first declarations (`param()`)

Declare each parameter in code, at module scope, with `param()`:

```python
from automation_core import param

REGION = param("region", str, required=True, choices=["north", "south"],
               description="Region to process")

def main():
    ctx = setup("MyBot")
    region = REGION.read(ctx.params)   # coerced value, or the declared default
```

- `param(name, type, *, required=False, description="", choices=None,
  default=None) -> Param`. `type` is a Python builtin (`str`/`int`/`float`/
  `bool`) or its JSON-type name (`"string"`/`"integer"`/`"number"`/
  `"boolean"`). `choices` restricts the value to a fixed set (rendered as a
  dropdown). `Param.read(params)` returns the supplied value or, when it was
  absent/blank, the declared `default`.
- Keep the arguments literal: the orchestrator reads these declarations
  straight from the deployed source (it parses the `param()` calls; it does not
  run the bot), so a declaration built from a runtime value would not be seen.
- `setup()` validates the supplied params against the declared `param()` set
  (falling back to `params.json` when none are declared in code), logging
  mismatches as warnings without failing the run.

#### `params.json` declarations file (repo root)

A bot may instead ship a `params.json` at its repo root declaring those params.
The orchestrator reads it to build the form; `lib-core` reads it to validate
what was actually supplied. Code-first `param()` declarations take precedence
when both are present.

It is a JSON object with a `params` array. Each entry:

| Field         | Type    | Required | Meaning                                             |
|---------------|---------|----------|-----------------------------------------------------|
| `name`        | string  | yes      | The key the bot reads via `ctx.params.get(name)`.   |
| `type`        | string  | yes      | One of `string`, `integer`, `number`, `boolean`.    |
| `required`    | boolean | no (def `false`) | Whether the GUI must collect it.            |
| `description` | string  | no (def `""`)    | Shown to the user in the GUI.               |

```json
{
  "params": [
    { "name": "region", "type": "string", "required": true, "description": "Region to process" },
    { "name": "dry_run", "type": "boolean", "required": false, "description": "Skip writes" }
  ]
}
```

#### API and validation

- `automation_core.load_param_definitions(root=None) -> list[dict]` — reads and
  validates `params.json` under `root` (default `Path.cwd()`), returning the
  normalised declarations (`name`, `type`, `required`, `description` always
  present). Returns `[]` when the file is absent. Raises `ConfigurationError`
  when the file exists but is malformed (bad JSON, no `params` array, unknown
  `type`, missing `name`, duplicate names) — a bot developer error, treated the
  same way as a bad `freshservice.defaults` block.
- `setup()` calls this and validates `ctx.params` against the declarations,
  logging each mismatch (missing required param, wrong type, undeclared key) as
  a **warning**. Validation mismatches do **not** fail the run — the
  orchestrator is the primary gate on required params — but a malformed
  `params.json` does propagate from `setup()`.

`bool` is not accepted where an `integer`/`number` is declared (despite being a
subclass of `int` in Python), and vice versa.

---

## 3.5 Desktop GUI automation (`automation_core.gui`) — optional extra

Added v1.10.0. An optional layer over `pywinauto` for driving Windows desktop
applications.

**It is an extra, not a dependency.** `pywinauto` and `pywin32` are Windows
only and irrelevant to most bots, so `automation_core/__init__.py` never
imports `gui`, and importing it without the extra raises `ImportError` naming
the install command.

```powershell
pip install automation-core[gui]
```

```python
from automation_core.gui import GuiApp, controls, windows, to_send_keys
from automation_core.gui.apps.energy_manager import EnergyManager
```

### Modules

| Module | Responsibility |
|--------|----------------|
| `gui.windows` | Find top-level windows by enumerating handles and reading cached captions, then address them by handle. `WindowInfo`, `find_window`, `wait_for_window`, `process_ids`. |
| `gui.controls` | Act on controls safely: `resolve`, `click`, `invoke`, `set_text`, `select`, `read_text`, `wait_until`, `wait_while_present`. Raises `ControlNotReady` / `ControlOffScreen`. |
| `gui.keys` | `to_send_keys("Ctrl+Alt+1") -> "^%1"`. Refuses anything it cannot translate confidently. |
| `gui.app` | `GuiApp`: launch or attach, find windows, get a specification per backend. |
| `gui.apps.<application>` | One driver per application, paired with a `<application>.yaml` knowledge base shipped as package data. |
| `gui.grid` | Reading a WinForms or DevExpress data grid. `read_grid()` returns ordered rows of `{column: value}`. Cells are UIA `DataItem` elements whose name is the address and whose value is in the LegacyIAccessible `Value` property; the grid's own Table pattern is unusable. |
| `gui.monitor` | Watching an operation that runs for hours. `wait_for_operation()` polls a liveness test, detects and dismisses error dialogs, screenshots failures, and emits a heartbeat. Returns a `MonitorResult`/`Outcome` rather than raising. |
| `gui.discover` | The tooling that *generates* those knowledge bases. `python -m automation_core.gui.discover.snapshot` maps a window and proves every selector; `.watcher` snapshots each window a process opens, catching transient dialogs. Ships with the library so a map can always be regenerated. |

`automation_core/gui/PLAYBOOK.md` ships as package data: the reasoning behind
every rule, beside the code that enforces them.

### Design rules it enforces

Each of these exists because the obvious `pywinauto` call fails silently,
intermittently, or destructively. They were established by measuring a real
application, and the point of the module is that a bot author does not have to
remember them.

- **Windows are found by handle**, never by `title=`. Title matching compares
  against `WM_GETTEXT`, which a busy or wedged UI thread does not answer.
- **`auto_id` is used only inside a window already found.** At the desktop
  level it makes `pywinauto` read a WinForms designer name out of every
  top-level window, which needs `VirtualAllocEx` in each target process and
  raises `AccessDenied` on the first protected one.
- **Clicks are posted, not sent.** `wrapper.click()` uses `SendMessage`, which
  does not return until the application finishes handling it; on a button
  that opens a modal dialog the bot hangs indefinitely.
- **Waits are on `visible enabled ready`, never `exists`.** A hidden win32
  control is still in the tree.
- **Drawn items are invoked through UIA.** A WinForms `ToolStrip` item or a
  DevExpress ribbon button is not a child window, so it has no handle to post
  to. `controls.invoke` uses the UIA InvokePattern.
- **No message is sent whose parameter is a pointer.** The receiving window
  procedure dereferences it inside the target process, so a plain integer
  crashes the application. Use `pywinauto`'s accessors, which allocate the
  buffer in the target process correctly.

### Application knowledge bases

An application driver is paired with a YAML file beside it, shipped via
`[tool.setuptools.package-data]`. The YAML is the single source of truth for
selectors, hotkeys and flows, and is **generated by discovery tooling rather
than hand-edited**: it is re-harvested after an application upgrade. It also
records what has *not* been mapped, because a map that omits its gaps gets
believed.

`gui.apps.energy_manager` is the reference implementation.

## 3.6 Branded email (`automation_core.email`)

Added v1.14.0. Builds and sends HTML email to the team's email design spec,
which ships in the package beside the code
(`automation_core/email/email-design-spec.md`), together with the base
template the renderer reads (`email-base-template.html`). Unlike `gui`, it is
part of the core install: it needs only `requests` and `msal`, which the
library already depends on. It is not re-exported from the top level, so the
import is `from automation_core.email import ...`.

```python
from automation_core.email import Email, EmailType, Kpi, Section, Table, send_email, subject_line
from automation_core.email.formatting import format_datetime, percent_of
```

### Modules

- `email.components`: the spec's components as frozen dataclasses (`Banner`,
  `Kpi`, `Table`, `Column`, `Group`, `Section`, `Button`, `AttachedFile`,
  `FolderLink`, `WebLink`), the `Email` that holds them, and
  `subject_line()`. `Email.render()` returns the complete HTML document.
- `email.formatting`: the spec's value rules as functions returning plain
  text: `format_number`, `format_percent`, `percent_of`, `format_money`,
  `format_size`, `format_date`, `format_time`, `format_datetime`, `file_url`.
- `email.graph`: `send_email()` and `split_recipients()`.

### Design rules it enforces

- **The renderer escapes every value.** Callers pass plain text, never HTML.
- **Order is fixed by the code**: header, status banner, summary, KPIs,
  sections, button, files, about, footer. A caller cannot reorder it.
- **Rules that depend only on how an email is built raise `ValueError`** on
  construction: an Alert without an Error or Warning banner or a "What to do"
  list, a Notification without a Success or Info banner, a Request without a
  Warning banner and a button, KPIs other than two to four, more than six list
  items, no summary. These fail on the first run, in development.
- **Rules that depend on the data never fail a run.** A subject over 80
  characters, a preheader outside 50 to 110, or HTML over 100 KB logs a
  warning. A table over 15 rows is cut to 15 with "Showing 15 of N rows."
  and the table's `more_note`; pass `total_rows` when the caller has already
  cut the data.
- **Numeric columns** are right-aligned, and ints in them get thousands
  separators. A column given as a plain string is numeric when every value in
  it is a number or formatted number text. Identifier columns (MPANs, job ids)
  must be declared `Column("MPAN")`, or they would be detected as numeric and
  shown with commas.
- **UK local time** comes from the fixed UK daylight saving rule, not
  `zoneinfo`, because Windows has no time zone database without the `tzdata`
  package.
- **Every email opens with the Inspired Automation header band** from the
  template: the wordmark and the email type (`EmailType.label`, filling
  `{{EMAIL_TYPE}}`) in live text on midnight, so there is no image to block
  and dark mode leaves it alone. The type is not repeated above the title.
  Inspired Automation is internal branding, so the Group logo rules do not
  apply to it, but the band goes on every email: a process that sends outside
  the company would need it made optional first.
- **The template's HTML comments are stripped** when it is loaded, except
  Outlook's `<!--[if mso]>` conditional. One of them names the `{{...}}`
  placeholders and would otherwise be filled in too.

### Sending

`send_email(subject, html, to, *, cc, bcc, attachments, importance, config)`.
`to`, `cc` and `bcc` take a list or a `;`/`,` separated string. `config`
defaults to `load_config()`. A message whose attachments total 2.5 MB or less
goes in one `sendMail` call; a larger one is created as a draft, attachments
of 3 MB or more are uploaded through an upload session in 3.1 MiB chunks
(a multiple of the 320 KiB Graph requires), and the draft is then sent. If any
step of that fails, the draft is deleted and the error re-raised. An
attachment over 150 MB raises `ValueError`; one over 10 MB logs a warning,
because the spec says to link to it instead. A rejected request raises
`requests.HTTPError` carrying Graph's explanation.

## 3.7 Human assist (`ctx.request_assist()`)

Added v1.15.0. Lets a bot ask the Control Room for a person to join its own
RDP session through noVNC, for the one step it cannot do itself (a captcha is
the standing example). Needs Control Room agent 0.45.0 or later on the host,
Human assist installed there, and an interactive bot: a headless job has no
desktop, so the agent refuses and no URL ever arrives.

```python
from automation_core import setup, collect_errors

def main():
    ctx = setup("SupplierPortal")
    with collect_errors(ctx) as errors:
        with ctx.request_assist("Solve the captcha on the login page",
                                timeout_s=900) as assist:
            wait_for_login_page_to_change()
        # assist.release has been written; the session is closed
```

`ctx.request_assist(reason, *, timeout_s=120, wait=True, poll_s=2) -> Assist`

The protocol is the agent's (`agent/vncops.py` in the Control Room repo).
All three files live in the job directory, the directory of `ctx.job_file`:

| File | Written by | Meaning |
|------|------------|---------|
| `assist.request` | bot | Please send a person. JSON `{"reason": "..."}`; the reason is shown on the job page. The agent deletes it when it acts on it. |
| `assist.json` | agent | The session is live: `{"phase", "url", "reason", "expires_in_s"}`. Deleted by the agent when the assist ends. |
| `assist.release` | bot | Carry on: the agent ends the session. Optional `{"reason": "..."}`, recorded as why it ended. |

Request and release are separate files because the bot runs as the runner
account and cannot overwrite or delete a file the agent (LocalSystem) wrote.

- **Entering** writes `assist.request` (via a temp file, so the agent never
  reads half of one; the reason is flattened to one line and capped at 300
  characters so the JSON always fits the 4096 bytes the agent reads) and, when
  `wait` is true, polls for `assist.json` with phase `live` for up to
  `timeout_s`. The agent notices a request within one progress tick (10
  seconds by default).
- **A timeout never raises.** It logs a warning and the block runs: the
  person can still be reached from the job page once the session starts.
- **`assist.url`** re-reads `assist.json` on every access and returns the URL
  only while the phase is `live`, otherwise `None`. `assist.state` is the
  whole file (or `None`), and `assist.wait_for_url(timeout_s=None)` waits
  again. The URL is never logged: it carries the per-assist token that is the
  session's only credential.
- **Leaving** the block always writes `assist.release`, with
  `{"reason": "released by the bot after <ExceptionType>"}` when the block
  raised (the exception still propagates). A request the agent has not
  consumed is deleted first, so it cannot start a session after the bot has
  moved on. `assist.release(reason=None)` releases early and is idempotent.
  Leaving ends the job's assist whoever started it, including one a person
  opened from the job page.
- **A release left over from an earlier block** in the same run is cleared
  before a new request. While the earlier assist is still live (`assist.json`
  present) the agent consumes it on its next tick, so this waits up to 30
  seconds for that; otherwise it is deleted, because the agent only reads a
  release while an assist is live and would end the new one on its first
  tick.
- **No job file** (a hand run): every call is a no-op and entering logs one
  warning, so a developer can run the same code locally. A request that
  cannot be written logs a warning, skips the wait, and writes no release.

## 4. Notification Behaviour

### 4.1 When notifications are sent

| Situation                                          | Notification sent? |
|----------------------------------------------------|---------------------|
| Clean exit, zero errors                            | No                  |
| Clean exit, ≥1 non-fatal error                     | Yes (summary)       |
| Uncaught exception (script terminated abnormally)  | Yes (critical)      |
| Running outside production root (dev)              | No, unless `AUTOMATION_FORCE_NOTIFY=1` |

### 4.2 Dev vs production detection

- Library compares `Path.cwd()` against `paths.production_root` from team.yaml.
- If `cwd` is the production root or a subfolder of it → production mode, notifications active.
- Otherwise → development mode, notifications suppressed.
- Environment variable `AUTOMATION_FORCE_NOTIFY=1` overrides suppression for testing real notifications.
- Either way, the log file is always written.

### 4.3 Overriding detection (`notifications.enabled`)

The `notifications.enabled` key in the merged config (set in a project's `config.yaml`) lets a
project override the automatic dev-vs-production detection described in 4.2. It has three states:

| Value              | Behaviour                                                                                  |
|--------------------|--------------------------------------------------------------------------------------------|
| `"auto"` (or absent) | Path-based detection from 4.2: production root notifies; development suppresses unless `AUTOMATION_FORCE_NOTIFY=1`. |
| `"always"`         | Always dispatch, regardless of path. `AUTOMATION_FORCE_NOTIFY` is irrelevant.              |
| `"never"`          | Never dispatch, regardless of path. `AUTOMATION_FORCE_NOTIFY=1` does NOT override this.    |

```yaml
# config.yaml
notifications:
  enabled: "always"  # auto | always | never
```

Notes:

- `AUTOMATION_FORCE_NOTIFY=1` only has an effect in the `auto` state. It cannot turn notifications
  back on when `enabled` is `never`, and it is redundant when `enabled` is `always`.
- Any unrecognised value is treated as `auto` (safe, backward-compatible default).
- This setting controls dispatch only. The log file is always written regardless of the value.

### 4.4 Email notification format (Microsoft Graph)

Since v1.14.0 the email is an Alert built with `automation_core.email` (see
3.6) by `notifications.alert_email`, and sent with importance high.

- From: team Graph sender address
- To: `notifications.recipient` (project override or team default; several addresses may be separated by `;` or `,`)
- Subject pattern (times in UK local time with zone):
  - Critical: `Failed: <ProcessName> | DD/MM/YYYY HH:MM GMT`
  - Summary: `Action needed: <ProcessName>: N issues during run | DD/MM/YYYY HH:MM GMT`
- Body: an Error banner (critical) or Warning banner (summary), a summary naming the host and time, a "What to do" list, the traceback (critical; the last 60 lines), the issues table (message and details, up to 15 rows, with the rest in the log file), a run details table (process, Control Room job or "Hand run", host, user, issue count), and a link to the log file with its path.
- No meta block. Before v1.14.0 the plain-text body ended with the JSON block from 3.4 for a Power Automate flow; that flow is retired, and the Control Room reads `cr_errors.json` (4.6). Freshservice tickets still carry the block.

### 4.5 Freshservice notification format

- Creates a ticket via the Freshservice API at `<base>/tickets`, where `<base>` is
  derived from `freshservice.domain` by `normalize_base_url()` (strips whitespace,
  a leading `http(s)://` scheme, trailing slashes, and a trailing `/api/v2`), so any
  reasonable domain format resolves to `https://<host>/api/v2`.
- Subject and body are the plain-text format the email used before v1.14.0:
  subject `[CRITICAL] <ProcessName> failed` or `[<ProcessName>] N issue(s) during run`,
  body inside `<pre>`, ending with the meta block (3.4). The email design spec
  covers email only, and Freshservice rules may match on these subjects.
- Priority: High (`2`) for critical, Low (`4`) for summary.
- Fields populated from the required `freshservice.defaults` block:
  - `workspace_id`, `group_id`, `type` — set directly from defaults.
  - `email` — set from `requester_email`.
  - `tags` — the configured `tags` list plus one appended tag: `critical` for
    critical tickets, `summary` for non-fatal summary tickets.

### 4.6 Control Room error sidecar (`cr_errors.json`)

Alongside dispatching the email/Freshservice notification, `collect_errors`
writes a JSON sidecar file next to the job file (`ctx.job_file.parent /
"cr_errors.json"`), written atomically (temp file + rename). This lets the
Control Room agent read a run's collected/critical errors back after the bot
process exits and fold them into the job's terminal report, so the console
can show the same errors already sent out — it is a read-back mechanism for
the Control Room only, not a second notification channel.

- Written whenever there is anything to report: on a clean exit with
  `errors.count > 0`, or on an unhandled exception. Never written on a clean,
  error-free exit, and never written at all when `ctx.job_file is None` (a
  hand run) — file absence is always the ordinary case.
- Written unconditionally, independent of the `notifications.enabled` setting
  (§4.3) — this is Control Room visibility, not the team's own notification
  routing decision.
- A write failure only logs a warning to the internal log; it never affects
  the run or the existing notification dispatch.

Schema (`schema: 1`):

```json
{
  "schema": 1,
  "is_critical": false,
  "error_count": 2,
  "timestamp_utc": "2026-07-26 10:15:32 UTC",
  "errors": [
    {"message": "row 4 missing SKU", "details": {"row": 4}, "exception": null},
    {"message": "upstream API timed out", "details": null, "exception": "TimeoutError: read timed out"}
  ],
  "traceback": null
}
```

`errors` uses the same serialization as the notification meta block's
`errors` array (§4.4): `exception` is `"TypeName: str(exc)"` or `null`, never
a live exception object. `traceback` is the full formatted traceback string
when `is_critical` is true and populated only in that case, otherwise `null`.
`job_id`/`process_name`/`host`/`user` are deliberately omitted: the agent
already knows which job it is reporting on from its own job record.

---

## 5. Logging Behaviour

The library handles all logging setup. Projects just import `logging` and use it normally.

- File handler writes to `<production_root>\<ProcessName>\logs\<YYYY>\<MonthName>\<DD>\<ProcessName>_<YYYYMMDD>_<HHMMSS>_<suffix>.log`, where `<suffix>` is `job<job_id>` when the Control Room supplied a job id (see §3.4) and `p<pid>` otherwise. The suffix keeps concurrent runs of the same bot in separate files: with a job id the name is globally unique; without one, the process id separates two same-second hand runs on a host.
- The file handler (`ReopeningFileHandler`) survives the share dropping mid-run. A failed write closes the dead handle, reopens the file in append mode and writes the record again. While the file stays unreachable, records are counted rather than written (the console still has them), a reopen is tried at most every 30 seconds so an unreachable share cannot stall every log call, and only the first failure prints a `--- Logging error ---` traceback. When the file is back, a WARNING line records how many lines it is missing. A plain `FileHandler` instead failed every write for the rest of the run.
- Console handler writes to stdout.
- Format: `%(asctime)s | %(levelname)s | %(name)s | %(message)s`.
- Encoding: UTF-8.
- Log root resolution:
  1. `config.yaml`'s `logging.log_root` if set
  2. Default: `<production_root>\<ProcessName>` (computed from team.yaml + process name)

If neither team.yaml's `paths.production_root` nor a `config.yaml` override exists, the library raises a clear error at setup.

---

## 6. Library Internal Structure

Suggested module layout (the implementer can adjust):

```
lib-core/
├── automation_core/
│   ├── __init__.py          # Public API re-exports
│   ├── config.py            # Loading team.yaml and config.yaml
│   ├── logging_setup.py     # Logging configuration
│   ├── context.py           # Context dataclass
│   ├── assist.py            # Human assist markers for the Control Room agent (see 3.7)
│   ├── params.py            # Run-param declarations (params.json) loader/validator
│   ├── errors.py            # ErrorCollector and collect_errors
│   ├── email/               # Branded HTML email (see 3.6)
│   │   ├── components.py    # Spec components and the renderer
│   │   ├── formatting.py    # Number, date, time and path formatting
│   │   ├── graph.py         # send_email via Microsoft Graph
│   │   ├── email-base-template.html
│   │   └── email-design-spec.md
│   ├── notifications/
│   │   ├── __init__.py      # Dispatch by method
│   │   ├── alert_email.py   # The error alert, built with automation_core.email
│   │   ├── email_graph.py   # Thin wrapper over email.graph.send_email
│   │   └── freshservice.py  # Freshservice ticket creator
│   └── _internal_log.py     # Logger for the library's own errors
├── tests/
│   └── ...
├── pyproject.toml
├── README.md
└── CHANGELOG.md
```

---

## 7. Implementation Notes

- Use `requests` for HTTP calls to Graph and Freshservice. Pin to a tested version.
- Use `msal` for Graph token acquisition (standard Microsoft pattern). Pin to a tested version.
- All library errors (e.g. Graph API call fails) must be caught and logged via a separate library logger, so they never silently propagate but also never crash the project's own error reporting.
- If notification dispatch itself fails (e.g. Graph API down), log it loudly and let the script exit normally. Don't make the failure of notification cause a cascading failure.
- The library must not depend on Watchdog or anything project-specific.
- No `print()` calls anywhere in the library; use `logging` only.

---

## 8. Versioning and Release Process

- Use semantic versioning (e.g. `1.0.0`, `1.1.0`, `2.0.0`).
- Tag releases on GitHub: `git tag v1.0.0 && git push --tags`.
- Document changes in `CHANGELOG.md`.
- Projects pin exact versions: `automation-core==1.0.0` in `requirements.txt`.
- Breaking changes require a major version bump.

---

## 9. Initial Release Scope (v1.0.0)

The first release must include:

- `setup(process_name)` returning Context
- `collect_errors(ctx)` context manager
- `ErrorCollector` with `add()` and `count`
- Config loading from team.yaml + config.yaml
- Logging setup with file + console handlers
- Production-vs-dev detection
- `AUTOMATION_FORCE_NOTIFY` env var support
- Email notification via Graph
- Freshservice ticket notification

Out of scope for v1, planned for later:

- Database connection helpers (pyodbc wrappers)
- Common SQL patterns
- Slack/Teams notification methods
- Retry helpers for API calls