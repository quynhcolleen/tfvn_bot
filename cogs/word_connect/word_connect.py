import random
from discord.ext import commands  # pyright: ignore[reportMissingImports]
import discord  # pyright: ignore[reportMissingImports]
import datetime


class WordConnectCommandCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        print(self.bot.global_vars)  # Debug: print global variables

        if (self.bot.global_vars["WORD_CONNECT_GAMES_CHANNELS"] is None or self.bot.global_vars["WORD_CONNECT_GAMES_CHANNELS"] == ""):
            raise ValueError("WORD_CONNECT_GAMES_CHANNELS is not set in global variables.")
        
        if not isinstance(self.bot.global_vars["WORD_CONNECT_GAMES_CHANNELS"], list):
            self.bot.global_vars["WORD_CONNECT_GAMES_CHANNELS"] = [self.bot.global_vars["WORD_CONNECT_GAMES_CHANNELS"]]
        self.word_list: list[str] = self.bot.WORD_CONNECT_WORDS
        self.channel_games: list[str] = self.bot.global_vars["WORD_CONNECT_GAMES_CHANNELS"]
        self.db = bot.db
        self.hint_timeout_datetime = None
        # self.rate_icon = {
        #     "brilliant": self.bot.get_emoji(1458179812177870984) or "🌟",
        #     "great": self.bot.get_emoji(1458179830368567545) or "👍",
        #     "good": self.bot.get_emoji(1458179823582318752) or "👌",
        #     "forced": self.bot.get_emoji(1458179821615190116) or "⚡",
        #     "miss": self.bot.get_emoji(1458179817781592124) or "❓",
        #     "blunder": self.bot.get_emoji(1458179814014845071) or "💥",
        # }
        self.rate_icon = {
            "brilliant": "<:brilliantmove:1458179812177870984>"  or "🌟",
            "great":     "<:greatmove:1458179830368567545>" or "👍",
            "good":      "<:goodmove:1458179823582318752>" or "👌",
            "forced":    "<:forcedmove:1458179821615190116>" or "⚡",
            "miss":      "<:missmove:1458179817781592124>" or "❓",
            "blunder":   "<:blundermove:1458179814014845071>" or "💥",
        }

        # Initialize attributes before loading context
        self.current_word = ""
        self.used_words = []
        self.last_player_id = None
        self.last_valid_message_id = None

        context = self._load_context()
        self.current_word: str = context["current_word"]
        self.used_words: list[str] = context["used_words"]
        self.last_player_id: int | None = context["last_player_id"]
        self.last_valid_message_id: int | None = context["last_valid_message_id"]

    def _load_context(self) -> dict:
        record = self.db["context"].find_one({"context_type": "word_connect"})

        if record:
            return {
                "current_word": record.get("current_word", ""),
                "used_words": record.get("used_words", []),
                "last_player_id": record.get("last_player_id"),
                "last_valid_message_id": record.get("last_valid_message_id"),
            }

        self._clear_context()
        self._start_new_game()

        return {
            "current_word": self.current_word,
            "used_words": self.used_words,
            "last_player_id": None,
            "last_valid_message_id": None,
        }

    def _save_context(self):
        doc = {
            "context_type": "word_connect",
            "current_word": self.current_word,
            "used_words": self.used_words,
            "last_player_id": self.last_player_id,
            "last_valid_message_id": self.last_valid_message_id,
        }

        self.db["context"].update_one(
            {"context_type": "word_connect"},
            {"$set": doc},
            upsert=True,
        )

    def _clear_context(self):
        self.db["context"].delete_many({"context_type": "word_connect"})

    def _random_word(self) -> str:
        return random.choice(self.word_list)

    def _is_dead_end(self, word: str) -> bool:
        last = word.split()[-1]
        return not any(
            w.startswith(last) and w != word and w not in self.used_words
            for w in self.word_list
        )

    def _start_new_game(self):
        while True:
            word = self._random_word()
            if not self._is_dead_end(word):
                break

        # DEBUG ONLY - REMOVE IN PRODUCTION
        # set initial game state for the word 'ốp đồng' for example
        word = "ốp đồng"

        self.current_word = word
        self.used_words = [word]
        self.last_player_id = None
        self.last_valid_message_id = None
        self._save_context()

    # def _count_dead_ends(self, word: str, word_list: list[str], visited: set[str], depth: int = 0, max_depth: int = 3) -> int:
    #     print(f"Counting dead ends for word: {word}, depth: {depth}, visited: {visited}")
    #     if word in visited:
    #         return 0
        
    #     if depth >= max_depth:
    #         return 0  # Stop exploring at max depth to prevent infinite recursion

    #     visited.add(word)
    #     last = word.split()[-1]

    #     candidates = [
    #         w for w in word_list if w.startswith(last) and w != word and w not in visited
    #     ]

    #     if not candidates:
    #         return 1  # Dead end found

    #     dead_end_count = 0
    #     for next_word in candidates:
    #         dead_end_count += self._count_dead_ends(next_word, word_list, visited.copy(), depth + 1, max_depth)

    #     return dead_end_count

    def _count_next_possible_words(self, word: str, word_list: list[str]) -> int:
        last = word.split()[-1]
        candidates = [
            w for w in word_list if w.startswith(last) and w != word
        ]
        return len(candidates)

    def _top_words(self, word: str) -> list[tuple[str, int]]:
        last = word.split()[-1]
        
        candidates = [w for w in self.word_list if w.startswith(last)]
        
        if not candidates:
            return []
        
        # (next_word, count of paths that lead to dead-end)
        results = []
        
        for next_word in candidates:
            dead_count = self._count_next_possible_words(next_word, self.word_list)
            results.append((next_word, dead_count))
            
            # Sort: smallest dead-end count first
            results.sort(key=lambda x: x[1])
            
        return results


    # COMMANDS
    @commands.group(name="noitu", invoke_without_command=True)
    async def noitu(self, ctx):
        embed = discord.Embed(
            title="🎮 NỐI TỪ",
            description="Luật chơi Word Connect",
            color=discord.Color.green(),
        )

        embed.add_field(
            name="📌 Cách chơi",
            value="Nối từ mới bắt đầu bằng **từ cuối** của từ trước",
            inline=False,
        )

        embed.add_field(
            name="🚫 Luật cấm",
            value=(
                "❌ Không được tự nối 2 lượt liên tiếp\n"
                "❌ Không được lặp lại từ đã dùng"
            ),
            inline=False,
        )

        embed.add_field(
            name="⚠️ Lưu ý", value="Vào **ngõ cụt** → game sẽ **reset**", inline=False
        )

        embed.set_footer(text="Chúc các bạn chơi vui vẻ 🎉!")

        await ctx.send(embed=embed)

    @noitu.command(name="status")
    async def wordconnect_current(self, ctx):
        embed = discord.Embed(title="🧠 Trạng thái game Nối Từ", color=0x2ECC71)

        embed.add_field(
            name="🔤 Từ hiện tại", value=f"**{self.current_word}**", inline=False
        )

        embed.add_field(
            name="📚 Các từ đã dùng",
            value=", ".join(self.used_words) if self.used_words else "Chưa có",
            inline=False,
        )

        embed.set_footer(
            text="Hãy nối tiếp bằng từ bắt đầu với **từ cuối** của từ hiện tại!"
        )

        await ctx.send(embed=embed)

    @noitu.command(name="hint")
    async def word_connect_top(self, ctx):
        # timeout 30 seconds to prevent spam
        now = datetime.datetime.now()
        if self.hint_timeout_datetime and (now - self.hint_timeout_datetime).total_seconds() < 30:
            await ctx.send("⏳ Vui lòng chờ trước khi yêu cầu gợi ý tiếp theo.")
            return
        
        self.hint_timeout_datetime = now
        top_suggestions = self._top_words(self.current_word.lower().strip())
        
        if not top_suggestions:
            await ctx.send("❌ Không có từ gợi ý nào khả dụng.")
            return
        
        suggestion_msg = "Gợi ý: \n\n"

        suggestion_msg += "Gợi ý top 5 khó nhất:\n"
        count = 0
        for word, dead_count in top_suggestions:
            count += 1
            if count > 5:
                break
            suggestion_msg += f"- {word} ({dead_count} từ tiếp theo để dẫn đến ngõ cụt)\n"

        suggestion_msg += "\n"

        suggestion_msg += "Gợi ý top 5 dễ nhất:\n"
        count = 0
        for word, dead_count in reversed(top_suggestions):
            count += 1
            if count > 5:
                break
            suggestion_msg += f"- {word} ({dead_count} từ tiếp theo để dẫn đến ngõ cụt)\n"

        await ctx.send(suggestion_msg)

    @noitu.command(name="end")
    async def wordconnect_end(self, ctx):
        self._clear_context()
        self._start_new_game()

        await ctx.send(f"🔄 Game đã reset!\nTừ bắt đầu mới là **{self.current_word}**!")

    @noitu.command(name="analyze")
    async def wordconnect_analyze(self, ctx):
        word = self.current_word.lower().strip()
        if word not in self.word_list:
            await ctx.send("❌ Từ này không có trong từ điển.")
            return
        
        # get the previous word
        prev_word = self.used_words[-2]

        next_of_the_prev = self._top_words(prev_word)

        # find the next possible words
        next_words = self._top_words(word)

        if not next_words:
            await ctx.send("❌ Không có từ nào có thể nối tiếp từ này.")
            return
        
        # if the previous of last word in self.used_words is forced then react with forcedmove
        if len(next_of_the_prev) == 1:
            channel = ctx.channel
            if self.last_valid_message_id:
                try:
                    last_message = await channel.fetch_message(self.last_valid_message_id)
                    await last_message.add_reaction(self.rate_icon["forced"])
                except discord.NotFound:
                    pass
            await ctx.send("🔍 Phân tích: Đây là nước đi bắt buộc.")
            return

        # if there is the next words more than one word but only one word that lead to instant dead end
        # check if any next_words[i][1] == 0:
        for i in next_words:
            if i[1] == 0:
                channel = ctx.channel
                if self.last_valid_message_id:
                    try:
                        last_message = await channel.fetch_message(self.last_valid_message_id)
                        await last_message.add_reaction(self.rate_icon["blunder"])
                    except discord.NotFound:
                        pass
                await ctx.send("🔍 Phân tích: Đây là nước đi ngôn tình (lù)!!")
                return


        # brilliant if the word leads to next word can lead to 2 forced move lead to dead ends
        for next_word, dead_count in next_words:
            next_next_words = self._top_words(next_word)
            forced_count = sum(1 for w in next_next_words if self._count_next_possible_words(w[0], self.word_list) == 1)
            if forced_count >= 2:
                channel = ctx.channel
                if self.last_valid_message_id:
                    try:
                        last_message = await channel.fetch_message(self.last_valid_message_id)
                        await last_message.add_reaction(self.rate_icon["brilliant"])
                    except discord.NotFound:
                        pass
                await ctx.send("🔍 Phân tích: Nước đi xuất sắc! Rất khó để đối phương phản công.")
                return
        
        await ctx.message.add_reaction(self.rate_icon["good"])
        await ctx.send("🔍 Phân tích: Nước đi này bình thường.")



    # MESSAGE LISTENER
    @commands.Cog.listener()
    async def on_message(self, message):
        if str(message.channel.id) not in self.channel_games:
            return

        if message.author.bot:
            return

        # check if word is have 2 words
        word = message.content.lower().strip()
        if len(word.split()) != 2:
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return

        if message.content.startswith(self.bot.command_prefix):
            return

        # ❌ Không được tự nối 2 lượt liên tiếp
        if self.last_player_id == message.author.id:
            await message.add_reaction("❌")
            msg = await message.reply(
                "❌ Bạn vừa nối từ trước đó rồi, hãy để người khác chơi nhé."
            )
            await msg.delete(delay=5)
            return

        # ❌ Không có trong từ điển
        if word not in self.word_list:
            await message.add_reaction("❌")
            msg = await message.reply("❌ Từ này không có trong từ điển.")
            await msg.delete(delay=5)
            return

        # ❌ Đã dùng
        if word in self.used_words:
            await message.add_reaction("❌")
            msg = await message.reply("❌ Từ này đã được sử dụng.")
            await msg.delete(delay=5)
            return

        # ❌ Nối sai
        last = self.current_word.split()[-1]
        if not word.startswith(last):
            await message.add_reaction("❌")
            msg = await message.reply(f"❌ Từ phải bắt đầu bằng **{last}**.")
            await msg.delete(delay=5)
            return

        if self._is_dead_end(word):
            await message.add_reaction("🏆")
            await message.channel.send(
                f"Không còn từ nào bắt đầu bằng **{last}**! 🎉 **{message.author.display_name} là người thắng cuộc!**\n"
            )

            self._clear_context()
            self._start_new_game()

            await message.channel.send(
                f"🔄 Game mới bắt đầu với từ: **{self.current_word}**"
            )
            return

        # ✅ HỢP LỆ
        self.used_words.append(word)
        self.current_word = word
        self.last_player_id = message.author.id
        self.last_valid_message_id = message.id
        self._save_context()

        await message.add_reaction("✅")


async def setup(bot):
    await bot.add_cog(WordConnectCommandCog(bot))
