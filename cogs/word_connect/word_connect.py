import discord
from discord.ext import commands
import aiohttp

class WordConnectCommandCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.word_list = bot.WORD_CONNECT_WORDS
        self.channel_games = bot.WORD_CONNECT_GAMES_CHANNELS
        self.db = bot.db  # access to database'
        
        context = self.load_context()
        self.current_word = context["current_word"]
        self.used_words = context["used_words"]

    def load_context(self) -> dict:
        # Placeholder for loading context if needed
        db_record = self.db["context"].find_one({
            "context_type": "word_connect",
        })
        if db_record:
            return {
                "current_word": db_record.get("current_word", ""),
                "used_words": db_record.get("used_words", []),
            }
        else:
            # init new game context - find a word that doesn't lead to dead end
            self.start_a_new_game()
            new_random_word = self.current_word

            return {
                "current_word": new_random_word,
                "used_words": [new_random_word],
            }

    def save_context(self, current_word: str, used_words: list[str]) -> dict:
        doc = {
            "context_type": "word_connect",
            "current_word": current_word,
            "used_words": used_words,
        }

        db_record = self.db["context"].find_one(doc)
        if db_record:
            self.db["context"].update_one(
                {"_id": db_record["_id"]},
                {"$set": doc}
            )
        else:
            self.db["context"].insert_one(doc)
    
    def clear_context(self):
        self.db["context"].delete_many({
            "context_type": "word_connect",
        })

    def find_random_word(self) -> str:
        import random
        return random.choice(self.word_list)
    
    def start_a_new_game(self):
        while True:
            new_random_word = self.find_random_word()
            if not self.check_if_dead_end(new_random_word, self.word_list):
                break

        # init new game context
        self.save_context(new_random_word, [new_random_word])

        self.current_word = new_random_word
        self.used_words = [new_random_word]
    

    
    def check_if_dead_end(self, word: str, word_lists: list[str]) -> bool:
        last = word.split()[-1]
        return not any(w.startswith(last) for w in word_lists)
    
    def top_words(self, word: str, word_lists: list[str]) -> list[tuple[str, int]]:
        last = word.split()[-1]
        
        candidates = [w for w in word_lists if w.startswith(last)]
        print(f"Candidates for '{word}': {candidates}")
        
        if not candidates:
            return []
        
        # (next_word, count of paths that lead to dead-end)
        results = []
        
        for next_word in candidates:
            dead_count = self._count_dead_ends(next_word, word_lists, set())
            results.append((next_word, dead_count))
            
            # Sort: smallest dead-end count first
            results.sort(key=lambda x: x[1])
            
            return results[:5]
        
    def _count_dead_ends(self, current: str, word_lists: list[str], used: set) -> int:
        """DFS count how many leaves are dead-ends"""
        used = used | {current}
        last = current.split()[-1]
        
        next_words = [w for w in word_lists if w not in used and w.startswith(last)]
        
        if not next_words:
            return 1  # this is a dead-end
        
        total_dead = 0
        for nxt in next_words:
            total_dead += self._count_dead_ends(nxt, word_lists, used)
        
        return total_dead

    @commands.command(name="wordconnect_help", help="Get help for Word Connect game")
    async def word_connect_help(self, ctx):
        await ctx.send("This is the help message for the Word Connect game. Use this command to get assistance.")

        await ctx.send("""
                       To play the game, simply type words in the designated channels. Valid words will be acknowledged!
                       """)
        
    @commands.command(name="wordconnect_stats", help="Get your Word Connect game statistics")
    async def word_connect_stats(self, ctx):
        # Placeholder for fetching user stats
        user_id = ctx.author.id
        # Here you would typically fetch stats from a database
        await ctx.send(f"Statistics for <@{user_id}>:\n- May be implement later")

    @commands.command(name="wordconnect_hint", help="Get top suggested words to avoid dead-ends")
    async def word_connect_top(self, ctx):
        top_suggestions = self.top_words(self.current_word.lower().strip(), self.word_list)
        
        if not top_suggestions:
            await ctx.send("No suggestions available. You might be at a dead-end!")
            return
        
        suggestion_msg = "Top suggested words to avoid dead-ends:\n"
        for word, dead_count in top_suggestions:
            suggestion_msg += f"- {word} (leads to {dead_count} dead-ends)\n"
        
        await ctx.send(suggestion_msg)

    @commands.command(name="wordconnect_current_game", help="Get current Word Connect game status")
    async def word_connect_current_game(self, ctx):
        # Placeholder for fetching user stats
        await ctx.send(f"Current Word Connect game status:\n- Current word: '{self.current_word}'\n- Used words: {', '.join(self.used_words)}")

    @commands.command(name="wordconnect_end", help="End the current Word Connect game")
    async def word_connect_end(self, ctx):
        self.clear_context()
        await ctx.send("The current Word Connect game has been ended. A new game can be started anytime!")

        self.start_a_new_game()
        await ctx.send(f"A new game has started! The starting word is '{self.current_word}'. Good luck!")
    
    # monitor messages for word connect game from channel
    @commands.Cog.listener()
    async def on_message(self, message, *args, **kwargs):
        if str(message.channel.id) not in self.channel_games:
            return
        
        if message.author.bot:
            return

        # Ignore messages that are bot commands
        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return

        # Process the message for word connect game logic
        content = message.content.lower().strip()
        if content not in self.word_list:
            await message.channel.send(f"❌ '{content}' is not a valid word.")
            return
        
        if content in self.used_words:
            await message.channel.send(f"❌ '{content}' has already been used.")
            return
        
        last_char = self.current_word.split()[-1]
        if not content.startswith(last_char):
            await message.channel.send(f"❌ '{content}' does not start with the last character of the current word '{self.current_word}'.")
            return

        if self.check_if_dead_end(message.content.lower().strip(), self.word_list):
            self.clear_context()
            await message.channel.send(f"⚠️ '{message.content.lower().strip()}' leads to a dead end! No further words can be formed.")

            self.start_a_new_game()
            await message.channel.send(f"🔄 A new game has started! The starting word is '{self.current_word}'.")

            return

        # Valid word, update context
        self.used_words.append(content)
        self.current_word = content
        self.save_context(self.current_word, self.used_words)

        success_msg = f"✅ '{content}' is accepted! Next word should start with '{content.split()[-1]}'."
        await message.channel.send(success_msg)

        
        
async def setup(bot):
    await bot.add_cog(WordConnectCommandCog(bot))