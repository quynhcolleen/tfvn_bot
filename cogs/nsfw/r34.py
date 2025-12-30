import asyncio
import logging
import random
import os
from urllib.parse import urlencode

import aiohttp  # pyright: ignore[reportMissingImports]
import discord  # pyright: ignore[reportMissingImports]
from discord.ext import commands  # pyright: ignore[reportMissingImports]
from dotenv import load_dotenv  # pyright: ignore[reportMissingImports]

logger = logging.getLogger(__name__)
load_dotenv()

# ===================== CONFIG =====================

RULE34_API_URL = os.getenv("RULE34_API_URL", "https://api.rule34.xxx/index.php")

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

MAX_PID = 10_000
_cred_index = 0


# ===================== HELPERS =====================


def get_next_credentials():
    global _cred_index
    cred = RULE34_CREDENTIALS[_cred_index]
    _cred_index = (_cred_index + 1) % len(RULE34_CREDENTIALS)
    return cred


def random_recent_page(max_page: int = MAX_PID) -> int:
    r = random.random()

    if r < 0.25:
        return random.randint(0, 20)
    elif r < 0.65:
        return random.randint(21, 800)
    elif r < 0.90:
        return random.randint(801, 1500)
    else:
        return random.randint(1501, max_page)


def pick_post(posts: list[dict]) -> dict:
    weights = []

    for p in posts:
        score = int(p.get("score", 0))
        weights.append(max(score + 5, 1))

    return random.choices(posts, weights=weights, k=1)[0]


# ===================== API =====================


async def fetch_rule34_posts(tags: str, page: int, limit: int = 5) -> list[dict] | None:
    formatted_tags = tags.strip().replace(" ", "+") + "+-ai_generated"
    cred = get_next_credentials()

    params = {
        "page": "dapi",
        "s": "post",
        "q": "index",
        "pid": page,
        "limit": limit,
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
            if not data or not isinstance(data, list):
                return None

            return data


# ===================== COG =====================


class Rule34Cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def r34(self, ctx, *, query: str | None = None):
        if not ctx.channel.is_nsfw():
            await ctx.message.add_reaction("⚠️")
            warn = await ctx.reply("🔞 Dùng lệnh này trong channel NSFW nhé.")
            await asyncio.sleep(5)
            await warn.delete()
            await ctx.message.delete()
            return

        if not query:
            msg = await ctx.reply("⚠️ Bạn cần nhập tag.\nVí dụ: `!tf r34 trap anal_sex`")
            await asyncio.sleep(5)
            await msg.delete()
            return

        search_msg = await ctx.send(f"🔍 Đang tìm: `{query}`")

        try:
            used_pages: set[int] = set()
            posts: list[dict] | None = None

            # ---------- Phase 1: random thông minh ----------
            for _ in range(4):
                page = random_recent_page()
                if page in used_pages:
                    continue

                used_pages.add(page)
                posts = await fetch_rule34_posts(query, page)
                if posts:
                    break

            # ---------- Phase 2: ép page thấp ----------
            if not posts:
                for page in range(0, 6):
                    if page in used_pages:
                        continue

                    used_pages.add(page)
                    posts = await fetch_rule34_posts(query, page)
                    if posts:
                        break

            # ---------- Phase 3: vét sạch pid = 0 ----------
            if not posts:
                posts = await fetch_rule34_posts(query, 0, limit=100)

            if not posts:
                await search_msg.delete()
                await ctx.reply("❌ Không tìm thấy kết quả.")
                return

            post = pick_post(posts)

            file_url = post.get("file_url")
            if not file_url:
                await search_msg.delete()
                await ctx.reply("❌ Post không có file.")
                return

            file_url = file_url.replace("http://", "https://")
            await search_msg.delete()

            if file_url.endswith((".mp4", ".webm")):
                await ctx.reply(f"Kết quả tìm kiếm cho `{query}`")
                await ctx.reply(file_url)
                return

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


# ===================== SETUP =====================


async def setup(bot):
    await bot.add_cog(Rule34Cog(bot))
