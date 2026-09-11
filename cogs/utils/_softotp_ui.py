"""Discord panel and forms for Soft OTP issuance and staff verification."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

from cogs.utils._softotp_helpers import SoftOtpError, normalize_challenge


if TYPE_CHECKING:
    from cogs.utils.softotp import SoftOtpCog

logger = logging.getLogger(__name__)

SOFTOTP_UI_TIMEOUT_SECONDS = 180
NO_MENTIONS = discord.AllowedMentions.none()


def can_verify_softotp(user: object) -> bool:
    """Administrator or Manage Server may verify a submitted Soft OTP."""
    permissions = getattr(user, "guild_permissions", None)
    if permissions is None:
        return False
    if getattr(permissions, "administrator", False):
        return True
    return bool(getattr(permissions, "manage_guild", False))


def staff_can_verify_softotp():
    """Command check matching `can_verify_softotp` for prefix verify."""

    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if can_verify_softotp(ctx.author):
            return True
        raise commands.MissingPermissions(["manage_guild"])

    return commands.check(predicate)


async def send_private(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
) -> None:
    """Send a mention-free private response before or after acknowledgement."""
    kwargs: dict[str, Any] = {
        "ephemeral": True,
        "allowed_mentions": NO_MENTIONS,
    }
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    try:
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)
    except discord.HTTPException:
        logger.debug("Could not send Soft OTP UI response", exc_info=True)


class SoftOtpOwnedView(discord.ui.View):
    """Owner-only Soft OTP workflow bound to the guild that opened it."""

    def __init__(
        self,
        cog: SoftOtpCog,
        *,
        guild_id: int,
        author_id: int,
        prefix: str,
    ) -> None:
        super().__init__(timeout=SOFTOTP_UI_TIMEOUT_SECONDS)
        self.cog = cog
        self.guild_id = guild_id
        self.author_id = author_id
        self.prefix = prefix
        self.message: discord.Message | None = None

    def stop(self) -> None:
        super().stop()
        views = getattr(self.cog, "_views", None)
        if views is not None:
            views.discard(self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            message = "Bảng Soft OTP này chỉ dùng được trong server đã mở nó."
        elif interaction.user.id != self.author_id:
            message = (
                "Chỉ người đã mở bảng được thao tác. "
                f"Hãy mở bảng riêng bằng `{self.prefix}softotp`."
            )
        elif self.is_finished() or getattr(self.cog, "_unloading", False):
            message = (
                f"Bảng đã đóng hoặc hết hạn. Hãy gọi lại `{self.prefix}softotp`."
            )
        else:
            return True
        await send_private(interaction, message)
        return False

    def _disable_controls(self) -> None:
        for item in self.children:
            item.disabled = True

    async def on_timeout(self) -> None:
        self._disable_controls()
        self.stop()
        if self.message is not None:
            try:
                await self.message.edit(view=self, allowed_mentions=NO_MENTIONS)
            except discord.HTTPException:
                logger.debug("Could not disable expired Soft OTP panel", exc_info=True)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        logger.error(
            "Soft OTP UI action failed guild=%s user=%s",
            self.guild_id,
            self.author_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        await send_private(
            interaction,
            "Không thể xử lý Soft OTP lúc này. Hãy thử lại sau.",
        )


class SoftOtpView(SoftOtpOwnedView):
    """Get OTP for everyone; verify is shown only to Administrator/Manage Server."""

    def __init__(
        self,
        cog: SoftOtpCog,
        *,
        guild_id: int,
        author_id: int,
        prefix: str,
        can_verify: bool,
    ) -> None:
        super().__init__(cog, guild_id=guild_id, author_id=author_id, prefix=prefix)
        self.can_verify = can_verify
        self.claimed_user_id: int | None = None
        self.claimed_user_label: str | None = None
        self.add_item(_GetOtpButton())
        if can_verify:
            self.add_item(_VerifyOtpButton())
        self.add_item(_ClosePanelButton())
        if can_verify:
            self.add_item(_ClaimedMemberSelect())

    def build_embed(self) -> discord.Embed:
        verify_line = (
            "Quản trị viên dùng **Xác minh** với challenge và OTP lấy từ Google Form "
            "để biết mã thuộc tài khoản Discord nào.\n"
            if self.can_verify
            else ""
        )
        embed = discord.Embed(
            title="🔐 Soft OTP",
            description=(
                "Mỗi challenge cho một mã OTP khác nhau, gắn với tài khoản Discord "
                "và server hiện tại. Dùng mã này để chứng minh bạn là chủ tài khoản "
                "khi điền Google Form.\n"
                f"{verify_line}"
                f"Lệnh nhanh: `{self.prefix}softotp get <challenge>`"
                + (
                    f" · `{self.prefix}softotp verify <challenge> <otp> [@user]`"
                    if self.can_verify
                    else ""
                )
            ),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Challenge",
            value=(
                "Một cụm **không khoảng trắng**, 1–64 ký tự, ví dụ `123456` "
                "hoặc `jd3s1s`. Viết hoa/thường không đổi mã."
            ),
            inline=False,
        )
        embed.add_field(
            name="Lấy OTP",
            value=(
                "Nhấn **Lấy OTP**, nhập đúng challenge trên form, rồi dán "
                "**toàn bộ** mã `tfotp1.<khóa>.<unix>.<mã>` vào Google Form. "
                "Đổi khóa sẽ vô hiệu mọi OTP hiện tại."
            ),
            inline=False,
        )
        if self.can_verify:
            embed.add_field(
                name="Xác minh",
                value=(
                    "Nên chọn thành viên khai trên form, rồi nhập challenge và OTP. "
                    "OTP của người khác sẽ bị từ chối, không gán nhầm sang tài khoản khác. "
                    "Bỏ trống thì bot tra mã đã lưu để biết ai lấy. "
                    "Cần quyền Administrator hoặc Manage Server."
                ),
                inline=False,
            )
        if self.claimed_user_label is not None:
            embed.add_field(
                name="Tài khoản đang đối chiếu",
                value=self.claimed_user_label,
                inline=False,
            )
        embed.set_footer(
            text=(
                "Chỉ người mở bảng được thao tác • "
                "Điều khiển hết hạn sau 3 phút không thao tác"
            )
        )
        return embed


class SoftOtpRevealView(SoftOtpOwnedView):
    """One-click private reveal when a prefix get cannot be delivered by DM."""

    def __init__(
        self,
        cog: SoftOtpCog,
        *,
        guild_id: int,
        author_id: int,
        prefix: str,
        challenge: str,
    ) -> None:
        super().__init__(cog, guild_id=guild_id, author_id=author_id, prefix=prefix)
        self.challenge = challenge
        self.add_item(_RevealOtpButton())


class _ClaimedMemberSelect(discord.ui.UserSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Chọn thành viên trên form (tránh nhầm OTP)…",
            min_values=0,
            max_values=1,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SoftOtpView):
            return
        if not await view.interaction_check(interaction):
            return
        if not can_verify_softotp(interaction.user):
            await send_private(
                interaction,
                "Bạn cần quyền Administrator hoặc Manage Server để xác minh Soft OTP.",
            )
            return
        selected = self.values[0] if self.values else None
        if selected is None:
            view.claimed_user_id = None
            view.claimed_user_label = None
        else:
            view.claimed_user_id = selected.id
            view.claimed_user_label = f"{selected.mention} (`{selected.id}`)"
        await interaction.response.edit_message(
            embed=view.build_embed(),
            view=view,
            allowed_mentions=NO_MENTIONS,
        )


class _GetOtpButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Lấy OTP",
            style=discord.ButtonStyle.primary,
            emoji="🔐",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SoftOtpView):
            return
        if not await view.interaction_check(interaction):
            return
        await interaction.response.send_modal(GetOtpModal(view))


class _VerifyOtpButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Xác minh",
            style=discord.ButtonStyle.success,
            emoji="✅",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SoftOtpView):
            return
        if not await view.interaction_check(interaction):
            return
        if not can_verify_softotp(interaction.user):
            await send_private(
                interaction,
                "Bạn cần quyền Administrator hoặc Manage Server để xác minh Soft OTP.",
            )
            return
        await interaction.response.send_modal(VerifyOtpModal(view))


class _ClosePanelButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Đóng",
            style=discord.ButtonStyle.secondary,
            emoji="✖️",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SoftOtpView):
            return
        if not await view.interaction_check(interaction):
            return
        view._disable_controls()
        view.stop()
        await interaction.response.edit_message(view=view, allowed_mentions=NO_MENTIONS)


class _RevealOtpButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Hiện OTP",
            style=discord.ButtonStyle.primary,
            emoji="🔐",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SoftOtpRevealView):
            return
        if not await view.interaction_check(interaction):
            return
        guild = interaction.guild
        if guild is None:
            await send_private(interaction, "Bảng Soft OTP này chỉ dùng được trong server.")
            return
        try:
            token = await view.cog.issue_token(
                guild.id, interaction.user.id, view.challenge
            )
        except SoftOtpError as error:
            await send_private(interaction, str(error))
            return
        await send_private(
            interaction,
            embed=view.cog.build_issued_embed(view.challenge, token, guild.name),
        )


class GetOtpModal(discord.ui.Modal):
    """Challenge form that returns a private Soft OTP."""

    def __init__(self, view: SoftOtpView) -> None:
        super().__init__(title="Lấy Soft OTP", timeout=SOFTOTP_UI_TIMEOUT_SECONDS)
        self.panel = view
        self.challenge_input = discord.ui.TextInput(
            label="Challenge",
            placeholder="Ví dụ: 123456 hoặc jd3s1s",
            style=discord.TextStyle.short,
            min_length=1,
            max_length=64,
        )
        self.add_item(self.challenge_input)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.panel.interaction_check(interaction)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.interaction_check(interaction):
            return
        guild = interaction.guild
        if guild is None:
            await send_private(interaction, "Soft OTP chỉ dùng được trong server.")
            return
        try:
            challenge = normalize_challenge(str(self.challenge_input.value))
            token = await self.panel.cog.issue_token(
                guild.id, interaction.user.id, challenge
            )
        except SoftOtpError as error:
            await send_private(interaction, str(error))
            return
        await send_private(
            interaction,
            embed=self.panel.cog.build_issued_embed(challenge, token, guild.name),
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error(
            "Soft OTP get modal failed guild=%s user=%s",
            self.panel.guild_id,
            self.panel.author_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        await send_private(
            interaction,
            "Không thể tạo Soft OTP lúc này. Hãy thử lại sau.",
        )


class VerifyOtpModal(discord.ui.Modal):
    """Staff form that identifies the Discord account bound to an OTP."""

    def __init__(self, view: SoftOtpView) -> None:
        super().__init__(title="Xác minh Soft OTP", timeout=SOFTOTP_UI_TIMEOUT_SECONDS)
        self.panel = view
        self.challenge_input = discord.ui.TextInput(
            label="Challenge",
            placeholder="Challenge trên Google Form",
            style=discord.TextStyle.short,
            min_length=1,
            max_length=64,
        )
        self.otp_input = discord.ui.TextInput(
            label="OTP",
            placeholder="tfotp1.<khóa>.<unix>.<8 ký tự>",
            style=discord.TextStyle.short,
            min_length=1,
            max_length=128,
        )
        self.add_item(self.challenge_input)
        self.add_item(self.otp_input)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await self.panel.interaction_check(interaction):
            return False
        if not can_verify_softotp(interaction.user):
            await send_private(
                interaction,
                "Bạn cần quyền Administrator hoặc Manage Server để xác minh Soft OTP.",
            )
            return False
        return True

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.interaction_check(interaction):
            return
        guild = interaction.guild
        if guild is None:
            await send_private(interaction, "Soft OTP chỉ dùng được trong server.")
            return
        try:
            user_id = await self.panel.cog.verify_token(
                guild.id,
                str(self.challenge_input.value),
                str(self.otp_input.value),
                claimed_user_id=self.panel.claimed_user_id,
            )
        except SoftOtpError as error:
            await send_private(interaction, f"❌ {error}")
            return
        member_label = (
            self.panel.claimed_user_label
            or await self.panel.cog.resolve_member_label(guild, user_id)
        )
        await send_private(
            interaction,
            embed=self.panel.cog.build_verified_embed(
                normalize_challenge(str(self.challenge_input.value)),
                member_label,
                claimed=self.panel.claimed_user_id is not None,
            ),
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error(
            "Soft OTP verify modal failed guild=%s user=%s",
            self.panel.guild_id,
            self.panel.author_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        await send_private(
            interaction,
            "Không thể xác minh Soft OTP lúc này. Hãy thử lại sau.",
        )
