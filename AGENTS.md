# Repository Guidelines

## Documentation Map

Read [README.md](README.md) for setup and operation, [CODEBASE.md](CODEBASE.md) for runtime flow and file ownership, [FUNCTIONS.md](FUNCTIONS.md) for the full command and feature catalog, and [CODING_CONVENSION.md](CODING_CONVENSION.md) for detailed implementation rules. Update the relevant document when structure, configuration, or conventions change.

## Project Structure & Module Organization

`main.py` configures Discord intents, loads datasets, and discovers extensions. `db.py` creates the MongoDB client, while `dataloader.py` handles files under `data/`. Bot features live in domain-based packages under `cogs/` (`mod/`, `booster/`, `minigames/`, `interaction/`, and others). Every loadable cog must expose `async def setup(bot)`. Prefix helper modules with `_` so production discovery skips them.

Static media constants belong in `assets/`, maintenance utilities in `scripts/`, and automated tests in `test/`. Docker deployment files are at the repository root.

## Build, Test, and Development Commands

- `python -m venv venv` and `.\venv\Scripts\Activate.ps1`: create and activate the Windows development environment.
- `python -m pip install -r requirements.txt`: install the pinned Python dependencies.
- `python -m pip install -r requirements-dev.txt`: install runtime dependencies and pytest for development.
- `python main.py`: run the bot from the repository root so relative data paths resolve.
- `python -m pytest`: run the full unit-test suite, matching the GitHub PR check.
- `python -m unittest discover -s test -p "test_*.py"`: run the suite with the standard-library runner.
- `docker compose up --build -d`: build and start the production-style container. MongoDB is external to this Compose file.

For focused development, set `ENVIRONMENT=development` and list dotted cog paths such as `cogs.mod.*` in the ignored `dev_cogs.txt` file.

## Coding Style & Naming Conventions

Target Python 3.11 and use four-space indentation. Follow existing conventions: `snake_case` for modules, functions, and variables; `PascalCase` for cog/view classes; and `UPPER_SNAKE_CASE` for constants. Keep Discord callbacks asynchronous, add type hints to new public helpers, and group standard-library, third-party, then local imports. No formatter or linter is configured, so match surrounding code and avoid unrelated reformatting.

## Testing Guidelines

Tests use the standard `unittest` framework and run through pytest in CI. Name discovered files `test_*.py`, classes `Test...`, and methods `test_...`. Keep unit tests deterministic and isolated from live Discord, MongoDB, and external APIs. There is no enforced coverage threshold; add focused regression tests for changed parsing, persistence, or game logic. The `pytest` PR check must be required in GitHub branch protection to block merges on failure; see README.md.

## Commit & Pull Request Guidelines

Recent history favors short, imperative subjects such as `Add giveaway cog` and `Improve data prep`. Keep each commit scoped to one behavior. Pull requests should explain user-visible changes, configuration or schema impacts, and verification performed; link relevant issues. Include screenshots for changed Discord embeds or interactive views. Never commit tokens, API credentials, `.env` files, production IDs, or database exports.
