import asyncio
import logging
from discord.ext import commands  # pyright: ignore[reportMissingImports]
import discord  # pyright: ignore[reportMissingImports]
import aiohttp  # pyright: ignore[reportMissingImports]
import os
from urllib.parse import urlencode
from dotenv import load_dotenv  # pyright: ignore[reportMissingImports]
import random

logger = logging.getLogger(__name__)
load_dotenv()

RULE34_CREDENTIALS = [
    {
        "api_key": os.getenv("RULE34_API_KEY"),
        "user_id": os.getenv("RULE34_USER_ID"),
    },
    {
        "api_key": os.getenv("SECOND_RULE34_API_KEY"),
        "user_id": os.getenv("RULE34_SECOND_USER_ID"),
    },
]

_cred_index = 0

RULE34_API_URL = os.getenv("RULE34_API_URL", "https://api.rule34.xxx/index.php")


def get_next_credentials():
    global _cred_index
    cred = RULE34_CREDENTIALS[_cred_index]
    _cred_index = (_cred_index + 1) % len(RULE34_CREDENTIALS)
    return cred


async def fetch_rule34_one(tags: str, page: int):
    formatted_tags = tags.strip().replace(" ", "+") + "+-ai_generated"

    cred = get_next_credentials()

    params = {
        "page": "dapi",
        "s": "post",
        "q": "index",
        "pid": page,
        "limit": 1,
        "json": 1,
        "tags": formatted_tags,
        "api_key": cred["api_key"],
        "user_id": cred["user_id"],
    }

    url = f"{RULE34_API_URL}?{urlencode(params).replace('%2B', '+')}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=10) as res:
            if res.status != 200:
                raise Exception(f"API lỗi: {res.status}")

            data = await res.json()

            if not data:
                return None

            if isinstance(data, list):
                return data[0]

            if isinstance(data, dict) and "post" in data:
                return data["post"]

            return None


class Rule34Cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def r34(self, ctx, *, query: str | None = None):
        if not ctx.channel.is_nsfw():
            await ctx.message.add_reaction("⚠️")
            warn_msg = await ctx.reply("🔞 Dùng lệnh này trong channel NSFW nhé.")
            await asyncio.sleep(5)
            await warn_msg.delete()
            await ctx.message.delete()
            return
        
        if not query:
            msg = await ctx.reply(
                "⚠️ Bạn cần nhập tag để tìm.\n"
                "Ví dụ: `!tf r34 trap anal_sex`"
            )
            await asyncio.sleep(5)
            await msg.delete()
            return

        search_msg = await ctx.send(f"🔍 Đang tìm: `{query}`")

        try:
            post = None

            for _ in range(5):
                page = random.randint(0, 1000)
                post = await fetch_rule34_one(query, page)
                if post:
                    break

            if not post:
                await ctx.reply("❌ Không tìm thấy kết quả.")
                return

            file_url = post.get("file_url")
            if not file_url:
                await ctx.reply("❌ Post không có file.")
                return

            file_url = file_url.replace("http://", "https://")

            if file_url.endswith((".mp4", ".webm")):
                await search_msg.delete()
                await ctx.reply(f"Kết quả tìm kiếm cho: `{query}`")
                await ctx.reply(file_url)
                return

            await search_msg.delete()
            embed = discord.Embed(title="Rule34 Result", color=0x30FC78)
            embed.set_image(url=file_url)
            embed.set_footer(text=f"Tags: {query}")

            await ctx.reply(embed=embed)

        except aiohttp.ClientError:
            await ctx.reply("⚠️ Lỗi kết nối tới API.")
            logger.exception("Rule34 API connection error")

        except asyncio.TimeoutError:
            await ctx.reply("⏱️ API phản hồi quá lâu.")
            logger.exception("Rule34 API timeout")

        except Exception:
            await ctx.reply("⚠️ Có lỗi xảy ra.")
            logger.exception("Unexpected error in r34 command")


async def setup(bot):
    await bot.add_cog(Rule34Cog(bot))
