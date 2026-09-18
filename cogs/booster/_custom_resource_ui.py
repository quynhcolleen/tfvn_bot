import logging
from dataclasses import dataclass, replace
from typing import Awaitable, Callable

import discord

from cogs.booster._role_colors import RoleColorSpec, parse_hex_color


logger = logging.getLogger(__name__)

BOOSTER_UI_TIMEOUT_SECONDS = 120
ROLE_SOLID_SELECT_CUSTOM_ID = "booster-role:solid"
ROLE_GRADIENT_SELECT_CUSTOM_ID = "booster-role:gradient"
ROOM_LIMIT_SELECT_CUSTOM_ID = "booster-room:limit"


@dataclass(frozen=True)
class BoosterActionResult:
    completed: bool
    message: str


@dataclass(frozen=True)
class RoleColorPreset:
    key: str
    label: str
    primary: int
    secondary: int | None
    emoji: str
    description: str

    def color_spec(self) -> RoleColorSpec:
        return RoleColorSpec(
            primary=discord.Color(self.primary),
            secondary=(
                discord.Color(self.secondary)
                if self.secondary is not None
                else None
            ),
        )


SOLID_ROLE_COLOR_PRESETS = (
    RoleColorPreset("pink", "Hồng", 0xFF66B3, None, "🩷", "#FF66B3"),
    RoleColorPreset("red", "Đỏ", 0xED4245, None, "❤️", "#ED4245"),
    RoleColorPreset("orange", "Cam", 0xF47B20, None, "🧡", "#F47B20"),
    RoleColorPreset("yellow", "Vàng", 0xFEE75C, None, "💛", "#FEE75C"),
    RoleColorPreset("green", "Xanh lá", 0x57F287, None, "💚", "#57F287"),
    RoleColorPreset("teal", "Xanh ngọc", 0x1ABC9C, None, "🩵", "#1ABC9C"),
    RoleColorPreset("blue", "Xanh dương", 0x5865F2, None, "💙", "#5865F2"),
    RoleColorPreset("purple", "Tím", 0x9B59B6, None, "💜", "#9B59B6"),
    RoleColorPreset("magenta", "Hồng tím", 0xEB459E, None, "💗", "#EB459E"),
    RoleColorPreset("white", "Trắng", 0xF5F5F5, None, "🤍", "#F5F5F5"),
)

GRADIENT_ROLE_COLOR_PRESETS = (
    RoleColorPreset(
        "sunset",
        "Hoàng hôn",
        0xFF512F,
        0xDD2476,
        "🌇",
        "#FF512F → #DD2476",
    ),
    RoleColorPreset(
        "ocean",
        "Đại dương",
        0x2193B0,
        0x6DD5ED,
        "🌊",
        "#2193B0 → #6DD5ED",
    ),
    RoleColorPreset(
        "sakura",
        "Sakura",
        0xFF9A9E,
        0xFAD0C4,
        "🌸",
        "#FF9A9E → #FAD0C4",
    ),
    RoleColorPreset(
        "aurora",
        "Cực quang",
        0x00F260,
        0x0575E6,
        "🌌",
        "#00F260 → #0575E6",
    ),
    RoleColorPreset(
        "candy",
        "Kẹo ngọt",
        0xFC5C7D,
        0x6A82FB,
        "🍬",
        "#FC5C7D → #6A82FB",
    ),
    RoleColorPreset(
        "fire",
        "Rực lửa",
        0xF12711,
        0xF5AF19,
        "🔥",
        "#F12711 → #F5AF19",
    ),
)


@dataclass(frozen=True)
class RoleDesignDraft:
    role_name: str
    color_spec: RoleColorSpec


@dataclass(frozen=True)
class RoomDesignDraft:
    room_name: str
    user_limit: int = 0


RoleSubmitter = Callable[
    [discord.Interaction, RoleDesignDraft],
    Awaitable[BoosterActionResult],
]
RoomSubmitter = Callable[
    [discord.Interaction, RoomDesignDraft],
    Awaitable[BoosterActionResult],
]
ActionOperation = Callable[[], Awaitable[BoosterActionResult]]


def _hex_value(color: discord.Color) -> str:
    return f"#{color.value:06X}"


def _safe_text(value: str) -> str:
    return discord.utils.escape_mentions(discord.utils.escape_markdown(value))


class BoosterSetupView(discord.ui.View):
    def __init__(
        self,
        *,
        author_id: int,
        command_name: str,
        owner_denial: str | None = None,
        owner_modal_denial: str | None = None,
        cancel_message: str | None = None,
        footer_note: str | None = None,
    ) -> None:
        super().__init__(timeout=BOOSTER_UI_TIMEOUT_SECONDS)
        self.author_id = author_id
        self.command_name = command_name
        self.owner_denial = (
            owner_denial or "Chỉ Booster đã mở bảng này mới có thể sử dụng."
        )
        self.owner_modal_denial = (
            owner_modal_denial
            or "Chỉ Booster đã mở bảng này mới có thể chỉnh sửa."
        )
        self.cancel_message = cancel_message or "Đã hủy thao tác Booster."
        self.footer_note = footer_note
        self.message: discord.Message | None = None
        self.completed = False
        self.submitting = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                self.owner_denial,
                ephemeral=True,
            )
            return False
        if self.completed:
            await interaction.response.send_message(
                "Bảng này đã hoàn tất. Hãy gọi lại lệnh nếu bạn muốn thao tác tiếp.",
                ephemeral=True,
            )
            return False
        if self.submitting:
            await interaction.response.send_message(
                "Yêu cầu đang được xử lý, vui lòng chờ một chút.",
                ephemeral=True,
            )
            return False
        return True

    async def modal_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                self.owner_modal_denial,
                ephemeral=True,
            )
            return False
        if self.completed or self.submitting or self.is_finished():
            await interaction.response.send_message(
                "Bảng này đã hết hạn hoặc đang được xử lý. Hãy gọi lại lệnh.",
                ephemeral=True,
            )
            return False
        return True

    def disable_all(self) -> None:
        for item in self.children:
            item.disabled = True

    async def cancel(self, interaction: discord.Interaction) -> None:
        self.completed = True
        self.disable_all()
        self.stop()
        await interaction.response.edit_message(
            content=self.cancel_message,
            embed=None,
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def submit(
        self,
        interaction: discord.Interaction,
        operation: ActionOperation,
    ) -> None:
        self.submitting = True
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            self.submitting = False
            raise

        try:
            result = await operation()
        except Exception:
            self.submitting = False
            logger.exception(
                "Unexpected failure while submitting booster UI command %s for user %s.",
                self.command_name,
                self.author_id,
            )
            try:
                await interaction.followup.send(
                    "Đã xảy ra lỗi ngoài dự kiến. Vui lòng thử lại.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.exception(
                    "Could not report booster UI failure for user %s.",
                    self.author_id,
                )
            return

        if not result.completed:
            self.submitting = False
            try:
                await interaction.followup.send(
                    result.message,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.exception(
                    "Could not report retryable booster UI result for user %s.",
                    self.author_id,
                )
            return

        self.completed = True
        self.disable_all()
        try:
            await interaction.edit_original_response(
                content=result.message,
                embed=None,
                view=self,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.exception(
                "Could not update completed booster UI command %s for user %s.",
                self.command_name,
                self.author_id,
            )
            fallback_succeeded = False
            if self.message is not None:
                try:
                    await self.message.edit(
                        content=result.message,
                        embed=None,
                        view=self,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    fallback_succeeded = True
                except discord.HTTPException:
                    logger.exception(
                        "Could not update stored booster UI message for user %s.",
                        self.author_id,
                    )
            if fallback_succeeded:
                return
            try:
                await interaction.followup.send(
                    result.message,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.exception(
                    "Could not deliver completed booster UI result for user %s.",
                    self.author_id,
                )
        finally:
            self.stop()

    async def on_timeout(self) -> None:
        self.disable_all()
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug(
                "Could not disable expired booster UI for user %s.",
                self.author_id,
                exc_info=True,
            )


class RolePresetSelect(discord.ui.Select):
    def __init__(
        self,
        editor: "BoosterRoleEditorView",
        *,
        gradient: bool,
    ) -> None:
        self.editor = editor
        self.gradient = gradient
        presets = (
            GRADIENT_ROLE_COLOR_PRESETS
            if gradient
            else SOLID_ROLE_COLOR_PRESETS
        )
        options = [
            discord.SelectOption(
                label=preset.label,
                value=preset.key,
                description=preset.description,
                emoji=preset.emoji,
            )
            for preset in presets
        ]
        super().__init__(
            custom_id=(
                ROLE_GRADIENT_SELECT_CUSTOM_ID
                if gradient
                else ROLE_SOLID_SELECT_CUSTOM_ID
            ),
            placeholder=(
                "🌈 Chọn gradient có sẵn"
                if gradient
                else "🎨 Chọn màu đơn có sẵn"
            ),
            min_values=1,
            max_values=1,
            options=options,
            row=1 if gradient else 0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        presets = (
            GRADIENT_ROLE_COLOR_PRESETS
            if self.gradient
            else SOLID_ROLE_COLOR_PRESETS
        )
        preset = next(
            (item for item in presets if item.key == self.values[0]),
            None,
        )
        if preset is None:
            await interaction.response.send_message(
                "Mẫu màu này không còn khả dụng. Hãy mở lại bảng chọn.",
                ephemeral=True,
            )
            return
        await self.editor.open_role_modal(
            interaction,
            color_spec=preset.color_spec(),
        )


class RoleColorButton(discord.ui.Button):
    def __init__(
        self,
        editor: "BoosterRoleEditorView",
        *,
        gradient: bool,
    ) -> None:
        self.editor = editor
        self.gradient = gradient
        super().__init__(
            label="Gradient HEX" if gradient else "Màu HEX",
            emoji="🌈" if gradient else "🖌️",
            style=discord.ButtonStyle.secondary,
            custom_id=(
                "booster-role:custom-gradient"
                if gradient
                else "booster-role:custom-solid"
            ),
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        current = self.editor.draft.color_spec if self.editor.draft else None
        if current is not None and current.is_gradient != self.gradient:
            current = None
        await self.editor.open_role_modal(
            interaction,
            color_spec=current,
            gradient=self.gradient,
        )


class ConfirmRoleButton(discord.ui.Button):
    def __init__(self, editor: "BoosterRoleEditorView") -> None:
        self.editor = editor
        super().__init__(
            label="Xác nhận",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id="booster-role:confirm",
            disabled=True,
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.editor.confirm(interaction)


class CancelButton(discord.ui.Button):
    def __init__(self, setup_view: BoosterSetupView, *, resource: str) -> None:
        self.setup_view = setup_view
        super().__init__(
            label="Hủy",
            emoji="✖️",
            style=discord.ButtonStyle.danger,
            custom_id=f"booster-{resource}:cancel",
            row=3 if resource == "role" else 1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.setup_view.cancel(interaction)


class RoleDesignModal(discord.ui.Modal):
    def __init__(
        self,
        editor: "BoosterRoleEditorView",
        *,
        color_spec: RoleColorSpec | None,
        gradient: bool,
    ) -> None:
        super().__init__(
            title=(
                "Thiết kế gradient cho role"
                if gradient
                else "Thiết kế màu cho role"
            ),
            timeout=BOOSTER_UI_TIMEOUT_SECONDS,
        )
        self.editor = editor
        self.gradient = gradient
        existing_name = (
            editor.draft.role_name
            if editor.draft is not None
            else editor.default_role_name
        )
        self.role_name = discord.ui.TextInput(
            label="Tên role",
            placeholder="Ví dụ: Góc nhỏ của Kien",
            default=existing_name or None,
            min_length=1,
            max_length=100,
        )
        self.primary_color = discord.ui.TextInput(
            label="Màu chính",
            placeholder="#FF66B3",
            default=(
                _hex_value(color_spec.primary)
                if color_spec is not None
                else None
            ),
            min_length=6,
            max_length=7,
        )
        self.add_item(self.role_name)
        self.add_item(self.primary_color)
        self.secondary_color: discord.ui.TextInput | None = None
        if gradient:
            self.secondary_color = discord.ui.TextInput(
                label="Màu phụ",
                placeholder="#5865F2",
                default=(
                    _hex_value(color_spec.secondary)
                    if color_spec is not None
                    and color_spec.secondary is not None
                    else None
                ),
                min_length=6,
                max_length=7,
            )
            self.add_item(self.secondary_color)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.editor.modal_check(interaction):
            return

        role_name = self.role_name.value.strip()
        primary = parse_hex_color(self.primary_color.value)
        secondary = (
            parse_hex_color(self.secondary_color.value)
            if self.secondary_color is not None
            else None
        )
        if not role_name:
            await interaction.response.send_message(
                "Tên role không được chỉ chứa khoảng trắng.",
                ephemeral=True,
            )
            return
        if primary is None or (self.gradient and secondary is None):
            await interaction.response.send_message(
                "Màu không hợp lệ. Hãy nhập theo dạng `#RRGGBB`.",
                ephemeral=True,
            )
            return

        self.editor.draft = RoleDesignDraft(
            role_name=role_name,
            color_spec=RoleColorSpec(primary=primary, secondary=secondary),
        )
        self.editor.confirm_button.disabled = False
        await interaction.response.edit_message(
            embed=self.editor.build_embed(),
            view=self.editor,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class BoosterRoleEditorView(BoosterSetupView):
    def __init__(
        self,
        *,
        author_id: int,
        command_name: str,
        submitter: RoleSubmitter,
        default_role_name: str = "",
        initial_color_spec: RoleColorSpec | None = None,
        icon_attached: bool = False,
        owner_denial: str | None = None,
        owner_modal_denial: str | None = None,
        cancel_message: str | None = None,
        footer_note: str | None = None,
    ) -> None:
        super().__init__(
            author_id=author_id,
            command_name=command_name,
            owner_denial=owner_denial,
            owner_modal_denial=owner_modal_denial,
            cancel_message=cancel_message,
            footer_note=footer_note,
        )
        self.submitter = submitter
        self.default_role_name = default_role_name
        self.icon_attached = icon_attached
        self.draft = (
            RoleDesignDraft(default_role_name, initial_color_spec)
            if default_role_name and initial_color_spec is not None
            else None
        )
        self.solid_select = RolePresetSelect(self, gradient=False)
        self.gradient_select = RolePresetSelect(self, gradient=True)
        self.custom_solid_button = RoleColorButton(self, gradient=False)
        self.custom_gradient_button = RoleColorButton(self, gradient=True)
        self.confirm_button = ConfirmRoleButton(self)
        self.cancel_button = CancelButton(self, resource="role")
        self.add_item(self.solid_select)
        self.add_item(self.gradient_select)
        self.add_item(self.custom_solid_button)
        self.add_item(self.custom_gradient_button)
        self.add_item(self.confirm_button)
        self.add_item(self.cancel_button)
        if self.draft is not None:
            self.confirm_button.disabled = False

    def build_embed(self) -> discord.Embed:
        action = "cập nhật" if self.command_name == "update_custom_role" else "tạo"
        embed = discord.Embed(
            title=f"🎨 {action.capitalize()} custom role",
            description=(
                "Chọn một màu có sẵn hoặc nhập mã HEX. "
                "Bạn sẽ được xem trước trước khi xác nhận."
            ),
            color=(
                self.draft.color_spec.primary
                if self.draft is not None
                else discord.Color.blurple()
            ),
        )
        if self.draft is None:
            embed.add_field(
                name="Trạng thái",
                value="Chưa chọn thiết kế.",
                inline=False,
            )
        else:
            color_text = _hex_value(self.draft.color_spec.primary)
            if self.draft.color_spec.secondary is not None:
                color_text += (
                    f" → {_hex_value(self.draft.color_spec.secondary)}"
                )
            embed.add_field(
                name="Tên role",
                value=_safe_text(self.draft.role_name),
                inline=False,
            )
            embed.add_field(name="Màu", value=f"`{color_text}`", inline=False)
        embed.set_footer(
            text=(
                self.footer_note
                if self.footer_note
                else (
                    "Icon PNG từ tin nhắn gốc sẽ được sử dụng."
                    if self.icon_attached
                    else "Có thể dùng cú pháp cũ kèm PNG nếu muốn đặt icon."
                )
            )
        )
        return embed

    async def open_role_modal(
        self,
        interaction: discord.Interaction,
        *,
        color_spec: RoleColorSpec | None = None,
        gradient: bool | None = None,
    ) -> None:
        is_gradient = (
            color_spec.is_gradient
            if color_spec is not None
            else bool(gradient)
        )
        await interaction.response.send_modal(
            RoleDesignModal(
                self,
                color_spec=color_spec,
                gradient=is_gradient,
            )
        )

    async def confirm(self, interaction: discord.Interaction) -> None:
        if self.draft is None:
            await interaction.response.send_message(
                "Hãy chọn màu và nhập tên role trước khi xác nhận.",
                ephemeral=True,
            )
            return
        await self.submit(
            interaction,
            lambda: self.submitter(interaction, self.draft),
        )


ROOM_LIMIT_OPTIONS = (
    (0, "Không giới hạn", "Phù hợp cho phòng cộng đồng nhỏ"),
    (2, "2 người", "Phòng trò chuyện riêng"),
    (5, "5 người", "Nhóm nhỏ"),
    (10, "10 người", "Nhóm vừa"),
    (25, "25 người", "Nhóm lớn"),
    (50, "50 người", "Sự kiện nhỏ"),
    (99, "99 người", "Mức tối đa của Discord"),
)


class RoomLimitSelect(discord.ui.Select):
    def __init__(self, creator: "BoosterRoomCreatorView") -> None:
        self.creator = creator
        super().__init__(
            custom_id=ROOM_LIMIT_SELECT_CUSTOM_ID,
            placeholder="👥 Chọn giới hạn người tham gia",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=label,
                    value=str(limit),
                    description=description,
                    default=limit == creator.user_limit,
                )
                for limit, label, description in ROOM_LIMIT_OPTIONS
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            selected_limit = int(self.values[0])
        except (TypeError, ValueError):
            await interaction.response.send_message(
                "Giới hạn phòng không hợp lệ. Hãy mở lại bảng tạo phòng.",
                ephemeral=True,
            )
            return
        if selected_limit not in {item[0] for item in ROOM_LIMIT_OPTIONS}:
            await interaction.response.send_message(
                "Giới hạn phòng không còn khả dụng.",
                ephemeral=True,
            )
            return
        self.creator.user_limit = selected_limit
        if self.creator.draft is not None:
            self.creator.draft = replace(
                self.creator.draft,
                user_limit=selected_limit,
            )
        for option in self.options:
            option.default = option.value == str(selected_limit)
        await interaction.response.edit_message(
            embed=self.creator.build_embed(),
            view=self.creator,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class RoomNameButton(discord.ui.Button):
    def __init__(self, creator: "BoosterRoomCreatorView") -> None:
        self.creator = creator
        super().__init__(
            label="Nhập tên phòng",
            emoji="✏️",
            style=discord.ButtonStyle.primary,
            custom_id="booster-room:name",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(RoomNameModal(self.creator))


class ConfirmRoomButton(discord.ui.Button):
    def __init__(self, creator: "BoosterRoomCreatorView") -> None:
        self.creator = creator
        super().__init__(
            label="Tạo phòng",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id="booster-room:confirm",
            disabled=True,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.creator.confirm(interaction)


class RoomNameModal(discord.ui.Modal):
    def __init__(self, creator: "BoosterRoomCreatorView") -> None:
        super().__init__(
            title="Đặt tên custom room",
            timeout=BOOSTER_UI_TIMEOUT_SECONDS,
        )
        self.creator = creator
        self.room_name = discord.ui.TextInput(
            label="Tên phòng",
            placeholder="Ví dụ: Phòng chill của Kien",
            default=(creator.draft.room_name if creator.draft else None),
            min_length=1,
            max_length=100,
        )
        self.add_item(self.room_name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.creator.modal_check(interaction):
            return
        room_name = self.room_name.value.strip()
        if not room_name:
            await interaction.response.send_message(
                "Tên phòng không được chỉ chứa khoảng trắng.",
                ephemeral=True,
            )
            return
        self.creator.draft = RoomDesignDraft(
            room_name=room_name,
            user_limit=self.creator.user_limit,
        )
        self.creator.confirm_button.disabled = False
        await interaction.response.edit_message(
            embed=self.creator.build_embed(),
            view=self.creator,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class BoosterRoomCreatorView(BoosterSetupView):
    def __init__(
        self,
        *,
        author_id: int,
        submitter: RoomSubmitter,
    ) -> None:
        super().__init__(author_id=author_id, command_name="custom_room")
        self.submitter = submitter
        self.user_limit = 0
        self.draft: RoomDesignDraft | None = None
        self.limit_select = RoomLimitSelect(self)
        self.name_button = RoomNameButton(self)
        self.confirm_button = ConfirmRoomButton(self)
        self.cancel_button = CancelButton(self, resource="room")
        self.add_item(self.limit_select)
        self.add_item(self.name_button)
        self.add_item(self.confirm_button)
        self.add_item(self.cancel_button)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🔊 Tạo custom voice room",
            description=(
                "Chọn số người tối đa, nhập tên phòng rồi kiểm tra lại trước khi tạo."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Tên phòng",
            value=(
                _safe_text(self.draft.room_name)
                if self.draft is not None
                else "Chưa nhập"
            ),
            inline=False,
        )
        embed.add_field(
            name="Giới hạn",
            value=(
                "Không giới hạn"
                if self.user_limit == 0
                else f"{self.user_limit} người"
            ),
            inline=False,
        )
        embed.set_footer(text="Phòng sẽ được tạo trong category Booster đã cấu hình.")
        return embed

    async def confirm(self, interaction: discord.Interaction) -> None:
        if self.draft is None:
            await interaction.response.send_message(
                "Hãy nhập tên phòng trước khi xác nhận.",
                ephemeral=True,
            )
            return
        await self.submit(
            interaction,
            lambda: self.submitter(interaction, self.draft),
        )
