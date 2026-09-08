import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

import db
from cogs._beta_function import BetaFunctionError
from cogs._feature_flags import cog_disabled
from cogs.operation._graceful_shutdown import (
    GracefulShutdownManager,
    ShutdownInProgress,
    drain_and_close,
    install_shutdown_signal_handlers,
    send_shutdown_rejection,
)
from cogs.operation._lifecycle import BotLifecycleRecorder
from dataloader import DataLoader

load_dotenv()
environment = os.getenv("ENVIRONMENT", "production").strip().lower()
token = os.getenv("DISCORD_TOKEN")

COG_PROFILE_FILES = {
    "development": "dev_cogs.txt",
}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix=os.getenv("COMMAND_PREFIX", "!tf "),
    intents=intents,
    help_command=None,
)

bot.db = db.db
bot.environment = environment
bot.lifecycle_recorder = BotLifecycleRecorder(bot.db, environment)
bot.shutdown_manager = GracefulShutdownManager()
bot.CONTENT_VERIFICATION_KEYS_JSON = os.getenv(
    "CONTENT_VERIFICATION_KEYS_JSON"
)
bot.CONTENT_VERIFICATION_ACTIVE_KEY_ID = os.getenv(
    "CONTENT_VERIFICATION_ACTIVE_KEY_ID"
)

# inject environment variables to all class
# TODO: inject environment variables to all class for better practice
bot.WORD_CONNECT_GAMES_CHANNELS = os.getenv(
    "WORD_CONNECT_GAMES_CHANNELS", ""
).split(",")

# Load static data files once during startup.
# Load banned words globally
loader = DataLoader(base_path="data")

bot.BANNED_WORDS = loader.load_lines("banned_word_list.txt")
bot.WORD_CONNECT_WORDS = loader.load_lines("word_connect_valid_list.txt")
bot.FAKE_LOADING_SENTENCES = loader.load_lines("fake_loading_sentences.txt")
bot.FEMBOY_ROLE = loader.load_lines("femboy_role.txt")


def command_admission_check(ctx: commands.Context) -> bool:
    """Atomically reject new commands after graceful draining begins."""
    bot.shutdown_manager.admit(ctx)
    return True


bot.add_check(command_admission_check, call_once=True)


def configure_logging() -> None:
    """Configure file logging when writable and always retain console output."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.insert(
            0,
            logging.FileHandler(
                filename="bot.log",
                encoding="utf-8",
                mode="a",
            ),
        )
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, handlers=handlers)


@bot.event
async def on_ready() -> None:
    bot.lifecycle_recorder.capture_ready(len(bot.guilds))
    print(f"✅ Bot is ready! Environment: {environment}")


@bot.event
async def on_resumed() -> None:
    bot.lifecycle_recorder.capture_resumed(len(bot.guilds))


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return

    ctx = await bot.get_context(message)
    if ctx.command is not None:
        try:
            bot.shutdown_manager.admit(ctx)
        except ShutdownInProgress:
            await send_shutdown_rejection(ctx)
            return
    elif bot.shutdown_manager.draining and ctx.invoked_with:
        await send_shutdown_rejection(ctx)
        return
    try:
        await bot.invoke(ctx)
    finally:
        # This is the synchronous lifecycle boundary for incoming prefix
        # commands. Event listeners are asynchronous and cannot safely own it.
        bot.shutdown_manager.finish(ctx)


@bot.event
async def on_command_completion(ctx: commands.Context) -> None:
    """Release commands invoked outside the normal on_message path."""
    bot.shutdown_manager.finish(ctx)


@bot.event
async def on_command_error(
    ctx: commands.Context,
    error: commands.CommandError,
) -> None:
    bot.shutdown_manager.finish(ctx)
    if isinstance(error, ShutdownInProgress):
        await send_shutdown_rejection(ctx)
        return
    if isinstance(error, BetaFunctionError):
        await ctx.send(
            error.user_message,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return
    await commands.Bot.on_command_error(bot, ctx, error)


def get_cogs_from_path(base_path: str) -> list[str]:
    """Helper to get all cog modules from a path (supports wildcards)."""
    cogs: list[str] = []
    if os.path.isdir(base_path):
        for root, _, files in os.walk(base_path):
            for file in files:
                if file.endswith(".py") and not file.startswith("_"):
                    path = os.path.join(root, file)
                    module = (
                        path.replace("\\", ".")
                        .replace("/", ".")
                        .removesuffix(".py")
                    )
                    cogs.append(module)
    return cogs


def get_cogs_from_profile(profile_path: str) -> list[str]:
    """Load explicit modules and wildcard directories from a profile file."""
    cogs: list[str] = []
    with open(profile_path, "r", encoding="utf-8") as profile:
        for line in profile:
            entry = line.strip()
            if not entry or entry.startswith("#"):
                continue
            if entry.endswith(".*"):
                base_path = entry[:-2].replace(".", "/")
                cogs.extend(get_cogs_from_path(base_path))
            else:
                cogs.append(entry)
    return list(dict.fromkeys(cogs))


async def load_cogs() -> None:
    bot.selected_extensions = set()
    if not hasattr(bot, "extension_load_failures"):
        bot.extension_load_failures = {}
    profile_path = COG_PROFILE_FILES.get(environment)
    if profile_path:
        if os.path.exists(profile_path):
            cogs_to_load = get_cogs_from_profile(profile_path)
        else:
            print(
                f"❌ {profile_path} not found. No cogs loaded in "
                f"{environment} mode."
            )
            return
    else:
        # Production: Load all cogs from cogs directory
        cogs_to_load = get_cogs_from_path("cogs")

    disabled = [module for module in cogs_to_load if cog_disabled(module)]
    cogs_to_load = [
        module for module in cogs_to_load if not cog_disabled(module)
    ]
    bot.selected_extensions = set(cogs_to_load)
    for module in disabled:
        print(f"⏭️ Disabled cog: {module}")

    settings_cog = "cogs.settings.variable_setting"

    # Prioritize loading the settings cog first
    if settings_cog in cogs_to_load:
        cogs_to_load.remove(settings_cog)
        cogs_to_load.insert(0, settings_cog)  # Insert at the beginning

    for module in cogs_to_load:
        try:
            await bot.load_extension(module)
            bot.extension_load_failures.pop(module, None)
            print(f"✅ Loaded cog: {module}")
        except Exception as exc:
            original = exc.original if isinstance(exc, commands.ExtensionFailed) else exc
            error_type = type(original).__name__
            bot.extension_load_failures[module] = error_type
            print(f"❌ Failed to load cog {module}: {error_type}")


async def main() -> None:
    configure_logging()
    if not token:
        raise RuntimeError("DISCORD_TOKEN is required")
    await bot.lifecycle_recorder.start()
    try:
        async with bot:
            await load_cogs()
            shutdown_task: asyncio.Task[bool] | None = None

            def request_shutdown(reason: str) -> None:
                nonlocal shutdown_task
                if bot.shutdown_manager.begin_shutdown(reason):
                    shutdown_task = asyncio.create_task(
                        drain_and_close(bot, bot.shutdown_manager),
                        name="graceful-bot-shutdown",
                    )
                    return
                bot.shutdown_manager.force_shutdown()

            restore_signal_handlers = install_shutdown_signal_handlers(
                asyncio.get_running_loop(),
                request_shutdown,
            )
            try:
                await bot.start(token)
            finally:
                restore_signal_handlers()
                if shutdown_task is not None:
                    if not shutdown_task.done() and not bot.is_closed():
                        bot.shutdown_manager.force_shutdown()
                    await shutdown_task
    finally:
        await bot.lifecycle_recorder.close()


if __name__ == "__main__":
    asyncio.run(main())
