"""MongoDB catalog, inventory, and atomic Trap Coin purchases for the shop."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import discord
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from cogs.economy._shop_helpers import (
    MAX_CATALOG_ITEMS,
    clean_display_text,
    format_price,
)


logger = logging.getLogger(__name__)

SHOP_ITEMS_COLLECTION = "shop_items"
SHOP_INVENTORY_COLLECTION = "shop_inventory"
ACCOUNTS_COLLECTION = "user_accounts"
TRANSACTIONS_COLLECTION = "transaction_logs"


@dataclass(frozen=True)
class PurchaseResult:
    success: bool
    message: str
    item: dict[str, Any] | None = None
    balance: int | None = None
    already_owned: bool = False


class ShopStore:
    """Guild catalog and member inventory backed by ``bot.db``."""

    def __init__(self, database: Any) -> None:
        self.items = database[SHOP_ITEMS_COLLECTION]
        self.inventory = database[SHOP_INVENTORY_COLLECTION]
        self.accounts = database[ACCOUNTS_COLLECTION]
        self.transactions = database[TRANSACTIONS_COLLECTION]
        self.ensure_indexes()

    def ensure_indexes(self) -> None:
        try:
            self.items.create_index(
                [("guild_id", ASCENDING), ("item_id", ASCENDING)],
                unique=True,
                name="guild_item_unique",
            )
            self.items.create_index(
                [("guild_id", ASCENDING), ("enabled", ASCENDING)],
                name="guild_enabled_items",
            )
            self.inventory.create_index(
                [
                    ("guild_id", ASCENDING),
                    ("user_id", ASCENDING),
                    ("item_id", ASCENDING),
                ],
                unique=True,
                name="guild_user_item_unique",
            )
            self.transactions.create_index(
                [("user_id", ASCENDING), ("timestamp", DESCENDING)],
                name="user_transactions_recent",
            )
        except PyMongoError:
            logger.exception("Failed to create shop indexes")

    def find_enabled(self, guild_id: int, item_id: str) -> dict[str, Any] | None:
        return self.items.find_one(
            {"guild_id": guild_id, "item_id": item_id, "enabled": True}
        )

    def find_item(self, guild_id: int, item_id: str) -> dict[str, Any] | None:
        return self.items.find_one({"guild_id": guild_id, "item_id": item_id})

    def list_enabled(self, guild_id: int) -> list[dict[str, Any]]:
        return list(
            self.items.find({"guild_id": guild_id, "enabled": True})
            .sort([("price", ASCENDING), ("item_id", ASCENDING)])
            .limit(MAX_CATALOG_ITEMS)
        )

    def list_inventory(self, guild_id: int, user_id: int) -> list[dict[str, Any]]:
        return list(
            self.inventory.find({"guild_id": guild_id, "user_id": user_id})
            .sort("purchased_at", ASCENDING)
            .limit(MAX_CATALOG_ITEMS)
        )

    def owned_record(
        self,
        guild_id: int,
        user_id: int,
        item_id: str,
    ) -> dict[str, Any] | None:
        return self.inventory.find_one(
            {"guild_id": guild_id, "user_id": user_id, "item_id": item_id}
        )

    def owns(self, guild_id: int, user_id: int, item_id: str) -> bool:
        return self.owned_record(guild_id, user_id, item_id) is not None

    def get_balance(self, user_id: int) -> int:
        account = self.accounts.find_one({"user_id": user_id}) or {}
        return int(account.get("balance", 0))

    def get_active_badge(self, user_id: int, guild_id: int) -> dict[str, Any] | None:
        account = self.accounts.find_one({"user_id": user_id}) or {}
        badge = account.get("active_badge") or {}
        if badge.get("guild_id") != guild_id:
            return None
        return badge or None

    def set_active_badge(
        self,
        *,
        user_id: int,
        guild_id: int,
        item_id: str,
        name: str,
    ) -> None:
        self.accounts.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "active_badge": {
                        "guild_id": guild_id,
                        "item_id": item_id,
                        "name": name,
                    }
                },
                "$setOnInsert": {"balance": 0},
            },
            upsert=True,
        )

    def clear_active_badge(self, user_id: int) -> None:
        self.accounts.update_one(
            {"user_id": user_id},
            {"$unset": {"active_badge": ""}},
        )

    def _refund(self, user_id: int, amount: int) -> None:
        try:
            self.accounts.update_one(
                {"user_id": user_id},
                {"$inc": {"balance": amount}},
            )
        except PyMongoError:
            logger.exception("Failed to refund shop purchase for user %s", user_id)

    def purchase(
        self,
        *,
        guild_id: int,
        user_id: int,
        item: dict[str, Any],
    ) -> PurchaseResult:
        item_id = str(item["item_id"])
        ownership_filter = {
            "guild_id": guild_id,
            "user_id": user_id,
            "item_id": item_id,
        }
        if self.inventory.find_one(ownership_filter):
            return PurchaseResult(
                False,
                "Bạn đã sở hữu vật phẩm này.",
                item=item,
                already_owned=True,
            )

        price = int(item["price"])
        try:
            self.accounts.update_one(
                {"user_id": user_id},
                {"$setOnInsert": {"balance": 0}},
                upsert=True,
            )
            account = self.accounts.find_one_and_update(
                {"user_id": user_id, "balance": {"$gte": price}},
                {"$inc": {"balance": -price}},
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError:
            logger.exception("Failed to debit shop purchase for user %s", user_id)
            return PurchaseResult(
                False,
                "Không thể hoàn tất giao dịch. Vui lòng thử lại.",
                item=item,
            )
        if account is None:
            return PurchaseResult(
                False,
                f"Bạn không có đủ Trap Coin. Vật phẩm này giá {format_price(price)}.",
                item=item,
                balance=self.get_balance(user_id),
            )

        now = discord.utils.utcnow()
        balance = int(account.get("balance", 0))
        try:
            self.inventory.insert_one(
                {
                    **ownership_filter,
                    "item_type": item["item_type"],
                    "name": item["name"],
                    "purchased_at": now,
                }
            )
        except DuplicateKeyError:
            self._refund(user_id, price)
            return PurchaseResult(
                False,
                "Bạn đã sở hữu vật phẩm này.",
                item=item,
                already_owned=True,
                balance=self.get_balance(user_id),
            )
        except PyMongoError:
            self._refund(user_id, price)
            logger.exception("Failed to save shop purchase; refunded user")
            return PurchaseResult(
                False,
                "Không thể hoàn tất giao dịch. Trap Coin đã được hoàn lại.",
                item=item,
            )

        try:
            self.transactions.insert_one(
                {
                    "guild_id": guild_id,
                    "user_id": user_id,
                    "type": "shop_purchase",
                    "transaction_type": "debit",
                    "amount": price,
                    "item_id": item_id,
                    "balance_after": balance,
                    "timestamp": now,
                }
            )
        except PyMongoError:
            logger.exception("Failed to write shop transaction log")

        return PurchaseResult(
            True,
            (
                f"Đã mua **{item['name']}** với {format_price(price)}. "
                f"Số dư còn lại: **{balance:,} TC**."
            ),
            item=item,
            balance=balance,
        )

    def upsert_item(
        self,
        *,
        guild_id: int,
        item_id: str,
        name: str,
        description: str,
        price: int,
        item_type: str,
        updated_by: int,
        role_id: int | None = None,
    ) -> dict[str, Any]:
        now = discord.utils.utcnow()
        document: dict[str, Any] = {
            "guild_id": guild_id,
            "item_id": item_id,
            "name": clean_display_text(name, fallback=item_id, limit=100),
            "description": clean_display_text(
                description, fallback="Không có mô tả", limit=300
            ),
            "price": price,
            "item_type": item_type,
            "enabled": True,
            "updated_at": now,
            "updated_by": updated_by,
        }
        if role_id is not None:
            document["role_id"] = role_id

        self.items.update_one(
            {"guild_id": guild_id, "item_id": item_id},
            {
                "$set": document,
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        return document

    def disable_item(self, guild_id: int, item_id: str) -> bool:
        result = self.items.update_one(
            {"guild_id": guild_id, "item_id": item_id},
            {
                "$set": {
                    "enabled": False,
                    "updated_at": discord.utils.utcnow(),
                }
            },
        )
        return int(getattr(result, "matched_count", 0)) > 0
