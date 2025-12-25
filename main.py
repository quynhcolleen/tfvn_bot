import discord  # pyright: ignore[reportMissingImports]
from discord.ext import commands  # pyright: ignore[reportMissingImports]
import os
import asyncio
import logging
from dotenv import load_dotenv  # pyright: ignore[reportMissingImports]

load_dotenv()
token = os.getenv("DISCORD_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler(filename="bot.log", encoding="utf-8", mode="a"),
        logging.StreamHandler(),
    ],
)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!tf ", intents=intents)


@bot.event
async def on_ready():
    print("✅ Bot is ready!")


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    await bot.process_commands(message)


async def load_cogs():
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py") and not filename.startswith("_"):
            try:
                await bot.load_extension(f"cogs.{filename[:-3]}")
                print(f"✅ Loaded cog: {filename}")
            except Exception as e:
                print(f"❌ Failed to load cog {filename}: {e}")


async def main():
    async with bot:
        await load_cogs()
        await bot.start(token)


asyncio.run(main())
