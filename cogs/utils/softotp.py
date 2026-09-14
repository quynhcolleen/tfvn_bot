"""Challenge-bound Soft OTP issuance and staff verification."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands
from pymongo import ASCENDING
from pymongo.errors import PyMongoError

from cogs._hash_verification import (
    VerificationConfigurationError,
    VerificationKeyring,
    verification_keyring_from_bot,
)
from cogs.utils._softotp_helpers import (
    SOFTOTP_COLLECTION,
    SoftOtpError,
    confirm_softotp_token,
    persist_softotp_issuance,
    normalize_challenge,
    parse_softotp_token,
    resolve_softotp_issuance,
)
from cogs.utils._softotp_ui import (
    NO_MENTIONS,
    SoftOtpRevealView,
    SoftOtpView,
    can_verify_softotp,
    staff_can_verify_softotp,
)


logger = logging.getLogger(__name__)


class SoftOtpCog(commands.Cog):
    """Issue and verify guild-scoped Soft OTP codes for Google Form identity checks."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.issuances = bot.db[SOFTOTP_COLLECTION]
        self._views: set[discord.ui.View] = set()
        self._unloading = False
        try:
            self.verification_keyring: VerificationKeyring | None = (
                verification_keyring_from_bot(bot)
            )
        except VerificationConfigurationError:
            self.verification_keyring = None
        self._ensure_indexes()

    def cog_unload(self) -> None:
        self._unloading = True
        for view in tuple(self._views):
            view.stop()
        self._views.clear()

    def _ensure_indexes(self) -> None:
        try:
            self.issuances.create_index(
                [
                    ("guild_id", ASCENDING),
                    ("user_id", ASCENDING),
                    ("challenge", ASCENDING),
                ],
                unique=True,
                name="softotp_guild_user_challenge_unique",
            )
        except PyMongoError:
            logger.exception("Failed to create Soft OTP indexes")

    def _require_keyring(self) -> VerificationKeyring:
        if self.verification_keyring is None:
            raise SoftOtpError(
                "Tính năng Soft OTP chưa được cấu hình an toàn. "
                "Hãy báo quản trị viên bot."
            )
        return self.verification_keyring

    async def issue_token(self, guild_id: int, user_id: int, challenge: object) -> str:
        """Persist and return the current opaque Soft OTP token."""
        return await asyncio.to_thread(
            persist_softotp_issuance,
            self.issuances,
            self._require_keyring(),
            guild_id=guild_id,
            user_id=user_id,
            challenge=challenge,
            issued_at=discord.utils.utcnow(),
        )

    async def verify_token(
        self,
        guild_id: int,
        challenge: object,
        token: object,
        *,
        claimed_user_id: int | None = None,
    ) -> int:
        """Authenticate an OTP, optionally bound to a claimed member."""
        keyring = self._require_keyring()
        if claimed_user_id is None:
            return await asyncio.to_thread(
                resolve_softotp_issuance,
                self.issuances,
                keyring,
                guild_id=guild_id,
                challenge=challenge,
                token=token,
            )
        return await asyncio.to_thread(
            confirm_softotp_token,
            keyring,
            guild_id=guild_id,
            user_id=claimed_user_id,
            challenge=challenge,
            token=token,
        )

    @staticmethod
    def build_issued_embed(
        challenge: str,
        token: str,
        guild_name: str,
    ) -> discord.Embed:
        embed = discord.Embed(
            title="🔐 Soft OTP của bạn",
            description=(
                "Dán **toàn bộ** mã OTP vào Google Form. Challenge khác sẽ cho mã khác. "
                "Mã gắn với phiên bản khóa và thời điểm phát hành; đổi khóa sẽ vô hiệu "
                "mọi OTP hiện tại. Mã không chứa Discord ID."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Server",
            value=discord.utils.escape_markdown(guild_name) or "Không rõ",
            inline=False,
        )
        embed.add_field(
            name="Challenge",
            value=f"`{challenge}`",
            inline=True,
        )
        try:
            parsed = parse_softotp_token(token)
        except SoftOtpError:
            parsed = None
        if parsed is not None:
            embed.add_field(name="Khóa", value=f"`{parsed.kid}`", inline=True)
            embed.add_field(
                name="Phát hành",
                value=f"<t:{parsed.issued_at}:F>",
                inline=True,
            )
        embed.add_field(name="OTP", value=f"`{token}`", inline=False)
        return embed

    @staticmethod
    def build_verified_embed(
        challenge: str,
        member_label: str,
        *,
        claimed: bool = False,
    ) -> discord.Embed:
        description = (
            "OTP khớp tài khoản đã chọn. OTP của người khác sẽ bị từ chối."
            if claimed
            else (
                "Chữ ký khớp challenge. Đây là tài khoản Discord đã lấy mã — "
                "đừng tin Discord ID người dùng tự điền trên form."
            )
        )
        embed = discord.Embed(
            title="✅ Soft OTP hợp lệ",
            description=description,
            color=discord.Color.green(),
        )
        embed.add_field(name="Tài khoản", value=member_label, inline=False)
        embed.add_field(name="Challenge", value=f"`{challenge}`", inline=True)
        return embed

    async def resolve_member_label(
        self,
        guild: discord.Guild,
        user_id: int,
    ) -> str:
        member = guild.get_member(user_id)
        if member is None:
            fetch_member = getattr(guild, "fetch_member", None)
            if callable(fetch_member):
                try:
                    member = await fetch_member(user_id)
                except (discord.NotFound, discord.HTTPException):
                    member = None
        if member is not None:
            return f"{member.mention} (`{user_id}`)"
        return f"`{user_id}` (không còn trong server)"

    def _usage(self, ctx: commands.Context) -> str:
        prefix = ctx.clean_prefix
        return (
            f"`{prefix}softotp` — mở bảng Discord.\n"
            f"`{prefix}softotp get <challenge>` — lấy OTP (gửi qua DM).\n"
            f"`{prefix}softotp verify <challenge> <otp> [@user]` — "
            "Administrator hoặc Manage Server xác minh."
        )

    def _track(self, view: discord.ui.View) -> None:
        self._views.add(view)

    @commands.group(
        name="softotp",
        invoke_without_command=True,
        help="Mở bảng Soft OTP để lấy hoặc xác minh mã theo challenge.",
    )
    @commands.guild_only()
    @commands.cooldown(3, 10, commands.BucketType.user)
    async def softotp_group(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        view = SoftOtpView(
            self,
            guild_id=ctx.guild.id,
            author_id=ctx.author.id,
            prefix=ctx.clean_prefix,
            can_verify=can_verify_softotp(ctx.author),
        )
        self._track(view)
        try:
            view.message = await ctx.reply(
                "||Chỉ bạn thấy bảng này.||",
                embed=view.build_embed(),
                view=view,
                allowed_mentions=NO_MENTIONS,
                mention_author=False,
            )
        finally:
            if view.message is None:
                view.stop()

    @softotp_group.command(
        name="get",
        help="Lấy Soft OTP theo challenge để điền Google Form.",
        ignore_extra=False,
    )
    @commands.guild_only()
    @commands.cooldown(3, 10, commands.BucketType.user)
    async def softotp_get(self, ctx: commands.Context, challenge: str) -> None:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        try:
            normalized = normalize_challenge(challenge)
            token = await self.issue_token(ctx.guild.id, ctx.author.id, normalized)
        except SoftOtpError as error:
            await ctx.send(str(error), allowed_mentions=NO_MENTIONS)
            return

        embed = self.build_issued_embed(normalized, token, ctx.guild.name)
        try:
            await ctx.author.send(embed=embed, allowed_mentions=NO_MENTIONS)
        except (discord.Forbidden, discord.HTTPException):
            view = SoftOtpRevealView(
                self,
                guild_id=ctx.guild.id,
                author_id=ctx.author.id,
                prefix=ctx.clean_prefix,
                challenge=normalized,
            )
            self._track(view)
            try:
                view.message = await ctx.reply(
                    "Không gửi được tin nhắn riêng. Nhấn nút bên dưới để xem OTP "
                    "(chỉ bạn thấy).",
                    view=view,
                    allowed_mentions=NO_MENTIONS,
                    mention_author=False,
                )
            finally:
                if view.message is None:
                    view.stop()
            return

        await ctx.send(
            "Đã gửi Soft OTP qua tin nhắn riêng. Nếu không thấy, hãy mở DM với bot "
            f"hoặc dùng `{ctx.clean_prefix}softotp`.",
            allowed_mentions=NO_MENTIONS,
        )

    @softotp_group.command(
        name="verify",
        help="Xác minh Soft OTP theo challenge, OTP và tuỳ chọn tài khoản khai trên form.",
        ignore_extra=False,
    )
    @commands.guild_only()
    @staff_can_verify_softotp()
    @commands.cooldown(5, 20, commands.BucketType.user)
    async def softotp_verify(
        self,
        ctx: commands.Context,
        challenge: str,
        otp: str,
        member: discord.Member | None = None,
    ) -> None:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        try:
            normalized = normalize_challenge(challenge)
            user_id = await self.verify_token(
                ctx.guild.id,
                normalized,
                otp,
                claimed_user_id=None if member is None else member.id,
            )
        except SoftOtpError as error:
            await ctx.send(f"❌ {error}", allowed_mentions=NO_MENTIONS)
            return

        member_label = await self.resolve_member_label(ctx.guild, user_id)
        await ctx.send(
            embed=self.build_verified_embed(
                normalized,
                member_label,
                claimed=member is not None,
            ),
            allowed_mentions=NO_MENTIONS,
        )

    async def cog_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.NoPrivateMessage):
            message = "Lệnh softotp chỉ dùng được trong server."
        elif isinstance(error, commands.MissingPermissions):
            message = (
                "Bạn cần quyền Administrator hoặc Manage Server để xác minh Soft OTP."
            )
        elif isinstance(error, commands.CommandOnCooldown):
            seconds = max(1, round(error.retry_after))
            message = f"Chậm thôi, hãy thử lại sau **{seconds}** giây."
        elif isinstance(
            error,
            (commands.MissingRequiredArgument, commands.TooManyArguments),
        ):
            message = f"Sai cú pháp.\n{self._usage(ctx)}"
        elif isinstance(error, commands.UserInputError):
            message = f"Sai cú pháp.\n{self._usage(ctx)}"
        else:
            original = getattr(error, "original", error)
            logger.error(
                "Soft OTP command failed",
                exc_info=(type(original), original, original.__traceback__),
            )
            message = "Không thể xử lý Soft OTP lúc này. Hãy thử lại sau."
        await ctx.send(message, allowed_mentions=NO_MENTIONS)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SoftOtpCog(bot))
