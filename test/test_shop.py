import unittest
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from pymongo.errors import DuplicateKeyError, PyMongoError

from cogs.economy._shop_catalog import CatalogProduct
from cogs.economy._shop_helpers import (
    CUSTOM_ROLE_ITEM_ID,
    ITEM_TYPE_BADGE,
    ITEM_TYPE_CUSTOM_ROLE,
    ITEM_TYPE_ROLE,
    catalog_option_description,
    catalog_option_label,
    is_reserved_item_id,
    item_icon,
    item_type_label,
)
from cogs.economy._shop_products import (
    get_shop_product,
    register_shop_product,
)
from cogs.economy._shop_store import ShopStore
from cogs.economy._shop_ui import (
    PANEL_INVENTORY,
    PANEL_STORE,
    SHOP_BUY_CUSTOM_ID,
    SHOP_CLOSE_CUSTOM_ID,
    SHOP_UI_TIMEOUT_SECONDS,
    ShopView,
)
from cogs.economy.shop import ShopCog
from cogs.roles._role_safety import dangerous_permission_names


NOW = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
GUILD_ID = 100
USER_ID = 42


class FakeCollection:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = deepcopy(documents or [])
        self.unique_indexes: list[tuple[str, ...]] = []
        self.index_error: Exception | None = None

    def create_index(self, keys, unique: bool = False, **kwargs) -> str:
        if self.index_error is not None:
            raise self.index_error
        if unique:
            if isinstance(keys, str):
                self.unique_indexes.append((keys,))
            else:
                self.unique_indexes.append(tuple(key for key, _direction in keys))
        return str(kwargs.get("name", "index"))

    def find_one(self, query: dict) -> dict | None:
        document = next(
            (item for item in self.documents if self._matches(item, query)),
            None,
        )
        return deepcopy(document) if document is not None else None

    def find(self, query: dict) -> "FakeCursor":
        return FakeCursor(
            [
                deepcopy(document)
                for document in self.documents
                if self._matches(document, query)
            ]
        )

    def update_one(
        self,
        query: dict,
        update: dict,
        *,
        upsert: bool = False,
    ) -> SimpleNamespace:
        document = next(
            (item for item in self.documents if self._matches(item, query)),
            None,
        )
        inserted = False
        if document is None and upsert:
            document = {
                key: deepcopy(value)
                for key, value in query.items()
                if "." not in key and not isinstance(value, dict)
            }
            self.documents.append(document)
            inserted = True
        if document is None:
            return SimpleNamespace(modified_count=0, matched_count=0, upserted_id=None)

        before = deepcopy(document)
        if inserted:
            document.update(deepcopy(update.get("$setOnInsert", {})))
        document.update(deepcopy(update.get("$set", {})))
        for key, amount in update.get("$inc", {}).items():
            document[key] = int(document.get(key, 0)) + int(amount)
        for key in update.get("$unset", {}):
            document.pop(key, None)
        return SimpleNamespace(
            modified_count=int(document != before),
            matched_count=1,
            upserted_id=1 if inserted else None,
        )

    def find_one_and_update(self, query: dict, update: dict, **kwargs) -> dict | None:
        document = next(
            (item for item in self.documents if self._matches(item, query)),
            None,
        )
        if document is None:
            if not kwargs.get("upsert"):
                return None
            self.update_one(query, update, upsert=True)
            return self.find_one(query)
        document.update(deepcopy(update.get("$set", {})))
        for key, amount in update.get("$inc", {}).items():
            document[key] = int(document.get(key, 0)) + int(amount)
        return deepcopy(document)

    def update_many(self, query: dict, update: dict) -> SimpleNamespace:
        matched = [deepcopy(row) for row in self.documents if self._matches(row, query)]
        for row in matched:
            self.update_one(row, update)
        return SimpleNamespace(matched_count=len(matched), modified_count=len(matched))

    def insert_one(self, document: dict) -> SimpleNamespace:
        for fields in self.unique_indexes:
            key = tuple(document.get(field) for field in fields)
            for existing in self.documents:
                if tuple(existing.get(field) for field in fields) == key:
                    raise DuplicateKeyError("duplicate")
        self.documents.append(deepcopy(document))
        return SimpleNamespace(inserted_id=len(self.documents))

    def delete_one(self, query: dict) -> SimpleNamespace:
        for index, document in enumerate(self.documents):
            if self._matches(document, query):
                del self.documents[index]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    @staticmethod
    def _matches(document: dict, query: dict) -> bool:
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict):
                for operator, operand in expected.items():
                    if operator == "$exists":
                        if (key in document) != operand:
                            return False
                        continue
                    left, right = actual, operand
                    if isinstance(left, datetime) and isinstance(right, datetime):
                        left = left.replace(tzinfo=timezone.utc) if left.tzinfo is None else left
                        right = right.replace(tzinfo=timezone.utc) if right.tzinfo is None else right
                    if operator == "$gte" and (left is None or left < right):
                        return False
                    if operator == "$lte" and (left is None or left > right):
                        return False
                    if operator not in {"$gte", "$lte"}:
                        raise AssertionError(f"Unsupported query operator: {operator}")
                continue
            if actual != expected:
                return False
        return True


class FakeCursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents

    def sort(self, specification, direction: int | None = None) -> "FakeCursor":
        if isinstance(specification, str):
            specification = [(specification, 1 if direction is None else direction)]
        for key, sort_direction in reversed(list(specification)):
            self.documents.sort(
                key=lambda document, item_key=key: document.get(item_key) or 0,
                reverse=sort_direction < 0,
            )
        return self

    def limit(self, amount: int) -> "FakeCursor":
        self.documents = self.documents[:amount]
        return self

    def __iter__(self):
        return iter(self.documents)


class FakeDatabase:
    def __init__(self, **collections: FakeCollection) -> None:
        self.collections = dict(collections)

    def __getitem__(self, name: str) -> FakeCollection:
        if name not in self.collections:
            self.collections[name] = FakeCollection()
        return self.collections[name]


class FakeRole:
    def __init__(
        self,
        role_id: int,
        name: str,
        position: int,
        *,
        managed: bool = False,
        default: bool = False,
        permissions: object | None = None,
    ) -> None:
        self.id = role_id
        self.name = name
        self.position = position
        self.managed = managed
        self.mention = f"<@&{role_id}>"
        self.permissions = permissions or SimpleNamespace(administrator=False)
        self._default = default

    def is_default(self) -> bool:
        return self._default

    def __ge__(self, other: "FakeRole") -> bool:
        return self.position >= other.position

    def __gt__(self, other: "FakeRole") -> bool:
        return self.position > other.position


class FakeMember:
    def __init__(
        self,
        member_id: int,
        *,
        roles: list[FakeRole] | None = None,
        top_role: FakeRole | None = None,
        display_name: str = "Buyer",
        owner: bool = False,
    ) -> None:
        self.id = member_id
        self.roles = list(roles or [])
        self.top_role = top_role or FakeRole(1, "member", 1)
        self.display_name = display_name
        self.mention = f"<@{member_id}>"
        self.guild = None
        self._owner = owner
        self.add_roles = AsyncMock()

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FakeMember) and other.id == self.id


def role_item(**overrides: object) -> dict:
    item = {
        "guild_id": GUILD_ID,
        "item_id": "pink",
        "name": "Pink Role",
        "description": "A cosmetic pink role",
        "price": 100,
        "item_type": ITEM_TYPE_ROLE,
        "role_id": 55,
        "enabled": True,
    }
    item.update(overrides)
    return item


def badge_item(**overrides: object) -> dict:
    item = {
        "guild_id": GUILD_ID,
        "item_id": "helper",
        "name": "Helper",
        "description": "Badge Helper",
        "price": 250,
        "item_type": ITEM_TYPE_BADGE,
        "enabled": True,
    }
    item.update(overrides)
    return item


def make_guild(
    *,
    role: FakeRole | None = None,
    bot_top: FakeRole | None = None,
    member: FakeMember | None = None,
) -> SimpleNamespace:
    bot_top = bot_top or FakeRole(900, "bot-top", 100)
    sold = role or FakeRole(55, "Pink Role", 5)
    bot_member = FakeMember(9, top_role=bot_top)
    bot_member.guild_permissions = SimpleNamespace(manage_roles=True)
    buyer = member or FakeMember(USER_ID, top_role=FakeRole(11, "buyer-top", 20))
    roles = {sold.id: sold, bot_top.id: bot_top, buyer.top_role.id: buyer.top_role}
    guild = SimpleNamespace(
        id=GUILD_ID,
        me=bot_member,
        owner=FakeMember(1, owner=True) if False else SimpleNamespace(id=1),
        get_role=lambda role_id: roles.get(role_id),
        get_member=lambda member_id: bot_member if member_id == 9 else buyer,
    )
    buyer.guild = guild
    bot_member.guild = guild
    return guild, buyer, sold, bot_member


def make_bot(database: FakeDatabase) -> SimpleNamespace:
    return SimpleNamespace(
        db=database,
        user=SimpleNamespace(id=9),
        command_prefix="!tf ",
        global_vars={},
    )


def make_interaction(
    *,
    user: FakeMember,
    guild: SimpleNamespace,
    acknowledged: dict | None = None,
) -> SimpleNamespace:
    state = acknowledged if acknowledged is not None else {"done": False}

    async def acknowledge(*args: object, **kwargs: object) -> None:
        state["done"] = True

    interaction = SimpleNamespace(
        guild=guild,
        guild_id=guild.id,
        user=user,
        response=SimpleNamespace(
            is_done=lambda: state["done"],
            send_message=AsyncMock(side_effect=acknowledge),
            edit_message=AsyncMock(side_effect=acknowledge),
            defer=AsyncMock(side_effect=acknowledge),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        message=SimpleNamespace(edit=AsyncMock()),
    )
    return interaction


class TestShopHelperExtensions(unittest.TestCase):
    def test_reserved_custom_role_id_and_icons(self) -> None:
        self.assertTrue(is_reserved_item_id(CUSTOM_ROLE_ITEM_ID))
        self.assertFalse(is_reserved_item_id("pink"))
        self.assertEqual(item_icon(ITEM_TYPE_ROLE), "🎭")
        self.assertEqual(item_icon(ITEM_TYPE_BADGE), "🏷️")
        self.assertEqual(item_icon(ITEM_TYPE_CUSTOM_ROLE), "🎨")
        self.assertEqual(item_type_label(ITEM_TYPE_CUSTOM_ROLE), "Custom role")

    def test_catalog_select_option_text(self) -> None:
        item = role_item(name="Pink Role", price=1000)
        self.assertEqual(catalog_option_label(item), "Pink Role")
        self.assertEqual(catalog_option_description(item), "1,000 TC · Role")

    def test_dangerous_permission_names_alias(self) -> None:
        permissions = SimpleNamespace(administrator=True, manage_roles=False)
        self.assertEqual(dangerous_permission_names(permissions), ("administrator",))


class TestShopStore(unittest.TestCase):
    def setUp(self) -> None:
        self.database = FakeDatabase()
        self.store = ShopStore(self.database)
        self.store.upsert_item(
            guild_id=GUILD_ID,
            item_id="pink",
            name="Pink Role",
            description="cosmetic",
            price=100,
            item_type=ITEM_TYPE_ROLE,
            updated_by=1,
            role_id=55,
        )
        self.database["user_accounts"].documents.append(
            {"user_id": USER_ID, "balance": 150}
        )

    def test_purchase_debits_inventory_and_log(self) -> None:
        item = self.store.find_enabled(GUILD_ID, "pink")
        assert item is not None
        with patch("discord.utils.utcnow", return_value=NOW):
            result = self.store.purchase(
                guild_id=GUILD_ID, user_id=USER_ID, item=item
            )
        self.assertTrue(result.success)
        self.assertEqual(result.balance, 50)
        self.assertTrue(self.store.owns(GUILD_ID, USER_ID, "pink"))
        self.assertEqual(self.store.get_balance(USER_ID), 50)
        log = self.database["transaction_logs"].documents[0]
        self.assertEqual(log["type"], "shop_purchase")
        self.assertEqual(log["amount"], 100)
        self.assertEqual(log["balance_after"], 50)

    def test_purchase_rejects_insufficient_funds_without_inventory(self) -> None:
        self.database["user_accounts"].documents[0]["balance"] = 10
        item = self.store.find_enabled(GUILD_ID, "pink")
        assert item is not None
        result = self.store.purchase(guild_id=GUILD_ID, user_id=USER_ID, item=item)
        self.assertFalse(result.success)
        self.assertIn("không có đủ Trap Coin", result.message)
        self.assertFalse(self.store.owns(GUILD_ID, USER_ID, "pink"))
        self.assertEqual(self.store.get_balance(USER_ID), 10)

    def test_duplicate_purchase_is_rejected(self) -> None:
        item = self.store.find_enabled(GUILD_ID, "pink")
        assert item is not None
        first = self.store.purchase(guild_id=GUILD_ID, user_id=USER_ID, item=item)
        second = self.store.purchase(guild_id=GUILD_ID, user_id=USER_ID, item=item)
        self.assertTrue(first.success)
        self.assertFalse(second.success)
        self.assertTrue(second.already_owned)
        self.assertEqual(self.store.get_balance(USER_ID), 50)

    def test_inventory_insert_failure_refunds(self) -> None:
        item = self.store.find_enabled(GUILD_ID, "pink")
        assert item is not None

        def fail_insert(_document: dict) -> None:
            raise PyMongoError("unavailable")

        self.database["shop_inventory"].insert_one = fail_insert  # type: ignore[method-assign]
        result = self.store.purchase(guild_id=GUILD_ID, user_id=USER_ID, item=item)
        self.assertFalse(result.success)
        self.assertIn("hoàn lại", result.message)
        self.assertEqual(self.store.get_balance(USER_ID), 150)

    def test_disable_hides_enabled_listing(self) -> None:
        self.assertTrue(self.store.disable_item(GUILD_ID, "pink"))
        self.assertIsNone(self.store.find_enabled(GUILD_ID, "pink"))
        self.assertIsNotNone(self.store.find_item(GUILD_ID, "pink"))
        self.assertFalse(self.store.disable_item(GUILD_ID, "missing"))

    def test_active_badge_round_trip(self) -> None:
        self.store.set_active_badge(
            user_id=USER_ID, guild_id=GUILD_ID, item_id="helper", name="Helper"
        )
        badge = self.store.get_active_badge(USER_ID, GUILD_ID)
        self.assertEqual(badge["item_id"], "helper")
        self.store.clear_active_badge(USER_ID)
        self.assertIsNone(self.store.get_active_badge(USER_ID, GUILD_ID))


class TestCatalogProduct(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.database = FakeDatabase()
        self.store = ShopStore(self.database)
        self.product = CatalogProduct(ITEM_TYPE_ROLE, self.store)
        self.guild, self.member, self.role, _bot = make_guild()

    async def test_buy_denial_for_existing_and_dangerous_roles(self) -> None:
        item = role_item()
        self.assertIsNone(await self.product.buy_denial(self.guild, self.member, item))
        self.member.roles.append(self.role)
        self.assertEqual(
            await self.product.buy_denial(self.guild, self.member, item),
            "Bạn đã có role của vật phẩm này.",
        )

        dangerous = FakeRole(
            56,
            "Admin",
            5,
            permissions=SimpleNamespace(administrator=True),
        )
        self.guild.get_role = lambda role_id: dangerous if role_id == 56 else None
        denial = await self.product.buy_denial(self.guild, self.member, role_item(role_id=56))
        self.assertIsNotNone(denial)
        assert denial is not None
        self.assertIn("chưa bị trừ", denial)

    async def test_use_assigns_role_and_equips_badge(self) -> None:
        notice = await self.product.use_item(
            guild=self.guild,
            member=self.member,
            item=role_item(),
            source=SimpleNamespace(),
        )
        self.assertIn("Đã kích hoạt", notice or "")
        self.member.add_roles.assert_awaited_once()

        badge_product = CatalogProduct(ITEM_TYPE_BADGE, self.store)
        equipped = await badge_product.use_item(
            guild=self.guild,
            member=self.member,
            item=badge_item(),
            source=SimpleNamespace(),
        )
        self.assertEqual(equipped, "Đã trang bị badge **Helper**.")
        self.assertEqual(
            self.store.get_active_badge(USER_ID, GUILD_ID)["item_id"],
            "helper",
        )


class TestShopCogAndUI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.database = FakeDatabase()
        self.bot = make_bot(self.database)
        self.cog = ShopCog(self.bot)
        self.addCleanup(self.cog.cog_unload)
        self.store = self.cog.store
        self.store.upsert_item(
            guild_id=GUILD_ID,
            item_id="pink",
            name="Pink Role",
            description="cosmetic",
            price=100,
            item_type=ITEM_TYPE_ROLE,
            updated_by=1,
            role_id=55,
        )
        self.store.upsert_item(
            guild_id=GUILD_ID,
            item_id="helper",
            name="Helper",
            description="badge",
            price=250,
            item_type=ITEM_TYPE_BADGE,
            updated_by=1,
        )
        self.database["user_accounts"].documents.append(
            {"user_id": USER_ID, "balance": 400}
        )
        self.guild, self.member, self.role, _bot = make_guild()

    def test_catalog_products_are_registered(self) -> None:
        self.assertIsNotNone(get_shop_product(ITEM_TYPE_ROLE))
        self.assertIsNotNone(get_shop_product(ITEM_TYPE_BADGE))

    def test_view_structure_and_owner_lock(self) -> None:
        view = ShopView(
            self.cog,
            guild_id=GUILD_ID,
            author_id=USER_ID,
            prefix="!tf ",
        )
        self.addCleanup(view.stop)
        self.assertEqual(view.timeout, SHOP_UI_TIMEOUT_SECONDS)
        self.assertEqual(view.panel, PANEL_STORE)
        custom_ids = [getattr(child, "custom_id", None) for child in view.children]
        self.assertIn(SHOP_BUY_CUSTOM_ID, custom_ids)
        self.assertIn(SHOP_CLOSE_CUSTOM_ID, custom_ids)
        buy = next(
            child
            for child in view.children
            if getattr(child, "custom_id", None) == SHOP_BUY_CUSTOM_ID
        )
        self.assertTrue(buy.disabled)

    async def test_stranger_cannot_use_panel(self) -> None:
        view = ShopView(
            self.cog,
            guild_id=GUILD_ID,
            author_id=USER_ID,
            prefix="!tf ",
        )
        self.addCleanup(view.stop)
        stranger = FakeMember(99)
        interaction = make_interaction(user=stranger, guild=self.guild)
        self.assertFalse(await view.interaction_check(interaction))
        interaction.response.send_message.assert_awaited()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    async def test_expired_panels_are_released_even_if_message_edit_fails(self) -> None:
        for message_state in ("editable", "missing", "deleted"):
            with self.subTest(message_state=message_state):
                view = ShopView(
                    self.cog,
                    guild_id=GUILD_ID,
                    author_id=USER_ID,
                    prefix="!tf ",
                )
                self.cog._views.add(view)
                if message_state != "missing":
                    view.message = SimpleNamespace(edit=AsyncMock())
                    if message_state == "deleted":
                        view.message.edit.side_effect = discord.HTTPException(
                            SimpleNamespace(status=404, reason="Not Found"), "gone"
                        )

                await view.on_timeout()

                self.assertTrue(view.is_finished())
                self.assertFalse(self.cog._views)
                self.assertTrue(all(child.disabled for child in view.children))

    async def test_buy_requires_confirmation_then_charges(self) -> None:
        view = ShopView(
            self.cog,
            guild_id=GUILD_ID,
            author_id=USER_ID,
            prefix="!tf ",
        )
        self.addCleanup(view.stop)
        view.selected_id = "pink"
        view.rebuild()
        first = make_interaction(user=self.member, guild=self.guild)
        await self.cog.handle_shop_action(first, view, "buy")
        self.assertEqual(view.confirming, ("buy", "pink", 100))
        self.assertEqual(self.store.get_balance(USER_ID), 400)
        self.assertFalse(self.store.owns(GUILD_ID, USER_ID, "pink"))

        second = make_interaction(user=self.member, guild=self.guild)
        await self.cog.handle_shop_action(second, view, "buy")
        self.assertTrue(self.store.owns(GUILD_ID, USER_ID, "pink"))
        self.assertEqual(self.store.get_balance(USER_ID), 300)

    async def test_inventory_panel_and_unequip(self) -> None:
        self.store.set_active_badge(
            user_id=USER_ID, guild_id=GUILD_ID, item_id="helper", name="Helper"
        )
        self.database["shop_inventory"].documents.append(
            {
                "guild_id": GUILD_ID,
                "user_id": USER_ID,
                "item_id": "helper",
                "item_type": ITEM_TYPE_BADGE,
                "name": "Helper",
            }
        )
        view = ShopView(
            self.cog,
            guild_id=GUILD_ID,
            author_id=USER_ID,
            prefix="!tf ",
        )
        self.addCleanup(view.stop)
        interaction = make_interaction(user=self.member, guild=self.guild)
        await self.cog.handle_shop_action(interaction, view, "inventory")
        self.assertEqual(view.panel, PANEL_INVENTORY)
        self.assertEqual([item["item_id"] for item in view.panel_items], ["helper"])

        unequip = make_interaction(user=self.member, guild=self.guild)
        await self.cog.handle_shop_action(unequip, view, "unequip")
        self.assertIsNone(self.store.get_active_badge(USER_ID, GUILD_ID))

    async def test_text_buy_and_reserved_role_id(self) -> None:
        ctx = SimpleNamespace(
            guild=self.guild,
            author=self.member,
            send=AsyncMock(),
            clean_prefix="!tf ",
        )
        await ShopCog.shop_buy.callback(self.cog, ctx, "pink")
        self.assertTrue(self.store.owns(GUILD_ID, USER_ID, "pink"))

        await ShopCog.shop_add_role.callback(
            self.cog,
            ctx,
            CUSTOM_ROLE_ITEM_ID,
            50,
            self.role,
        )
        ctx.send.assert_awaited()
        self.assertIn("dành riêng", ctx.send.await_args.args[0])

    async def test_add_custom_role_writes_fixed_listing(self) -> None:
        ctx = SimpleNamespace(
            guild=self.guild,
            author=self.member,
            send=AsyncMock(),
            clean_prefix="!tf ",
        )
        await ShopCog.shop_add_custom_role.callback(self.cog, ctx, 5000)
        item = self.store.find_enabled(GUILD_ID, CUSTOM_ROLE_ITEM_ID)
        assert item is not None
        self.assertEqual(item["item_type"], ITEM_TYPE_CUSTOM_ROLE)
        self.assertEqual(item["price"], 5000)


class TestShopProductRegistry(unittest.TestCase):
    def test_unload_unregisters_catalog_products(self) -> None:
        previous_role = get_shop_product(ITEM_TYPE_ROLE)
        previous_badge = get_shop_product(ITEM_TYPE_BADGE)
        cog = ShopCog(make_bot(FakeDatabase()))
        try:
            self.assertIsNotNone(get_shop_product(ITEM_TYPE_ROLE))
            cog.cog_unload()
            self.assertIsNone(get_shop_product(ITEM_TYPE_ROLE))
            self.assertIsNone(get_shop_product(ITEM_TYPE_BADGE))
        finally:
            if previous_role is not None:
                register_shop_product(previous_role)
            if previous_badge is not None:
                register_shop_product(previous_badge)


if __name__ == "__main__":
    unittest.main()
