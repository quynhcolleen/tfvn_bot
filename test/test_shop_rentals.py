import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from pymongo.errors import PyMongoError

from cogs.economy._shop_rentals import (
    MIGRATION_ID, RENTAL_DURATION, RentalAccess, ensure_rental_migration,
    rental_active, utc_datetime,
)
from cogs.economy._shop_store import ShopStore
from cogs.economy._shop_ui import ShopView, SHOP_BUY_CUSTOM_ID, SHOP_USE_CUSTOM_ID
from cogs.economy.shop import ShopCog
from cogs.roles._personal_roles import personal_resource_lock
from test_shop import FakeDatabase, NOW, GUILD_ID, USER_ID, make_bot


def rental_item(item_type="custom_role"):
    return {"guild_id": GUILD_ID, "item_id": item_type, "item_type": item_type,
            "name": item_type, "price": 100, "enabled": True}


def entitlement(item_type="custom_role", expires_at=NOW):
    return {"guild_id": GUILD_ID, "user_id": USER_ID, "item_id": item_type,
            "item_type": item_type, "name": item_type, "purchased_at": NOW,
            "expires_at": expires_at, "expiry_cleanup_pending": True}


class TestRentalPurchases(unittest.TestCase):
    def setUp(self):
        self.clock = patch("discord.utils.utcnow", return_value=NOW).start()
        self.addCleanup(patch.stopall)
        self.db = FakeDatabase()
        self.store = ShopStore(self.db)
        self.db["user_accounts"].documents.append({"user_id": USER_ID, "balance": 1000})

    def buy(self, item_type="custom_role"):
        return self.store.purchase(guild_id=GUILD_ID, user_id=USER_ID, item=rental_item(item_type))

    def test_first_purchase_and_early_renewal_preserve_remaining_time(self):
        for item_type in ("custom_role", "custom_room"):
            with self.subTest(item_type=item_type):
                self.clock.return_value = NOW
                first = self.buy(item_type)
                self.assertEqual(first.expires_at, NOW + RENTAL_DURATION)
                self.clock.return_value = NOW + timedelta(days=10)
                second = self.buy(item_type)
                self.assertEqual(second.expires_at, NOW + 2 * RENTAL_DURATION)
                self.assertEqual(len(self.db["shop_inventory"].find({"item_id": item_type}).documents), 1)
                self.assertEqual(self.db["transaction_logs"].documents[-1]["expires_at"], second.expires_at)
        self.assertEqual(self.store.get_balance(USER_ID), 600)

    def test_expired_purchase_starts_at_payment_time(self):
        self.buy()
        self.clock.return_value = NOW + timedelta(days=80)
        result = self.buy()
        self.assertEqual(result.expires_at, NOW + timedelta(days=110))

    def test_insufficient_funds_never_extends_expiry(self):
        self.buy()
        self.db["user_accounts"].documents[0]["balance"] = 0
        old = self.store.owned_record(GUILD_ID, USER_ID, "custom_role")
        self.assertFalse(self.buy().success)
        self.assertEqual(self.store.owned_record(GUILD_ID, USER_ID, "custom_role"), old)

    def test_failed_renewal_save_refunds_and_preserves_expiry(self):
        self.buy()
        self.db["shop_inventory"].update_one = Mock(side_effect=PyMongoError("offline"))
        old = self.store.owned_record(GUILD_ID, USER_ID, "custom_role")
        with self.assertLogs("cogs.economy._shop_store", level="ERROR"):
            result = self.buy()
        self.assertFalse(result.success)
        self.assertEqual(self.store.get_balance(USER_ID), 900)
        self.assertEqual(self.store.owned_record(GUILD_ID, USER_ID, "custom_role"), old)

    def test_conditional_update_conflict_refunds_without_extending(self):
        self.buy()
        self.db["shop_inventory"].update_one = Mock(return_value=SimpleNamespace(matched_count=0))
        result = self.buy()
        self.assertFalse(result.success)
        self.assertEqual(self.store.get_balance(USER_ID), 900)
        self.assertEqual(self.store.owned_record(GUILD_ID, USER_ID, "custom_role")["expires_at"], NOW + RENTAL_DURATION)

    def test_naive_expiry_is_utc_and_boundary_is_expired(self):
        record = entitlement(expires_at=NOW.replace(tzinfo=None))
        self.assertFalse(rental_active(record))
        self.assertEqual(utc_datetime(record["expires_at"]), NOW)
        record["expires_at"] += timedelta(days=10)
        self.db["shop_inventory"].documents.append(record)
        self.assertEqual(self.buy().expires_at, NOW + timedelta(days=40))

    def test_legacy_migration_grants_one_fixed_grace_period(self):
        legacy = entitlement()
        legacy.pop("expires_at")
        self.db["shop_inventory"].documents.append(legacy)
        self.assertTrue(ensure_rental_migration(self.db))
        self.clock.return_value = NOW + timedelta(days=10)
        self.assertTrue(ensure_rental_migration(self.db))
        self.assertEqual(self.store.owned_record(GUILD_ID, USER_ID, "custom_role")["expires_at"], NOW + RENTAL_DURATION)

    def test_failed_migration_blocks_purchase_then_resumes_original_grace(self):
        legacy = entitlement()
        legacy.pop("expires_at")
        self.db["shop_inventory"].documents.append(legacy)
        original_update = self.db["shop_inventory"].update_many
        self.db["shop_inventory"].update_many = Mock(side_effect=PyMongoError("offline"))
        with self.assertLogs("cogs.economy._shop_rentals", level="ERROR"):
            self.assertFalse(self.buy().success)
        self.assertEqual(self.store.get_balance(USER_ID), 1000)
        self.assertNotIn("expires_at", self.db["shop_inventory"].documents[0])
        self.db["shop_inventory"].update_many = original_update
        self.clock.return_value = NOW + timedelta(days=10)
        self.assertTrue(ensure_rental_migration(self.db))
        self.assertEqual(self.db["shop_inventory"].documents[0]["expires_at"], NOW + RENTAL_DURATION)
        self.assertTrue(self.db["shop_migrations"].find_one({"_id": MIGRATION_ID})["completed"])


class TestRentalExpiryWorker(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = patch("discord.utils.utcnow", return_value=NOW).start()
        self.addCleanup(patch.stopall)
        self.db = FakeDatabase()
        self.bot = make_bot(self.db)
        self.guild = SimpleNamespace(id=GUILD_ID, unavailable=False)
        self.bot.get_guild = lambda guild_id: self.guild if guild_id == GUILD_ID else None
        self.bot.wait_until_ready = AsyncMock()
        self.cleanup = AsyncMock(return_value=True)
        self.worker = RentalAccess(self.bot, "custom_role", self.cleanup)
        self.addCleanup(self.worker.stop)
        self.db["shop_inventory"].documents.append(entitlement())

    async def test_ready_worker_cleans_expired_rows_without_catalog(self):
        completed = asyncio.Event()

        async def cleanup(*args, **kwargs):
            completed.set()
            return True

        self.cleanup.side_effect = cleanup
        self.worker.start()
        try:
            await asyncio.wait_for(completed.wait(), timeout=2)
        finally:
            self.worker.stop()
            await asyncio.gather(self.worker.expiry_loop.get_task(), return_exceptions=True)
        self.bot.wait_until_ready.assert_awaited_once()
        self.assertFalse(self.db["shop_inventory"].documents[0]["expiry_cleanup_pending"])

    async def test_failed_cleanup_is_retried_and_inventory_kept(self):
        self.cleanup.side_effect = [False, True]
        await self.worker.sweep()
        self.assertTrue(self.db["shop_inventory"].documents[0]["expiry_cleanup_pending"])
        await self.worker.sweep()
        self.assertEqual(self.cleanup.await_count, 2)
        self.assertEqual(len(self.db["shop_inventory"].documents), 1)
        self.assertFalse(self.db["shop_inventory"].documents[0]["expiry_cleanup_pending"])

    async def test_sweep_rereads_expiry_after_waiting_for_renewal_lock(self):
        lock = personal_resource_lock(self.bot, GUILD_ID, USER_ID, "custom_role")
        async with lock:
            sweep = asyncio.create_task(self.worker.sweep())
            await asyncio.sleep(0)
            self.db["shop_inventory"].documents[0]["expires_at"] = NOW + RENTAL_DURATION
        await asyncio.wait_for(sweep, timeout=2)
        self.cleanup.assert_not_awaited()

    async def test_missing_guild_and_migration_failure_preserve_resources(self):
        self.bot.get_guild = lambda guild_id: None
        await self.worker.sweep()
        self.cleanup.assert_not_awaited()
        self.assertTrue(self.db["shop_inventory"].documents[0]["expiry_cleanup_pending"])
        self.worker.ready = False
        self.db["shop_migrations"].find_one = Mock(side_effect=PyMongoError("offline"))
        with self.assertLogs("cogs.economy._shop_rentals", level="ERROR"):
            await self.worker.sweep()
        self.cleanup.assert_not_awaited()

    async def test_sweep_has_no_catalog_display_limit(self):
        for user_id in range(100, 130):
            self.db["shop_inventory"].documents.append({**entitlement(), "user_id": user_id})
        await self.worker.sweep()
        self.assertEqual(self.cleanup.await_count, 31)

    async def test_expired_inventory_disables_use_and_allows_renewal(self):
        cog = ShopCog(self.bot)
        self.addCleanup(cog.cog_unload)
        self.db["shop_items"].documents.append(rental_item())
        view = ShopView(cog, guild_id=GUILD_ID, author_id=USER_ID, prefix="!tfd ")
        self.addCleanup(view.stop)
        view.selected_id = "custom_role"
        view.rebuild()
        buttons = {child.custom_id: child for child in view.children}
        self.assertFalse(buttons[SHOP_BUY_CUSTOM_ID].disabled)
        self.assertEqual(buttons[SHOP_BUY_CUSTOM_ID].label, "Gia hạn")
        self.assertTrue(buttons[SHOP_USE_CUSTOM_ID].disabled)
        self.assertIn("Hết hạn", view._selection_text(for_store=True))
        self.db["shop_inventory"].documents[0]["expires_at"] = NOW + RENTAL_DURATION
        view.reload()
        self.assertTrue(view.can_use_selected())
        self.assertIn("30 ngày", view._selection_text(for_store=True))
