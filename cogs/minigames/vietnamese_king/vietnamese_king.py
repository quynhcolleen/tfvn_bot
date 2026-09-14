import asyncio
import json
import logging
import os
import random

import discord  # pyright: ignore[reportMissingImports]
from discord.ext import commands  # pyright: ignore[reportMissingImports]
from pymongo.errors import PyMongoError

from cogs.minigames._card_game_economy import CardGameBank
from cogs.minigames._word_game_leaderboard import (
    ensure_win_leaderboard_index,
    send_win_leaderboard,
)


logger = logging.getLogger(__name__)
WIN_REWARD = 10


class VietnameseKingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        # Load the configuration for the channel
        channel_var = self.bot.global_vars.get("VIETNAMESE_KING_GAMES_CHANNELS")
        if not channel_var:
            raise ValueError("VIETNAMESE_KING_GAMES_CHANNELS is not set in global variables.")

        if not isinstance(channel_var, list):
            channel_var = [channel_var]
        self.VIETNAMESE_KING_GAMES_CHANNELS = [str(channel_id) for channel_id in channel_var]
        self.db = bot.db
        self.bank = CardGameBank(self.db)
        ensure_win_leaderboard_index(self.db["transaction_logs"])
        
        # Load the vietnamese king data
        data_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'data', 'vietnamese_king_data.json')
        try:
            with open(data_path, 'r', encoding='utf-8') as f:
                self.words_data = json.load(f)
        except Exception as e:
            print(f"Failed to load vietnamese_king_data.json: {e}")
            self.words_data = []

        self.current_word = None
        self.current_standardized_word = None
        self.scrambled_letters = None
        self.revealed_indices = []
        self.round_lock = asyncio.Lock()
        self._round_generation = 0
        self._round_started_at = None

        # Try to restore context
        self._load_context()

    def _load_context(self):
        record = self.db["context"].find_one({"context_type": "vietnamese_king"})
        if record:
            self.current_word = record.get("current_word")
            self.current_standardized_word = (
                record.get("current_standardized_word")
                or self._normalize_old_tone((self.current_word or "").lower().strip())
            )
            self.scrambled_letters = record.get("scrambled_letters")
            self.revealed_indices = record.get("revealed_indices", [])
        else:
            self._start_new_round()

    def _save_context(self):
        doc = {
            "context_type": "vietnamese_king",
            "current_word": self.current_word,
            "current_standardized_word": self.current_standardized_word,
            "scrambled_letters": self.scrambled_letters,
            "revealed_indices": self.revealed_indices,
        }
        self.db["context"].update_one(
            {"context_type": "vietnamese_king"},
            {"$set": doc},
            upsert=True,
        )

    def _clear_context(self):
        self.db["context"].delete_many({"context_type": "vietnamese_king"})
        self.current_word = None
        self.current_standardized_word = None
        self.scrambled_letters = None
        self.revealed_indices = []
        self._round_generation += 1
        self._round_started_at = discord.utils.utcnow()

    def _is_vietnamese_king_channel(self, channel_id: int) -> bool:
        return str(channel_id) in self.VIETNAMESE_KING_GAMES_CHANNELS

    def _normalize_old_tone(self, s: str) -> str:
        replacements = {
            "oà": "òa", "oá": "óa", "oả": "ỏa", "oã": "õa", "oạ": "ọa",
            "oè": "òe", "oé": "óe", "oẻ": "ỏe", "oẽ": "õe", "oẹ": "ọe",
            "uà": "ùa", "uá": "úa", "uả": "ủa", "uã": "ũa", "uạ": "ụa",
            "ưà": "ừa", "ưá": "ứa", "ưả": "ửa", "ưã": "ữa", "ưạ": "ựa",
            "ườ": "ường", "ướ": "ướ", "ưở": "ưởng", "ưỡ": "ưỡng", "ượ": "ượng",
            "ià": "ìa", "iá": "ía", "iả": "ỉa", "iã": "ĩa", "ịa": "ịa",
            "yà": "ỳa", "yá": "ýa", "yả": "ỷa", "yã": "ỹa", "yạ": "ỵa",
            "uỳ": "ùy", "uý": "úy", "uỷ": "ủy", "uỹ": "ũy", "ụy": "ụy",
            "uồ": "uồ", "uố": "uố", "uổ": "uổ", "uỗ": "uỗ", "uộ": "uộ",
        }

        for old, new in replacements.items():
            s = s.replace(old, new)

        return s.replace("quì", "quỳ").strip()

    def _word_structure(self) -> str:
        structure = []
        for i, c in enumerate(self.current_word or ""):
            if c == " " or c == "-":
                structure.append(c)
            elif i in self.revealed_indices:
                structure.append(c.upper())
            else:
                structure.append("_")

        return "".join(structure)

    def _round_message(self, puzzle_label: str) -> str:
        return (
            "👑 **VUA TIẾNG VIỆT** 👑\n"
            f"🧩 Cấu trúc: `{self._word_structure()}`\n"
            f"🔠 {puzzle_label}: **{self.scrambled_letters}**"
        )

    async def _is_command_message(self, message: discord.Message) -> bool:
        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return True

        content = message.content.strip()
        if not content:
            return False

        prefix = self.bot.command_prefix
        if isinstance(prefix, str) and content.startswith(prefix.strip()):
            return True
        if isinstance(prefix, (list, tuple)) and any(content.startswith(str(p).strip()) for p in prefix):
            return True

        command_name = content.split()[0].lower()
        return command_name in self.bot.all_commands

    def _start_new_round(self) -> None:
        previous = (
            self.current_word,
            self.current_standardized_word,
            self.scrambled_letters,
            self.revealed_indices,
        )
        choices = [entry for entry in self.words_data if entry.get("word_len", 0) >= 3]
        self.current_word = None
        self.current_standardized_word = None
        self.scrambled_letters = None
        self.revealed_indices = []
        if choices:
            choice = random.choice(choices)
            word = choice["word"]
            self.current_word = word
            self.current_standardized_word = choice.get("standardize") or (
                self._normalize_old_tone(word.lower().strip())
            )
            characters = list(word.replace(" ", "").replace("-", ""))
            scrambled = characters[:]
            for _ in range(10):
                random.shuffle(scrambled)
                if scrambled != characters:
                    break
            self.scrambled_letters = " ".join(scrambled).upper()

        # Persist the transition before settling the old round. A failed save
        # must not leave a playable puzzle that only exists in memory.
        try:
            self._save_context()
        except PyMongoError:
            (
                self.current_word,
                self.current_standardized_word,
                self.scrambled_letters,
                self.revealed_indices,
            ) = previous
            raise
        self._round_generation += 1
        self._round_started_at = discord.utils.utcnow()

    def _next_round_message(self) -> str:
        if self.scrambled_letters:
            return self._round_message("Câu đố mới")
        return "Không thể bắt đầu câu đố mới do chưa tải được dữ liệu."

    async def _send_messages(
        self, destination: discord.abc.Messageable, messages: list[str]
    ) -> None:
        for content in messages:
            try:
                await destination.send(content)
            except discord.HTTPException:
                logger.exception("Failed to send Vietnamese King round announcement")

    @commands.group(name="vtv", invoke_without_command=True)
    async def vtv(self, ctx):
        if not self._is_vietnamese_king_channel(ctx.channel.id):
            return
            
        embed = discord.Embed(
            title="👑 VUA TIẾNG VIỆT",
            description="Luật chơi: Hãy sắp xếp lại các chữ cái để tạo thành từ/cụm từ đúng!",
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="🏆 Phần thưởng",
            value=(
                f"Người đầu tiên giải đúng nhận **{WIN_REWARD} TC**, kể cả khi đã dùng gợi ý.\n"
                "Bỏ qua câu đố hoặc hết lượt gợi ý không có thưởng.\n"
                "Xem bảng xếp hạng bằng `vtv top`."
            ),
            inline=False,
        )
        embed.set_footer(text="Gõ trực tiếp từ bạn đoán vào kênh này.")
        await ctx.send(embed=embed)
        
        if self.scrambled_letters:
            await ctx.send(
                f"🧩 Cấu trúc: `{self._word_structure()}`\n"
                f"🔠 Câu đố hiện tại: **{self.scrambled_letters}**"
            )

    @vtv.command(name="status")
    async def vtv_status(self, ctx):
        if not self._is_vietnamese_king_channel(ctx.channel.id):
            return
            
        if self.scrambled_letters:
            embed = discord.Embed(title="👑 VUA TIẾNG VIỆT - TRẠNG THÁI", color=discord.Color.blue())
            embed.add_field(name="🧩 Cấu trúc", value=f"`{self._word_structure()}`", inline=False)
            embed.add_field(name="🔠 Câu đố", value=f"**{self.scrambled_letters}**", inline=False)
            embed.set_footer(text="Hãy sắp xếp lại các chữ cái để tạo thành từ đúng!")
            await ctx.send(embed=embed)
        else:
            await ctx.send("Chưa có lượt chơi nào đang diễn ra. Dùng `!vtv next` để bắt đầu!")

    @vtv.command(name="next")
    async def vtv_next(self, ctx):
        if not self._is_vietnamese_king_channel(ctx.channel.id):
            return

        async with self.round_lock:
            try:
                self._start_new_round()
            except PyMongoError:
                logger.exception("Failed to persist Vietnamese King reset user=%s", ctx.author.id)
                response = "⚠️ Không thể lưu câu đố mới. Vui lòng thử lại sau."
            else:
                response = self._next_round_message()
        await self._send_messages(ctx, [response])

    @vtv.command(name="top", help="Bảng xếp hạng người thắng Vua Tiếng Việt.")
    async def vtv_top(self, ctx):
        if not self._is_vietnamese_king_channel(ctx.channel.id):
            return
        await send_win_leaderboard(ctx, self.db["transaction_logs"], "vietnamese_king")

    @vtv.command(name="hint")
    async def vtv_hint(self, ctx):
        if not self._is_vietnamese_king_channel(ctx.channel.id):
            return

        async with self.round_lock:
            if not self.current_word:
                responses = ["Chưa có lượt chơi nào diễn ra."]
            else:
                previous_revealed = self.revealed_indices[:]
                valid_indices = [
                    i for i, char in enumerate(self.current_word) if char not in " -"
                ]
                unrevealed = [i for i in valid_indices if i not in self.revealed_indices]
                if unrevealed:
                    self.revealed_indices.append(random.choice(unrevealed))
                word_structure = self._word_structure()
                remaining_hidden = sum(
                    i not in self.revealed_indices for i in valid_indices
                )
                try:
                    if remaining_hidden <= 1:
                        answer = self.current_word
                        self._start_new_round()
                        responses = [
                            f"💡 Gợi ý: Cấu trúc từ: `{word_structure}`\n"
                            f"⌛ Hết lượt gợi ý! Không ai chiến thắng. Đáp án là: **{answer}**",
                            self._next_round_message(),
                        ]
                    else:
                        self._save_context()
                        responses = [f"💡 Gợi ý: Cấu trúc từ: `{word_structure}`"]
                except PyMongoError:
                    self.revealed_indices = previous_revealed
                    logger.exception(
                        "Failed to persist Vietnamese King hint user=%s", ctx.author.id
                    )
                    responses = ["⚠️ Không thể lưu gợi ý. Vui lòng thử lại sau."]

        await self._send_messages(ctx, responses)

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
            
        if not self._is_vietnamese_king_channel(message.channel.id):
            return

        round_generation = self._round_generation
        # Ignore commands and command-like text in the game channel.
        if await self._is_command_message(message):
            return

        guess = self._normalize_old_tone(message.content.lower().strip())
        response = None
        next_round_message = None

        async with self.round_lock:
            if round_generation != self._round_generation or not self.current_word:
                return
            # Queued messages can start their listener after the previous win
            # when command detection does not yield. They belong to the old round.
            if self._round_started_at and message.created_at < self._round_started_at:
                return
            answer_key = self.current_standardized_word or self._normalize_old_tone(self.current_word.lower().strip())
            is_correct = guess == answer_key
            reaction = "✅" if is_correct else "❌"
            if is_correct:
                answer = self.current_word
                try:
                    self._start_new_round()
                except PyMongoError:
                    logger.exception(
                        "Failed to persist Vietnamese King win message=%s user=%s",
                        message.id,
                        message.author.id,
                    )
                    reaction = "⚠️"
                    response = (
                        "⚠️ Không thể lưu kết quả lượt chơi nên chưa cộng thưởng. "
                        "Vui lòng thử lại sau."
                    )
                else:
                    next_round_message = self._next_round_message()
                    response = f"🎉 Chúc mừng bạn đã giải đúng! Đáp án là: **{answer}**"
                    try:
                        self.bank.credit(
                            user_id=message.author.id,
                            guild_id=message.guild.id if message.guild else None,
                            game="vietnamese_king",
                            amount=WIN_REWARD,
                            session_id=str(message.id),
                            reason="win",
                        )
                    except PyMongoError:
                        logger.exception(
                            "Could not confirm Vietnamese King reward message=%s user=%s",
                            message.id,
                            message.author.id,
                        )
                        response += (
                            "\n⚠️ Chưa thể xác nhận phần thưởng Trap Coin. "
                            "Vui lòng báo quản trị viên."
                        )
                    else:
                        response += f"\n🪙 Bạn nhận được **{WIN_REWARD} TC**!"

        # Discord delivery is independent of settlement and never retries credit.
        try:
            await message.add_reaction(reaction)
        except discord.HTTPException:
            logger.exception("Failed to react to Vietnamese King message=%s", message.id)
        if response is not None:
            try:
                await message.reply(response)
            except discord.HTTPException:
                logger.exception("Failed to reply to Vietnamese King message=%s", message.id)
        if next_round_message is not None:
            await self._send_messages(message.channel, [next_round_message])

async def setup(bot):
    await bot.add_cog(VietnameseKingCog(bot))
