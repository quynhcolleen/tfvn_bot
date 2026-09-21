"""Thirty-day entitlements, legacy migration, and retryable Discord cleanup."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord.ext import commands, tasks
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError

from cogs.roles._personal_roles import personal_resource_lock


logger = logging.getLogger(__name__)
RENTAL_DURATION = timedelta(days=30)
MIGRATION_ID = "monthly_custom_roles_v1"
MIGRATION_DENIAL = "Chưa thể cập nhật thời hạn shop. Vui lòng thử lại sau."
EXPIRED_DENIAL = "Bạn chưa mua hoặc đã hết hạn vật phẩm này. Hãy mua/gia hạn trong shop."


def utc_datetime(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def rental_active(record: dict[str, Any] | None, now: datetime | None = None) -> bool:
    expiry = utc_datetime((record or {}).get("expires_at"))
    return expiry is not None and expiry > (utc_datetime(now) or discord.utils.utcnow())


def rental_status(record: dict[str, Any]) -> str:
    expiry = utc_datetime(record.get("expires_at"))
    if expiry is None:
        return "Đang cập nhật thời hạn"
    label = "Hết hạn" if not rental_active(record) else "Còn hạn đến"
    return f"{label}: <t:{int(expiry.timestamp())}:f> (<t:{int(expiry.timestamp())}:R>)"


def ensure_rental_migration(database: Any) -> bool:
    """Persist one grace deadline, then safely resume incomplete migrations."""
    try:
        migrations = database["shop_migrations"]
        marker = migrations.find_one({"_id": MIGRATION_ID})
        if marker and marker.get("completed") is True:
            return True
        now = discord.utils.utcnow()
        marker = migrations.find_one_and_update(
            {"_id": MIGRATION_ID},
            {"$setOnInsert": {"started_at": now, "grace_expires_at": now + RENTAL_DURATION}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        database["shop_inventory"].update_many(
            {"item_type": "custom_role", "expires_at": {"$exists": False}},
            {"$set": {
                "expires_at": marker["grace_expires_at"],
                "expiry_cleanup_pending": True,
            }},
        )
        migrations.update_one(
            {"_id": MIGRATION_ID}, {"$set": {"completed": True}}
        )
        return True
    except (PyMongoError, KeyError, TypeError, ValueError):
        logger.exception("Could not migrate shop custom-role rentals; access and cleanup paused")
        return False


def paid_resource_denial(database: Any, guild_id: int, user_id: int, item_type: str) -> str | None:
    """Prevent booster creation while paid time reserves the same resource."""
    if not ensure_rental_migration(database):
        return MIGRATION_DENIAL
    record = database["shop_inventory"].find_one(
        {"guild_id": guild_id, "user_id": user_id, "item_id": item_type}
    )
    if rental_active(record):
        return f"Bạn đã mua {item_type} còn hạn. Hãy dùng `shop use {item_type}`."
    return None


class RentalAccess:
    """One product's entitlement checks and persisted expiry worker."""

    def __init__(
        self,
        bot: commands.Bot,
        item_type: str,
        cleanup: Callable[..., Awaitable[bool]],
    ) -> None:
        self.bot = bot
        self.item_type = item_type
        self.inventory = bot.db["shop_inventory"]
        self.cleanup = cleanup
        self.ready = False

    def ensure_ready(self) -> bool:
        if not self.ready:
            self.ready = ensure_rental_migration(self.bot.db)
        return self.ready

    def record(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        return self.inventory.find_one(
            {"guild_id": guild_id, "user_id": user_id, "item_id": self.item_type}
        )

    def denial(self, guild_id: int, user_id: int) -> str | None:
        if not self.ensure_ready():
            return MIGRATION_DENIAL
        try:
            if not rental_active(self.record(guild_id, user_id)):
                return EXPIRED_DENIAL
        except PyMongoError:
            logger.exception("Could not verify paid entitlement")
            return "Không thể kiểm tra thời hạn lúc này. Vui lòng thử lại."
        return None

    def start(self) -> None:
        try:
            self.inventory.create_index(
                [("item_type", 1), ("expiry_cleanup_pending", 1), ("expires_at", 1)],
                name="rental_expiry_cleanup",
            )
        except PyMongoError:
            logger.exception("Could not index rental expiry cleanup")
        self.ensure_ready()
        self.expiry_loop.start()

    def stop(self) -> None:
        self.expiry_loop.cancel()

    async def sweep(self) -> None:
        if not self.ensure_ready():
            return
        try:
            records = self.inventory.find({
                "item_type": self.item_type,
                "expiry_cleanup_pending": True,
                "expires_at": {"$lte": discord.utils.utcnow()},
            })
            for record in records:
                try:
                    guild = self.bot.get_guild(record["guild_id"])
                    if guild is None or guild.unavailable:
                        continue
                    user_id = record["user_id"]
                    lock = personal_resource_lock(self.bot, guild.id, user_id, self.item_type)
                    async with lock:
                        current = self.record(guild.id, user_id)
                        if not current or rental_active(current):
                            continue
                        if utc_datetime(current.get("expires_at")) is None:
                            continue
                        if await self.cleanup(guild, user_id, reason="Shop rental expired"):
                            self.inventory.update_one(
                                {"guild_id": guild.id, "user_id": user_id,
                                 "item_id": self.item_type, "expires_at": current["expires_at"]},
                                {"$set": {"expiry_cleanup_pending": False}},
                            )
                except (PyMongoError, discord.HTTPException, ValueError, KeyError, TypeError):
                    logger.exception("Could not expire %s for guild %s user %s",
                                     self.item_type, record.get("guild_id"), record.get("user_id"))
        except PyMongoError:
            logger.exception("Could not read expired shop rentals")

    @tasks.loop(seconds=60)
    async def expiry_loop(self) -> None:
        await self.sweep()

    @expiry_loop.before_loop
    async def before_expiry_loop(self) -> None:
        await self.bot.wait_until_ready()
