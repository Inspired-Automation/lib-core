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

Path: `I:\BPI\Automation Team\Tools\Scripts\yaml\team.yaml`

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

notifications:
  default_recipient: "bpiautomation@inspiredenergy.co.uk"

paths:
  production_root: "I:\\BPI\\Automation Team\\Automated Processes"
```

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
```

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

---

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

- From: team Graph sender address
- To: `notifications.recipient` (project override or team default)
- Subject pattern:
  - Critical: `[CRITICAL] <ProcessName> failed`
  - Summary: `[<ProcessName>] N issue(s) during run`
- Body: machine name, run timestamp, log file path, error list with details.

### 4.5 Freshservice notification format

- Creates a ticket via Freshservice API
- Subject and body match the email format
- Priority: High for critical, Low for summary

---

## 5. Logging Behaviour

The library handles all logging setup. Projects just import `logging` and use it normally.

- File handler writes to `<production_root>\<ProcessName>\logs\<YYYY>\<MonthName>\<DD>\<ProcessName>_<YYYYMMDD>_<HHMMSS>.log`.
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
│   ├── errors.py            # ErrorCollector and collect_errors
│   ├── notifications/
│   │   ├── __init__.py      # Dispatch by method
│   │   ├── email_graph.py   # Microsoft Graph email sender
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