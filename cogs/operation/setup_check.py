import logging

import discord
from discord.ext import commands
from pymongo.errors import PyMongoError

from cogs._beta_function import BETA_ROLE_IDS_SETTING, get_beta_role_ids
from cogs.operation._setup_helpers import (
    SetupCheck,
    parse_discord_id,
    summarize_checks,
)


logger = logging.getLogger(__name__)

CHANNEL_VARIABLES = {
    "JOIN_CHANNEL": "Thông báo member tham gia",
    "RULE_CHANNEL": "Nội quy",
    "ROLE_CHANNEL": "Chọn role",
    "BYE_CHANNEL": "Thông báo rời/kick/ban",
    "BIRTHDAY_CHANNEL": "Thông báo sinh nhật",
    "HIGHLIGHT_CHANNEL": "Kênh highlight",
    "AREA_51_CHANNEL_ID": "Area 51 guard",
}
ROLE_VARIABLES = {
    "FALLEN_FEMBOY_ROLE_ID": "Verified role",
    "KING_ROLE_ID": "NSFW King role",
    "QUEEN_ROLE_ID": "NSFW Queen role",
    "BOOSTER_CUSTOM_ROLE_ANCHOR_ID": "Booster role anchor",
}
CHANNEL_ARRAY_VARIABLES = {
    "WORD_CONNECT_GAMES_CHANNELS": "Nối từ",
    "VIETNAMESE_KING_GAMES_CHANNELS": "Vua Tiếng Việt",
}
FEATURE_COGS = {
    "ShopCog": ("Trap Coin shop", "cogs.economy.shop"),
    "ModerationCasesCog": ("Moderation cases", "cogs.mod.cases"),
}


class SetupCheckCog(commands.Cog):
    """Read-only diagnostics for configuration, IDs, and Discord permissions."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db

    def _check_bot_permissions(self, guild: discord.Guild) -> list[SetupCheck]:
        bot_member = guild.me
        permissions = bot_member.guild_permissions
        required = {
            "view_channel": "View Channels",
            "send_messages": "Send Messages",
            "embed_links": "Embed Links",
            "read_message_history": "Read Message History",
        }
        feature_permissions = {
            "create_instant_invite": "Create Invite",
            "manage_roles": "Manage Roles",
            "manage_channels": "Manage Channels",
            "manage_messages": "Manage Messages",
            "moderate_members": "Timeout Members",
            "kick_members": "Kick Members",
            "ban_members": "Ban Members",
            "view_audit_log": "View Audit Log",
            "attach_files": "Attach Files",
        }
        checks = []
        for attribute, label in required.items():
            granted = getattr(permissions, attribute, False)
            checks.append(
                SetupCheck(
                    "ok" if granted else "error",
                    f"Permission: {label}",
                    "Đã cấp." if granted else "Bot đang thiếu quyền bắt buộc.",
                    None if granted else "Cấp quyền cho role của bot.",
                )
            )
        for attribute, label in feature_permissions.items():
            granted = getattr(permissions, attribute, False)
            checks.append(
                SetupCheck(
                    "ok" if granted else "warning",
                    f"Permission: {label}",
                    "Đã cấp." if granted else "Một số tính năng sẽ không hoạt động.",
                    None if granted else "Cấp quyền nếu server dùng tính năng liên quan.",
                )
            )
        if bot_member.top_role.is_default():
            checks.append(
                SetupCheck(
                    "error",
                    "Role hierarchy",
                    "Bot chưa có role riêng phía trên @everyone.",
                    "Đặt role của bot cao hơn các role bot cần quản lý.",
                )
            )
        else:
            checks.append(
                SetupCheck(
                    "ok",
                    "Role hierarchy",
                    f"Role cao nhất của bot: {bot_member.top_role.name}.",
                )
            )
        return checks

    def _check_channel(
        self, guild: discord.Guild, key: str, label: str, value
    ) -> SetupCheck:
        channel_id = parse_discord_id(value)
        if channel_id is None:
            return SetupCheck(
                "warning",
                key,
                f"Chưa cấu hình channel {label}.",
                f"setting set_variable {key}",
            )
        channel = guild.get_channel(channel_id)
        if channel is None:
            return SetupCheck(
                "error",
                key,
                f"Không tìm thấy channel ID {channel_id}.",
                f"Cập nhật {key} bằng channel ID hợp lệ.",
            )
        permissions = channel.permissions_for(guild.me)
        if not permissions.view_channel or not permissions.send_messages:
            return SetupCheck(
                "error",
                key,
                f"Bot không thể xem/gửi tin trong #{channel.name}.",
                "Cập nhật permission overwrite của channel.",
            )
        return SetupCheck("ok", key, f"#{channel.name} ({channel.id}).")

    def _check_role(
        self, guild: discord.Guild, key: str, label: str, value
    ) -> SetupCheck:
        role_id = parse_discord_id(value)
        if role_id is None:
            return SetupCheck(
                "warning",
                key,
                f"Chưa cấu hình {label}.",
                f"setting set_variable {key}",
            )
        role = guild.get_role(role_id)
        if role is None:
            return SetupCheck(
                "error",
                key,
                f"Không tìm thấy role ID {role_id}.",
                f"Cập nhật {key} bằng role ID hợp lệ.",
            )
        if role >= guild.me.top_role:
            return SetupCheck(
                "error",
                key,
                f"Role {role.name} cao hơn hoặc bằng role bot.",
                "Di chuyển role bot lên trên role này.",
            )
        return SetupCheck("ok", key, f"@{role.name} ({role.id}).")

    def _check_global_variables(self, guild: discord.Guild) -> list[SetupCheck]:
        variables = getattr(self.bot, "global_vars", None)
        if not isinstance(variables, dict):
            return [
                SetupCheck(
                    "error",
                    "Global variables",
                    "Settings cog chưa nạp bot.global_vars.",
                    "Bật cogs.settings.variable_setting và kiểm tra MongoDB.",
                )
            ]

        checks = [
            self._check_channel(guild, key, label, variables.get(key))
            for key, label in CHANNEL_VARIABLES.items()
        ]
        checks.extend(
            self._check_role(guild, key, label, variables.get(key))
            for key, label in ROLE_VARIABLES.items()
        )
        beta_role_ids = sorted(get_beta_role_ids(self.bot))
        if not beta_role_ids:
            checks.append(
                self._check_role(
                    guild,
                    BETA_ROLE_IDS_SETTING,
                    "Beta access role",
                    None,
                )
            )
        else:
            beta_roles = [guild.get_role(role_id) for role_id in beta_role_ids]
            missing_role_ids = [
                role_id
                for role_id, role in zip(beta_role_ids, beta_roles)
                if role is None
            ]
            if missing_role_ids:
                checks.append(
                    SetupCheck(
                        "error",
                        BETA_ROLE_IDS_SETTING,
                        f"Không tìm thấy Beta role ID: {missing_role_ids}.",
                        f"Cập nhật {BETA_ROLE_IDS_SETTING} bằng role ID hợp lệ.",
                    )
                )
            else:
                role_names = ", ".join(
                    f"@{role.name} ({role.id})"
                    for role in beta_roles
                    if role is not None
                )
                checks.append(
                    SetupCheck(
                        "ok",
                        BETA_ROLE_IDS_SETTING,
                        role_names,
                    )
                )
        for key, label in CHANNEL_ARRAY_VARIABLES.items():
            raw_value = variables.get(key)
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            provided = [
                value
                for value in values
                if value is not None and str(value).strip()
            ]
            if not provided:
                checks.append(
                    SetupCheck(
                        "warning",
                        key,
                        f"Chưa cấu hình channel cho {label}.",
                        f"setting set_variable {key}",
                    )
                )
                continue
            parsed_ids = [parse_discord_id(value) for value in provided]
            invalid_count = sum(channel_id is None for channel_id in parsed_ids)
            ids = [channel_id for channel_id in parsed_ids if channel_id is not None]
            missing = [
                channel_id for channel_id in ids if not guild.get_channel(channel_id)
            ]
            inaccessible = []
            for channel_id in ids:
                channel = guild.get_channel(channel_id)
                if channel is None:
                    continue
                permissions = channel.permissions_for(guild.me)
                if not permissions.view_channel or not permissions.send_messages:
                    inaccessible.append(channel_id)
            has_error = bool(invalid_count or missing or inaccessible)
            if has_error:
                details = []
                if invalid_count:
                    details.append(f"{invalid_count} ID sai định dạng")
                if missing:
                    details.append(f"ID không tồn tại: {missing}")
                if inaccessible:
                    details.append(f"bot không thể xem/gửi: {inaccessible}")
                detail = "; ".join(details) + "."
            else:
                detail = f"{len(ids)} channel hợp lệ."
            checks.append(
                SetupCheck(
                    "error" if has_error else "ok",
                    key,
                    detail,
                    f"Cập nhật {key}." if has_error else None,
                )
            )
        return checks

    def _check_feature_configs(self, guild: discord.Guild) -> list[SetupCheck]:
        checks = []
        moderation = self.db["moderation_config"].find_one({"guild_id": guild.id})
        moderation_channel_id = parse_discord_id(
            moderation.get("log_channel_id") if moderation else None
        )
        moderation_channel = (
            guild.get_channel(moderation_channel_id)
            if moderation_channel_id
            else None
        )
        checks.append(
            SetupCheck(
                "ok" if isinstance(moderation_channel, discord.TextChannel) else "warning",
                "Moderation log",
                (
                    f"Đã cấu hình #{moderation_channel.name}."
                    if isinstance(moderation_channel, discord.TextChannel)
                    else "Chưa có moderation case log channel."
                ),
                None
                if isinstance(moderation_channel, discord.TextChannel)
                else "case log_channel #channel",
            )
        )
        item_count = self.db["shop_items"].count_documents(
            {"guild_id": guild.id, "enabled": True}, limit=1
        )
        checks.append(
            SetupCheck(
                "ok" if item_count else "warning",
                "Shop catalog",
                "Có vật phẩm đang bán." if item_count else "Shop chưa có vật phẩm.",
                None if item_count else "shop add_role hoặc shop add_badge",
            )
        )
        return checks

    @staticmethod
    def _result_lines(checks: list[SetupCheck], level: str) -> str:
        icon = {"ok": "✅", "warning": "⚠️", "error": "❌"}[level]
        selected = [check for check in checks if check.level == level]
        lines = []
        for check in selected[:12]:
            line = f"{icon} **{check.name}:** {check.detail}"
            if check.fix:
                line += f" Fix: {check.fix}"
            lines.append(line)
        if len(selected) > 12:
            lines.append(f"… và {len(selected) - 12} mục khác.")
        return "\n".join(lines) or "Không có."

    @commands.group(
        name="setup",
        aliases=["diagnose"],
        invoke_without_command=True,
        help="Kiểm tra cấu hình server.",
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    @commands.cooldown(1, 15, commands.BucketType.guild)
    async def setup_group(self, ctx: commands.Context) -> None:
        await self.run_setup_check(ctx)

    @setup_group.command(name="check")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def setup_check(self, ctx: commands.Context) -> None:
        await self.run_setup_check(ctx)

    async def run_setup_check(self, ctx: commands.Context) -> None:
        environment = getattr(self.bot, "environment", "production")
        checks = [
            SetupCheck(
                "ok",
                "Runtime mode",
                f"Bot đang chạy ở chế độ {environment}.",
            )
        ]
        checks.extend(self._check_bot_permissions(ctx.guild))
        checks.extend(self._check_global_variables(ctx.guild))
        try:
            self.db.command("ping")
            checks.append(SetupCheck("ok", "MongoDB", "Kết nối thành công."))
            checks.extend(self._check_feature_configs(ctx.guild))
        except PyMongoError:
            logger.exception("MongoDB setup check failed")
            checks.append(
                SetupCheck(
                    "error",
                    "MongoDB",
                    "Không thể truy vấn database.",
                    "Kiểm tra DB_HOST, credential và network.",
                )
            )

        for cog_name, (label, module) in FEATURE_COGS.items():
            loaded = self.bot.get_cog(cog_name) is not None
            checks.append(
                SetupCheck(
                    "ok" if loaded else "warning",
                    label,
                    "Cog đã load." if loaded else "Cog chưa load.",
                    None if loaded else f"Thêm {module} vào dev_cogs.txt.",
                )
            )

        totals = summarize_checks(checks)
        color = (
            discord.Color.red()
            if totals["error"]
            else discord.Color.orange()
            if totals["warning"]
            else discord.Color.green()
        )
        embed = discord.Embed(
            title="🩺 TFVN setup check",
            description=(
                f"✅ {totals['ok']} · ⚠️ {totals['warning']} · ❌ {totals['error']}"
            ),
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Lỗi cần sửa",
            value=self._result_lines(checks, "error")[:1024],
            inline=False,
        )
        embed.add_field(
            name="Cảnh báo",
            value=self._result_lines(checks, "warning")[:1024],
            inline=False,
        )
        embed.add_field(
            name="Đã đạt",
            value=self._result_lines(checks, "ok")[:1024],
            inline=False,
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @setup_group.error
    async def setup_group_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Vui lòng thử lại sau {error.retry_after:.0f} giây.")
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Bạn cần quyền Manage Server để chạy setup check.")
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SetupCheckCog(bot))
