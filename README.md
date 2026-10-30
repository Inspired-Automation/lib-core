# automation-core

Shared utilities for Inspired-Automation team projects.

Provides logging setup, configuration loading, error collection, and notification dispatch so every project handles these concerns identically without reimplementing boilerplate.

## Installation

In a project's `requirements.txt`:

```
automation-core @ git+https://github.com/Inspired-Automation/lib-core.git@v1.0.0
```

Then `pip install -r requirements.txt`.

## Usage

```python
from automation_core import setup, collect_errors

PROCESS_NAME = "MyProcess"


def main() -> None:
    ctx = setup(PROCESS_NAME)
    with collect_errors(ctx) as errors:
        # ... project work ...
        # non-fatal: errors.add("description", exception=exc)
        # fatal: raise normally
        pass


if __name__ == "__main__":
    main()
```

## Sending email

Branded report, alert, notification and request emails are built with `automation_core.email` (1.14.0 or later), which follows the team's email design spec. The spec ships in the package at `automation_core/email/email-design-spec.md`, and the module docstring has a complete example. See `lib-core-spec.md` §3.6.

## Asking for a person (human assist)

A bot running under the Control Room that hits something only a person can do, such as a captcha, can ask for someone to join its session (1.15.0 or later, agent 0.45.0 or later, interactive bots only):

```python
with ctx.request_assist("Solve the captcha on the login page", timeout_s=900) as assist:
    # Entering asked the Control Room and waited for the session to go live.
    # assist.url is the noVNC link (None until live); the job page shows it too.
    wait_for_login_page_to_change()
# Leaving the block, even on an exception, releases the session.
```

A slow or missing URL logs a warning and never raises. On a hand run there is no job file, so it logs a warning and does nothing. Pass `wait=False` to return at once and poll `assist.url` yourself. See `lib-core-spec.md` §3.7.

## Configuration

Two config files are read at startup:

1. **Team config (required):** `\\inspiredenergysolutions.local\DFS\Public\!IES\BPI\Automation Team\Tools\Scripts\yaml\team.yaml`. Contains Graph credentials, Freshservice credentials, default notification recipient, and the production root path.
2. **Project config (optional):** `config/config.yaml` in the project root. Overrides team defaults on a key-by-key basis.

See the full specification in `lib-core-spec.md`.

## Development

Install in editable mode:

```
pip install -e .
```

## Versioning

Semantic versioning. Tag releases on GitHub:

```
git tag v1.0.0
git push --tags
```