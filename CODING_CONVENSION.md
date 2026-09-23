# Coding Conventions

These are the target conventions for new and changed code. Some legacy modules differ; improve the code you touch without reformatting unrelated files. Use [AGENTS.md](AGENTS.md) for the short contributor workflow and [CODEBASE.md](CODEBASE.md) to find subsystem ownership.

## Python Style

- Target Python 3.11, save source/data files as UTF-8, and keep tracked text files on LF line endings as enforced by `.gitattributes`.
- Indent with four spaces; never use tabs.
- Keep lines readable (roughly 88–100 characters when practical), but do not churn existing code only to wrap lines.
- Use `snake_case` for files, functions, methods, variables, commands, and MongoDB fields; `PascalCase` for cogs, views, and other classes; `UPPER_SNAKE_CASE` for constants.
- Prefer type hints on new functions, public helpers, cog constructors, and return values. Use modern syntax such as `str | None` and `list[str]`.
- Write docstrings for reusable helpers or behavior that is not obvious. Comments should explain intent or constraints, not repeat the code.

Group imports in this order, separated by blank lines:

1. Python standard library
2. Third-party packages (`discord`, `aiohttp`, `pymongo`)
3. Local project modules (`assets`, `cogs`, `dataloader`)

No formatter or linter is configured. Match the surrounding file and validate changes with the test command in `AGENTS.md`.

## Cog and Command Pattern

Every public module under `cogs/` is treated as a production extension. It must expose an asynchronous setup function. Name non-extension helpers with a leading underscore, for example `_meter_helper.py`.

```python
import logging

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)


class ExampleCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db

    @commands.command(name="example", help="Mô tả ngắn cho lệnh.")
    @commands.guild_only()
    async def example(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        target = member or ctx.author
        await ctx.send(f"Đã chọn {target.mention}.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ExampleCog(bot))
```

Use explicit command names and concise `help` text. Add aliases only when they are stable and unambiguous. Use subcommand groups for one feature with multiple operations. Keep user-facing command responses in Vietnamese unless the surrounding feature deliberately uses another language; keep identifiers, logs, and technical comments in English.

Mark experimental commands with `@BetaFunction` from
`cogs._beta_function`, placed closest to the callback underneath the Discord
command decorator. The shared check requires the member to hold at least one
role configured in the MongoDB `BETA_ROLE_IDS` array, regardless of runtime; do
not duplicate that check inside commands.

## Discord and Async Safety

- Use `async def` for commands, listeners, view callbacks, and Discord I/O.
- Never call `time.sleep()` in async code; use `await asyncio.sleep()`.
- Ignore bot-authored messages in listeners unless handling them is intentional.
- Add `@commands.guild_only()` when a command requires guild roles, channels, permissions, or members.
- Enforce caller permissions with decorators and separately verify the bot's permission and role hierarchy before moderation, role, or channel actions.
- Catch specific Discord exceptions such as `discord.Forbidden`, `discord.NotFound`, and `discord.HTTPException`. Log unexpected failures before sending a safe user-facing message.
- Escape or restrict mentions when repeating user-controlled text. Use `discord.AllowedMentions.none()` where mentions are not part of the feature.
- Apply cooldowns to commands that are expensive, destructive, or easy to spam.
- Age-restricted commands must check `ctx.channel.is_nsfw()` before fetching or displaying content.

For `tasks.loop`, wait for readiness in a `before_loop` hook and cancel the task in `cog_unload`. Track manually created `asyncio.Task` objects and cancel them during unload. Persistent Discord views must be re-registered after restart.

## Configuration and Secrets

Use environment variables for credentials and process-level boot settings:

- Discord token
- MongoDB connection fields
- External API credentials
- `ENVIRONMENT` and `COMMAND_PREFIX`
- Process-level extension controls such as `DISABLED_COGS`

Use MongoDB `global_variables` for guild-specific IDs, configurable media arrays, and feature settings. Read them through `bot.global_vars`; validate required values at cog initialization and name the missing key in the error. Optional settings should have an explicit default or disable only the affected behavior.

Use `DISABLED_COGS` when an entire extension must be skipped before import;
patterns are parsed by `cogs._feature_flags`.

Never commit `.env`, `.env.prod`, tokens, API keys, production IDs, database dumps, or log files. Do not print secrets or full connection strings.

## MongoDB Conventions

- Reuse `bot.db`; do not create a new `MongoClient` in a cog.
- Keep collection and field names stable and lowercase `snake_case`.
- Store Discord IDs as integers and include `guild_id` when records can exist in multiple guilds.
- Use `discord.utils.utcnow()` for persisted timestamps and keep comparisons in UTC.
- Prefer atomic `update_one(..., upsert=True)` operations for mutable state.
- Bound queries with filters and limits; do not introduce full collection scans in message listeners.
- Create indexes for persisted interactive features that resolve records by IDs or deadlines.

PyMongo calls are synchronous in the current architecture. Keep operations small. Move potentially slow bulk work to a maintenance script or a worker thread rather than blocking the Discord event loop. Schema changes that transform existing data belong in `scripts/` and must be safe to rerun where practical.

## External APIs and Data Files

Use `aiohttp` for HTTP inside async features. Set an explicit timeout, check response status, validate response shapes, and handle connection and timeout errors separately. Prefer a cog-owned reusable session for frequent calls and close it in `cog_unload`.

Load project data relative to the repository or module path and specify `encoding="utf-8"`. Do not hand-edit large generated datasets such as `data/vietnamese_king_data.json`; update the preparation script and regenerate the output. Keep source lists one item per line unless their existing format requires JSON.

## Logging and Error Handling

Create a module logger with `logging.getLogger(__name__)`. Use logs for technical context and concise Discord messages for users. Avoid new `print()` debugging. Do not use a broad `except Exception` unless the handler logs the traceback, performs necessary cleanup, and returns a generic response.

Validate user input before mutating Discord or MongoDB state. For multi-step commands, apply timeouts to `bot.wait_for`, scope checks to the same author and channel, and provide a cancellation path when appropriate.

## Testing Conventions

Tests use `unittest`, live under `test/`, and run through pytest in CI:

- Files: `test_<feature>.py`
- Classes: `TestBehavior`
- Methods: `test_expected_result`

Extract parsing, scoring, formatting, and validation into pure helpers so they can be tested without Discord. Mock Discord, MongoDB, time, randomness, and HTTP boundaries; automated tests must not require production credentials or network access. Add a regression test for each fixed bug and cover both success and invalid-input paths.

Install development dependencies and run the same full suite as the PR check:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

`test/word_stardardlize.py` is a manual data utility and can write files; it is not part of normal test execution.

## Definition of Done

Before handing off a change:

- Run the relevant unit tests and the full discovered suite.
- Exercise the affected cog with a focused `dev_cogs.txt` profile when Discord behavior changed.
- Document new environment variables, Mongo global variables, collections, or migrations.
- Update `CODEBASE.md` when adding, removing, or moving modules.
- Check that no secret, log, cache, generated scratch file, or unrelated formatting entered the diff.
