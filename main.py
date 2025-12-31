import discord
from discord.ext import commands
import os
import asyncio
import logging
from dotenv import load_dotenv 
import db
from dataloader import DataLoader
import boto3

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

bot = commands.Bot(command_prefix=os.getenv("COMMAND_PREFIX", "!tf "), intents=intents, help_command=None)

bot.db = db.db

bot.s3 = boto3.client(
    service_name="s3",
    # Provide your Cloudflare account ID
    endpoint_url=os.getenv("S3_CLIENT_ENDPOINT", ""),
    # Retrieve your S3 API credentials for your R2 bucket via API tokens (see: https://developers.cloudflare.com/r2/api/tokens)
    aws_access_key_id=os.getenv("ACCESS_KEY_ID", ""),
    aws_secret_access_key=os.getenv("SECRET_KEY_ACCESS", ""),
    region_name="auto", # Required by SDK but not used by R2
)

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
    for root, _, files in os.walk("cogs"):
        for file in files:
            if file.endswith(".py") and not file.startswith("_"):
                path = os.path.join(root, file)

                module = path.replace("\\", ".").replace("/", ".").removesuffix(".py")

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
