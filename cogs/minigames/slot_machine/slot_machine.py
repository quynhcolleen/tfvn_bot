import discord
from discord.ext import commands
import random
import asyncio

class SlotMachineCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.emojis = ["🍒", "🔔", "🍋", "🍊", "7️⃣", "💎", "BAR"]


    @commands.command(name="slot", description="Spin the slot machine")
    async def slot(self, ctx):
        # deduct cost to play
        user_id = ctx.author.id
        account = self.db["user_accounts"].find_one({"user_id": user_id})
        if not account or account.get("balance", 0) < 5:
            await ctx.send(f"{ctx.author.mention}, bạn không có đủ Trap Coins để chơi! (Cần ít nhất 5 TC)")
            return
        
        self.db["user_accounts"].update_one(
            {"user_id": user_id},
            {"$inc": {"balance": -5}}
        )

        self.db["transaction_logs"].insert_one({
            "user_id": user_id,
            "type": "slot_machine_play",
            "transaction_type": "debit",
            "amount": 5,
            "timestamp": discord.utils.utcnow(),
        })

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

        return_amount = 0

        if a == b == c:
            return_amount = 100
            await msg.edit(content=f"**{result}**\n**NỔ HŨ!** 🎉")
        elif a == b or b == c or a == c:
            return_amount = 10
            await msg.edit(content=f"**{result}**\n**Tuyệt! Thắng rùi!** ✓")
        else:
            await msg.edit(content=f"**{result}**\n**Thua!!!** 💀")

        if return_amount > 0:
            self.db["user_accounts"].update_one(
                {"user_id": user_id},
                {"$inc": {"balance": return_amount}}
            )

            self.db["transaction_logs"].insert_one({
                "user_id": user_id,
                "type": "slot_machine_win",
                "transaction_type": "credit",
                "amount": return_amount,
                "timestamp": discord.utils.utcnow(),
            })

            await ctx.send(f"{ctx.author.mention}, bạn nhận được {return_amount} Trap Coins!")

async def setup(bot: commands.Bot):
    await bot.add_cog(SlotMachineCog(bot))