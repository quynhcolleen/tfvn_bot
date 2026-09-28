import asyncio
import logging

import discord
from discord.ext import commands
from pymongo.errors import PyMongoError

from cogs.settings._guild_variables import (
    ANNOUNCEMENT_VARIABLES,
    ensure_guild_variable_index,
    get_guild_variables,
    valid_guild_id,
)


logger = logging.getLogger(__name__)
ANNOUNCEMENT_ONLY_MESSAGE = (
    "❌ Lệnh này chỉ hỗ trợ biến thông báo chào mừng và rời/kick/ban. "
    "Dùng `setting` để xem danh sách."
)


class VariableSetting(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.collection = bot.db["global_variables"]
        ensure_guild_variable_index(self.collection)
        self.bot.global_vars, self.bot.guild_vars = self.load_variables()

    def save_variable(self, guild_id: int, name: str, value: str) -> None:
        """Persist only an announcement setting for the specified guild."""
        if name not in ANNOUNCEMENT_VARIABLES:
            raise ValueError("Only announcement variables are supported.")
        if not valid_guild_id(guild_id):
            raise ValueError("A positive guild ID is required.")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Announcement settings require a nonempty string.")
        self.collection.update_one(
            {"guild_id": guild_id, "name": name},
            {"$set": {
                "guild_id": guild_id, "name": name, "type": "STRING", "value": value,
            }},
            upsert=True,
        )
        self.bot.guild_vars.setdefault(guild_id, {})[name] = value

    def load_variables(self) -> tuple[dict[str, object], dict[int, dict[str, str]]]:
        """Load guild announcements and retain read compatibility for existing cogs."""
        legacy = {}
        guilds = {}
        for doc in self.collection.find():
            name = doc.get("name")
            if not isinstance(name, str) or "value" not in doc:
                continue
            if "guild_id" not in doc:
                # Existing features still read the original flat configuration.
                legacy[name] = doc["value"]
            elif name in ANNOUNCEMENT_VARIABLES and valid_guild_id(doc["guild_id"]):
                value = doc["value"]
                if isinstance(value, str) and value.strip():
                    guilds.setdefault(doc["guild_id"], {})[name] = value
        return legacy, guilds

    @commands.group(name="setting", invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def setting(self, ctx: commands.Context) -> None:
        """Quản lý thông báo chào mừng và rời/kick/ban của server."""
        variables = ", ".join(f"`{name}`" for name in sorted(ANNOUNCEMENT_VARIABLES))
        await ctx.send(
            "Dùng `setting set_variable <NAME>` để đặt biến hoặc "
            "`setting get_variable <NAME>` để xem biến thông báo của server này.\n"
            f"Biến được hỗ trợ: {variables}"
        )

    @setting.command(name="set_variable")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.max_concurrency(1, per=commands.BucketType.member, wait=False)
    async def set_variable(self, ctx: commands.Context, name: str) -> None:
        """Đặt một biến thông báo cho server hiện tại."""
        if name not in ANNOUNCEMENT_VARIABLES:
            await ctx.send(ANNOUNCEMENT_ONLY_MESSAGE)
            return

        def check(message: discord.Message) -> bool:
            return (
                message.author == ctx.author
                and message.channel == ctx.channel
                and message.guild is not None
                and message.guild.id == ctx.guild.id
            )

        try:
            while True:
                await ctx.send(
                    f"Nhập giá trị cho `{name}` của server này. Gõ `cancel` để hủy."
                )
                message = await self.bot.wait_for("message", check=check, timeout=120)
                value = message.content.strip()
                if value.lower() == "cancel":
                    await ctx.send("✅ Đã hủy thiết lập biến.")
                    return
                if value:
                    break
                await ctx.send("❌ Giá trị không được rỗng.")
        except asyncio.TimeoutError:
            await ctx.send("⏰ Hết thời gian chờ. Vui lòng thử lại.")
            return

        current_member = ctx.guild.get_member(ctx.author.id)
        if current_member is None or not current_member.guild_permissions.administrator:
            await ctx.send("❌ Bạn cần quyền Administrator để thiết lập biến.")
            return
        try:
            self.save_variable(ctx.guild.id, name, value)
        except PyMongoError:
            logger.exception("Could not save an announcement setting for guild %s", ctx.guild.id)
            await ctx.send("❌ Không lưu được biến. Vui lòng thử lại sau.")
            return
        await ctx.send(
            f"✅ Đã thiết lập biến '{name}' cho server này (ID: {ctx.guild.id}).",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @setting.command(name="get_variable")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def get_variable(self, ctx: commands.Context, requested_name: str) -> None:
        """Xem một biến thông báo của server hiện tại."""
        if requested_name not in ANNOUNCEMENT_VARIABLES:
            await ctx.send(ANNOUNCEMENT_ONLY_MESSAGE)
            return
        variables = get_guild_variables(self.bot, ctx.guild.id)
        if requested_name not in variables:
            await ctx.send("❌ Không tìm thấy biến thông báo trong server này.")
            return
        text = f"Biến '{requested_name}' của server này:\n{variables[requested_name]}"
        for start in range(0, len(text), 1900):
            await ctx.send(
                text[start:start + 1900], allowed_mentions=discord.AllowedMentions.none()
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VariableSetting(bot))
