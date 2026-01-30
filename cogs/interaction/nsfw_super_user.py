import random
import discord  # pyright: ignore[reportMissingImports]
from discord.ext import commands  # pyright: ignore[reportMissingImports]
import asyncio
from datetime import datetime, timedelta

class NSFWSuperUser(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.KING_ROLE_ID = None
        self.QUEEN_ROLE_ID = None

        if (
            self.bot.global_vars["KING_ROLE_ID"] is None or 
            self.bot.global_vars["KING_ROLE_ID"] == "" or
            self.bot.global_vars["QUEEN_ROLE_ID"] is None or
            self.bot.global_vars["QUEEN_ROLE_ID"] == ""
        ):
            raise ValueError("KING_ROLE_ID or QUEEN_ROLE_ID is not set in global variables.")
        
        king_role_id = self.bot.global_vars["KING_ROLE_ID"]
        queen_role_id = self.bot.global_vars["QUEEN_ROLE_ID"]
        try:
            self.KING_ROLE_ID = int(king_role_id)  # Always convert to int
            self.QUEEN_ROLE_ID = int(queen_role_id)  # Always convert to int
        except ValueError:
            raise ValueError("KING_ROLE_ID and QUEEN_ROLE_ID must be valid integers representing role IDs.")

    async def _nsfw_guard(self, ctx: commands.Context) -> bool:
        if ctx.channel.is_nsfw():
            return True

        await ctx.message.add_reaction("⚠️")
        warn_msg = await ctx.reply("🔞 Dùng lệnh này trong channel NSFW nhé.")
        await asyncio.sleep(5)
        await warn_msg.delete()
        await ctx.message.delete()
        return False

    def is_king(self, member_roles: list[int]) -> bool:
        if self.KING_ROLE_ID in member_roles:
            return True
        
        return False

    def is_queen(self, member_roles: list[int]) -> bool:
        if self.QUEEN_ROLE_ID in member_roles:
            return True
        
        return False

    @commands.command(name="locknsfw")
    async def lock_nsfw(self, ctx, member: discord.Member):
        if not await self._nsfw_guard(ctx):
            return
        
        """Locks NSFW commands to only King and Queen roles."""
        member_roles = [role.id for role in ctx.author.roles]

        # Check if user has Queen role
        if not (self.is_queen(member_roles)):
            await ctx.send("Cưng hông được xài lệnh này nha, hư quá à.")
            return

        # Proceed to lock NSFW commands (implementation depends on your bot's architecture)
        # check if user has passed cool down period
        cooled_down = self.db["nsfw_settings"].find_one({
            "lock_by": ctx.author.id,
            "cooldown_at": {"$gte": discord.utils.utcnow()}
        })

        print(cooled_down)

        if cooled_down:
            await ctx.send(f"{ctx.author.mention}, cưng còn đang trong thời gian cooldown nha.")
            return

        # check if member is already locked by someone else or themselves
        existing_lock = self.db["nsfw_settings"].find_one({
            "user_locked": member.id,
            "lock_until": {"$gte": discord.utils.utcnow()}
        })
        if existing_lock:
            await ctx.send(f"{member.mention} đã bị khoá lệnh NSFW rồi mà.")
            return

        # Your locking logic here
        self.db["nsfw_settings"].insert_one({
            "lock_by": ctx.author.id,
            "user_locked": member.id,
            "timestamp": discord.utils.utcnow(),
            "lock_until": discord.utils.utcnow() + timedelta(hours=24),
            "cooldown_at": discord.utils.utcnow() + timedelta(days=3),
        })

        await ctx.send(f"{ctx.author.mention}, đã khoá lệnh NSFW cho {member.mention} trong 24 giờ rồi nha.")


    @commands.command(name="unlocknsfw")
    async def unlock_nsfw(self, ctx):
        if not await self._nsfw_guard(ctx):
            return
        
        """Unlocks NSFW commands to all users."""
        member_roles = [role.id for role in ctx.author.roles]

        if not (self.is_queen(member_roles)):
            await ctx.send("Cưng không gỡ được đâu haha.")
            return

        # Proceed to unlock NSFW commands (implementation depends on your bot's architecture)
        # check if user lock someone nsfw
        existing_lock = self.db["nsfw_settings"].find_one({
            "lock_by": ctx.author.id,
            "lock_until": {"$gte": discord.utils.utcnow()}
        })
        if not existing_lock:
            await ctx.send(f"{ctx.author.mention}, cưng hông có khoá ai mà mở đâu.")
            return
        
        # Your unlocking logic here
        self.db["nsfw_settings"].update_one(
            {
                "lock_by": ctx.author.id,
                "lock_until": {
                    "$gte": discord.utils.utcnow()
                }
            },
            {
                "$set": {
                    "lock_until": discord.utils.utcnow()
                }
            }
        )
        await ctx.send(f"{ctx.author.mention}, đã mở khoá lệnh NSFW cho người dùng rồi nha.")


async def setup(bot: commands.Bot):
    await bot.add_cog(NSFWSuperUser(bot))