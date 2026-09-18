import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from cogs.booster._role_colors import RoleColorSpec
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

        async def create_role(**kwargs):
            role = FakeRole(77, kwargs["name"], 8)
            role.guild = self.guild
            role.colour = kwargs.get("colour", discord.Color(0))
            role.edit = AsyncMock()
            role.delete = AsyncMock()
            self.guild._roles[role.id] = role
            return role

        self.guild.create_role.side_effect = create_role

    def test_product_registers_custom_role_type(self) -> None:
        self.assertIs(get_shop_product(ITEM_TYPE_CUSTOM_ROLE), self.cog)

    def test_buy_denial_when_bot_cannot_manage_roles(self) -> None:
        self.bot_member.guild_permissions = SimpleNamespace(manage_roles=False)
        denial = self.cog.buy_denial(
            self.guild,
            self.member,
            {"item_id": CUSTOM_ROLE_ITEM_ID, "item_type": ITEM_TYPE_CUSTOM_ROLE},
        )
        self.assertIsNotNone(denial)
        assert denial is not None
        self.assertIn("Manage Roles", denial)
        self.assertIn("chưa bị trừ", denial)

    def test_buy_denial_when_booster_role_exists(self) -> None:
        booster_role = FakeRole(70, "Booster look", 8)
        self.guild._roles[70] = booster_role
        self.database["booster_custom_roles"].documents.append(
            {"guild_id": GUILD_ID, "user_id": USER_ID, "role_id": 70}
        )
        denial = self.cog.buy_denial(
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
