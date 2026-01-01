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
            results.append("⚫ Triple wins!")
        else:
            if 4 <= total <= 10:
                results.append("🔵 Small wins!")
            elif 11 <= total <= 17:
                results.append("🔴 Big wins!")

        # Fetch all reactions and determine winners
        winners = []
        for react in init_msg.reactions:
            if react.emoji in betting_options:
                users = [u async for u in react.users() if not u.bot]
                
                # Check if this reaction emoji corresponds to a winning bet
                if (react.emoji == "⚫" and is_triple) or \
                   (react.emoji == "🔵" and 4 <= total <= 10 and not is_triple) or \
                   (react.emoji == "🔴" and 11 <= total <= 17 and not is_triple):
                    winners.extend(users)

        # Log bet pools
        bet_pools = {}
        for react in init_msg.reactions:
            if react.emoji in betting_options:
                users = [u async for u in react.users() if not u.bot]
                bet_pools[react.emoji] = [u.name for u in users]

        # Print bet pools to console
        print("=== Bet Pools ===")
        for emoji, desc in betting_options.items():
            players = bet_pools.get(emoji, [])
            print(f"{emoji} {desc}: {', '.join(players) if players else 'No bets'}")
        print("=================")

        # Create final result message
        result_message = f"The dice have been rolled! Results: {' | '.join(str(d) for d in dice)}\n"
        result_message += f"Total: {total}\n"
        result_message += "\n".join(results)

        if winners:
            winner_mentions = ", ".join(u.mention for u in winners)
            result_message += f"\n\n🎉 Congratulations to the winners: {winner_mentions}!"
        else:
            result_message += f"\n\n❌ No winners this round!"

        await init_msg.edit(content=result_message)

    # let the user react only one reaction, if they react another, remove the previous one
    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        if user.bot:
            return
        
        if reaction.message.author != self.bot.user:
            return
        
        if reaction.message.content.startswith("Waiting for players to place their bets"):
            # check if user has already reacted with another option
            for react in reaction.message.reactions:
                if react.emoji != reaction.emoji:
                    users = [u async for u in react.users()]
                    if user in users:
                        await react.remove(user)
                        break
        

async def setup(bot: commands.Bot):
    await bot.add_cog(SicBoCommandCog(bot))

