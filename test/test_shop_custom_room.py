import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from pymongo.errors import PyMongoError

from cogs.booster._custom_resource_ui import RoomDesignDraft
from cogs.booster.create_custom_room import BoosterCustomRoomCog
from cogs.economy._shop_rentals import RENTAL_DURATION, rental_active
from cogs.economy.shop import ShopCog
from cogs.economy.shop_custom_room import ShopCustomRoomCog
from test_shop import FakeDatabase, GUILD_ID, USER_ID, NOW, make_bot, make_interaction
from test_shop_rentals import entitlement, rental_item


class TestShopCustomRoom(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = patch("discord.utils.utcnow", return_value=NOW).start()
        self.addCleanup(patch.stopall)
        self.db = FakeDatabase()
        self.bot = make_bot(self.db)
        self.category = Mock(spec=discord.CategoryChannel)
        self.category.id = 10
        self.category.permissions_for.return_value = SimpleNamespace(manage_channels=True, manage_roles=True)
        self.bot.global_vars = {"BOOSTER_CUSTOM_VOICE_CATEGORY_ID": "10"}
        self.member = Mock(spec=discord.Member)
        self.member.id = USER_ID
        self.member.bot = False
        self.member.premium_since = None
        bot_member = Mock(spec=discord.Member)
        bot_member.id = 9
        bot_member.guild_permissions = SimpleNamespace(manage_channels=True, manage_roles=True)
        self.guild = SimpleNamespace(
            id=GUILD_ID, unavailable=False, default_role=Mock(spec=discord.Role), me=bot_member,
            fetch_channel=AsyncMock(), create_voice_channel=AsyncMock(),
        )
        self.channels = {10: self.category}
        self.guild.get_channel = self.channels.get
        self.guild.get_member = lambda member_id: bot_member if member_id == 9 else self.member
        self.member.guild = self.guild
        self.bot.get_guild = lambda guild_id: self.guild if guild_id == GUILD_ID else None
        self.db["shop_inventory"].documents.append(entitlement("custom_room", NOW + RENTAL_DURATION))
        self.db["shop_items"].documents.append(rental_item("custom_room"))
        self.db["user_accounts"].documents.append({"user_id": USER_ID, "balance": 1000})
        self.cog = ShopCustomRoomCog(self.bot)
        self.addCleanup(self.cog.cog_unload)
        self.shop = ShopCog(self.bot)
        self.addCleanup(self.shop.cog_unload)

        async def create(**kwargs):
            channel = self.room(77)
            channel.name = kwargs["name"]
            channel.user_limit = kwargs["user_limit"]
            self.channels[channel.id] = channel
            return channel

        self.guild.create_voice_channel.side_effect = create

    def room(self, channel_id):
        channel = Mock(spec=discord.VoiceChannel)
        channel.id = channel_id
        channel.name = "Private room"
        channel.user_limit = 5
        channel.mention = f"<#{channel_id}>"
        channel.edit = AsyncMock()
        channel.delete = AsyncMock(side_effect=lambda **kwargs: self.channels.pop(channel_id, None))
        return channel

    async def create(self):
        return await self.cog._save_room(self.guild, self.member, RoomDesignDraft("Quiet corner", 5))

    async def open_editor(self):
        ctx = SimpleNamespace(reply=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())))
        result = await self.shop._use_item(
            guild=self.guild, member=self.member, item_id="custom_room", source=ctx,
        )
        self.assertIsNone(result)
        return ctx.reply.await_args.kwargs["view"]

    async def test_create_is_private_and_update_preserves_sharing(self):
        result = await self.create()
        self.assertTrue(result.completed)
        payload = self.guild.create_voice_channel.await_args.kwargs
        self.assertIs(payload["category"], self.category)
        overwrites = payload["overwrites"]
        self.assertEqual(len(overwrites), 3)
        self.assertFalse(overwrites[self.guild.default_role].view_channel)
        self.assertTrue(overwrites[self.member].manage_permissions)
        self.assertTrue(overwrites[self.guild.me].manage_channels)
        channel = self.channels[77]
        updated = await self.cog._save_room(
            self.guild, self.member, RoomDesignDraft("Renamed", 10), expected_channel_id=77,
        )
        self.assertTrue(updated.completed)
        self.assertNotIn("overwrites", channel.edit.await_args.kwargs)
        self.assertNotIn("category", channel.edit.await_args.kwargs)
        self.assertEqual(self.db["shop_custom_rooms"].documents[0]["channel_name"], "Renamed")
        self.guild.create_voice_channel.assert_awaited_once()

    async def test_purchase_checks_missing_category_and_permissions_before_charging(self):
        for missing in ("category", "manage_roles", "manage_channels"):
            with self.subTest(missing=missing):
                self.bot.global_vars = {} if missing == "category" else {"BOOSTER_CUSTOM_VOICE_CATEGORY_ID": 10}
                self.guild.me.guild_permissions = SimpleNamespace(
                    manage_channels=missing != "manage_channels", manage_roles=missing != "manage_roles",
                )
                success, _ = await self.shop._purchase_item(
                    guild=self.guild, member=self.member, item_id="custom_room",
                )
                self.assertFalse(success)
                self.assertEqual(self.shop.store.get_balance(USER_ID), 1000)

    async def test_uncached_booster_room_prevents_purchase(self):
        self.db["booster_custom_rooms"].documents.append(
            {"guild_id": GUILD_ID, "user_id": USER_ID, "channel_id": 66}
        )
        self.guild.fetch_channel.return_value = self.room(66)
        success, notice = await self.shop._purchase_item(
            guild=self.guild, member=self.member, item_id="custom_room",
        )
        self.assertFalse(success)
        self.assertIn("Booster", notice)
        self.assertEqual(self.shop.store.get_balance(USER_ID), 1000)

    async def test_paid_time_reserves_room_against_booster_creation(self):
        self.member.premium_since = NOW
        booster = BoosterCustomRoomCog(self.bot)
        result = await booster._create_custom_room(
            guild=self.guild, member=self.member, room_name="Booster room",
        )
        self.assertFalse(result.completed)
        self.guild.create_voice_channel.assert_not_awaited()

    async def test_duplicate_create_checks_http_cache_miss(self):
        self.assertTrue((await self.create()).completed)
        channel = self.channels.pop(77)
        self.guild.fetch_channel.return_value = channel
        self.assertFalse((await self.create()).completed)
        self.guild.create_voice_channel.assert_awaited_once()

    async def test_failed_save_rolls_back_created_room(self):
        self.db["shop_custom_rooms"].update_one = Mock(side_effect=PyMongoError("offline"))
        with self.assertLogs("cogs.economy.shop_custom_room", level="ERROR"):
            result = await self.create()
        self.assertFalse(result.completed)
        self.assertNotIn(77, self.channels)
        self.assertFalse(self.db["shop_custom_rooms"].documents)

    async def test_editor_rechecks_expiry_before_create_and_update(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                self.clock.return_value = NOW
                if existing:
                    await self.create()
                view = await self.open_editor()
                self.clock.return_value = NOW + RENTAL_DURATION
                result = await view.submitter(
                    SimpleNamespace(guild=self.guild, user=self.member), RoomDesignDraft("Late", 2),
                )
                self.assertFalse(result.completed)
                if existing:
                    self.channels[77].edit.assert_not_awaited()
                else:
                    self.guild.create_voice_channel.assert_not_awaited()
                view.stop()

    async def test_leave_then_rejoin_can_recreate_using_remaining_time(self):
        await self.create()
        await self.cog.on_member_remove(self.member)
        self.assertFalse(self.db["shop_custom_rooms"].documents)
        self.assertTrue(rental_active(self.db["shop_inventory"].documents[0]))
        self.assertTrue((await self.create()).completed)

    async def test_expiry_deletes_then_purchase_allows_recreation(self):
        await self.create()
        channel = self.channels[77]
        self.clock.return_value = NOW + RENTAL_DURATION
        await self.cog.rental.sweep()
        channel.delete.assert_awaited_once()
        self.assertFalse(self.db["shop_custom_rooms"].documents)
        self.assertFalse(self.db["shop_inventory"].documents[0]["expiry_cleanup_pending"])
        success, _ = await self.shop._purchase_item(
            guild=self.guild, member=self.member, item_id="custom_room",
        )
        self.assertTrue(success)
        self.assertTrue((await self.create()).completed)

    async def test_forbidden_deletion_is_retried(self):
        await self.create()
        channel = self.channels[77]
        channel.delete.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")
        self.clock.return_value = NOW + RENTAL_DURATION
        with self.assertLogs("cogs.economy.shop_custom_room", level="ERROR"):
            await self.cog.rental.sweep()
        self.assertTrue(self.db["shop_custom_rooms"].documents)
        self.assertTrue(self.db["shop_inventory"].documents[0]["expiry_cleanup_pending"])
        channel.delete.side_effect = None
        await self.cog.rental.sweep()
        self.assertFalse(self.db["shop_custom_rooms"].documents)

    async def test_missing_channel_clears_record_after_http_confirmation(self):
        await self.create()
        self.channels.pop(77)
        self.guild.fetch_channel.side_effect = discord.NotFound(SimpleNamespace(status=404, reason="Missing"), "gone")
        self.clock.return_value = NOW + RENTAL_DURATION
        await self.cog.rental.sweep()
        self.assertFalse(self.db["shop_custom_rooms"].documents)

    async def test_room_renewal_requires_ui_confirmation_and_preserves_channel(self):
        await self.create()
        channel = self.channels[77]
        from cogs.economy._shop_ui import ShopView
        view = ShopView(self.shop, guild_id=GUILD_ID, author_id=USER_ID, prefix="!tfd ")
        self.addCleanup(view.stop)
        view.selected_id = "custom_room"
        for _ in range(2):
            interaction = make_interaction(user=self.member, guild=self.guild)
            await self.shop.handle_shop_action(interaction, view, "buy")
        self.assertEqual(self.shop.store.get_balance(USER_ID), 900)
        self.assertEqual(self.db["shop_inventory"].documents[0]["expires_at"], NOW + 2 * RENTAL_DURATION)
        channel.delete.assert_not_awaited()
        self.guild.create_voice_channel.assert_awaited_once()

    async def test_editor_lifecycle_releases_views(self):
        for action in ("cancel", "timeout", "complete"):
            with self.subTest(action=action):
                view = await self.open_editor()
                interaction = SimpleNamespace(
                    guild=self.guild, user=self.member,
                    response=SimpleNamespace(defer=AsyncMock(), edit_message=AsyncMock()),
                    edit_original_response=AsyncMock(), followup=SimpleNamespace(send=AsyncMock()),
                )
                if action == "cancel":
                    await view.cancel(interaction)
                elif action == "timeout":
                    await view.on_timeout()
                else:
                    view.draft = RoomDesignDraft("Finished", 2)
                    await view.confirm(interaction)
                self.assertFalse(self.cog._views)

    async def test_room_editor_send_failure_releases_view(self):
        ctx = SimpleNamespace(reply=AsyncMock(side_effect=discord.HTTPException(
            SimpleNamespace(status=500, reason="Failed"), "offline")))
        with self.assertLogs("cogs.economy.shop_custom_room", level="ERROR"):
            result = await self.cog.use_item(
                guild=self.guild, member=self.member, item=rental_item("custom_room"), source=ctx,
            )
        self.assertIsNotNone(result)
        self.assertFalse(self.cog._views)
