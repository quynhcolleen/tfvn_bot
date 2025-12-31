import discord
from discord.ext import commands
import random
import asyncio

class SicBoCommandCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.active_games_id = "sicbo_active_games"
        self.is_games_initialized = False

    @commands.command(name="sicbo_start")
    async def start_sicbo(self, ctx: commands.Context):
        if not self.is_games_initialized:
            if self.db[self.active_games_id].count_documents({}) == 0:
                self.db[self.active_games_id].insert_one({"active_games": {}})
            self.is_games_initialized = True
        init_msg = await ctx.send("Sic Bo game has been initialized!")

        betting_options = {
            "🔴": "Big (11-17)",
            "🔵": "Small (4-10)",
            "⚫": "Triple (all three dice the same)"
        }

        # update message to wait for player bets
        await init_msg.edit(content=
                            f"Waiting for players to place their bets"
                            f"\nReact with:\n" +
                            "\n".join(f"{emoji} for {desc}" for emoji, desc in betting_options.items()))

        # add reaction emojis for betting options
        for emoji in betting_options.keys():
            await init_msg.add_reaction(emoji)

        await init_msg.edit(content=
                            f"Waiting for players to place their bets... "
                            f"\nReact with: \n" +
                            f"\n".join(f"{emoji} for {desc}" for emoji, desc in betting_options.items()) +
                            f"\nYou have 30 seconds!")
        
        # update message 5 seconds each countdown
        for i in range(30, 0, -5):
            await asyncio.sleep(5)
            await init_msg.edit(content=
                                f"Waiting for players to place their bets..."
                                f"\nReact with: \n" +
                                "\n".join(f"{emoji} for {desc}" for emoji, desc in betting_options.items()) +
                                f"\nYou have {i-5} seconds!")
            await asyncio.sleep(5)

        
        # after 30 seconds, roll the dice
        dice = [random.randint(1, 6) for _ in range(3)]

        # using spoiler tags to hide the dice results
        dice_results = " | ".join(f"||{d}||" for d in dice)
        await init_msg.edit(content=
                            f"The dice have been rolled! Results: "
                            f"{dice_results}"
                            f"\nResult will be revealed within 15 seconds..."
                            )
        
        for i in range(15, 0, -5):
            await asyncio.sleep(5)
            await init_msg.edit(content=
                                f"The dice have been rolled! Results: "
                                f"{dice_results}"
                                f"\nResult will be revealed within {i-5} seconds..."
                                )
            await asyncio.sleep(5)
        

        total = sum(dice)
        is_triple = dice[0] == dice[1] == dice[2]

        # determine winners and losers
        results = []

        if is_triple:
            results.append("It's a Triple! All bets on Triple win!")

        elif total >= 11:
            results.append("It's a Big! All bets on Big win!")

        else:
            results.append("It's a Small! All bets on Small win!")

        await init_msg.edit(content="\n".join(results))

async def setup(bot: commands.Bot):
    await bot.add_cog(SicBoCommandCog(bot))

