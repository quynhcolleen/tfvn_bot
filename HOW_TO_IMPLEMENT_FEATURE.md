# Feature Implementation Workflow

Follow these steps when adding a feature or changing existing behavior. Use
focused tests while developing. Run the full suite only after implementation,
regression tests, development checks, documentation, and diff review are complete.

## Step 1 — Define the expected behavior

Describe what the member or administrator should experience before writing code:

- What starts the feature: a command, button, event, or timer?
- Who can use it, and in which channels?
- What does success look like, including the Vietnamese response text?
- What happens for invalid input, missing permissions, or unavailable services?
- What should happen on repeated requests, restart, cog reload, and timeout?

Turn these answers into a short acceptance checklist. Keep unrelated changes out
of the feature. Resolve routine implementation choices using existing patterns;
ask for clarification when missing information changes the expected behavior.

## Step 2 — Find the owning module and existing patterns

Read [AGENTS.md](AGENTS.md) and [CODING_CONVENSION.md](CODING_CONVENSION.md).
Use [CODEBASE.md](CODEBASE.md) to locate the owning cog and helpers, and
[FUNCTIONS.md](FUNCTIONS.md) to check current user-facing behavior. Consult
[README.md](README.md) for setup and configuration.

Inspect the affected code and its tests. Check `git status --short` and preserve
unrelated work already in the workspace. Identify shared helpers and dependent
cogs before deciding which files to change.

## Step 3 — Prepare a focused development environment

Use Python 3.11 and the repository's virtual environment. Install development
dependencies when setting up the environment or when dependencies change:

```powershell
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
$env:ENVIRONMENT = "development"
```

Edit the ignored `dev_cogs.txt` to select the feature and its required cogs.
For example, highlight development needs:

```text
cogs.settings.variable_setting
cogs.utils.highlight
```

Include the settings cog when the feature reads `bot.global_vars`. Add other
dependencies only when needed. `sample.dev_cogs.txt` is a reference; editing it
does not change the active development profile. Review the existing local profile
before changing it, and do not commit local credentials or server IDs.

## Step 4 — Implement one behavior at a time

Keep the feature in its domain under `cogs/`. A loadable module must expose
`async def setup(bot)`; supporting modules start with `_` so discovery skips them.
Reuse existing helpers and extract deterministic logic when that makes it easier
to test.

Follow the coding conventions for asynchronous Discord I/O, permission checks,
mention handling, MongoDB state, and error reporting. For background or interactive
features, handle task cancellation, repeated events, and view registration after
restart. Add indexes or migrations when the persistence design needs them.

Implement small, reviewable pieces. Move between this step and Step 5 until the
acceptance checklist is satisfied.

## Step 5 — Run partial tests during development

Add or update focused regression tests for changed behavior. Keep automated tests
isolated from live Discord, MongoDB, external APIs, real delays, and production
credentials.

Start with the smallest relevant test selection. These examples use highlight;
replace the filename and selection with those for the feature being developed:

```powershell
# Only prompt-related tests while working on prompt behavior.
python -m pytest test/test_highlight.py -k prompt -q

# The affected module once its behavior is ready.
python -m pytest test/test_highlight.py -q

# Include directly affected modules when shared code changes.
python -m pytest test/test_highlight.py test/test_highlight_media.py -q
```

Choose the selection that matches the edit; these are not three mandatory runs
after every change. Test success, meaningful failure cases, permissions, and
repeated or concurrent requests where relevant. Fix failures and rerun the affected
selection. Expand to direct consumers of changed shared code as needed.

**Do not run the full suite during the implementation loop or after every small
edit.** Do not rerun passing tests without a new change or an unresolved concern.

## Step 6 — Check Discord behavior in development

When commands, listeners, embeds, buttons, or timers change, run the focused
profile from the repository root in a development server:

```powershell
python main.py
```

Exercise the acceptance checklist with the configured bot prefix. Check the
visible response, button actions, required permissions, and relevant logs. Test
restart, reload, duplicate events, or timeout behavior when the feature depends
on them. Capture screenshots for changed embeds or interactive views.

Fix issues and return to the focused tests in Step 5. If live Discord validation
is unavailable, record what remains unverified; do not describe it as tested.

## Step 7 — Finish documentation and review the diff

Update the documents affected by the completed implementation:

| Change | Document |
| --- | --- |
| Commands, buttons, or automatic behavior | `FUNCTIONS.md` |
| Modules, ownership, or persistence boundaries | `CODEBASE.md` |
| Setup, configuration, dependencies, or permissions | `README.md` |
| Coding rules or development workflow | `CODING_CONVENSION.md`, `AGENTS.md`, or this guide |

Review `git diff` and run `git diff --check`. Remove accidental changes, debugging
code, secrets, and generated artifacts from the proposed diff without discarding
someone else's work. Confirm the implementation and tests cover the acceptance
checklist. Complete planned edits before the final test run.

## Step 8 — Run the full suite once the work is complete

Run the final integration check from the repository root:

```powershell
python -m pytest
```

This is the same full-suite command used by the GitHub `pytest` check. Run it once
for the completed feature. If it fails, investigate the failures, make the needed
fixes, and use focused tests while fixing them. Run the full suite again only
after those fixes are complete. If executable code or tests change after a passing
final run, verify the affected tests and repeat the full suite when work is complete
again. Do not repeat a passing final run when nothing relevant has changed.

For documentation-only or copy-only edits that cannot affect executable behavior,
check the changed text, links, and diff instead of running the full suite. If copy
is asserted by existing tests, update those assertions and run the affected tests.
Changes to test configuration or CI are not documentation-only changes.

## Step 9 — Hand off the completed feature

Report what changed, any configuration or migration needed, and the tests actually
run. State any live checks that remain unverified. Mention whether a bot restart or
cog reload is needed to activate the change.

When preparing a commit or PR, keep it scoped to the feature and include the
user-visible behavior, relevant screenshots, and validation results. CI still runs
the full suite for PRs; focused local development does not replace the final check.
