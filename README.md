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

## Configuration

Two config files are read at startup:

1. **Team config (required):** `\\inspiredenergysolutions.local\DFS\Public\!IE\BPI\Automation Team\Tools\Scripts\yaml\team.yaml`. Contains Graph credentials, Freshservice credentials, default notification recipient, and the production root path.
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