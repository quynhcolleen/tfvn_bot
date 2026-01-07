import discord
from discord.ext import commands
import random
import asyncio

class SlotMachineCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.emojis = ["🍒", "🔔", "🍋", "🍊", "7️⃣", "💎", "BAR"]


    @commands.command(name="slot", description="Spin the slot machine")
    async def slot(self, ctx):
        # Rolling animation
        msg = await ctx.send("🎰 Spinning... 🎰")
        await asyncio.sleep(1)

        a, b, c = [random.choice(self.emojis) for _ in range(3)]
        
        for _ in range(3):
            intermediate = [random.choice(self.emojis) for _ in range(3)]
            await msg.edit(content=f"**{intermediate[0]} | {intermediate[1]} | {intermediate[2]}**\n🎰 Spinning... 🎰")
            await asyncio.sleep(0.5)
        result = f"{a} | {b} | {c}"
        await msg.edit(content=f"**{result}**")

        await asyncio.sleep(5)
        if a == b == c:
            await msg.edit(content=f"**{result}**\n**NỔ HŨ!** 🎉")
        elif a == b or b == c or a == c:
            await msg.edit(content=f"**{result}**\n**Tuyệt! Thắng rùi!** ✓")
        else:
            await msg.edit(content=f"**{result}**\n**Thua!!!** 💀")

async def setup(bot: commands.Bot):
    await bot.add_cog(SlotMachineCog(bot))