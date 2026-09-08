"""Read-only, feature-aware diagnostics shared by setup and the status dashboard."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import math
import os
from typing import Any
from urllib.parse import urlsplit

import discord
from discord.ext import commands
import pymongo
from pymongo.errors import PyMongoError

from cogs._beta_function import get_beta_role_ids, is_beta_function
from cogs._hash_verification import (
    VerificationConfigurationError,
    verification_keyring_from_bot,
)
from cogs.operation._setup_helpers import SetupCheck, parse_discord_id


DATABASE_TIMEOUT_SECONDS = 5
MESSAGE_PERMISSIONS = ("view_channel", "send_messages", "embed_links")
GAME_PERMISSIONS = MESSAGE_PERMISSIONS + ("read_message_history", "add_reactions")
CORE_ENVIRONMENT_VARIABLES = (
    "DISCORD_TOKEN", "DB_METHOD", "DB_USERNAME", "DB_PASSWORD", "DB_HOST",
)
PROOF_MODULES = frozenset({
    "cogs.utils.quote", "cogs.funny_things.femboy_card", "cogs.utils.hash_verify",
})
BOORU_ENVIRONMENT = {
    "cogs.nsfw.r34": (
        "RULE34_API_KEY", "RULE34_USER_ID", "SECOND_RULE34_API_KEY",
        "RULE34_SECOND_USER_ID", "RULE34_API_URL",
    ),
    "cogs.nsfw.gelbooru": (
        "GELBOORU_API_KEY", "GELBOORU_USER_ID", "SECOND_GELBOORU_API_KEY",
        "GELBOORU_SECOND_USER_ID", "GELBOORU_API_URL",
    ),
}


@dataclass(frozen=True)
class ChannelRequirement:
    key: str
    modules: tuple[str, ...]
    permissions: tuple[str, ...] = MESSAGE_PERMISSIONS
    kind: str = "message"
    array: bool = False


CHANNEL_REQUIREMENTS = (
    ChannelRequirement("JOIN_CHANNEL", ("cogs.announcement.welcome",)),
    ChannelRequirement("RULE_CHANNEL", ("cogs.announcement.welcome",), (), "reference"),
    ChannelRequirement("ROLE_CHANNEL", ("cogs.announcement.welcome",), (), "reference"),
    ChannelRequirement("BYE_CHANNEL", ("cogs.announcement.goodbye",)),
    ChannelRequirement("BIRTHDAY_CHANNEL", ("cogs.funny_things.birthday",)),
    ChannelRequirement(
        "HIGHLIGHT_CHANNEL", ("cogs.utils.highlight",),
        MESSAGE_PERMISSIONS + ("attach_files",),
    ),
    ChannelRequirement(
        "AREA_51_CHANNEL_ID", ("cogs.mod.area_51_guard",),
        MESSAGE_PERMISSIONS + ("manage_messages",),
    ),
    ChannelRequirement(
        "WORD_CONNECT_GAMES_CHANNELS", ("cogs.minigames.word_connect.word_connect",),
        GAME_PERMISSIONS, array=True,
    ),
    ChannelRequirement(
        "VIETNAMESE_KING_GAMES_CHANNELS",
        ("cogs.minigames.vietnamese_king.vietnamese_king",),
        GAME_PERMISSIONS, array=True,
    ),
    ChannelRequirement(
        "BOOSTER_CUSTOM_VOICE_CATEGORY_ID", ("cogs.booster.create_custom_room",),
        ("manage_channels", "manage_roles"), "category",
    ),
)
# The final value determines whether the bot must be able to assign/manage the role.
ROLE_REQUIREMENTS = (
    ("FALLEN_FEMBOY_ROLE_ID", ("cogs.mod.verified",), True),
    ("BOOSTER_CUSTOM_ROLE_ANCHOR_ID", ("cogs.booster.create_custom_role",), True),
    ("KING_ROLE_ID", ("cogs.interaction.nsfw_interaction", "cogs.interaction.nsfw_super_user"), False),
    ("QUEEN_ROLE_ID", ("cogs.interaction.nsfw_interaction", "cogs.interaction.nsfw_super_user"), False),
)
GUILD_PERMISSION_MODULES = {
    "manage_roles": (
        "cogs.mod.role", "cogs.mod.mute", "cogs.mod.softban", "cogs.mod.verified",
        "cogs.onboarding.role_exam", "cogs.economy.shop",
        "cogs.booster.create_custom_role", "cogs.booster.update_custom_role",
        "cogs.booster.create_custom_room", "cogs.booster.janitor_unboosted",
    ),
    "manage_channels": ("cogs.booster.create_custom_room", "cogs.booster.janitor_unboosted"),
    "kick_members": ("cogs.mod.kick",),
    "ban_members": ("cogs.mod.ban", "cogs.mod.unban", "cogs.mod.area_51_guard"),
    "moderate_members": ("cogs.mod.timeout",),
    "manage_nicknames": ("cogs.mod.nickname",),
    "view_audit_log": ("cogs.announcement.goodbye",),
}
CACHED_GLOBAL_SETTINGS = (
    ("WelcomeCog", "join_channel", "JOIN_CHANNEL"),
    ("WelcomeCog", "rule_channel", "RULE_CHANNEL"),
    ("WelcomeCog", "role_channel", "ROLE_CHANNEL"),
    ("GoodbyeCog", "bye_channel", "BYE_CHANNEL"),
    ("BirthdayCog", "birthday_channel_id", "BIRTHDAY_CHANNEL"),
    ("WordConnectCommandCog", "channel_games", "WORD_CONNECT_GAMES_CHANNELS"),
    ("VietnameseKingCog", "VIETNAMESE_KING_GAMES_CHANNELS", "VIETNAMESE_KING_GAMES_CHANNELS"),
    ("NSFWInteractionCog", "KING_ROLE_ID", "KING_ROLE_ID"),
    ("NSFWInteractionCog", "QUEEN_ROLE_ID", "QUEEN_ROLE_ID"),
    ("NSFWSuperUser", "KING_ROLE_ID", "KING_ROLE_ID"),
    ("NSFWSuperUser", "QUEEN_ROLE_ID", "QUEEN_ROLE_ID"),
)


def _active_modules(bot: commands.Bot) -> set[str]:
    """Loaded modules remain authoritative even after process flags change."""
    selected = getattr(bot, "selected_extensions", ())
    loaded = getattr(bot, "extensions", {})
    return {module for module in (*selected, *loaded) if isinstance(module, str)}


def _enabled(modules: set[str], required: tuple[str, ...]) -> bool:
    return any(module in modules for module in required)


def _configured(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _valid_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


def _check_environment(bot: commands.Bot, modules: set[str]) -> list[SetupCheck]:
    checks = []
    required = list(CORE_ENVIRONMENT_VARIABLES)
    if "cogs.general" in modules:
        required.extend(("INVITE_LINK", "VERIFY_CHANNEL"))
    for module, names in BOORU_ENVIRONMENT.items():
        if module in modules:
            required.extend(names[:-1])
    for name in required:
        present = _configured(os.environ.get(name))
        checks.append(SetupCheck(
            "ok" if present else "warning", f"Environment: {name}",
            "Đã đặt." if present else "Biến môi trường cần cho tính năng đang bật chưa được đặt.",
            None if present else f"Đặt {name} rồi khởi động lại bot.",
        ))

    validators = {
        "DB_METHOD": lambda value: value in {"mongodb", "mongodb+srv"},
        "ENVIRONMENT": lambda value: value.strip().lower() in {"development", "production"},
        "COMMAND_PREFIX": lambda value: bool(value.strip()),
        "DB_NAME": lambda value: bool(value.strip()),
    }
    if "cogs.general" in modules:
        validators["INVITE_LINK"] = _valid_url
    for module, names in BOORU_ENVIRONMENT.items():
        if module in modules:
            validators[names[-1]] = _valid_url
            for name in (names[1], names[3]):
                validators[name] = lambda value: parse_discord_id(value) is not None
    for name, validator in validators.items():
        value = os.environ.get(name)
        if name in required and not _configured(value):
            continue
        if value is not None and not validator(value):
            checks.append(SetupCheck(
                "warning", f"Environment: {name}", "Giá trị cấu hình không hợp lệ.",
                f"Sửa {name} theo README rồi khởi động lại bot.",
            ))
    if modules & PROOF_MODULES:
        try:
            verification_keyring_from_bot(bot)
        except VerificationConfigurationError:
            checks.append(SetupCheck(
                "error", "Content verification",
                "Cấu hình khóa ký hiện tại bị thiếu hoặc không hợp lệ.",
                "Kiểm tra CONTENT_VERIFICATION_KEYS_JSON và "
                "CONTENT_VERIFICATION_ACTIVE_KEY_ID rồi khởi động lại bot.",
            ))
        else:
            checks.append(SetupCheck("ok", "Content verification", "Khóa ký hiện tại hợp lệ."))
    if "cogs.general" in modules:
        general = bot.get_cog("GeneralCog")
        if general is not None and getattr(general, "invite_link", None) != os.environ.get("INVITE_LINK"):
            checks.append(SetupCheck(
                "warning", "Runtime config: INVITE_LINK",
                "Lệnh invite đang dùng cấu hình khác với biến môi trường hiện tại.",
                "Nạp lại cogs.general hoặc khởi động lại bot để áp dụng cấu hình.",
            ))
    return checks


def _check_runtime(bot: commands.Bot, modules: set[str]) -> list[SetupCheck]:
    checks = []
    loaded = getattr(bot, "extensions", {})
    failures = getattr(bot, "extension_load_failures", {})
    for module in sorted(modules - set(loaded)):
        detail = "Extension được chọn nhưng chưa tải thành công."
        failure_type = failures.get(module)
        if (
            isinstance(failure_type, str) and len(failure_type) <= 80
            and failure_type.isascii() and failure_type.isidentifier()
        ):
            detail += f" Loại lỗi: {failure_type}."
        checks.append(SetupCheck(
            "error", f"Extension: {module}", detail,
            "Kiểm tra log khởi động và cấu hình của extension, rồi khởi động lại bot.",
        ))
    intents = getattr(bot, "intents", None)
    required_intents = {"guilds", "guild_messages", "message_content"}
    if _enabled(modules, (
        "cogs.announcement.welcome", "cogs.announcement.goodbye",
        "cogs.funny_things.birthday", "cogs.booster.janitor_unboosted",
        "cogs.operation.operation_dashboard",
    )):
        required_intents.add("members")
    if _enabled(modules, (
        "cogs.utils.highlight", "cogs.utils.vote", "cogs.minigames.sicbo.sicbo",
    )):
        required_intents.add("guild_reactions")
    for name in sorted(required_intents):
        granted = bool(getattr(intents, name, False))
        checks.append(SetupCheck(
            "ok" if granted else "warning", f"Intent: {name}",
            "Đã bật trong runtime." if granted else "Intent cần cho tính năng hiện tại chưa bật.",
            None if granted else "Bật intent trong cấu hình bot; kiểm tra Developer Portal nếu cần.",
        ))
    return checks


def _check_guild_permissions(guild: discord.Guild, modules: set[str]) -> list[SetupCheck]:
    member = guild.me
    if member is None:
        return [SetupCheck(
            "warning", "Bot member", "Chưa có thành viên bot trong cache của server.",
            "Thử lại khi bot kết nối và nạp cache xong.",
        )]
    required = {
        name for name, dependencies in GUILD_PERMISSION_MODULES.items()
        if _enabled(modules, dependencies)
    }
    permissions = member.guild_permissions
    checks = []
    for name in sorted(required):
        granted = bool(getattr(permissions, name, False))
        affected = ", ".join(
            module for module in GUILD_PERMISSION_MODULES[name] if module in modules
        )
        checks.append(SetupCheck(
            "ok" if granted else "warning", f"Guild permission: {name}",
            "Đã cấp ở cấp server." if granted else
            f"Thiếu quyền cấp server cần cho {affected}.",
            None if granted else f"Kiểm tra quyền {name} của role bot cho tính năng đang bật.",
        ))
    return checks


def _resolve_channel(guild: discord.Guild, channel_id: int) -> Any:
    getter = getattr(guild, "get_channel_or_thread", None)
    return getter(channel_id) if callable(getter) else guild.get_channel(channel_id)


def _other_guild_channel(bot: commands.Bot, guild: discord.Guild, channel_id: int) -> bool:
    channel = bot.get_channel(channel_id)
    other_guild = getattr(channel, "guild", None)
    return other_guild is not None and other_guild.id != guild.id


def _check_effective_permissions(
    guild: discord.Guild, channel: Any, name: str, required: tuple[str, ...],
) -> list[SetupCheck]:
    if guild.me is None or not required:
        return []
    permissions = channel.permissions_for(guild.me)
    missing = []
    for permission in required:
        actual = (
            "send_messages_in_threads"
            if permission == "send_messages" and isinstance(channel, discord.Thread)
            else permission
        )
        if not getattr(permissions, actual, False):
            missing.append(actual)
    if missing:
        return [SetupCheck(
            "error", name, "Quyền hiệu lực tại kênh đang thiếu: " + ", ".join(missing) + ".",
            "Kiểm tra role bot và permission overwrite của kênh/category.",
        )]
    return [SetupCheck("ok", name, "Bot có đủ quyền cần thiết tại kênh.")]


def _check_channel_value(
    bot: commands.Bot, guild: discord.Guild, requirement: ChannelRequirement, value: Any,
) -> list[SetupCheck]:
    values = value if requirement.array and isinstance(value, list) else [value]
    if not values or all(not _configured(item) for item in values):
        return [SetupCheck(
            "warning", requirement.key, "Chưa đặt kênh cho tính năng đang bật.",
            f"setting set_variable {requirement.key}",
        )]
    checks = []
    seen = set()
    for item in values:
        channel_id = parse_discord_id(item)
        if channel_id is None:
            checks.append(SetupCheck(
                "error", requirement.key, "Có channel ID sai định dạng.",
                f"Sửa {requirement.key} bằng ID nguyên dương.",
            ))
            continue
        if channel_id in seen:
            continue
        seen.add(channel_id)
        channel = _resolve_channel(guild, channel_id)
        if channel is None:
            if _other_guild_channel(bot, guild, channel_id):
                continue
            checks.append(SetupCheck(
                "warning", requirement.key,
                f"Channel ID {channel_id} chưa có trong cache server hiện tại.",
                "Kiểm tra ID, quyền truy cập và thử lại sau khi cache được nạp.",
            ))
            continue
        valid_type = (
            isinstance(channel, discord.CategoryChannel)
            if requirement.kind == "category" else
            isinstance(channel, discord.TextChannel)
            if requirement.kind == "text" else
            True if requirement.kind == "reference" else
            isinstance(channel, discord.abc.Messageable)
        )
        name = f"{requirement.key} ({channel_id})"
        if not valid_type:
            checks.append(SetupCheck(
                "error", name, "Loại kênh không phù hợp với tính năng.",
                "Chọn text channel." if requirement.kind == "text" else
                "Chọn category cho phòng booster hoặc kênh có thể gửi tin cho thông báo.",
            ))
            continue
        checks.extend(_check_effective_permissions(guild, channel, name, requirement.permissions))
        if not requirement.permissions:
            checks.append(SetupCheck("ok", name, "Kênh tham chiếu có trong server."))
    return checks


def _check_role_value(
    bot: commands.Bot, guild: discord.Guild, name: str, value: Any,
    *, assignable: bool,
) -> list[SetupCheck]:
    role_id = parse_discord_id(value)
    if role_id is None:
        return [SetupCheck(
            "warning" if not _configured(value) else "error", name,
            "Role ID chưa đặt hoặc sai định dạng.", f"Kiểm tra {name} trong settings.",
        )]
    role = guild.get_role(role_id)
    if role is None:
        if any(
            other.id != guild.id and other.get_role(role_id) is not None
            for other in getattr(bot, "guilds", ())
        ):
            return []
        return [SetupCheck(
            "warning", name, f"Role ID {role_id} chưa có trong cache server hiện tại.",
            "Kiểm tra ID và thử lại sau khi cache được nạp.",
        )]
    if assignable:
        if role.managed or role.is_default():
            return [SetupCheck(
                "error", name, "Bot không thể quản lý role mặc định hoặc role của integration.",
                "Chọn role thường mà bot có thể quản lý.",
            )]
        if guild.me is not None and role >= guild.me.top_role:
            return [SetupCheck(
                "error", name, "Role cần quản lý cao hơn hoặc bằng role cao nhất của bot.",
                "Di chuyển role bot lên trên role này.",
            )]
    return [SetupCheck("ok", name, "Role có trong server và phù hợp với tính năng.")]


def _normal_ids(value: Any) -> tuple[int | None, ...]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    parsed = {parse_discord_id(item) for item in values if _configured(item)}
    return tuple(sorted(parsed, key=lambda item: item or 0))


def _local_ids(
    bot: commands.Bot, guild: discord.Guild, value: Any, *, role: bool,
) -> tuple[int | None, ...]:
    """Ignore configuration changes that affect only another known guild."""
    ids = []
    for identifier in _normal_ids(value):
        if identifier is None:
            ids.append(identifier)
        elif role:
            if not any(
                other.id != guild.id and other.get_role(identifier) is not None
                for other in getattr(bot, "guilds", ())
            ):
                ids.append(identifier)
        elif not _other_guild_channel(bot, guild, identifier):
            ids.append(identifier)
    return tuple(ids)


def _invalid_beta_entries(value: Any) -> bool:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    for item in values:
        if isinstance(item, bool) or item is None:
            return True
        tokens = str(item).replace(",", " ").split()
        if not tokens or any(parse_discord_id(token) is None for token in tokens):
            return True
    return False


def _check_global_settings(
    bot: commands.Bot, guild: discord.Guild, modules: set[str],
) -> list[SetupCheck]:
    beta_enabled = any(is_beta_function(command) for command in bot.walk_commands()) or (
        "cogs.operation.heartbeat" in modules
    )
    needs_variables = beta_enabled or any(
        _enabled(modules, requirement.modules) for requirement in CHANNEL_REQUIREMENTS
    ) or any(
        name != "BOOSTER_CUSTOM_ROLE_ANCHOR_ID" and _enabled(modules, dependencies)
        for name, dependencies, _ in ROLE_REQUIREMENTS
    )
    variables = getattr(bot, "global_vars", None)
    if not isinstance(variables, Mapping):
        if not needs_variables:
            return []
        return [SetupCheck(
            "warning", "Global variables", "Settings chưa nạp bot.global_vars.",
            "Bật cogs.settings.variable_setting và kiểm tra MongoDB rồi khởi động lại bot.",
        )]
    checks = []
    for requirement in CHANNEL_REQUIREMENTS:
        if _enabled(modules, requirement.modules):
            checks.extend(_check_channel_value(
                bot, guild, requirement, variables.get(requirement.key),
            ))
    for name, dependencies, assignable in ROLE_REQUIREMENTS:
        if _enabled(modules, dependencies):
            if name == "BOOSTER_CUSTOM_ROLE_ANCHOR_ID" and not _configured(variables.get(name)):
                continue
            checks.extend(_check_role_value(
                bot, guild, name, variables.get(name), assignable=assignable,
            ))
    if beta_enabled:
        role_ids = sorted(get_beta_role_ids(bot))
        if not role_ids:
            checks.append(SetupCheck(
                "warning", "BETA_ROLE_IDS", "Chưa có Beta role ID hợp lệ.",
                "setting set_variable BETA_ROLE_IDS",
            ))
        elif _invalid_beta_entries(variables.get("BETA_ROLE_IDS")):
            checks.append(SetupCheck(
                "warning", "BETA_ROLE_IDS", "Có phần tử Beta role ID sai định dạng bị bỏ qua.",
                "Chỉ lưu role ID nguyên dương trong BETA_ROLE_IDS.",
            ))
        for role_id in role_ids:
            checks.extend(_check_role_value(
                bot, guild, "BETA_ROLE_IDS", role_id, assignable=False,
            ))
    if "cogs.mod.area_51_guard" in modules:
        raw_hours = variables.get("AREA_51_PRUNE_HOURS")
        if _configured(raw_hours):
            try:
                hours = float(raw_hours)
                valid = not isinstance(raw_hours, bool) and math.isfinite(hours) and 0 <= hours <= 168
            except (ValueError, TypeError, OverflowError):
                valid = False
            if not valid:
                checks.append(SetupCheck(
                    "warning", "AREA_51_PRUNE_HOURS", "Số giờ không hợp lệ hoặc ngoài khoảng 0–168.",
                    "Đặt số giờ trong khoảng 0–168; bỏ trống để dùng mặc định 1 giờ.",
                ))
    for cog_name, attribute, key in CACHED_GLOBAL_SETTINGS:
        cog = bot.get_cog(cog_name)
        if cog is not None and hasattr(cog, attribute):
            is_role = key in {"KING_ROLE_ID", "QUEEN_ROLE_ID"}
            current = _local_ids(bot, guild, getattr(cog, attribute), role=is_role)
            configured = _local_ids(bot, guild, variables.get(key), role=is_role)
            if current != configured:
                checks.append(SetupCheck(
                    "warning", f"Runtime config: {key}",
                    f"{cog_name} đang dùng cấu hình khác với settings hiện tại.",
                    "Nạp lại extension hoặc khởi động lại bot để áp dụng settings đã lưu.",
                ))
    return checks


def _read_database(bot: commands.Bot, guild_id: int, *, check_cases: bool) -> Any:
    """Apply the driver's timeout inside the worker, including server selection."""
    with pymongo.timeout(DATABASE_TIMEOUT_SECONDS):
        bot.db.command("ping")
        if check_cases:
            return bot.db["moderation_config"].find_one(
                {"guild_id": guild_id}, {"_id": 0, "log_channel_id": 1},
            )
    return None


async def collect_doctor_checks(
    bot: commands.Bot, guild: discord.Guild, channel: Any,
) -> list[SetupCheck]:
    """Inspect current runtime and configured targets without mutating or fetching Discord state."""
    modules = _active_modules(bot)
    checks = _check_environment(bot, modules)
    checks.extend(_check_runtime(bot, modules))
    checks.extend(_check_guild_permissions(guild, modules))
    if channel is not None and getattr(getattr(channel, "guild", None), "id", None) == guild.id:
        checks.extend(_check_effective_permissions(
            guild, channel, "Kênh mở dashboard", MESSAGE_PERMISSIONS,
        ))
    checks.extend(_check_global_settings(bot, guild, modules))
    if "cogs.general" in modules and _configured(os.environ.get("VERIFY_CHANNEL")):
        verify_checks = _check_channel_value(
            bot, guild, ChannelRequirement("VERIFY_CHANNEL", (), (), "reference"),
            os.environ.get("VERIFY_CHANNEL"),
        )
        checks.extend(
            SetupCheck(
                check.level, check.name, check.detail,
                "Kiểm tra biến môi trường VERIFY_CHANNEL rồi khởi động lại bot."
                if check.fix else None,
            )
            for check in verify_checks
        )
    if getattr(bot, "db", None) is None:
        checks.append(SetupCheck(
            "error", "MongoDB", "Runtime chưa có kết nối database.",
            "Kiểm tra cấu hình MongoDB rồi khởi động lại bot.",
        ))
        return checks
    try:
        moderation = await asyncio.to_thread(
            _read_database, bot, guild.id, check_cases="cogs.mod.cases" in modules,
        )
    except (PyMongoError, TimeoutError):
        checks.append(SetupCheck(
            "error", "MongoDB", "Không thể kết nối hoặc truy vấn database (giới hạn 5 giây).",
            "Kiểm tra cấu hình MongoDB, quyền truy cập và kết nối mạng.",
        ))
    else:
        checks.append(SetupCheck("ok", "MongoDB", "Kết nối thành công."))
        if isinstance(moderation, Mapping) and _configured(moderation.get("log_channel_id")):
            log_checks = _check_channel_value(
                bot, guild, ChannelRequirement("Moderation log", (), kind="text"),
                moderation["log_channel_id"],
            )
            checks.extend(
                SetupCheck(
                    check.level, check.name, check.detail,
                    "Kiểm tra quyền kênh hoặc dùng case log_channel #channel để đổi kênh."
                    if check.fix else None,
                )
                for check in log_checks
            )
    return checks
