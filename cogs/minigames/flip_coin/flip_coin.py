import discord
from discord.ext import commands
import random

class FlipCoinCommandCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    @commands.command(name="flip_coin")
    async def start_flip_coin(self, ctx: commands.Context, choice: str, bet_value: int):
        # Validate user choice
        choice = choice.lower()
        if choice not in ["head", "tail"]:
            await ctx.send("Invalid choice! Please choose 'head' or 'tail'.")
            return
        
        # Validate bet value
        if bet_value <= 0:
            await ctx.send("Bet value must be a positive integer.")
            return
        
        # deduct bet from user's balance
        user_id = ctx.author.id

        # Check if user exists in the database
        account = self.db["user_accounts"].find_one({"user_id": user_id})

        if not account or account.get("balance", 0) < 5:
            await ctx.send(f"{ctx.author.mention}, bạn không có đủ Trap Coins để chơi! (Cần ít nhất 5 TC)")
            return
        
        self.db["user_accounts"].update_one(
            {"user_id": user_id},
            {"$inc": {"balance": -bet_value}}
        )

        # Simulate coin flip
        # random a number from 0 to 100
        result = random.randint(0, 100)
        if result % 2 == 0:
            result = "head"
        else:
            result = "tail"

        if result == choice:
            winnings = bet_value * 2
            self.db["user_accounts"].update_one(
                {"user_id": user_id},
                {"$inc": {"balance": winnings}}
            )
            await ctx.send(f"The coin landed on **{result}**! You won {winnings} coins!")
        else:
            await ctx.send(f"The coin landed on **{result}**. You lost {bet_value} coins.")

async def setup(bot):
    await bot.add_cog(FlipCoinCommandCog(bot))