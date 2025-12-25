import asyncio
from asyncio import log
from discord.ext import commands
import discord
import aiohttp
import os
from urllib.parse import urlencode
from dotenv import load_dotenv
import random

load_dotenv()

RULE34_API_URL = os.getenv("RULE34_API_URL", "https://api.rule34.xxx/index.php")


async def fetch_rule34_one(tags: str, page: int):
    formatted_tags = tags.strip().replace(" ", "+") + "+-ai_generated"

    params = {
        "page": "dapi",
        "s": "post",
        "q": "index",
        "pid": page,
        "limit": 1,
        "json": 1,
        "tags": formatted_tags,
        "api_key": os.getenv("RULE34_API_KEY"),
        "user_id": os.getenv("RULE34_USER_ID"),
    }

    url = f"{RULE34_API_URL}?{urlencode(params).replace('%2B', '+')}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=10) as res:
            if res.status != 200:
                raise Exception(f"API lỗi: {res.status}")

            data = await res.json()

            # không có kết quả
            if not data:
                return None

            # Rule34 trả list khi limit=1
            if isinstance(data, list):
                return data[0]

            # fallback (hiếm)
            if isinstance(data, dict) and "post" in data:
                return data["post"]

            return None


class BooruCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def r34(self, ctx, *, query: str):
        if not ctx.channel.is_nsfw():
            await ctx.send("🔞 Lệnh này chỉ dùng trong channel NSFW.")
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
                await ctx.send("❌ Không tìm thấy kết quả.")
                return

            file_url = post.get("file_url")
            if not file_url:
                await ctx.send("❌ Post không có file.")
                return

            file_url = file_url.replace("http://", "https://")

            if file_url.endswith((".mp4", ".webm")):
                await search_msg.delete()
                await ctx.send(f"Kết quả tìm kiếm cho: `{query}`")
                await ctx.send(file_url)
                return
            
            await search_msg.delete()
            embed = discord.Embed(title="Rule34 Result", color=discord.Color.purple())
            embed.set_image(url=file_url)
            embed.set_footer(text=f"Tags: {query}")

            await ctx.send(embed=embed)

        except aiohttp.ClientError:
            await ctx.send("⚠️ Lỗi kết nối tới API.")
            log.exception("Rule34 API connection error")

        except asyncio.TimeoutError:
            await ctx.send("⏱️ API phản hồi quá lâu.")
            log.exception("Rule34 API timeout")

        except Exception:
            await ctx.send("⚠️ Có lỗi xảy ra.")
            log.exception("Unexpected error in r34 command")


async def setup(bot):
    await bot.add_cog(BooruCog(bot))
