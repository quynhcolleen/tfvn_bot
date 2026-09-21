import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from cogs.booster._custom_resource_ui import BoosterActionResult, RoleDesignDraft
from cogs.booster._role_colors import RoleColorSpec
from cogs.booster.create_custom_role import BoosterCustomRoleCog
from cogs.economy._shop_helpers import CUSTOM_ROLE_ITEM_ID, ITEM_TYPE_CUSTOM_ROLE
from cogs.economy._shop_products import get_shop_product, register_shop_product
from cogs.economy.shop_custom_role import ShopCustomRoleCog
from test_shop import (
    FakeDatabase,
    FakeMember,
    FakeRole,
    GUILD_ID,
    USER_ID,
    make_bot,
)


BOT_ID = 9


class TestShopCustomRoleProduct(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.database = FakeDatabase()
        self.bot = make_bot(self.database)
        self.bot.user = SimpleNamespace(id=BOT_ID)
        self.cog = ShopCustomRoleCog(self.bot)
        self.addCleanup(self.cog.cog_unload)
        self.guild = SimpleNamespace(
            id=GUILD_ID,
            unavailable=False,
            _roles={},
            create_role=AsyncMock(),
            fetch_roles=AsyncMock(return_value=[]),
        )
        bot_top = FakeRole(900, "bot-top", 100)
        self.bot_member = FakeMember(BOT_ID, top_role=bot_top)
        self.bot_member.guild_permissions = SimpleNamespace(manage_roles=True)
        self.member = FakeMember(USER_ID, top_role=FakeRole(11, "buyer-top", 20))
        self.member.guild = self.guild
        self.bot_member.guild = self.guild
        self.guild.me = self.bot_member
        self.guild.get_member = (
            lambda member_id: self.bot_member if member_id == BOT_ID else self.member
        )
        self.guild.get_role = lambda role_id: self.guild._roles.get(role_id)
        self.bot.get_guild = lambda guild_id: self.guild if guild_id == GUILD_ID else None
        self.database["shop_inventory"].documents.append({
            "guild_id": GUILD_ID, "user_id": USER_ID, "item_id": "custom_role",
            "item_type": "custom_role", "name": "Custom role",
            "expires_at": discord.utils.utcnow() + timedelta(days=30),
            "expiry_cleanup_pending": True,
        })

        async def create_role(**kwargs):
            role = FakeRole(77, kwargs["name"], 8)
            role.guild = self.guild
            role.colour = kwargs.get("colour", discord.Color(0))
            role.edit = AsyncMock()
            role.delete = AsyncMock()
            self.guild._roles[role.id] = role
            return role

        self.guild.create_role.side_effect = create_role

    async def _create_role(self) -> BoosterActionResult:
        return await self.cog._create_custom_role(
            guild=self.guild,
            member=self.member,
            color_spec=RoleColorSpec(primary=discord.Color(0xFF66B3)),
            role_name="Shop Pink",
        )

    async def test_expired_editor_cannot_create_or_update_role(self) -> None:
        for updating in (False, True):
            with self.subTest(updating=updating):
                self.database["shop_inventory"].documents[0]["expires_at"] = discord.utils.utcnow() + timedelta(days=30)
                if updating:
                    await self._create_role()
                view = await self._open_editor()
                self.database["shop_inventory"].documents[0]["expires_at"] = discord.utils.utcnow() - timedelta(seconds=1)
                result = await view.submitter(
                    SimpleNamespace(guild=self.guild, user=self.member),
                    RoleDesignDraft("Expired", RoleColorSpec(discord.Color(123))),
                )
                self.assertFalse(result.completed)
                if updating:
                    self.guild._roles[77].edit.assert_not_awaited()
                else:
                    self.guild.create_role.assert_not_awaited()
                view.stop()

    async def test_expiry_sweep_deletes_role_and_keeps_inventory(self) -> None:
        await self._create_role()
        role = self.guild._roles[77]
        self.database["shop_inventory"].documents[0]["expires_at"] = discord.utils.utcnow() - timedelta(seconds=1)
        await self.cog.rental.sweep()
        role.delete.assert_awaited_once()
        self.assertFalse(self.database["shop_custom_roles"].documents)
        self.assertEqual(len(self.database["shop_inventory"].documents), 1)
        self.assertFalse(self.database["shop_inventory"].documents[0]["expiry_cleanup_pending"])

    async def _open_editor(self):
        ctx = SimpleNamespace(
            reply=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
        )
        notice = await self.cog.use_item(
            guild=self.guild,
            member=self.member,
            item={"item_id": CUSTOM_ROLE_ITEM_ID},
            source=ctx,
        )
        self.assertIsNone(notice)
        return ctx.reply.await_args.kwargs["view"]

    async def test_submit_rechecks_booster_role_created_after_editor_opened(self) -> None:
        view = await self._open_editor()
        booster_role = FakeRole(70, "Booster look", 8)
        self.guild._roles[70] = booster_role
        self.database["booster_custom_roles"].documents.append(
            {"guild_id": GUILD_ID, "user_id": USER_ID, "role_id": 70}
        )

        result = await view.submitter(
            SimpleNamespace(guild=self.guild, user=self.member),
            RoleDesignDraft(
                role_name="Shop Pink",
                color_spec=RoleColorSpec(primary=discord.Color(0xFF66B3)),
            ),
        )

        self.assertFalse(result.completed)
        self.assertIn("Booster", result.message)
        self.guild.create_role.assert_not_awaited()
        self.assertFalse(self.database["shop_custom_roles"].documents)

    async def test_booster_conflict_is_verified_on_cache_miss(self) -> None:
        self.database["booster_custom_roles"].documents.append(
            {"guild_id": GUILD_ID, "user_id": USER_ID, "role_id": 70}
        )
        self.guild.fetch_roles.return_value = [FakeRole(70, "Booster look", 8)]

        result = await self._create_role()

        self.assertFalse(result.completed)
        self.assertIn("Booster", result.message)
        self.guild.fetch_roles.assert_awaited_once_with()
        self.guild.create_role.assert_not_awaited()

    async def test_duplicate_create_is_rejected_before_gateway_caches_role(self) -> None:
        first = await self._create_role()
        role = self.guild._roles.pop(77)
        self.guild.fetch_roles.return_value = [role]

        second = await self._create_role()

        self.assertTrue(first.completed)
        self.assertFalse(second.completed)
        self.guild.create_role.assert_awaited_once()
        self.guild.fetch_roles.assert_awaited_once_with()
        record = self.database["shop_custom_roles"].find_one(
            {"guild_id": GUILD_ID, "user_id": USER_ID}
        )
        self.assertEqual(record["role_id"], role.id)
        role.delete.assert_not_awaited()

    async def test_failed_verification_cannot_create_or_replace_saved_role(self) -> None:
        for collection in ("shop_custom_roles", "booster_custom_roles"):
            with self.subTest(collection=collection):
                record = {"guild_id": GUILD_ID, "user_id": USER_ID, "role_id": 70}
                self.database[collection].documents.append(record)
                self.guild.fetch_roles.side_effect = discord.HTTPException(
                    SimpleNamespace(status=503, reason="Unavailable"), "retry later"
                )

                result = await self._create_role()

                self.assertFalse(result.completed)
                self.assertIn("xác minh", result.message)
                self.guild.create_role.assert_not_awaited()
                self.assertEqual(self.database[collection].documents, [record])
                self.database[collection].documents.clear()

    async def test_confirmed_deleted_role_can_be_recreated(self) -> None:
        self.database["shop_custom_roles"].documents.append(
            {"guild_id": GUILD_ID, "user_id": USER_ID, "role_id": 70}
        )
        self.guild.fetch_roles.return_value = []

        result = await self._create_role()

        self.assertTrue(result.completed)
        self.guild.fetch_roles.assert_awaited_once_with()
        self.guild.create_role.assert_awaited_once()
        self.assertEqual(self.database["shop_custom_roles"].documents[0]["role_id"], 77)

    async def test_leave_deletes_uncached_role_before_removing_record(self) -> None:
        await self._create_role()
        role = self.guild._roles.pop(77)
        self.guild.fetch_roles.return_value = [role]

        await self.cog._cleanup_member(self.guild, USER_ID, reason="test leave")

        role.delete.assert_awaited_once()
        self.assertFalse(self.database["shop_custom_roles"].documents)

    async def test_leave_keeps_record_when_role_cannot_be_verified(self) -> None:
        await self._create_role()
        role = self.guild._roles.pop(77)
        self.guild.fetch_roles.side_effect = discord.HTTPException(
            SimpleNamespace(status=503, reason="Unavailable"), "retry later"
        )

        await self.cog._cleanup_member(self.guild, USER_ID, reason="test leave")

        role.delete.assert_not_awaited()
        self.assertEqual(self.database["shop_custom_roles"].documents[0]["role_id"], 77)

    async def test_use_resolves_uncached_role_and_opens_update_editor(self) -> None:
        await self._create_role()
        role = self.guild._roles.pop(77)
        self.guild.fetch_roles.return_value = [role]

        view = await self._open_editor()
        result = await view.submitter(
            SimpleNamespace(guild=self.guild, user=self.member),
            RoleDesignDraft(
                role_name="Updated Pink",
                color_spec=RoleColorSpec(primary=discord.Color(0xFF66B3)),
            ),
        )

        self.assertTrue(result.completed)
        self.assertEqual(view.command_name, "update_custom_role")
        role.edit.assert_awaited_once()
        self.guild.create_role.assert_awaited_once()

    async def test_use_acknowledges_interaction_before_role_lookup(self) -> None:
        await self._create_role()
        role = self.guild._roles.pop(77)
        response = SimpleNamespace(is_done=Mock(return_value=False), defer=AsyncMock())

        async def defer():
            response.is_done.return_value = True

        async def fetch_roles():
            response.defer.assert_awaited_once()
            return [role]

        response.defer.side_effect = defer
        self.guild.fetch_roles.side_effect = fetch_roles
        interaction = Mock(spec=discord.Interaction)
        interaction.response = response
        interaction.edit_original_response = AsyncMock()

        notice = await self.cog.use_item(
            guild=self.guild,
            member=self.member,
            item={"item_id": CUSTOM_ROLE_ITEM_ID},
            source=interaction,
        )

        self.assertIsNone(notice)
        interaction.edit_original_response.assert_awaited_once()
        view = interaction.edit_original_response.await_args.kwargs["view"]
        self.assertEqual(view.command_name, "update_custom_role")

    async def _assert_concurrent_creation(self, *, booster_first: bool) -> None:
        booster_cog = BoosterCustomRoleCog(self.bot)
        self.member.premium_since = object()
        entered = asyncio.Event()
        release = asyncio.Event()
        original_create = self.guild.create_role.side_effect

        async def create_role(**kwargs):
            entered.set()
            await release.wait()
            role = await original_create(**kwargs)
            self.guild._roles.pop(role.id)
            self.guild.fetch_roles.return_value = [role]
            return role

        self.guild.create_role.side_effect = create_role

        async def create_booster_role():
            return await booster_cog._create_custom_role(
                guild=self.guild,
                member=self.member,
                color_spec=RoleColorSpec(primary=discord.Color(0xFF66B3)),
                role_name="Booster Pink",
                icon_attachment=None,
            )

        first = create_booster_role if booster_first else self._create_role
        second = self._create_role if booster_first else create_booster_role
        first_task = asyncio.create_task(first())
        second_task = None
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            second_task = asyncio.create_task(second())
            await asyncio.sleep(0)
            release.set()
            results = await asyncio.wait_for(
                asyncio.gather(first_task, second_task), timeout=2
            )
        finally:
            release.set()
            tasks = [task for task in (first_task, second_task) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        self.assertEqual([result.completed for result in results], [True, False])
        self.guild.create_role.assert_awaited_once()
        self.assertEqual(
            len(self.database["booster_custom_roles"].documents)
            + len(self.database["shop_custom_roles"].documents),
            1,
        )

    async def test_paid_entitlement_reserves_role_before_creation(self) -> None:
        self.member.premium_since = object()
        booster = BoosterCustomRoleCog(self.bot)
        result = await booster._create_custom_role(
            guild=self.guild, member=self.member,
            color_spec=RoleColorSpec(discord.Color(123)),
            role_name="Booster", icon_attachment=None,
        )
        self.assertFalse(result.completed)
        self.assertIn("còn hạn", result.message)
        self.guild.create_role.assert_not_awaited()

    async def test_booster_waits_for_inflight_shop_creation(self) -> None:
        await self._assert_concurrent_creation(booster_first=False)

    async def test_finished_editors_are_released(self) -> None:
        for action in ("complete", "cancel", "timeout", "timeout_missing", "timeout_error"):
            with self.subTest(action=action):
                view = await self._open_editor()
                interaction = SimpleNamespace(
                    response=SimpleNamespace(defer=AsyncMock(), edit_message=AsyncMock()),
                    edit_original_response=AsyncMock(),
                )
                if action == "complete":
                    view.draft = RoleDesignDraft(
                        role_name="Pink",
                        color_spec=RoleColorSpec(primary=discord.Color(0xFF66B3)),
                    )
                    view.submitter = AsyncMock(
                        return_value=BoosterActionResult(True, "Done")
                    )
                    await view.confirm(interaction)
                elif action == "cancel":
                    await view.cancel(interaction)
                else:
                    if action == "timeout_missing":
                        view.message = None
                    elif action == "timeout_error":
                        view.message.edit.side_effect = discord.HTTPException(
                            SimpleNamespace(status=404, reason="Not Found"), "gone"
                        )
                    await view.on_timeout()
                self.assertTrue(view.is_finished())
                self.assertFalse(self.cog._views)

    def test_product_registers_custom_role_type(self) -> None:
        self.assertIs(get_shop_product(ITEM_TYPE_CUSTOM_ROLE), self.cog)

    async def test_buy_denial_when_bot_cannot_manage_roles(self) -> None:
        self.bot_member.guild_permissions = SimpleNamespace(manage_roles=False)
        denial = await self.cog.buy_denial(
            self.guild,
            self.member,
            {"item_id": CUSTOM_ROLE_ITEM_ID, "item_type": ITEM_TYPE_CUSTOM_ROLE},
        )
        self.assertIsNotNone(denial)
        assert denial is not None
        self.assertIn("Manage Roles", denial)
        self.assertIn("chưa bị trừ", denial)

    async def test_buy_denial_when_booster_role_exists(self) -> None:
        booster_role = FakeRole(70, "Booster look", 8)
        self.guild._roles[70] = booster_role
        self.database["booster_custom_roles"].documents.append(
            {"guild_id": GUILD_ID, "user_id": USER_ID, "role_id": 70}
        )
        denial = await self.cog.buy_denial(
            self.guild,
            self.member,
            {"item_id": CUSTOM_ROLE_ITEM_ID},
        )
        self.assertIn("Booster", denial or "")

    async def test_create_custom_role_persists_and_assigns(self) -> None:
        result = await self.cog._create_custom_role(
            guild=self.guild,
            member=self.member,
            color_spec=RoleColorSpec(primary=discord.Color(0xFF66B3)),
            role_name="Shop Pink",
        )
        self.assertTrue(result.completed)
        record = self.database["shop_custom_roles"].find_one(
            {"guild_id": GUILD_ID, "user_id": USER_ID}
        )
        assert record is not None
        self.assertEqual(record["role_name"], "Shop Pink")
        self.member.add_roles.assert_awaited_once()

    async def test_duplicate_create_is_rejected(self) -> None:
        first = await self.cog._create_custom_role(
            guild=self.guild,
            member=self.member,
            color_spec=RoleColorSpec(primary=discord.Color(0xFF66B3)),
            role_name="Shop Pink",
        )
        second = await self.cog._create_custom_role(
            guild=self.guild,
            member=self.member,
            color_spec=RoleColorSpec(primary=discord.Color(0x5865F2)),
            role_name="Shop Blue",
        )
        self.assertTrue(first.completed)
        self.assertFalse(second.completed)
        self.assertIn("đã có custom role", second.message)

    async def test_update_renames_existing_role(self) -> None:
        created = await self.cog._create_custom_role(
            guild=self.guild,
            member=self.member,
            color_spec=RoleColorSpec(primary=discord.Color(0xFF66B3)),
            role_name="Shop Pink",
        )
        self.assertTrue(created.completed)
        role = self.guild._roles[77]
        role.edit = AsyncMock()
        result = await self.cog._update_custom_role(
            guild=self.guild,
            member=self.member,
            role=role,
            color_spec=RoleColorSpec(primary=discord.Color(0x00FF00)),
            role_name="Shop Green",
        )
        self.assertTrue(result.completed)
        role.edit.assert_awaited()
        record = self.database["shop_custom_roles"].find_one(
            {"guild_id": GUILD_ID, "user_id": USER_ID}
        )
        assert record is not None
        self.assertEqual(record["role_name"], "Shop Green")

    async def test_use_opens_editor_from_context(self) -> None:
        ctx = SimpleNamespace(
            guild=self.guild,
            author=self.member,
            reply=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
        )
        notice = await self.cog.use_item(
            guild=self.guild,
            member=self.member,
            item={"item_id": CUSTOM_ROLE_ITEM_ID, "item_type": ITEM_TYPE_CUSTOM_ROLE},
            source=ctx,
        )
        self.assertIsNone(notice)
        ctx.reply.assert_awaited_once()
        self.assertTrue(self.cog._views)

    async def test_leave_deletes_role_and_record(self) -> None:
        await self.cog._create_custom_role(
            guild=self.guild,
            member=self.member,
            color_spec=RoleColorSpec(primary=discord.Color(0xFF66B3)),
            role_name="Shop Pink",
        )
        role = self.guild._roles[77]
        role.delete = AsyncMock()
        await self.cog._cleanup_member(
            self.guild,
            USER_ID,
            reason="test leave",
        )
        role.delete.assert_awaited_once()
        self.assertIsNone(
            self.database["shop_custom_roles"].find_one(
                {"guild_id": GUILD_ID, "user_id": USER_ID}
            )
        )

    async def test_failed_create_rolls_back_untracked_role(self) -> None:
        self.database["shop_custom_roles"].update_one = Mock(
            side_effect=RuntimeError("mongo down")
        )
        result = await self.cog._create_custom_role(
            guild=self.guild,
            member=self.member,
            color_spec=RoleColorSpec(primary=discord.Color(0xFF66B3)),
            role_name="Shop Pink",
        )
        self.assertFalse(result.completed)
        self.assertIn("thu hồi", result.message)
        role = self.guild._roles[77]
        role.delete.assert_awaited_once()


class TestShopCustomRoleBoosterConflict(unittest.TestCase):
    def test_registry_restored_after_custom_role_cog(self) -> None:
        previous = get_shop_product(ITEM_TYPE_CUSTOM_ROLE)
        cog = ShopCustomRoleCog(make_bot(FakeDatabase()))
        try:
            self.assertIs(get_shop_product(ITEM_TYPE_CUSTOM_ROLE), cog)
            cog.cog_unload()
            self.assertIsNone(get_shop_product(ITEM_TYPE_CUSTOM_ROLE))
        finally:
            if previous is not None:
                register_shop_product(previous)


if __name__ == "__main__":
    unittest.main()
