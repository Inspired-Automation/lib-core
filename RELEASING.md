# Releasing automation-core

How to cut a new release of `automation-core`.

## Versioning

Semantic versioning. Bump:

- **PATCH** (1.2.0 → 1.2.1) for bug fixes that don't change the API
- **MINOR** (1.2.0 → 1.3.0) for backward-compatible new features
- **MAJOR** (1.2.0 → 2.0.0) for breaking changes

## Pre-release checklist

Before tagging, confirm:

1. All tests pass:
   ```powershell
   pytest
   ```
2. Version is bumped in both places:
   - `pyproject.toml`
   - `src/automation_core/__init__.py` (`__version__`)
3. `CHANGELOG.md` has a new `## [X.Y.Z] - YYYY-MM-DD` block describing what changed.
4. `CLAUDE.md` Change Log has a one-line entry for this release.
5. If the public API or config schema changed, `lib-core-spec.md` is updated to match.

## Release steps

1. Confirm working tree is clean:
   ```powershell
   git status
   ```
   If anything is uncommitted, commit and push first.

2. Create the tag and push it:
   ```powershell
   git tag vX.Y.Z
   git push --tags
   ```

3. Build the wheel (requires the `build` package: `py -m pip install build`):
   ```powershell
   py -m build --wheel --outdir dist
   ```
   This produces `dist\automation_core-X.Y.Z-py3-none-any.whl`. The filename must
   match the version being released; if it doesn't, the version bump in
   `pyproject.toml` was missed.

4. Draft the GitHub release:
   - Go to https://github.com/Inspired-Automation/lib-core/releases
   - Click **Draft a new release**
   - **Choose a tag:** select `vX.Y.Z`
   - **Release title:** `vX.Y.Z`
   - **Description:** copy the matching `## [X.Y.Z]` block from `CHANGELOG.md`
   - **Attach the wheel:** drag `dist\automation_core-X.Y.Z-py3-none-any.whl` into
     the assets box. This step is required — projects that install by wheel URL
     get a 404 without it.
   - Leave **Set as the latest release** ticked
   - Click **Publish release**

## Post-release verification

1. The Releases page should show `vX.Y.Z` with the "Latest" badge and the
   `automation_core-X.Y.Z-py3-none-any.whl` asset attached.
2. From a clean folder, confirm the package installs from GitHub and the version is correct:
   ```powershell
   pip install git+https://github.com/Inspired-Automation/lib-core.git@vX.Y.Z --break-system-packages
   python -c "import automation_core; print(automation_core.__version__)"
   ```
   Should print `X.Y.Z`.
3. Confirm the wheel asset installs too:
   ```powershell
   pip install https://github.com/Inspired-Automation/lib-core/releases/download/vX.Y.Z/automation_core-X.Y.Z-py3-none-any.whl --break-system-packages
   ```

## Pinning in consuming projects

Either line works in a project's `requirements.txt`:

```
# From the git tag (needs git on the machine; pip builds the package itself)
automation-core @ git+https://github.com/Inspired-Automation/lib-core.git@vX.Y.Z

# From the release wheel asset (faster, no git needed; requires the wheel
# to have been uploaded to the release)
automation-core @ https://github.com/Inspired-Automation/lib-core/releases/download/vX.Y.Z/automation_core-X.Y.Z-py3-none-any.whl
```

## After releasing

- New Watchdog-scaffolded projects pick up the new release automatically (Watchdog fetches the latest release tag at project creation).
- Existing projects keep their pinned version. To upgrade, edit `requirements.txt` in that project and pin to the new tag.
- If the release affects day-to-day work for the team (new features, breaking changes, security fixes), let the team know in Teams.

## Rules

- **Never edit or move a tag after pushing it.** Tags are immutable references. If you need to fix a release, cut a new patch version.
- **Never force-push tags.** Same reason.
- **Never delete a release.** Deprecate it in the CHANGELOG if needed, but leave it in place.
- **Never skip the CHANGELOG entry.** Future you (and the team) need to know what changed.