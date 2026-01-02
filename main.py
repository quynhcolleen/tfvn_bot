import discord
from discord.ext import commands
import os
import asyncio
import logging
from dotenv import load_dotenv 
import db
from dataloader import DataLoader

load_dotenv()
token = os.getenv("DISCORD_TOKEN")
environment = os.getenv("ENVIRONMENT", "production").lower() 

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

bot = commands.Bot(command_prefix=os.getenv("COMMAND_PREFIX", "!tf "), intents=intents, help_command=None)

bot.db = db.db

# inject environment variables to all class
# TODO: inject environment variables to all class for better practice
bot.WORD_CONNECT_GAMES_CHANNELS = os.getenv("WORD_CONNECT_GAMES_CHANNELS", "").split(",")  # Example: "channel_id1,channel_id2"

# for data loader 
# Load banned words globally
loader = DataLoader(base_path="data")

bot.BANNED_WORDS = loader.load_lines("banned_word_list.txt")  # Now accessible as bot.BANNED_WORDS
bot.WORD_CONNECT_WORDS = loader.load_lines("word_connect_valid_list.txt")  # Now accessible as bot.WORD_CONNECT_WORDS

@bot.event
async def on_ready():
    print("✅ Bot is ready!")


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    await bot.process_commands(message)


async def load_cogs():
    if environment == "development":
        # Load only cogs listed in dev_cogs.txt
        dev_cogs_file = "dev_cogs.txt"
        if os.path.exists(dev_cogs_file):
            with open(dev_cogs_file, "r") as f:
                cogs_to_load = [line.strip() for line in f if line.strip()]
        else:
            print(f"❌ {dev_cogs_file} not found. No cogs loaded in dev mode.")
            return
    else:
        # Production: Load all cogs from cogs directory
        cogs_to_load = []
        for root, _, files in os.walk("cogs"):
            for file in files:
                if file.endswith(".py") and not file.startswith("_"):
                    path = os.path.join(root, file)
                    module = path.replace("\\", ".").replace("/", ".").removesuffix(".py")
                    cogs_to_load.append(module)

    for module in cogs_to_load:
        try:
            await bot.load_extension(module)
            print(f"✅ Loaded cog: {module}")
        except Exception as e:
            print(f"❌ Failed to load cog {module}: {e}")



async def main():
    async with bot:
        await load_cogs()
        await bot.start(token)


asyncio.run(main())
