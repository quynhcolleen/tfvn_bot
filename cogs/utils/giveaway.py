import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands
from pymongo import ASCENDING, ReturnDocument

from cogs.utils._giveaway_helpers import (
    MAX_WINNERS,
    GiveawayCreateError,
    GiveawayFormError,
    GiveawayRoleSettings,
    can_manage_giveaway,
    describe_role_settings,
    entry_weight,
    format_duration,
    is_blacklisted,
    member_role_ids,
    parse_giveaway_form,
    pick_weighted_winners,
    settings_from_mapping,
)
from cogs.utils._giveaway_ui import (
    GiveawayCreateView,
    GiveawaySettingsView,
    NO_MENTIONS,
)


GIVEAWAY_COLLECTION = "giveaways"
GIVEAWAY_SETTINGS_COLLECTION = "giveaway_settings"
GIVEAWAY_BUTTON_CUSTOM_ID = "tfvn:giveaway:join"
GIVEAWAY_LEAVE_BUTTON_CUSTOM_ID = "tfvn:giveaway:leave"
UPDATE_DEBOUNCE_SECONDS = 2.0
# Discord embed field value hard limit
MAX_EMBED_FIELD_CHARS = 1024


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def _mongo_utc(value: datetime | None = None) -> datetime:
    """Naive UTC datetime for consistent MongoDB storage/queries."""
    if value is None:
        value = datetime.now(timezone.utc)
    aware = _as_utc(value)
    return aware.replace(tzinfo=None)


def _jump_url(guild_id: int | None, channel_id: int, message_id: int) -> str:
    guild_part = guild_id if guild_id is not None else "@me"
    return f"https://discord.com/channels/{guild_part}/{channel_id}/{message_id}"


class GiveawayView(discord.ui.View):
    """Persistent join/leave buttons attached to the public giveaway message."""

    def __init__(
        self,
        cog: "GiveawayCog",
        message_id: int | None = None,
        ended: bool = False,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.message_id = message_id

        if ended:
            self.disable_buttons()

    def disable_buttons(self) -> None:
        for item in self.children:
            item.disabled = True

    def _resolve_message_id(self, interaction: discord.Interaction) -> int | None:
        if self.message_id is not None:
            return self.message_id
        if interaction.message is not None:
            return interaction.message.id
        return None

    @discord.ui.button(
        label="Tham gia",
        style=discord.ButtonStyle.success,
        emoji="🎉",
        custom_id=GIVEAWAY_BUTTON_CUSTOM_ID,
    )
    async def join_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        message_id = self._resolve_message_id(interaction)
        if message_id is None:
            await interaction.response.send_message(
                "Không tìm thấy tin nhắn giveaway này.",
                ephemeral=True,
            )
            return

        await self.cog.join_giveaway(interaction, message_id, self)

    @discord.ui.button(
        label="Rời",
        style=discord.ButtonStyle.danger,
        emoji="🚪",
        custom_id=GIVEAWAY_LEAVE_BUTTON_CUSTOM_ID,
    )
    async def leave_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        message_id = self._resolve_message_id(interaction)
        if message_id is None:
            await interaction.response.send_message(
                "Không tìm thấy tin nhắn giveaway này.",
                ephemeral=True,
            )
            return

        await self.cog.leave_giveaway(interaction, message_id, self)


class GiveawayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self.logger = logging.getLogger(__name__)
        self.pending_giveaways: dict[int, asyncio.Task] = {}
        self._update_tasks: dict[int, asyncio.Task] = {}
        self._ui_views: set[discord.ui.View] = set()
        self._synced_startup = False
        self._unloading = False
        self._ensure_indexes()

    def cog_unload(self) -> None:
        self._unloading = True
        for view in tuple(self._ui_views):
            view.stop()
        self._ui_views.clear()
        for task in self.pending_giveaways.values():
            task.cancel()
        for task in self._update_tasks.values():
            task.cancel()

    async def cog_load(self) -> None:
        # If the bot is already ready (e.g. cog reload), restore immediately.
        if self.bot.is_ready():
            await self._restore_giveaways_from_db()

    @property
    def collection(self):
        return self.db[GIVEAWAY_COLLECTION]

    @property
    def settings_collection(self):
        return self.db[GIVEAWAY_SETTINGS_COLLECTION]

    def _ensure_indexes(self) -> None:
        """Indexes so giveaways + entrants survive restarts and stay queryable."""
        try:
            self.collection.create_index(
                [("message_id", ASCENDING)],
                unique=True,
                name="message_id_unique",
            )
            self.collection.create_index(
                [("guild_id", ASCENDING), ("ended", ASCENDING), ("end_at", ASCENDING)],
                name="guild_active_end",
            )
            self.collection.create_index(
                [("ended", ASCENDING), ("end_at", ASCENDING)],
                name="ended_end_at",
            )
            self.collection.create_index(
                [("entries", ASCENDING)],
                name="entries_user",
            )
            self.settings_collection.create_index(
                [("guild_id", ASCENDING)],
                unique=True,
                name="giveaway_settings_guild_unique",
            )
        except Exception:
            self.logger.exception("Failed to ensure giveaway indexes")

    def get_guild_settings(self, guild_id: int | None) -> GiveawayRoleSettings:
        if guild_id is None:
            return GiveawayRoleSettings()
        try:
            document = self.settings_collection.find_one({"guild_id": int(guild_id)})
        except Exception:
            self.logger.exception(
                "Failed to load giveaway settings guild=%s",
                guild_id,
            )
            return GiveawayRoleSettings()
        return settings_from_mapping(document)

    def save_guild_settings(
        self,
        guild_id: int,
        settings: GiveawayRoleSettings,
        *,
        updated_by: int,
    ) -> GiveawayRoleSettings:
        payload = {
            "guild_id": int(guild_id),
            **settings.to_document(),
            "updated_at": _mongo_utc(),
            "updated_by": int(updated_by),
        }
        self.settings_collection.update_one(
            {"guild_id": int(guild_id)},
            {"$set": payload},
            upsert=True,
        )
        return settings

    def _usage_text(self, prefix: str | None = None) -> str:
        prefix = prefix if prefix is not None else self.bot.command_prefix
        return (
            f"**Cách dùng:**\n"
            f"`{prefix}giveaway` — mở biểu mẫu Discord để tạo giveaway\n"
            f"`{prefix}giveaway <thời gian> [số người thắng] <phần thưởng>` — tạo nhanh\n"
            f"`{prefix}giveaway settings` — role cấm tham gia và role tăng tỉ lệ thắng\n"
            f"`{prefix}giveaway list` — danh sách giveaway đang chạy\n"
            f"`{prefix}giveaway entries [message_id]` — ai đã join\n"
            f"`{prefix}giveaway end [message_id]` — kết thúc sớm (host/mod)\n"
            f"`{prefix}giveaway reroll [message_id] [số người]` — quay lại người thắng (host/mod)\n\n"
            f"**Ví dụ:**\n"
            f"`{prefix}giveaway 10m Nitro Classic`\n"
            f"`{prefix}giveaway 1h 3 Discord Nitro`\n"
            f"`{prefix}giveaway end` (reply tin giveaway)\n"
            f"`{prefix}giveaway reroll` (reply tin giveaway)"
        )

    def _entry_ids(self, giveaway: dict) -> list[int]:
        """Normalize entry user IDs from DB (ints, or legacy dicts)."""
        raw = giveaway.get("entries") or []
        ids: list[int] = []
        seen: set[int] = set()
        for item in raw:
            if isinstance(item, dict):
                uid = item.get("user_id")
            else:
                uid = item
            if uid is None:
                continue
            try:
                uid_int = int(uid)
            except (TypeError, ValueError):
                continue
            if uid_int not in seen:
                seen.add(uid_int)
                ids.append(uid_int)
        return ids

    def _format_user_list(self, user_ids: list[int], *, limit: int = 30) -> str:
        if not user_ids:
            return "_Chưa có ai tham gia._"
        shown = user_ids[:limit]
        lines = [f"• <@{uid}> (`{uid}`)" for uid in shown]
        remaining = len(user_ids) - len(shown)
        if remaining > 0:
            lines.append(f"… và **{remaining}** người nữa")
        text = "\n".join(lines)
        if len(text) > MAX_EMBED_FIELD_CHARS:
            text = text[: MAX_EMBED_FIELD_CHARS - 20] + "\n…"
        return text

    def _split_giveaway_args(
        self,
        winner_or_prize: str | None,
        prize: str | None,
    ) -> tuple[int, str | None]:
        if winner_or_prize is None:
            return 1, prize

        if winner_or_prize.isdigit():
            winner_count = int(winner_or_prize)
            return winner_count, prize

        full_prize = winner_or_prize
        if prize:
            full_prize = f"{winner_or_prize} {prize}"

        return 1, full_prize

    def _is_admin_or_mod(self, member: discord.Member | discord.User) -> bool:
        """Admin or mod: Administrator, Manage Server, or Manage Messages."""
        return can_manage_giveaway(member)

    def _is_host_or_mod(
        self,
        member: discord.Member | discord.User,
        giveaway: dict,
    ) -> bool:
        if member.id == giveaway.get("host_id"):
            return True
        return self._is_admin_or_mod(member)

    async def _open_create_form(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.reply(self._usage_text(ctx.clean_prefix), mention_author=False)
            return
        view = GiveawayCreateView(
            self,
            guild_id=ctx.guild.id,
            author_id=ctx.author.id,
            channel_id=ctx.channel.id,
            prefix=ctx.clean_prefix,
            settings=self.get_guild_settings(ctx.guild.id),
        )
        self._ui_views.add(view)
        try:
            view.message = await ctx.reply(
                embed=view.build_embed(),
                view=view,
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            view.stop()
            raise
        finally:
            if view.message is None:
                view.stop()

    async def _open_settings_panel(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.reply(self._usage_text(ctx.clean_prefix), mention_author=False)
            return
        view = GiveawaySettingsView(
            self,
            guild_id=ctx.guild.id,
            author_id=ctx.author.id,
            prefix=ctx.clean_prefix,
            settings=self.get_guild_settings(ctx.guild.id),
        )
        self._ui_views.add(view)
        try:
            view.message = await ctx.reply(
                embed=view.build_embed(),
                view=view,
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            view.stop()
            raise
        finally:
            if view.message is None:
                view.stop()

    async def start_giveaway(
        self,
        *,
        guild_id: int | None,
        channel: discord.abc.Messageable,
        host_id: int,
        prize: str,
        winner_count: int,
        seconds: int,
        settings: GiveawayRoleSettings | None = None,
    ) -> tuple[discord.Message, dict]:
        channel_id = getattr(channel, "id", None)
        if channel_id is None or not hasattr(channel, "send"):
            raise GiveawayCreateError(
                "Không tìm thấy kênh để đăng giveaway. Hãy gọi lại lệnh trong kênh text."
            )

        created_at = _mongo_utc()
        end_at = created_at + timedelta(seconds=seconds)
        role_settings = settings or self.get_guild_settings(guild_id)
        giveaway_doc = {
            "guild_id": guild_id,
            "channel_id": int(channel_id),
            "host_id": int(host_id),
            "message_id": 0,
            "prize": prize,
            "winner_count": winner_count,
            "entries": [],
            "entry_meta": {},
            "winner_ids": [],
            "reroll_history": [],
            "created_at": created_at,
            "updated_at": created_at,
            "end_at": end_at,
            "ended": False,
            **role_settings.to_document(),
        }

        view = GiveawayView(self)
        try:
            message = await channel.send(
                embed=self._giveaway_embed(giveaway_doc),
                view=view,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.Forbidden as error:
            raise GiveawayCreateError(
                "Bot không gửi được tin nhắn giveaway trong kênh này."
            ) from error
        except discord.HTTPException as error:
            self.logger.exception("Failed to send giveaway message")
            raise GiveawayCreateError(
                "Không thể tạo giveaway. Vui lòng thử lại."
            ) from error

        view.message_id = message.id
        giveaway_doc["message_id"] = int(message.id)
        self.bot.add_view(
            GiveawayView(self, message.id),
            message_id=message.id,
        )

        try:
            self.collection.insert_one(giveaway_doc)
        except Exception as error:
            self.logger.exception(
                "Failed to persist giveaway message_id=%s to database",
                message.id,
            )
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            raise GiveawayCreateError(
                "Không thể tạo giveaway. Vui lòng thử lại."
            ) from error

        self._track_giveaway(giveaway_doc)
        self.logger.info(
            "Created giveaway message_id=%s guild=%s prize=%r end_at=%s",
            message.id,
            giveaway_doc.get("guild_id"),
            giveaway_doc.get("prize"),
            end_at,
        )
        return message, giveaway_doc

    async def _resolve_message_id(
        self,
        ctx: commands.Context,
        message_id: int | None,
    ) -> int | None:
        if message_id is not None:
            return message_id
        if ctx.message.reference and ctx.message.reference.message_id:
            return ctx.message.reference.message_id
        return None

    def _giveaway_embed(self, giveaway: dict, ended: bool = False) -> discord.Embed:
        end_at = _as_utc(giveaway["end_at"])
        entries = self._entry_ids(giveaway)
        winner_count = giveaway.get("winner_count", 1)
        title = "🎉 Giveaway đã kết thúc" if ended else "🎉 Giveaway"
        color = discord.Color.dark_grey() if ended else discord.Color.gold()

        embed = discord.Embed(title=title, color=color, timestamp=end_at)
        embed.add_field(name="Phần thưởng", value=giveaway["prize"], inline=False)
        embed.add_field(
            name="Người tổ chức",
            value=f"<@{giveaway['host_id']}>",
            inline=True,
        )
        embed.add_field(
            name="Kết thúc",
            value=discord.utils.format_dt(end_at, style="R"),
            inline=True,
        )
        embed.add_field(
            name="Lượt tham gia",
            value=f"**{len(entries)}**",
            inline=True,
        )
        embed.add_field(
            name="Số người thắng",
            value=str(winner_count),
            inline=True,
        )
        role_settings = settings_from_mapping(giveaway)
        if role_settings.blacklist_role_ids or role_settings.bonus_role_ids:
            embed.add_field(
                name="Role",
                value=describe_role_settings(role_settings),
                inline=False,
            )

        winner_ids = giveaway.get("winner_ids") or []
        if ended:
            winner_text = (
                ", ".join(f"<@{winner_id}>" for winner_id in winner_ids)
                if winner_ids
                else "Không có lượt tham gia hợp lệ."
            )
            embed.add_field(name="Kết quả", value=winner_text, inline=False)
            embed.set_footer(text="Giveaway đã kết thúc")
        else:
            embed.set_footer(text="Nhấn 🎉 Tham gia hoặc 🚪 Rời bên dưới")

        return embed

    def _giveaway_result_embed(
        self,
        giveaway: dict,
        *,
        reroll: bool = False,
    ) -> discord.Embed:
        prize = giveaway.get("prize", "Phần thưởng không xác định")
        host_id = giveaway.get("host_id")
        winner_ids = giveaway.get("winner_ids") or []
        entry_count = len(self._entry_ids(giveaway))

        title = "🔄 Giveaway reroll!" if reroll else "🎉 Giveaway đã kết thúc!"
        embed = discord.Embed(title=title, color=discord.Color.gold())

        if winner_ids:
            winners = ", ".join(f"<@{winner_id}>" for winner_id in winner_ids)
            label = "Người thắng" if len(winner_ids) == 1 else "Những người thắng"
            embed.description = (
                f"**Phần thưởng:** {prize}\n\n"
                f"**{label}:** {winners}"
            )
            embed.set_footer(text="Chúc mừng!")
        else:
            embed.description = (
                f"**Phần thưởng:** {prize}\n\n"
                "Không ai tham gia giveaway này."
            )
            embed.set_footer(text="Chúc may mắn lần sau!")

        if host_id:
            embed.add_field(name="Người tổ chức", value=f"<@{host_id}>", inline=True)

        embed.add_field(name="Lượt tham gia", value=str(entry_count), inline=True)

        return embed

    async def _fetch_channel(
        self,
        channel_id: int,
    ) -> discord.abc.Messageable | None:
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel

        try:
            channel = await self.bot.fetch_channel(channel_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            self.logger.exception("Cannot fetch giveaway channel %s.", channel_id)
            return None

        return channel if isinstance(channel, discord.abc.Messageable) else None

    async def _edit_giveaway_message(
        self,
        giveaway: dict,
        ended: bool = False,
    ) -> None:
        channel = await self._fetch_channel(giveaway["channel_id"])
        if channel is None or not hasattr(channel, "fetch_message"):
            return

        try:
            message = await channel.fetch_message(giveaway["message_id"])
        except (discord.Forbidden, discord.NotFound):
            return
        except discord.HTTPException:
            self.logger.exception(
                "Cannot fetch giveaway message %s.",
                giveaway["message_id"],
            )
            return

        view = GiveawayView(self, giveaway["message_id"], ended=ended)

        try:
            await message.edit(
                embed=self._giveaway_embed(giveaway, ended=ended),
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.NotFound):
            return
        except discord.HTTPException:
            self.logger.exception(
                "Cannot edit giveaway message %s.",
                giveaway["message_id"],
            )

    def _schedule_message_update(self, message_id: int) -> None:
        """Debounce public message edits to avoid Discord rate limits."""
        existing = self._update_tasks.get(message_id)
        if existing is not None and not existing.done():
            existing.cancel()

        async def _debounced() -> None:
            try:
                await asyncio.sleep(UPDATE_DEBOUNCE_SECONDS)
                giveaway = self.collection.find_one({"message_id": message_id})
                if giveaway is None:
                    return
                await self._edit_giveaway_message(
                    giveaway,
                    ended=bool(giveaway.get("ended")),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception(
                    "Failed debounced update for giveaway %s.",
                    message_id,
                )
            finally:
                current = self._update_tasks.get(message_id)
                if current is asyncio.current_task():
                    self._update_tasks.pop(message_id, None)

        self._update_tasks[message_id] = asyncio.create_task(_debounced())

    async def _update_giveaway_message(
        self,
        message_id: int,
        *,
        immediate: bool = False,
    ) -> None:
        """Update the public giveaway message embed (entry count etc.)."""
        if immediate:
            existing = self._update_tasks.pop(message_id, None)
            if existing is not None and not existing.done():
                existing.cancel()
            giveaway = self.collection.find_one({"message_id": message_id})
            if giveaway is None:
                return
            await self._edit_giveaway_message(
                giveaway,
                ended=bool(giveaway.get("ended")),
            )
            return

        self._schedule_message_update(message_id)

    def _track_giveaway(self, giveaway: dict) -> None:
        message_id = int(giveaway["message_id"])
        if message_id in self.pending_giveaways:
            return

        task = asyncio.create_task(self.schedule_giveaway_end(giveaway))
        self.pending_giveaways[message_id] = task

    def _cancel_giveaway_task(self, message_id: int) -> None:
        """Drop the scheduled end task. Never cancels the *current* task.

        If we cancelled ourselves from inside ``end_giveaway``, the next
        ``await`` would raise CancelledError and skip winner announce/edit.
        """
        task = self.pending_giveaways.pop(int(message_id), None)
        if task is None or task.done():
            return
        if task is asyncio.current_task():
            return
        task.cancel()

    def _pick_winners(
        self,
        entries: list[int],
        winner_count: int,
        weights: dict[int, int] | None = None,
    ) -> list[int]:
        return pick_weighted_winners(entries, winner_count, weights)

    def _weights_for_entries(
        self,
        giveaway: dict,
        entries: list[int],
    ) -> tuple[list[int], dict[int, int]]:
        settings = settings_from_mapping(giveaway)
        meta = giveaway.get("entry_meta") or {}
        guild_id = giveaway.get("guild_id")
        guild = self.bot.get_guild(int(guild_id)) if guild_id else None
        eligible: list[int] = []
        weights: dict[int, int] = {}
        for user_id in entries:
            member = guild.get_member(user_id) if guild is not None else None
            role_ids = member_role_ids(member)
            if member is not None and is_blacklisted(role_ids, settings):
                continue
            stored = meta.get(str(user_id)) or meta.get(user_id) or {}
            stored_weight = stored.get("weight") if isinstance(stored, dict) else None
            if isinstance(stored_weight, int) and stored_weight >= 1:
                weight = stored_weight
            elif member is not None:
                weight = entry_weight(role_ids, settings)
            else:
                weight = 1
            if weight <= 0:
                continue
            eligible.append(user_id)
            weights[user_id] = weight
        return eligible, weights

    async def _restore_giveaways_from_db(self) -> None:
        """Reload active giveaways + entrants from MongoDB after restart."""
        if self._synced_startup:
            return
        self._synced_startup = True

        now = _mongo_utc()
        try:
            active_giveaways = list(
                self.collection.find(
                    {
                        "ended": False,
                        "end_at": {"$gt": now},
                    }
                )
            )
            expired_giveaways = list(
                self.collection.find(
                    {
                        "ended": False,
                        "end_at": {"$lte": now},
                    }
                )
            )
        except Exception:
            self.logger.exception("Failed loading giveaways from database")
            self._synced_startup = False
            return

        active_entries = 0
        for giveaway in active_giveaways:
            message_id = int(giveaway["message_id"])
            entry_count = len(self._entry_ids(giveaway))
            active_entries += entry_count
            self.bot.add_view(
                GiveawayView(self, message_id),
                message_id=message_id,
            )
            self._track_giveaway(giveaway)
            self.logger.info(
                "Restored active giveaway message_id=%s prize=%r entries=%s end_at=%s",
                message_id,
                giveaway.get("prize"),
                entry_count,
                giveaway.get("end_at"),
            )

        for giveaway in expired_giveaways:
            self._track_giveaway(giveaway)
            self.logger.info(
                "Restored expired giveaway (will end now) message_id=%s entries=%s",
                giveaway.get("message_id"),
                len(self._entry_ids(giveaway)),
            )

        self.logger.info(
            "Giveaway restore complete: %s active (%s total entrants), %s expired pending end",
            len(active_giveaways),
            active_entries,
            len(expired_giveaways),
        )

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self._restore_giveaways_from_db()

    async def schedule_giveaway_end(self, giveaway: dict) -> None:
        message_id = int(giveaway["message_id"])
        try:
            # Always re-read end_at from DB so restarts use the saved value
            fresh = self.collection.find_one({"message_id": message_id})
            if fresh is None:
                self.logger.warning(
                    "Giveaway %s missing from DB while scheduling end",
                    message_id,
                )
                return
            if fresh.get("ended"):
                return

            now = _as_utc(datetime.now(timezone.utc))
            end_at = _as_utc(fresh["end_at"])
            delay = max(0, (end_at - now).total_seconds())

            self.logger.info(
                "Scheduled giveaway %s to end in %.1fs (entries=%s)",
                message_id,
                delay,
                len(self._entry_ids(fresh)),
            )

            if delay:
                await asyncio.sleep(delay)

            ended = await self.end_giveaway(message_id)
            if ended is None:
                self.logger.info(
                    "Giveaway %s was already ended or missing when timer fired.",
                    message_id,
                )
            else:
                self.logger.info(
                    "Giveaway %s ended with winners=%s entries=%s",
                    message_id,
                    ended.get("winner_ids"),
                    len(self._entry_ids(ended)),
                )
        except asyncio.CancelledError:
            self.logger.debug("Giveaway end task cancelled for %s", message_id)
            raise
        except Exception:
            self.logger.exception("Failed to end giveaway %s", message_id)
        finally:
            self.pending_giveaways.pop(message_id, None)

    async def join_giveaway(
        self,
        interaction: discord.Interaction,
        message_id: int,
        view: GiveawayView,
    ) -> None:
        if interaction.user.bot:
            await interaction.response.send_message(
                "Tài khoản bot không thể tham gia giveaway.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        giveaway = self.collection.find_one({"message_id": message_id})
        if giveaway is None or giveaway.get("ended"):
            view.disable_buttons()
            await interaction.followup.send(
                "Giveaway này đã kết thúc.",
                ephemeral=True,
            )
            return

        settings = settings_from_mapping(giveaway)
        role_ids = member_role_ids(interaction.user)
        if is_blacklisted(role_ids, settings):
            await interaction.followup.send(
                "Role của bạn không được tham gia giveaway này.",
                ephemeral=True,
            )
            return

        user_id = int(interaction.user.id)
        joined_at = _mongo_utc()
        weight = entry_weight(role_ids, settings)
        # Persist entrant ID + join metadata so restarts keep the full list
        result = self.collection.find_one_and_update(
            {
                "message_id": message_id,
                "ended": False,
                "entries": {"$nin": [user_id]},
            },
            {
                "$addToSet": {"entries": user_id},
                "$set": {
                    f"entry_meta.{user_id}": {
                        "user_id": user_id,
                        "joined_at": joined_at,
                        "name": str(interaction.user),
                        "weight": weight,
                    },
                    "updated_at": joined_at,
                },
            },
            return_document=ReturnDocument.AFTER,
        )

        if result is not None:
            await self._update_giveaway_message(message_id)
            await interaction.followup.send(
                f"Bạn đã tham gia giveaway **{result['prize']}**. Chúc may mắn! 🎉",
                ephemeral=True,
            )
            return

        giveaway = self.collection.find_one({"message_id": message_id})
        if giveaway is None or giveaway.get("ended"):
            view.disable_buttons()
            await interaction.followup.send(
                "Giveaway này đã kết thúc.",
                ephemeral=True,
            )
            return

        if user_id in self._entry_ids(giveaway):
            await interaction.followup.send(
                "Bạn đã tham gia giveaway này rồi. Nhấn **Rời** nếu muốn rút lui.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "Không thể tham gia giveaway lúc này. Thử lại sau.",
            ephemeral=True,
        )

    async def leave_giveaway(
        self,
        interaction: discord.Interaction,
        message_id: int,
        view: GiveawayView | None = None,
    ) -> None:
        if interaction.user.bot:
            await interaction.response.send_message(
                "Tài khoản bot không thể rời giveaway.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        user_id = int(interaction.user.id)
        left_at = _mongo_utc()
        result = self.collection.find_one_and_update(
            {
                "message_id": message_id,
                "ended": False,
                "entries": user_id,
            },
            {
                "$pull": {"entries": user_id},
                "$unset": {f"entry_meta.{user_id}": ""},
                "$set": {"updated_at": left_at},
            },
            return_document=ReturnDocument.AFTER,
        )

        if result is not None:
            await self._update_giveaway_message(message_id)
            await interaction.followup.send(
                "Bạn đã rời giveaway.",
                ephemeral=True,
            )
            return

        giveaway = self.collection.find_one({"message_id": message_id})
        if giveaway is None or giveaway.get("ended"):
            if view is not None:
                view.disable_buttons()
            await interaction.followup.send(
                "Giveaway này đã kết thúc.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "Bạn không tham gia giveaway này.",
            ephemeral=True,
        )

    async def end_giveaway(
        self,
        message_id: int,
        *,
        announce: bool = True,
    ) -> dict | None:
        # Fresh read from DB so we roll against the latest persisted entrants
        giveaway = self.collection.find_one(
            {"message_id": message_id, "ended": False},
        )
        if giveaway is None:
            return None

        entries = self._entry_ids(giveaway)
        winner_count = int(giveaway.get("winner_count") or 1)
        eligible, weights = self._weights_for_entries(giveaway, entries)
        winner_ids = self._pick_winners(eligible, winner_count, weights)
        ended_at = _mongo_utc()

        # Atomic: mark ended + store winners in one write (kept in DB after restart)
        giveaway = self.collection.find_one_and_update(
            {"message_id": message_id, "ended": False},
            {
                "$set": {
                    "ended": True,
                    "ended_at": ended_at,
                    "winner_ids": winner_ids,
                    "updated_at": ended_at,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if giveaway is None:
            return None

        # Stop the timer task if someone ended early from a command.
        # When the timer task itself is ending, this is a no-op for self
        # (cancelling ourselves would skip the announce below).
        self._cancel_giveaway_task(message_id)

        try:
            await self._update_giveaway_message(message_id, immediate=True)
            if announce:
                await self._announce_winners(giveaway, reroll=False)
        except Exception:
            self.logger.exception("Failed finishing giveaway %s", message_id)

        return giveaway

    async def _announce_winners(
        self,
        giveaway: dict,
        *,
        reroll: bool = False,
    ) -> None:
        channel = await self._fetch_channel(giveaway["channel_id"])
        if channel is None or not hasattr(channel, "send"):
            return

        winner_ids = giveaway.get("winner_ids") or []
        embed = self._giveaway_result_embed(giveaway, reroll=reroll)
        ping = (
            ", ".join(f"<@{winner_id}>" for winner_id in winner_ids)
            if winner_ids
            else None
        )

        try:
            await channel.send(
                content=ping,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                    everyone=False,
                ),
            )
        except discord.Forbidden:
            self.logger.exception(
                "Cannot send giveaway result for message %s.",
                giveaway["message_id"],
            )
        except discord.HTTPException:
            self.logger.exception(
                "Failed to send giveaway result for message %s.",
                giveaway["message_id"],
            )

    async def reroll_giveaway(
        self,
        message_id: int,
        winner_count: int | None = None,
    ) -> dict | None:
        giveaway = self.collection.find_one({"message_id": message_id, "ended": True})
        if giveaway is None:
            return None

        entries = self._entry_ids(giveaway)
        count = winner_count if winner_count is not None else giveaway.get("winner_count", 1)
        previous = {int(w) for w in (giveaway.get("winner_ids") or [])}
        eligible, weights = self._weights_for_entries(giveaway, entries)

        # Prefer entrants who have not won yet; fall back to full pool
        unused = [uid for uid in eligible if uid not in previous]
        pool = unused if unused else list(eligible)
        winner_ids = self._pick_winners(pool, count, weights)
        rerolled_at = _mongo_utc()

        giveaway = self.collection.find_one_and_update(
            {"message_id": message_id, "ended": True},
            {
                "$set": {
                    "winner_ids": winner_ids,
                    "updated_at": rerolled_at,
                },
                "$push": {
                    "reroll_history": {
                        "at": rerolled_at,
                        "winner_ids": winner_ids,
                    }
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        if giveaway is None:
            return None

        await self._update_giveaway_message(message_id, immediate=True)
        await self._announce_winners(giveaway, reroll=True)
        return giveaway

    @commands.group(
        name="giveaway",
        aliases=["ga"],
        invoke_without_command=True,
        help="Tạo và quản lý giveaway.",
    )
    @commands.guild_only()
    async def giveaway(
        self,
        ctx: commands.Context,
        duration: str = None,
        winner_or_prize: str = None,
        *,
        prize: str = None,
    ) -> None:
        if duration is None:
            if self._is_admin_or_mod(ctx.author):
                await self._open_create_form(ctx)
            else:
                await ctx.reply(
                    self._usage_text(ctx.clean_prefix),
                    mention_author=False,
                )
            return

        # Subcommand names should not be treated as durations
        if duration.lower() in {"end", "reroll", "list", "entries", "help", "settings", "setting"}:
            await ctx.reply(
                self._usage_text(ctx.clean_prefix),
                mention_author=False,
            )
            return

        if not self._is_admin_or_mod(ctx.author):
            await ctx.reply(
                "Chỉ **admin** hoặc **mod** (Manage Messages / Manage Server) mới có thể tạo giveaway.",
                mention_author=False,
            )
            return

        winner_count, prize = self._split_giveaway_args(winner_or_prize, prize)
        if not prize:
            await ctx.reply(
                self._usage_text(ctx.clean_prefix),
                mention_author=False,
            )
            return

        try:
            draft = parse_giveaway_form(
                prize=prize,
                duration=duration,
                winners=winner_count,
            )
        except GiveawayFormError as error:
            await ctx.reply(str(error), mention_author=False)
            return

        try:
            message, giveaway_doc = await self.start_giveaway(
                guild_id=ctx.guild.id if ctx.guild else None,
                channel=ctx.channel,
                host_id=int(ctx.author.id),
                prize=draft.prize,
                winner_count=draft.winner_count,
                seconds=draft.seconds,
            )
        except GiveawayCreateError as error:
            await ctx.reply(str(error), mention_author=False)
            return

        end_at = giveaway_doc["end_at"]
        await ctx.reply(
            (
                f"Đã bắt đầu giveaway cho **{draft.prize}** "
                f"({draft.winner_count} người thắng).\n"
                f"Kết thúc sau **{format_duration(draft.seconds)}** "
                f"({discord.utils.format_dt(_as_utc(end_at), style='R')}).\n"
                f"Tin nhắn: {message.jump_url}"
            ),
            mention_author=False,
            allowed_mentions=NO_MENTIONS,
        )

    @giveaway.command(
        name="settings",
        aliases=["setting"],
        help="Cài đặt role cấm tham gia và role tăng tỉ lệ thắng.",
    )
    @commands.guild_only()
    async def giveaway_settings(self, ctx: commands.Context) -> None:
        if not self._is_admin_or_mod(ctx.author):
            await ctx.reply(
                "Chỉ **admin** hoặc **mod** (Manage Messages / Manage Server) mới có thể đổi cài đặt giveaway.",
                mention_author=False,
            )
            return
        await self._open_settings_panel(ctx)

    @giveaway.command(name="list", aliases=["ls", "active"], help="Danh sách giveaway đang chạy.")
    @commands.guild_only()
    async def giveaway_list(self, ctx: commands.Context) -> None:
        now = _mongo_utc()
        query = {
            "ended": False,
            "end_at": {"$gt": now},
        }
        if ctx.guild is not None:
            query["guild_id"] = ctx.guild.id

        giveaways = list(self.collection.find(query).sort("end_at", ASCENDING).limit(25))
        if not giveaways:
            await ctx.reply(
                "Không có giveaway đang chạy.",
                mention_author=False,
            )
            return

        embed = discord.Embed(
            title="📋 Giveaway đang chạy",
            color=discord.Color.gold(),
            description=f"Tìm thấy **{len(giveaways)}** giveaway active.",
        )

        for giveaway in giveaways:
            message_id = int(giveaway["message_id"])
            channel_id = int(giveaway["channel_id"])
            guild_id = giveaway.get("guild_id")
            entries = self._entry_ids(giveaway)
            end_at = _as_utc(giveaway["end_at"])
            prize = str(giveaway.get("prize", "?"))[:80]
            url = _jump_url(guild_id, channel_id, message_id)
            value = (
                f"[Nhảy tới tin nhắn]({url})\n"
                f"Host: <@{giveaway.get('host_id')}>\n"
                f"Entries: **{len(entries)}** · Winners: **{giveaway.get('winner_count', 1)}**\n"
                f"Kết thúc: {discord.utils.format_dt(end_at, style='R')}\n"
                f"`message_id`: `{message_id}`"
            )
            embed.add_field(name=prize, value=value, inline=False)

        await ctx.reply(embed=embed, mention_author=False)

    @giveaway.command(
        name="entries",
        aliases=["entrants", "joined", "who"],
        help="Xem ai đã join giveaway.",
    )
    @commands.guild_only()
    async def giveaway_entries(
        self,
        ctx: commands.Context,
        message_id: int = None,
    ) -> None:
        resolved_id = await self._resolve_message_id(ctx, message_id)
        if resolved_id is None:
            await ctx.reply(
                "Cần `message_id` hoặc reply tin nhắn giveaway.\n"
                f"Ví dụ: `{self.bot.command_prefix}giveaway entries 1234567890`",
                mention_author=False,
            )
            return

        giveaway = self.collection.find_one({"message_id": resolved_id})
        if giveaway is None:
            await ctx.reply(
                "Không tìm thấy giveaway này.",
                mention_author=False,
            )
            return

        if (
            giveaway.get("guild_id")
            and ctx.guild
            and giveaway["guild_id"] != ctx.guild.id
        ):
            await ctx.reply("Giveaway này không thuộc server này.", mention_author=False)
            return

        entries = self._entry_ids(giveaway)
        entry_meta = giveaway.get("entry_meta") or {}
        end_at = _as_utc(giveaway["end_at"])
        status = "đã kết thúc" if giveaway.get("ended") else "đang chạy"

        embed = discord.Embed(
            title="👥 Người tham gia giveaway",
            color=discord.Color.blurple(),
            description=(
                f"**Phần thưởng:** {giveaway.get('prize', '?')}\n"
                f"**Trạng thái:** {status}\n"
                f"**Tổng entries:** **{len(entries)}**"
            ),
        )
        embed.add_field(
            name="Danh sách",
            value=self._format_user_list(entries),
            inline=False,
        )

        # Show a few join timestamps when available
        timed = []
        for uid in entries[:10]:
            meta = entry_meta.get(str(uid)) or entry_meta.get(uid)
            if isinstance(meta, dict) and meta.get("joined_at"):
                joined = _as_utc(meta["joined_at"])
                timed.append(
                    f"<@{uid}> — {discord.utils.format_dt(joined, style='R')}"
                )
        if timed:
            embed.add_field(
                name="Thời gian join",
                value="\n".join(timed),
                inline=False,
            )

        if giveaway.get("ended") and giveaway.get("winner_ids"):
            winners = ", ".join(f"<@{w}>" for w in giveaway["winner_ids"])
            embed.add_field(name="Người thắng", value=winners, inline=False)

        embed.add_field(
            name="Thông tin",
            value=(
                f"Host: <@{giveaway.get('host_id')}>\n"
                f"Kết thúc: {discord.utils.format_dt(end_at, style='F')}\n"
                f"`message_id`: `{resolved_id}`"
            ),
            inline=False,
        )
        await ctx.reply(embed=embed, mention_author=False)

    @giveaway.command(name="end", help="Kết thúc giveaway sớm.")
    @commands.guild_only()
    async def giveaway_end(
        self,
        ctx: commands.Context,
        message_id: int = None,
    ) -> None:
        resolved_id = await self._resolve_message_id(ctx, message_id)
        if resolved_id is None:
            await ctx.reply(
                "Cần `message_id` hoặc reply tin nhắn giveaway.\n"
                f"Ví dụ: `{self.bot.command_prefix}giveaway end 1234567890`",
                mention_author=False,
            )
            return

        giveaway = self.collection.find_one({"message_id": resolved_id})
        if giveaway is None:
            await ctx.reply("Không tìm thấy giveaway này.", mention_author=False)
            return

        if giveaway.get("guild_id") and ctx.guild and giveaway["guild_id"] != ctx.guild.id:
            await ctx.reply("Giveaway này không thuộc server này.", mention_author=False)
            return

        if giveaway.get("ended"):
            await ctx.reply("Giveaway này đã kết thúc rồi.", mention_author=False)
            return

        if not self._is_host_or_mod(ctx.author, giveaway):
            await ctx.reply(
                "Chỉ host hoặc mod (Manage Messages / Manage Server) mới có thể kết thúc sớm.",
                mention_author=False,
            )
            return

        ended = await self.end_giveaway(resolved_id)
        if ended is None:
            await ctx.reply("Không thể kết thúc giveaway (có thể đã kết thúc).", mention_author=False)
            return

        await ctx.reply("Đã kết thúc giveaway.", mention_author=False)

    @giveaway.command(name="reroll", aliases=["rr"], help="Quay lại người thắng.")
    @commands.guild_only()
    async def giveaway_reroll(
        self,
        ctx: commands.Context,
        message_id: int = None,
        winners: int = None,
    ) -> None:
        # Allow `reroll <winners>` when replying to the giveaway message
        if (
            message_id is not None
            and winners is None
            and ctx.message.reference
            and message_id <= MAX_WINNERS
        ):
            # Ambiguous: could be winner count if replying
            winners = message_id
            message_id = None

        resolved_id = await self._resolve_message_id(ctx, message_id)
        if resolved_id is None:
            await ctx.reply(
                "Cần `message_id` hoặc reply tin nhắn giveaway.\n"
                f"Ví dụ: `{self.bot.command_prefix}giveaway reroll 1234567890`",
                mention_author=False,
            )
            return

        giveaway = self.collection.find_one({"message_id": resolved_id})
        if giveaway is None:
            await ctx.reply("Không tìm thấy giveaway này.", mention_author=False)
            return

        if giveaway.get("guild_id") and ctx.guild and giveaway["guild_id"] != ctx.guild.id:
            await ctx.reply("Giveaway này không thuộc server này.", mention_author=False)
            return

        if not giveaway.get("ended"):
            await ctx.reply(
                "Giveaway chưa kết thúc. Dùng `giveaway end` trước.",
                mention_author=False,
            )
            return

        if not self._is_host_or_mod(ctx.author, giveaway):
            await ctx.reply(
                "Chỉ host hoặc mod (Manage Messages / Manage Server) mới có thể reroll.",
                mention_author=False,
            )
            return

        if winners is not None and (winners < 1 or winners > MAX_WINNERS):
            await ctx.reply(
                f"Số người thắng phải từ 1 đến {MAX_WINNERS}.",
                mention_author=False,
            )
            return

        if not giveaway.get("entries"):
            await ctx.reply("Không có ai tham gia để reroll.", mention_author=False)
            return

        result = await self.reroll_giveaway(resolved_id, winners)
        if result is None:
            await ctx.reply("Không thể reroll giveaway này.", mention_author=False)
            return

        await ctx.reply("Đã reroll người thắng.", mention_author=False)

    @giveaway.error
    async def giveaway_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.reply(
                "Giveaway chỉ có thể dùng trong server.",
                mention_author=False,
            )
            return

        if isinstance(error, commands.BadArgument):
            await ctx.reply(self._usage_text(), mention_author=False)
            return

        raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(GiveawayCog(bot))
