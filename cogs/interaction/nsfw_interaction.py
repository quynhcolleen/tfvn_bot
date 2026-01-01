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
            "created_at": discord.datetime.utcnow()
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
    async def rank(self, ctx: commands.Context, interaction_type: str | None = None):
        if not await self._nsfw_guard(ctx):
            return
        
        nsfw_interactions = ["bj", "rj", "hj", "frot", "fuck", "cream"]
        if interaction_type not in (nsfw_interactions + [None]):
            await ctx.send("Loại tương tác không hợp lệ. Vui lòng sử dụng một trong: bj, rj, hj, frot, fuck, cream.")
            return
        
        pipeline = [
            {
                "$group": {
                    "_id": "$initMember",
                    "count": {"$sum": 1}
                }
            },
            {
                "$sort": {"count": -1}
            },
            {
                "$limit": 10
            }
        ]

        if interaction_type:
            pipeline.insert(0, {
                "$match": {"action": interaction_type}
            })
        else:
            pipeline.insert(0, {
                "$match": {"action": {"$in": nsfw_interactions}}
            })

        top_users = list(self.db["interactions"].aggregate(pipeline))

        description_lines = []
        for rank, user_record in enumerate(top_users, start=1):
            user_id = user_record["_id"]
            count = user_record["count"]
            user = self.bot.get_user(user_id)
            user_name = user.name if user else f"ID: {user_id}"
            description_lines.append(f"**{rank}. {user_name}** - {count} tương tác")

        description = "\n".join(description_lines) if description_lines else "Chưa có tương tác nào được ghi nhận."

        embed = discord.Embed(
            title="🏆 Top 10 Con quỷ sex của server",
            description=description
        )
        await ctx.send(embed=embed)
        
async def setup(bot: commands.Bot):
    await bot.add_cog(NSFWInteractionCog(bot))
