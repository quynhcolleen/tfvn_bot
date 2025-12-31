import random
from discord.ext import commands  # pyright: ignore[reportMissingImports]
import discord  # pyright: ignore[reportMissingImports]
import datetime

class WordConnectCommandCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.word_list: list[str] = bot.WORD_CONNECT_WORDS
        self.channel_games: list[str] = bot.WORD_CONNECT_GAMES_CHANNELS
        self.db = bot.db
        self.hint_timeout_datetime = None

        context = self._load_context()
        self.current_word: str = context["current_word"]
        self.used_words: list[str] = context["used_words"]
        self.last_player_id: int | None = context["last_player_id"]

    def _load_context(self) -> dict:
        record = self.db["context"].find_one({"context_type": "word_connect"})

        if record:
            return {
                "current_word": record.get("current_word", ""),
                "used_words": record.get("used_words", []),
                "last_player_id": record.get("last_player_id"),
            }

        self._start_new_game()
        return {
            "current_word": self.current_word,
            "used_words": self.used_words,
            "last_player_id": None,
        }

    def _save_context(self):
        doc = {
            "context_type": "word_connect",
            "current_word": self.current_word,
            "used_words": self.used_words,
            "last_player_id": self.last_player_id,
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

        self.current_word = word
        self.used_words = [word]
        self.last_player_id = None
        self._save_context()

    def _count_dead_ends(self, word: str, word_list: list[str], visited: set[str]) -> int:
        if word in visited:
            return 0

        visited.add(word)
        last = word.split()[-1]

        candidates = [
            w for w in word_list if w.startswith(last) and w != word and w not in visited
        ]

        if not candidates:
            return 1  # Dead end found

        dead_end_count = 0
        for next_word in candidates:
            dead_end_count += self._count_dead_ends(next_word, word_list, visited.copy())

        return dead_end_count
    

    def _top_words(self, word: str) -> list[tuple[str, int]]:
        last = word.split()[-1]
        
        candidates = [w for w in self.word_list if w.startswith(last)]
        print(f"Candidates for '{word}': {candidates}")
        
        if not candidates:
            return []
        
        # (next_word, count of paths that lead to dead-end)
        results = []
        
        for next_word in candidates:
            dead_count = self._count_dead_ends(next_word, self.word_list, set())
            results.append((next_word, dead_count))
            
            # Sort: smallest dead-end count first
            results.sort(key=lambda x: x[1])
            
            return results[:5]


    # COMMANDS
    @commands.command(name="noitu_help")
    async def wordconnect_help(self, ctx):
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

    @commands.command(name="noitu_current")
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

    @commands.command(name="noitu_hint")
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
        
        suggestion_msg = "Các từ gợi ý:\n"
        for word, dead_count in top_suggestions:
            suggestion_msg += f"- {word} ({dead_count} từ tiếp theo để dẫn đến ngõ cụt)\n"
        
        await ctx.send(suggestion_msg)

    @commands.command(name="noitu_end")
    async def wordconnect_end(self, ctx):
        self._clear_context()
        self._start_new_game()

        await ctx.send(f"🔄 Game đã reset!\nTừ bắt đầu mới là **{self.current_word}**!")

    # MESSAGE LISTENER
    @commands.Cog.listener()
    async def on_message(self, message):
        if str(message.channel.id) not in self.channel_games:
            return

        if message.author.bot:
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return

        print(self.bot.command_prefix)
        if message.content.startswith(tuple(self.bot.command_prefix)):
            return
        

        word = message.content.lower().strip()

        # ❌ Không được tự nối 2 lượt liên tiếp
        if self.last_player_id == message.author.id:
            await message.add_reaction("❌")
            msg = await message.channel.reply(
                "❌ Bạn vừa nối từ trước đó rồi, hãy để người khác chơi nhé."
            )
            await msg.delete(delay=5)
            return

        # ❌ Không có trong từ điển
        if word not in self.word_list:
            await message.add_reaction("❌")
            msg = await message.channel.reply("❌ Từ này không có trong từ điển.")
            await msg.delete(delay=5)
            return

        # ❌ Đã dùng
        if word in self.used_words:
            await message.add_reaction("❌")
            msg = await message.channel.reply("❌ Từ này đã được sử dụng.")
            await msg.delete(delay=5)
            return

        # ❌ Nối sai
        last = self.current_word.split()[-1]
        if not word.startswith(last):
            await message.add_reaction("❌")
            msg = await message.channel.reply(f"❌ Từ phải bắt đầu bằng **{last}**.")
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
        self._save_context()

        await message.add_reaction("✅")


async def setup(bot):
    await bot.add_cog(WordConnectCommandCog(bot))
