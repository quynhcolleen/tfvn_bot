import asyncio
import datetime
import logging
import random

import discord  # pyright: ignore[reportMissingImports]
from discord.ext import commands  # pyright: ignore[reportMissingImports]
from pymongo.errors import PyMongoError

from cogs.minigames._card_game_economy import CardGameBank


logger = logging.getLogger(__name__)
WIN_REWARD = 50


class WordConnectCommandCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        if (self.bot.global_vars["WORD_CONNECT_GAMES_CHANNELS"] is None or self.bot.global_vars["WORD_CONNECT_GAMES_CHANNELS"] == ""):
            raise ValueError("WORD_CONNECT_GAMES_CHANNELS is not set in global variables.")
        
        if not isinstance(self.bot.global_vars["WORD_CONNECT_GAMES_CHANNELS"], list):
            self.bot.global_vars["WORD_CONNECT_GAMES_CHANNELS"] = [self.bot.global_vars["WORD_CONNECT_GAMES_CHANNELS"]]
        self.word_list: list[str] = self.bot.WORD_CONNECT_WORDS
        self.channel_games: list[str] = [str(channel_id) for channel_id in self.bot.global_vars["WORD_CONNECT_GAMES_CHANNELS"]]
        self.db = bot.db
        self.bank = CardGameBank(self.db)
        self.round_lock = asyncio.Lock()
        self._round_generation = 0
        self._round_started_at: datetime.datetime | None = None
        self.hint_timeout_datetime = None
        # self.rate_icon = {
        #     "brilliant": "<:brilliantmove:1458179812177870984>"  or "🌟",
        #     "great":     "<:greatmove:1458179830368567545>" or "👍",
        #     "good":      "<:goodmove:1458179823582318752>" or "👌",
        #     "forced":    "<:forcedmove:1458179821615190116>" or "⚡",
        #     "miss":      "<:missmove:1458179817781592124>" or "❓",
        #     "blunder":   "<:blundermove:1458179814014845071>" or "💥",
        # }

        self.rate_icon = {
            "brilliant":  self.bot.global_vars.get("BRILLIANT_MOVE_ICON", "🌟"),
            "great":     self.bot.global_vars.get("GREAT_MOVE_ICON", "👍"),
            "good":      self.bot.global_vars.get("GOOD_MOVE_ICON", "👌"),
            "forced":    self.bot.global_vars.get("FORCED_MOVE_ICON", "⚡"),
            "miss":      self.bot.global_vars.get("MISS_MOVE_ICON", "❓"),
            "blunder":   self.bot.global_vars.get("BLUNDER_MOVE_ICON", "💥"),
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

    def _random_word(self) -> str:
        # A new round must ignore the previous round's used words. Selecting
        # from valid starters also avoids an endless retry on an exhausted list.
        words_by_first = {}
        for word in self.word_list:
            words_by_first.setdefault(word.split()[0], set()).add(word)
        starters = [
            word for word in self.word_list
            if words_by_first.get(word.split()[-1], set()) - {word}
        ]
        if not starters:
            raise ValueError("Word Connect needs at least one playable starting word")
        return random.choice(starters)

    def _is_word_connect_channel(self, channel_id: int) -> bool:
        return str(channel_id) in self.channel_games

    def _is_dead_end(self, word: str) -> bool:
        last = word.split()[-1]
        return not any(
            w.startswith(last + " ") and w != word and w not in self.used_words
            for w in self.word_list
        )

    def _start_new_game(self):
        word = self._random_word()
        previous_state = (
            self.current_word, self.used_words,
            self.last_player_id, self.last_valid_message_id,
        )
        self.current_word = word
        self.used_words = [word]
        self.last_player_id = None
        self.last_valid_message_id = None
        try:
            self._save_context()
        except PyMongoError:
            (
                self.current_word, self.used_words,
                self.last_player_id, self.last_valid_message_id,
            ) = previous_state
            raise
        self._round_generation += 1
        self._round_started_at = discord.utils.utcnow()

    def _turn_number_reactions(self, turn_number: int) -> list[str]:
        digit_reactions = {
            "0": "0️⃣",
            "1": "1️⃣",
            "2": "2️⃣",
            "3": "3️⃣",
            "4": "4️⃣",
            "5": "5️⃣",
            "6": "6️⃣",
            "7": "7️⃣",
            "8": "8️⃣",
            "9": "9️⃣",
        }
        return [digit_reactions[digit] for digit in str(turn_number)]

    async def _react_with_turn_number(self, message: discord.Message, result_reaction: str, turn_number: int):
        for reaction in [result_reaction, *self._turn_number_reactions(turn_number)]:
            await self._add_reaction(message, reaction)

    async def _add_reaction(self, message: discord.Message, reaction: str) -> None:
        try:
            await message.add_reaction(reaction)
        except discord.HTTPException:
            logger.exception("Failed to react to Word Connect message=%s", message.id)

    async def _send_game_message(
        self, channel: discord.abc.Messageable, content: str, message_id: int,
    ) -> None:
        try:
            await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.exception("Failed to send Word Connect announcement message=%s", message_id)

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
            w for w in word_list if w.startswith(last + " ") and w != word
        ]
        return len(candidates)

    def _top_words(self, word: str) -> list[tuple[str, int]]:
        last = word.split()[-1]
        
        candidates = [w for w in self.word_list if w.startswith(last + " ")]
        
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

    def _normalize_old_tone(self, s: str) -> str:
        """
        Convert legacy tone placement → modern standard
        (mainly oa/oe/ua/ia/ya/ưa groups)
        """
        replacements = {
            # oa group
            'oà': 'òa', 'oá': 'óa', 'oả': 'ỏa', 'oã': 'õa', 'oạ': 'ọa',
            'òa': 'òa', 'óa': 'óa', 'ỏa': 'ỏa', 'õa': 'õa', 'ọa': 'ọa',  # already correct

            # oe group (rare)
            'oè': 'òe', 'oé': 'óe', 'oẻ': 'ỏe', 'oẽ': 'õe', 'oẹ': 'ọe',

            # ua group
            'uà': 'ùa', 'uá': 'úa', 'uả': 'ủa', 'uã': 'ũa', 'uạ': 'ụa',

            # ưa group
            'ưà': 'ừa', 'ưá': 'ứa', 'ưả': 'ửa', 'ưã': 'ữa', 'ưạ': 'ựa',

            # ia / ya group
            'ià': 'ìa', 'iá': 'ía', 'iả': 'ỉa', 'iã': 'ĩa', 'ịa': 'ịa',
            'yà': 'ỳa', 'yá': 'ýa', 'yả': 'ỷa', 'yã': 'ỹa', 'yạ': 'ỵa',

            # uy group (very common in your data)
            'uỳ': 'ủy', 'uý': 'úy', 'uỷ': 'ủy', 'uỹ': 'ũy', 'ụy': 'ụy',

            # uô / ô group (less frequent misplacement)
            'uồ': 'uồ', 'uố': 'uố', 'uổ': 'uổ', 'uỗ': 'uỗ', 'uộ': 'uộ',
        }

        for old, new in replacements.items():
            s = s.replace(old, new)

        # Optional: fix some very common mistakes seen in old texts
        s = s.replace('quì', 'quỳ').replace('quỵ', 'quỵ')   # quỳ is usually kept

        return s.strip()

    # COMMANDS
    @commands.group(name="noitu", invoke_without_command=True)
    async def noitu(self, ctx):
        if not self._is_word_connect_channel(ctx.channel.id):
            return

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
            name="🏆 Phần thưởng",
            value=(
                f"Nối hợp lệ khiến không còn từ chưa dùng nào nối tiếp: thắng **{WIN_REWARD} TC**.\n"
                "Dùng gợi ý vẫn được nhận thưởng. Reset bằng lệnh không có thưởng."
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
        if not self._is_word_connect_channel(ctx.channel.id):
            return

        async with self.round_lock:
            # timeout 30 seconds to prevent spam
            now = datetime.datetime.now()
            on_cooldown = (
                self.hint_timeout_datetime is not None
                and (now - self.hint_timeout_datetime).total_seconds() < 30
            )
            if not on_cooldown:
                self.hint_timeout_datetime = now
                top_suggestions = self._top_words(self.current_word.lower().strip())

        if on_cooldown:
            await ctx.send("⏳ Vui lòng chờ trước khi yêu cầu gợi ý tiếp theo.")
            return
        
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
        if not self._is_word_connect_channel(ctx.channel.id):
            return

        async with self.round_lock:
            try:
                self._start_new_game()
            except PyMongoError:
                logger.exception(
                    "Failed to reset Word Connect message=%s user=%s",
                    ctx.message.id, ctx.author.id,
                )
                response = "❌ Không thể lưu ván mới. Game chưa được reset; không có TC được trao."
            else:
                response = f"🔄 Game đã reset!\nTừ bắt đầu mới là **{self.current_word}**!"

        await self._send_game_message(ctx.channel, response, ctx.message.id)

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

        round_generation = self._round_generation
        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return

        if message.content.startswith(self.bot.command_prefix):
            return
        
        word = self._normalize_old_tone(word)
        error_message = None
        persistence_error = None
        winner_announcement = None
        next_announcement = None

        async with self.round_lock:
            if round_generation != self._round_generation:
                return
            if self._round_started_at is not None and message.created_at < self._round_started_at:
                return

            turn_number = max(1, len(self.used_words))
            last = self.current_word.split()[-1]
            if self.last_player_id == message.author.id:
                error_message = (
                    "❌ Bạn vừa nối từ trước đó rồi, hãy để người khác chơi nhé. "
                    f"Từ hiện tại: **{self.current_word}**"
                )
            elif word not in self.word_list:
                error_message = (
                    "❌ Từ này không có trong từ điển.\n"
                    f"Từ hiện tại: **{self.current_word}**"
                )
            elif word in self.used_words:
                error_message = (
                    "❌ Từ này đã được sử dụng. Các từ đã dùng: "
                    + ", ".join(self.used_words)
                )
            elif not word.startswith(last + " "):
                error_message = (
                    f"❌ Từ phải bắt đầu bằng **{last}**. "
                    f"Từ hiện tại: **{self.current_word}**"
                )
            elif self._is_dead_end(word):
                try:
                    # Consume the round before any credit or Discord await.
                    self._start_new_game()
                except PyMongoError:
                    logger.exception(
                        "Failed to persist Word Connect winning round message=%s user=%s",
                        message.id, message.author.id,
                    )
                    persistence_error = (
                        "❌ Không thể lưu ván mới nên chưa trao TC. "
                        "Ván hiện tại được giữ nguyên."
                    )
                else:
                    try:
                        self.bank.credit(
                            user_id=message.author.id,
                            guild_id=message.guild.id if message.guild else None,
                            game="word_connect",
                            amount=WIN_REWARD,
                            session_id=str(message.id),
                            reason="win",
                        )
                    except PyMongoError:
                        logger.exception(
                            "Unable to confirm Word Connect reward message=%s user=%s",
                            message.id, message.author.id,
                        )
                        reward_message = f"⚠️ Chưa thể xác nhận phần thưởng **{WIN_REWARD} TC**."
                    else:
                        reward_message = f"💰 Đã cộng **{WIN_REWARD} TC** vào tài khoản!"

                    winner_announcement = (
                        f"Không còn từ chưa dùng nào bắt đầu bằng **{word.split()[-1]}**! "
                        f"🎉 **{message.author.display_name} là người thắng cuộc!**\n"
                        f"📊 Tổng số lượt nối: **{turn_number}**\n{reward_message}"
                    )
                    next_announcement = f"🔄 Game mới bắt đầu với từ: **{self.current_word}**"
            else:
                previous_state = (
                    self.current_word, self.used_words,
                    self.last_player_id, self.last_valid_message_id,
                )
                self.used_words = [*self.used_words, word]
                self.current_word = word
                self.last_player_id = message.author.id
                self.last_valid_message_id = message.id
                try:
                    self._save_context()
                except PyMongoError:
                    (
                        self.current_word, self.used_words,
                        self.last_player_id, self.last_valid_message_id,
                    ) = previous_state
                    logger.exception(
                        "Failed to persist Word Connect move message=%s user=%s",
                        message.id, message.author.id,
                    )
                    persistence_error = "❌ Không thể lưu lượt nối. Ván hiện tại được giữ nguyên."

        if error_message is not None:
            await self._add_reaction(message, "❌")
            try:
                reply = await message.reply(
                    error_message, allowed_mentions=discord.AllowedMentions.none(),
                )
                await reply.delete(delay=5)
            except discord.HTTPException:
                logger.exception("Failed to report invalid Word Connect move message=%s", message.id)
            return

        if persistence_error is not None:
            await self._send_game_message(message.channel, persistence_error, message.id)
            return

        await self._react_with_turn_number(message, "✅", turn_number)
        if winner_announcement is not None:
            await self._add_reaction(message, "🏆")
            await self._send_game_message(message.channel, winner_announcement, message.id)
            await self._send_game_message(message.channel, next_announcement, message.id)


async def setup(bot):
    await bot.add_cog(WordConnectCommandCog(bot))
