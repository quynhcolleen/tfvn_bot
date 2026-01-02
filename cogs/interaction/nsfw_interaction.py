import asyncio
import discord  # pyright: ignore[reportMissingImports]
from discord.ext import commands  # pyright: ignore[reportMissingImports]
import random
from collections import deque

from assets.nsfw_gifs import (
    BLOWJOB_GIFS,
    HANDJOB_GIFS,
    RIMJOB_GIFS,
    FROTTING_GIFS,
    FUCKING_GIFS,
    CREAMPIE_GIFS,
)


# Tránh lặp gif đcmmmmmmm
class GifPicker:
    def __init__(self, gifs: list[str], history_size: int = 5):
        self.gifs = gifs
        self.recent = deque(maxlen=history_size)

    def pick(self) -> str:
        candidates = [g for g in self.gifs if g not in self.recent]
        gif = random.choice(candidates or self.gifs)
        self.recent.append(gif)
        return gif


class NSFWInteractionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bj_picker = GifPicker(BLOWJOB_GIFS, history_size=len(BLOWJOB_GIFS))
        self.hj_picker = GifPicker(HANDJOB_GIFS, history_size=len(HANDJOB_GIFS))
        self.rj_picker = GifPicker(RIMJOB_GIFS, history_size=len(RIMJOB_GIFS))
        self.frot_picker = GifPicker(FROTTING_GIFS, history_size=len(FROTTING_GIFS))
        self.fuck_picker = GifPicker(FUCKING_GIFS, history_size=len(FUCKING_GIFS))
        self.cream_picker = GifPicker(CREAMPIE_GIFS, history_size=len(CREAMPIE_GIFS))
        self.db = bot.db

    def record_action(self, action: str, ctx: commands.Context, member: discord.Member):
        document = {
            "message_id": ctx.message.id,
            "initMember": ctx.author.id,
            "targetMember": member.id,
            "action": action,
            "created_at": discord.datetime.utcnow(),
        }
        self.db["interactions"].insert_one(document)

    async def _nsfw_guard(self, ctx: commands.Context) -> bool:
        if ctx.channel.is_nsfw():
            return True

        await ctx.message.add_reaction("⚠️")
        warn_msg = await ctx.reply("🔞 Dùng lệnh này trong channel NSFW nhé.")
        await asyncio.sleep(5)
        await warn_msg.delete()
        await ctx.message.delete()
        return False

    async def _send_embed(
        self,
        ctx: commands.Context,
        *,
        title: str,
        description: str,
        gif_url: str,
    ):
        embed = discord.Embed(
            title=title,
            description=description,
        )
        embed.set_image(url=gif_url)
        await ctx.send(embed=embed)

    # BLOWJOB
    @commands.command(name="bj")
    async def blowjob(self, ctx: commands.Context, member: discord.Member):
        if not await self._nsfw_guard(ctx):
            return

        self.record_action("bj", ctx, member)

        await self._send_embed(
            ctx,
            title="👅 Bú bú",
            description=f"{ctx.author.mention} bú cu {member.mention} 💖",
            gif_url=self.bj_picker.pick(),
        )

    # RIMJOB
    @commands.command(name="rj")
    async def rimjob(self, ctx: commands.Context, member: discord.Member):
        if not await self._nsfw_guard(ctx):
            return

        self.record_action("rj", ctx, member)

        await self._send_embed(
            ctx,
            title="🍑 Liếm cái ik~",
            description=f"{ctx.author.mention} liếm lồn {member.mention} 👅💦",
            gif_url=self.rj_picker.pick(),
        )

    # HANDJOB
    @commands.command(name="hj")
    async def handjob(self, ctx: commands.Context, member: discord.Member):
        if not await self._nsfw_guard(ctx):
            return

        self.record_action("hj", ctx, member)

        await self._send_embed(
            ctx,
            title="🥰 Sục cho nè~",
            description=f"{ctx.author.mention} sục cho {member.mention} 💦",
            gif_url=self.hj_picker.pick(),
        )

    # FROTTING
    @commands.command(name="frot")
    async def frotting(self, ctx: commands.Context, member: discord.Member):
        if not await self._nsfw_guard(ctx):
            return

        self.record_action("frot", ctx, member)

        await self._send_embed(
            ctx,
            title="🤺 Đấu kiếm nhẹ nhàng nha~",
            description=f"{ctx.author.mention} frot với {member.mention} 🌸",
            gif_url=self.frot_picker.pick(),
        )

    # FUCKING
    @commands.command(name="fuck")
    async def fucking(self, ctx: commands.Context, member: discord.Member):
        if not await self._nsfw_guard(ctx):
            return

        self.record_action("fuck", ctx, member)

        await self._send_embed(
            ctx,
            title="Lên giường thôi 👉🏻👌🏻💦",
            description=f"{ctx.author.mention} chịch {member.mention} 💦",
            gif_url=self.fuck_picker.pick(),
        )

    # CREAMPIE
    @commands.command(name="cream")
    async def creampie(self, ctx: commands.Context, member: discord.Member):
        if not await self._nsfw_guard(ctx):
            return

        self.record_action("cream", ctx, member)

        await self._send_embed(
            ctx,
            title="💦 Aaaahhh~! Em chịu không nổi nữa rồi...",
            description=f"{ctx.author.mention} ra bên trong {member.mention} 💦!",
            gif_url=self.cream_picker.pick(),
        )

    @commands.command(name="ranknsfw", aliases=["rankingnsfw"])
    async def ranknsfw(
        self,
        ctx: commands.Context,
        mode_or_action: str | None = None,
        interaction_type: str | None = None,
    ):
        if not await self._nsfw_guard(ctx):
            return

        nsfw_interactions = ["bj", "rj", "hj", "frot", "fuck", "cream"]

        # text cho NGƯỜI CHỦ ĐỘNG
        action_text_given = {
            "bj": "bú cu",
            "rj": "liếm lồn",
            "hj": "sục cho member khác",
            "frot": "đấu kiếm",
            "fuck": "địt member khác",
            "cream": "xuất trong",
        }

        # text cho NGƯỜI BỊ
        action_text_received = {
            "bj": "được bú cu",
            "rj": "được liếm lồn",
            "hj": "được sục cặc",
            "frot": "được đấu kiếm",
            "fuck": "bị địt",
            "cream": "bị xuất trong",
        }

        # mặc định: người CHỦ ĐỘNG
        mode = "given"

        if mode_or_action == "r":
            mode = "received"
            action = interaction_type
        else:
            action = mode_or_action

        if action not in (nsfw_interactions + [None]):
            await ctx.send(
                "Loại tương tác không hợp lệ.\nVui lòng sử dụng: `bj`, `rj`, `hj`, `frot`, `fuck`, `cream`."
            )
            return

        user_field = "$initMember" if mode == "given" else "$targetMember"

        pipeline = [
            {"$group": {"_id": user_field, "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10},
        ]

        if action:
            pipeline.insert(0, {"$match": {"action": action}})
        else:
            pipeline.insert(0, {"$match": {"action": {"$in": nsfw_interactions}}})

        top_users = list(self.db["interactions"].aggregate(pipeline))

        lines = []
        for rank, record in enumerate(top_users, start=1):
            user_id = record["_id"]
            count = record["count"]

            user = self.bot.get_user(user_id)
            name = user.mention if user else f"ID {user_id}"

            if mode == "given":
                if action:
                    text = f"{count} lần {action_text_given[action]}."
                else:
                    text = f"{count} lần chơi người khác."
            else:
                if action:
                    text = f"{count} lần {action_text_received[action]}."
                else:
                    text = f"{count} lần bị chơi."

            lines.append(f"**{rank}**. {name} – {text}")

        description = "\n".join(lines) if lines else "Chưa có dữ liệu."

        if mode == "given":
            title = "🏆 Top 10 con quỷ sex của server 😈"
            if action:
                title = f"🏆 Top 10 người {action_text_given[action]} nhiều nhất 💦"
        else:
            title = "🏆 Top 10 người làm sex slave nhiều nhất 👉🏻👌🏻💦"
            if action:
                title = f"🏆 Top 10 người {action_text_received[action]} nhiều nhất 💦"

        embed = discord.Embed(title=title, description=description)
        embed.set_author(name="BXH độ răm", icon_url=ctx.author.display_avatar.url)
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_image(
            url="https://api-cdn.rule34.xxx//images/1500/85f729598f01b951f528e47b49078414.gif?1585014"
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(NSFWInteractionCog(bot))
