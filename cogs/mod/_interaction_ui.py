import asyncio
import inspect
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping

import discord
from discord.ext import commands

from cogs.mod._case_helpers import clean_case_reason


logger = logging.getLogger(__name__)

MODERATION_UI_TIMEOUT_SECONDS = 180
COMPONENT_TOKEN_PATTERN = re.compile(r"^[a-z0-9_-]+$")


class FormInputError(ValueError):
    """Raised when a moderation workflow field cannot be validated."""


@dataclass(frozen=True)
class ActionResult:
    completed: bool
    message: str
    private_message: str | None = None


@dataclass(frozen=True)
class PrefixModerationContext:
    """Actor information shared with moderation handlers by prefix commands."""

    guild: discord.Guild
    user: discord.Member


async def run_prefix_action(
    ctx: commands.Context,
    submitter: Callable[..., Awaitable[Any]],
    request: Any,
) -> None:
    """Run a complete legacy request through the same guarded action as the UI."""
    result = await submitter(PrefixModerationContext(ctx.guild, ctx.author), request)
    await ctx.reply(
        result.message,
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@dataclass(frozen=True)
class WorkflowTarget:
    id: int
    name: str


@dataclass(frozen=True)
class FormAnswer:
    value: Any
    display: str


@dataclass(frozen=True)
class ChoiceOption:
    key: str
    label: str
    value: Any
    description: str | None = None
    emoji: str | None = None
    display: str | None = None


@dataclass(frozen=True)
class ModalInput:
    title: str
    label: str
    placeholder: str | None = None
    min_length: int | None = 1
    max_length: int | None = 1000
    style: discord.TextStyle = discord.TextStyle.short
    parser: Callable[[str], FormAnswer | Any] | None = None
    button_label: str = "Nhập giá trị"
    button_emoji: str = "⌨️"


@dataclass(frozen=True)
class ReasonPreset:
    key: str
    label: str
    reason: str
    description: str | None = None
    emoji: str = "🛡️"


@dataclass(frozen=True)
class ReasonConfig:
    presets: tuple[ReasonPreset, ...]
    select_placeholder: str = "Chọn lý do"
    custom_title: str = "Nhập lý do"
    custom_label: str = "Lý do"
    custom_placeholder: str = "Mô tả ngắn gọn lý do thực hiện thao tác"


COMMON_REASON_CONFIG = ReasonConfig(
    presets=(
        ReasonPreset(
            "rules",
            "Vi phạm nội quy",
            "Vi phạm nội quy server",
            "Không tuân thủ quy định của server",
        ),
        ReasonPreset(
            "spam",
            "Spam hoặc quảng cáo",
            "Spam hoặc quảng cáo không được phép",
            "Spam, scam hoặc quảng cáo",
        ),
        ReasonPreset(
            "harassment",
            "Quấy rối hoặc xúc phạm",
            "Quấy rối, công kích hoặc xúc phạm thành viên khác",
            "Quấy rối hoặc công kích",
        ),
        ReasonPreset(
            "content",
            "Nội dung không phù hợp",
            "Đăng nội dung không phù hợp với cộng đồng",
            "Nội dung hoặc hành vi không phù hợp",
        ),
        ReasonPreset(
            "moderator",
            "Quyết định của moderator",
            "Thực hiện theo quyết định của đội ngũ moderator",
            "Quyết định quản trị",
        ),
    )
)


def safe_ui_text(value: object, *, max_length: int = 1000) -> str:
    escaped = discord.utils.escape_mentions(
        discord.utils.escape_markdown(str(value))
    )
    if len(escaped) <= max_length:
        return escaped
    if max_length <= 1:
        return escaped[:max_length]
    return f"{escaped[: max_length - 1]}…"


class FormField:
    """One sequential input step in a configurable moderation workflow."""

    def __init__(self, key: str, label: str) -> None:
        self.key = key
        self.label = label

    def build_items(
        self,
        workflow: "ConfigurableModerationView",
    ) -> list[discord.ui.Item[Any]]:
        raise NotImplementedError


class _ChoiceSelect(discord.ui.Select):
    def __init__(
        self,
        workflow: "ConfigurableModerationView",
        field: "ChoiceField",
    ) -> None:
        self.workflow = workflow
        self.field = field
        super().__init__(
            custom_id=workflow.component_id("field", field.key),
            placeholder=field.placeholder,
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=option.label[:100],
                    value=option.key,
                    description=(
                        option.description[:100]
                        if option.description is not None
                        else None
                    ),
                    emoji=option.emoji,
                )
                for option in field.options
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0] if self.values else None
        option = next(
            (item for item in self.field.options if item.key == selected),
            None,
        )
        if option is None:
            await interaction.response.send_message(
                "Lựa chọn này không còn hợp lệ. Hãy mở lại bảng thao tác.",
                ephemeral=True,
            )
            return
        await self.workflow.accept_answer(
            interaction,
            self.field.key,
            FormAnswer(
                option.value,
                option.display or option.label,
            ),
        )


class _OpenModalButton(discord.ui.Button):
    def __init__(
        self,
        workflow: "ConfigurableModerationView",
        field: "ModalField | ChoiceField",
        modal_input: ModalInput,
        *,
        row: int = 0,
    ) -> None:
        self.workflow = workflow
        self.field = field
        self.modal_input = modal_input
        super().__init__(
            label=modal_input.button_label[:80],
            emoji=modal_input.button_emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=workflow.component_id("modal", field.key),
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            _FieldModal(self.workflow, self.field, self.modal_input)
        )


class _FieldModal(discord.ui.Modal):
    def __init__(
        self,
        workflow: "ConfigurableModerationView",
        field: "ModalField | ChoiceField",
        modal_input: ModalInput,
    ) -> None:
        super().__init__(
            title=modal_input.title[:45],
            custom_id=workflow.component_id("modal-submit", field.key),
            timeout=MODERATION_UI_TIMEOUT_SECONDS,
        )
        self.workflow = workflow
        self.field = field
        self.modal_input = modal_input
        existing = workflow.values.get(field.key)
        default = str(existing.value) if existing is not None else None
        self.input = discord.ui.TextInput(
            label=modal_input.label[:45],
            placeholder=(
                modal_input.placeholder[:100]
                if modal_input.placeholder is not None
                else None
            ),
            default=default,
            min_length=modal_input.min_length,
            max_length=modal_input.max_length,
            style=modal_input.style,
            custom_id=workflow.component_id("input", field.key),
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.workflow.modal_check(interaction, self.field.key):
            return
        try:
            answer = self.field.parse_modal_value(
                self.input.value,
                self.modal_input,
            )
        except (FormInputError, ValueError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await self.workflow.accept_answer(
            interaction,
            self.field.key,
            answer,
        )


class ChoiceField(FormField):
    def __init__(
        self,
        key: str,
        label: str,
        options: Iterable[ChoiceOption],
        *,
        placeholder: str | None = None,
        custom_input: ModalInput | None = None,
    ) -> None:
        super().__init__(key, label)
        self.options = tuple(options)
        if not self.options:
            raise ValueError("ChoiceField requires at least one option")
        keys = [option.key for option in self.options]
        if len(set(keys)) != len(keys):
            raise ValueError("ChoiceField option keys must be unique")
        if any(not key or len(key) > 100 for key in keys):
            raise ValueError("ChoiceField option keys must contain 1-100 characters")
        self.placeholder = placeholder or f"Chọn {label.lower()}"
        self.custom_input = custom_input

    def build_items(
        self,
        workflow: "ConfigurableModerationView",
    ) -> list[discord.ui.Item[Any]]:
        items: list[discord.ui.Item[Any]] = [_ChoiceSelect(workflow, self)]
        if self.custom_input is not None:
            items.append(
                _OpenModalButton(
                    workflow,
                    self,
                    self.custom_input,
                    row=1,
                )
            )
        return items

    @staticmethod
    def parse_modal_value(value: str, modal_input: ModalInput) -> FormAnswer:
        return _parse_modal_answer(value, modal_input)


class ModalField(FormField):
    def __init__(self, key: str, label: str, modal_input: ModalInput) -> None:
        super().__init__(key, label)
        self.modal_input = modal_input

    def build_items(
        self,
        workflow: "ConfigurableModerationView",
    ) -> list[discord.ui.Item[Any]]:
        return [_OpenModalButton(workflow, self, self.modal_input)]

    @staticmethod
    def parse_modal_value(value: str, modal_input: ModalInput) -> FormAnswer:
        return _parse_modal_answer(value, modal_input)


class IntegerField(ModalField):
    def __init__(
        self,
        key: str,
        label: str,
        *,
        minimum: int,
        maximum: int,
        title: str | None = None,
        placeholder: str | None = None,
        button_label: str | None = None,
    ) -> None:
        def parse_integer(raw: str) -> FormAnswer:
            stripped = raw.strip()
            if not stripped.isdecimal():
                raise FormInputError(
                    f"{label} phải là số nguyên từ {minimum:,} đến {maximum:,}."
                )
            number = int(stripped)
            if not minimum <= number <= maximum:
                raise FormInputError(
                    f"{label} phải từ {minimum:,} đến {maximum:,}."
                )
            return FormAnswer(number, f"{number:,}")

        super().__init__(
            key,
            label,
            ModalInput(
                title=title or label,
                label=label,
                placeholder=placeholder,
                min_length=1,
                max_length=max(1, len(str(maximum))),
                parser=parse_integer,
                button_label=button_label or f"Nhập {label.lower()}",
            ),
        )


class _RoleSelect(discord.ui.RoleSelect):
    def __init__(
        self,
        workflow: "ConfigurableModerationView",
        field: "RoleField",
    ) -> None:
        self.workflow = workflow
        self.field = field
        super().__init__(
            custom_id=workflow.component_id("field", field.key),
            placeholder=field.placeholder,
            min_values=1,
            max_values=1,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        await self.workflow.accept_answer(
            interaction,
            self.field.key,
            FormAnswer(role.id, f"{role.name} (`{role.id}`)"),
        )


class RoleField(FormField):
    def __init__(
        self,
        key: str = "role_id",
        label: str = "Role",
        *,
        placeholder: str = "Chọn role",
    ) -> None:
        super().__init__(key, label)
        self.placeholder = placeholder

    def build_items(
        self,
        workflow: "ConfigurableModerationView",
    ) -> list[discord.ui.Item[Any]]:
        return [_RoleSelect(workflow, self)]


class _ChannelSelect(discord.ui.ChannelSelect):
    def __init__(
        self,
        workflow: "ConfigurableModerationView",
        field: "ChannelField",
    ) -> None:
        self.workflow = workflow
        self.field = field
        super().__init__(
            custom_id=workflow.component_id("field", field.key),
            placeholder=field.placeholder,
            channel_types=list(field.channel_types),
            min_values=1,
            max_values=1,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = self.values[0]
        await self.workflow.accept_answer(
            interaction,
            self.field.key,
            FormAnswer(channel.id, f"#{channel.name} (`{channel.id}`)"),
        )


class ChannelField(FormField):
    def __init__(
        self,
        key: str = "channel_id",
        label: str = "Kênh",
        *,
        placeholder: str = "Chọn kênh",
        channel_types: Iterable[discord.ChannelType] = (
            discord.ChannelType.text,
        ),
    ) -> None:
        super().__init__(key, label)
        self.placeholder = placeholder
        self.channel_types = tuple(channel_types)

    def build_items(
        self,
        workflow: "ConfigurableModerationView",
    ) -> list[discord.ui.Item[Any]]:
        return [_ChannelSelect(workflow, self)]


class _UserSelect(discord.ui.UserSelect):
    def __init__(
        self,
        workflow: "ConfigurableModerationView",
        field: "UserField",
    ) -> None:
        self.workflow = workflow
        self.field = field
        super().__init__(
            custom_id=workflow.component_id("field", field.key),
            placeholder=field.placeholder,
            min_values=1,
            max_values=1,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        user = self.values[0]
        if self.field.members_only and not isinstance(user, discord.Member):
            await interaction.response.send_message(
                "Hãy chọn một thành viên đang ở trong server.",
                ephemeral=True,
            )
            return
        await self.workflow.accept_answer(
            interaction,
            self.field.key,
            FormAnswer(user.id, f"{user} (`{user.id}`)"),
        )


class UserField(FormField):
    def __init__(
        self,
        key: str = "user_id",
        label: str = "Thành viên",
        *,
        placeholder: str = "Chọn thành viên",
        members_only: bool = True,
    ) -> None:
        super().__init__(key, label)
        self.placeholder = placeholder
        self.members_only = members_only

    def build_items(
        self,
        workflow: "ConfigurableModerationView",
    ) -> list[discord.ui.Item[Any]]:
        return [_UserSelect(workflow, self)]


def _parse_modal_answer(value: str, modal_input: ModalInput) -> FormAnswer:
    stripped = value.strip()
    if modal_input.min_length and not stripped:
        raise FormInputError(f"{modal_input.label} không được để trống.")
    parsed = modal_input.parser(stripped) if modal_input.parser else stripped
    if isinstance(parsed, FormAnswer):
        return parsed
    return FormAnswer(parsed, stripped)


class _ReasonSelect(discord.ui.Select):
    def __init__(self, workflow: "ConfigurableModerationView") -> None:
        self.workflow = workflow
        config = workflow.spec.reason
        if config is None:
            raise ValueError("Reason select requires a ReasonConfig")
        options = [
            discord.SelectOption(
                label=preset.label[:100],
                value=preset.key[:100],
                description=(
                    preset.description[:100]
                    if preset.description is not None
                    else None
                ),
                emoji=preset.emoji,
            )
            for preset in config.presets
        ]
        if workflow.initial_reason is not None:
            options.insert(
                0,
                discord.SelectOption(
                    label="Dùng lý do đã nhập trong lệnh",
                    value="provided",
                    description=safe_ui_text(
                        workflow.initial_reason,
                        max_length=100,
                    ),
                    emoji="⌨️",
                ),
            )
        super().__init__(
            custom_id=workflow.component_id("reason"),
            placeholder=config.select_placeholder[:150],
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0] if self.values else None
        if selected == "provided" and self.workflow.initial_reason is not None:
            reason = self.workflow.initial_reason
        else:
            config = self.workflow.spec.reason
            reason = next(
                (
                    preset.reason
                    for preset in config.presets
                    if preset.key == selected
                ),
                None,
            )
        if reason is None:
            await interaction.response.send_message(
                "Lý do này không còn hợp lệ. Hãy mở lại bảng thao tác.",
                ephemeral=True,
            )
            return
        await self.workflow.accept_reason(interaction, reason)


class _ReasonModal(discord.ui.Modal):
    def __init__(self, workflow: "ConfigurableModerationView") -> None:
        config = workflow.spec.reason
        if config is None:
            raise ValueError("Reason modal requires a ReasonConfig")
        super().__init__(
            title=config.custom_title[:45],
            custom_id=workflow.component_id("reason-modal"),
            timeout=MODERATION_UI_TIMEOUT_SECONDS,
        )
        self.workflow = workflow
        self.reason_input = discord.ui.TextInput(
            label=config.custom_label[:45],
            placeholder=config.custom_placeholder[:100],
            default=workflow.reason or workflow.initial_reason,
            min_length=1,
            max_length=1000,
            style=discord.TextStyle.paragraph,
            custom_id=workflow.component_id("reason-input"),
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.workflow.modal_check(interaction, "reason"):
            return
        reason = self.reason_input.value.strip()
        if not reason:
            await interaction.response.send_message(
                "Lý do không được chỉ chứa khoảng trắng.",
                ephemeral=True,
            )
            return
        await self.workflow.accept_reason(interaction, reason)


class _ReasonModalButton(discord.ui.Button):
    def __init__(self, workflow: "ConfigurableModerationView") -> None:
        self.workflow = workflow
        super().__init__(
            label="Nhập lý do khác",
            emoji="✏️",
            style=discord.ButtonStyle.secondary,
            custom_id=workflow.component_id("reason-custom"),
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_ReasonModal(self.workflow))


class _BackButton(discord.ui.Button):
    def __init__(self, workflow: "ConfigurableModerationView", row: int) -> None:
        self.workflow = workflow
        super().__init__(
            label="Quay lại",
            emoji="↩️",
            style=discord.ButtonStyle.secondary,
            custom_id=workflow.component_id("back"),
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.workflow.go_back(interaction)


class _ConfirmButton(discord.ui.Button):
    def __init__(self, workflow: "ConfigurableModerationView") -> None:
        self.workflow = workflow
        super().__init__(
            label=workflow.spec.confirm_label[:80],
            emoji="✅",
            style=workflow.spec.confirm_style,
            custom_id=workflow.component_id("confirm"),
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.workflow.confirm(interaction)


class _CancelButton(discord.ui.Button):
    def __init__(self, workflow: "ConfigurableModerationView", row: int) -> None:
        self.workflow = workflow
        super().__init__(
            label="Không, hủy" if workflow.step == "confirm" else "Hủy",
            emoji="✖️",
            style=discord.ButtonStyle.secondary,
            custom_id=workflow.component_id("cancel"),
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.workflow.cancel(interaction)


@dataclass(frozen=True)
class WorkflowSpec:
    namespace: str
    title: str
    action_text: str
    confirm_label: str
    fields: tuple[FormField, ...] = ()
    reason: ReasonConfig | None = None
    icon: str = "🛡️"
    color: int = 0x5865F2
    confirm_color: int = 0xFEE75C
    confirm_style: discord.ButtonStyle = discord.ButtonStyle.danger


Submitter = Callable[[discord.Interaction, Any], Awaitable[ActionResult]]
RequestBuilder = Callable[[Mapping[str, FormAnswer], str | None], Any]
LivePermissionCheck = Callable[
    [discord.Guild, discord.Member],
    str | None | Awaitable[str | None],
]


class ConfigurableModerationView(discord.ui.View):
    """Sequential form/reason workflow with a mandatory final confirmation."""

    def __init__(
        self,
        *,
        spec: WorkflowSpec,
        author_id: int,
        guild_id: int,
        target: WorkflowTarget | None,
        submitter: Submitter,
        request_builder: RequestBuilder,
        live_permission_check: LivePermissionCheck,
        initial_reason: str | None = None,
        initial_answers: Mapping[str, FormAnswer | Any] | None = None,
    ) -> None:
        super().__init__(timeout=MODERATION_UI_TIMEOUT_SECONDS)
        if len(spec.fields) + (1 if spec.reason else 0) > 5:
            raise ValueError("A moderation workflow cannot contain more than five steps")
        if (
            not spec.namespace
            or len(spec.namespace) > 40
            or COMPONENT_TOKEN_PATTERN.fullmatch(spec.namespace.lower()) is None
        ):
            raise ValueError(
                "Workflow namespace must use 1-40 letters, numbers, hyphens, or underscores"
            )
        field_keys = [field.key for field in spec.fields]
        if len(set(field_keys)) != len(field_keys):
            raise ValueError("Workflow field keys must be unique")
        if any(
            not key
            or len(key) > 40
            or COMPONENT_TOKEN_PATTERN.fullmatch(key.lower()) is None
            for key in field_keys
        ):
            raise ValueError(
                "Workflow field keys must use 1-40 letters, numbers, hyphens, or underscores"
            )
        for field in spec.fields:
            if isinstance(field, ChoiceField) and len(field.options) > 25:
                raise ValueError("Discord choice fields support at most 25 options")
        self.spec = spec
        self.author_id = author_id
        self.guild_id = guild_id
        self.target = target
        self.submitter = submitter
        self.request_builder = request_builder
        self.live_permission_check = live_permission_check
        self.initial_reason = (
            clean_case_reason(initial_reason)
            if initial_reason and initial_reason.strip()
            else None
        )
        if spec.reason is not None:
            preset_count = len(spec.reason.presets)
            max_presets = 24 if self.initial_reason is not None else 25
            if not 1 <= preset_count <= max_presets:
                raise ValueError(
                    f"ReasonConfig requires 1-{max_presets} presets for this workflow"
                )
            preset_keys = [preset.key for preset in spec.reason.presets]
            if len(set(preset_keys)) != len(preset_keys) or "provided" in preset_keys:
                raise ValueError("Reason preset keys must be unique and cannot use 'provided'")
            if any(not key or len(key) > 100 for key in preset_keys):
                raise ValueError("Reason preset keys must contain 1-100 characters")
        self.values: dict[str, FormAnswer] = {}
        for key, answer in (initial_answers or {}).items():
            self.values[key] = (
                answer
                if isinstance(answer, FormAnswer)
                else FormAnswer(answer, str(answer))
            )
        self.reason: str | None = None
        self.field_index = 0
        self.step = ""
        self.message: discord.Message | None = None
        self.completed = False
        self.submitting = False
        self._transition_lock = asyncio.Lock()
        self._show_initial_step()

    def component_id(self, *parts: str) -> str:
        component_id = f"{self.spec.namespace.lower()}:{':'.join(parts)}"
        if len(component_id) > 100:
            raise ValueError("Generated Discord component ID exceeds 100 characters")
        return component_id

    @property
    def total_steps(self) -> int:
        return len(self.spec.fields) + (1 if self.spec.reason else 0) + 1

    @property
    def current_step_number(self) -> int:
        if self.step.startswith("field:"):
            return self.field_index + 1
        if self.step == "reason":
            return len(self.spec.fields) + 1
        return self.total_steps

    def _show_initial_step(self) -> None:
        if self.spec.fields:
            self.field_index = 0
            self._show_field_step()
        elif self.spec.reason is not None:
            self._show_reason_step()
        else:
            self._show_confirm_step()

    def _show_field_step(self) -> None:
        field = self.spec.fields[self.field_index]
        self.step = f"field:{field.key}"
        self.clear_items()
        for item in field.build_items(self):
            self.add_item(item)
        if self.field_index > 0:
            self.add_item(_BackButton(self, row=2))
        self.add_item(_CancelButton(self, row=2))

    def _show_reason_step(self) -> None:
        self.step = "reason"
        self.clear_items()
        self.add_item(_ReasonSelect(self))
        self.add_item(_ReasonModalButton(self))
        if self.spec.fields:
            self.add_item(_BackButton(self, row=2))
        self.add_item(_CancelButton(self, row=2))

    def _show_confirm_step(self) -> None:
        self.step = "confirm"
        self.clear_items()
        self.add_item(_ConfirmButton(self))
        if self.spec.fields or self.spec.reason is not None:
            self.add_item(_BackButton(self, row=0))
        self.add_item(_CancelButton(self, row=1))

    def disable_all(self) -> None:
        for item in self.children:
            item.disabled = True

    def build_embed(self) -> discord.Embed:
        step = f"Bước {self.current_step_number}/{self.total_steps}"
        confirming = self.step == "confirm"
        title = safe_ui_text((
            f"⚠️ {self.spec.title} · {step}"
            if confirming
            else f"{self.spec.icon} {self.spec.title} · {step}"
        ), max_length=256)
        embed = discord.Embed(
            title=title,
            color=discord.Color(
                self.spec.confirm_color if confirming else self.spec.color
            ),
        )
        if self.target is not None:
            embed.description = (
                f"Mục tiêu: **{safe_ui_text(self.target.name, max_length=100)}** "
                f"(`{self.target.id}`)"
            )

        if self.step.startswith("field:"):
            field = self.spec.fields[self.field_index]
            prompt = f"Chọn hoặc nhập **{safe_ui_text(field.label, max_length=100)}**."
            embed.description = (
                f"{embed.description}\n{prompt}" if embed.description else prompt
            )
            existing = self.values.get(field.key)
            if existing is not None:
                embed.add_field(
                    name="Giá trị hiện tại",
                    value=safe_ui_text(existing.display, max_length=1024),
                    inline=False,
                )
            return embed

        if self.step == "reason":
            prompt = "Chọn một lý do có sẵn hoặc tự nhập lý do khác."
            embed.description = (
                f"{embed.description}\n{prompt}" if embed.description else prompt
            )
            if self.initial_reason is not None:
                embed.add_field(
                    name="Lý do đã nhập trong lệnh",
                    value=safe_ui_text(self.initial_reason, max_length=1024),
                    inline=False,
                )
            return embed

        confirmation = (
            f"Bạn có chắc muốn **{safe_ui_text(self.spec.action_text, max_length=250)}**? "
            "Thao tác chỉ được thực hiện sau khi bấm xác nhận."
        )
        embed.description = (
            f"{embed.description}\n{confirmation}"
            if embed.description
            else confirmation
        )
        for field in self.spec.fields:
            answer = self.values.get(field.key)
            embed.add_field(
                name=safe_ui_text(field.label, max_length=100),
                value=safe_ui_text(
                    answer.display if answer is not None else "Chưa chọn",
                    max_length=800,
                ),
                inline=False,
            )
        if self.spec.reason is not None:
            embed.add_field(
                name="Lý do",
                value=safe_ui_text(
                    self.reason or "Chưa chọn",
                    max_length=800,
                ),
                inline=False,
            )
        return embed

    async def _permission_denial(
        self,
        interaction: discord.Interaction,
    ) -> str | None:
        guild = interaction.guild
        if guild is None or guild.id != self.guild_id:
            return "Bảng thao tác này chỉ dùng được trong server đã mở bảng."
        try:
            denial = self.live_permission_check(guild, interaction.user)
            if inspect.isawaitable(denial):
                denial = await denial
            return denial
        except Exception:
            logger.exception(
                "Moderation UI permission check failed namespace=%s moderator=%s",
                self.spec.namespace,
                interaction.user.id,
            )
            return "Không thể kiểm tra quyền lúc này. Hãy thử lại sau."

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Chỉ moderator đã mở bảng này mới có thể sử dụng.",
                ephemeral=True,
            )
            return False
        if self.completed or self.is_finished():
            await interaction.response.send_message(
                "Bảng thao tác này đã hoàn tất hoặc hết hạn. Hãy gọi lại lệnh.",
                ephemeral=True,
            )
            return False
        if self.submitting:
            await interaction.response.send_message(
                "Thao tác đang được xử lý, vui lòng chờ.",
                ephemeral=True,
            )
            return False
        denial = await self._permission_denial(interaction)
        if denial is not None:
            await interaction.response.send_message(
                denial,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return False
        return True

    async def modal_check(
        self,
        interaction: discord.Interaction,
        expected_step: str,
    ) -> bool:
        if not await self.interaction_check(interaction):
            return False
        current = "reason" if self.step == "reason" else self.step.removeprefix("field:")
        if current != expected_step:
            await interaction.response.send_message(
                "Biểu mẫu này đã cũ. Hãy dùng nút đang hiển thị trên bảng.",
                ephemeral=True,
            )
            return False
        return True

    async def accept_answer(
        self,
        interaction: discord.Interaction,
        key: str,
        answer: FormAnswer,
    ) -> None:
        if self._transition_lock.locked():
            await interaction.response.send_message(
                "Bảng đang xử lý một tương tác khác. Hãy thử lại.",
                ephemeral=True,
            )
            return
        async with self._transition_lock:
            await self._accept_answer_unlocked(interaction, key, answer)

    async def _accept_answer_unlocked(
        self,
        interaction: discord.Interaction,
        key: str,
        answer: FormAnswer,
    ) -> None:
        if self.completed or self.is_finished():
            await interaction.response.send_message(
                "Bảng thao tác này đã hoàn tất hoặc hết hạn.",
                ephemeral=True,
            )
            return
        if self.step != f"field:{key}":
            await interaction.response.send_message(
                "Lựa chọn này đã cũ. Hãy dùng bước đang hiển thị.",
                ephemeral=True,
            )
            return
        self.values[key] = answer
        if self.field_index + 1 < len(self.spec.fields):
            self.field_index += 1
            self._show_field_step()
        elif self.spec.reason is not None:
            self._show_reason_step()
        else:
            self._show_confirm_step()
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def accept_reason(
        self,
        interaction: discord.Interaction,
        reason: str,
    ) -> None:
        if self._transition_lock.locked():
            await interaction.response.send_message(
                "Bảng đang xử lý một tương tác khác. Hãy thử lại.",
                ephemeral=True,
            )
            return
        async with self._transition_lock:
            await self._accept_reason_unlocked(interaction, reason)

    async def _accept_reason_unlocked(
        self,
        interaction: discord.Interaction,
        reason: str,
    ) -> None:
        if self.completed or self.is_finished():
            await interaction.response.send_message(
                "Bảng thao tác này đã hoàn tất hoặc hết hạn.",
                ephemeral=True,
            )
            return
        if self.step != "reason":
            await interaction.response.send_message(
                "Lựa chọn lý do này đã cũ. Hãy dùng bước đang hiển thị.",
                ephemeral=True,
            )
            return
        self.reason = clean_case_reason(reason)
        self._show_confirm_step()
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def go_back(self, interaction: discord.Interaction) -> None:
        if self._transition_lock.locked():
            await interaction.response.send_message(
                "Bảng đang xử lý một tương tác khác. Hãy thử lại.",
                ephemeral=True,
            )
            return
        async with self._transition_lock:
            await self._go_back_unlocked(interaction)

    async def _go_back_unlocked(self, interaction: discord.Interaction) -> None:
        if self.completed or self.is_finished():
            await interaction.response.send_message(
                "Bảng thao tác này đã hoàn tất hoặc hết hạn.",
                ephemeral=True,
            )
            return
        if self.step == "confirm":
            if self.spec.reason is not None:
                self._show_reason_step()
            elif self.spec.fields:
                self.field_index = len(self.spec.fields) - 1
                self._show_field_step()
        elif self.step == "reason" and self.spec.fields:
            self.field_index = len(self.spec.fields) - 1
            self._show_field_step()
        elif self.step.startswith("field:") and self.field_index > 0:
            self.field_index -= 1
            self._show_field_step()
        else:
            await interaction.response.send_message(
                "Bạn đang ở bước đầu tiên.",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def cancel(self, interaction: discord.Interaction) -> None:
        if self._transition_lock.locked():
            await interaction.response.send_message(
                "Bảng đang xử lý một tương tác khác. Hãy thử lại.",
                ephemeral=True,
            )
            return
        async with self._transition_lock:
            await self._cancel_unlocked(interaction)

    async def _cancel_unlocked(self, interaction: discord.Interaction) -> None:
        if self.completed or self.is_finished():
            await interaction.response.send_message(
                "Bảng thao tác này đã hoàn tất hoặc hết hạn.",
                ephemeral=True,
            )
            return
        self.completed = True
        self.disable_all()
        self.stop()
        target = (
            f" cho {safe_ui_text(self.target.name, max_length=100)}"
            if self.target is not None
            else ""
        )
        await interaction.response.edit_message(
            content=f"Đã hủy thao tác {safe_ui_text(self.spec.action_text)}{target}.",
            embed=None,
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    def _has_all_inputs(self) -> bool:
        return all(field.key in self.values for field in self.spec.fields) and (
            self.spec.reason is None or self.reason is not None
        )

    async def confirm(self, interaction: discord.Interaction) -> None:
        if self._transition_lock.locked():
            await interaction.response.send_message(
                "Bảng đang xử lý một tương tác khác. Hãy thử lại.",
                ephemeral=True,
            )
            return
        async with self._transition_lock:
            await self._confirm_unlocked(interaction)

    async def _confirm_unlocked(self, interaction: discord.Interaction) -> None:
        if self.completed or self.is_finished():
            await interaction.response.send_message(
                "Bảng thao tác này đã hoàn tất hoặc hết hạn.",
                ephemeral=True,
            )
            return
        if self.submitting:
            await interaction.response.send_message(
                "Thao tác đang được xử lý, vui lòng chờ.",
                ephemeral=True,
            )
            return
        if self.step != "confirm" or not self._has_all_inputs():
            await interaction.response.send_message(
                "Bảng thao tác chưa đủ thông tin. Hãy hoàn tất các bước trước.",
                ephemeral=True,
            )
            return

        try:
            request = self.request_builder(dict(self.values), self.reason)
        except (FormInputError, ValueError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        self.submitting = True
        try:
            await interaction.response.defer()
        except Exception:
            self.submitting = False
            raise

        try:
            result = await self.submitter(interaction, request)
        except Exception:
            self.submitting = False
            logger.exception(
                "Unexpected moderation UI submit failure namespace=%s moderator=%s",
                self.spec.namespace,
                self.author_id,
            )
            try:
                await interaction.followup.send(
                    "Đã xảy ra lỗi ngoài dự kiến. Hãy kiểm tra Audit Log trước khi thử lại.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.exception("Could not report moderation UI submit failure")
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
                logger.exception("Could not report retryable moderation UI failure")
            return

        self.completed = True
        self.disable_all()
        try:
            updated = False
            try:
                await interaction.edit_original_response(
                    content=result.message,
                    embed=None,
                    view=self,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                updated = True
            except discord.HTTPException:
                logger.exception(
                    "Could not update completed moderation UI namespace=%s",
                    self.spec.namespace,
                )
            if not updated and self.message is not None:
                try:
                    await self.message.edit(
                        content=result.message,
                        embed=None,
                        view=self,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    updated = True
                except discord.HTTPException:
                    logger.exception("Could not update stored moderation UI message")
            if not updated:
                try:
                    await interaction.followup.send(
                        result.message,
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    logger.exception("Could not deliver moderation UI result")
            if result.private_message is not None:
                try:
                    await interaction.followup.send(
                        result.private_message,
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    logger.exception("Could not deliver private moderation UI result")
        finally:
            self.stop()

    async def on_timeout(self) -> None:
        self.disable_all()
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("Could not disable expired moderation UI", exc_info=True)
