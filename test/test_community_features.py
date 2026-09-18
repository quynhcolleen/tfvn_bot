import ast
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from discord.ext import commands

from cogs._beta_function import (
    BetaFunction,
    BetaFunctionError,
    beta_access_denial,
    beta_function_check,
    get_beta_role_ids,
    is_beta_function,
)
from cogs._feature_flags import cog_disabled
from cogs.economy._shop_helpers import (
    CUSTOM_ROLE_ITEM_ID,
    ITEM_TYPE_CUSTOM_ROLE,
    clean_display_text,
    format_price,
    is_reserved_item_id,
    item_icon,
    normalize_item_id,
    validate_price,
)
from cogs.mod._case_helpers import (
    can_moderate,
    clean_case_reason,
    format_audit_reason,
    normalize_case_status,
)
from cogs.operation._setup_helpers import (
    SetupCheck,
    parse_discord_id,
    summarize_checks,
)


ROOT = Path(__file__).resolve().parents[1]


class TestBetaFunction(unittest.TestCase):
    @staticmethod
    def _context(
        *,
        configured_roles: object,
        member_roles: list,
        existing_role_ids: tuple[int, ...] = (42, 84),
        environment: str = "production",
    ):
        guild = SimpleNamespace(
            get_role=lambda requested: (
                SimpleNamespace(id=requested)
                if requested in existing_role_ids
                else None
            )
        )
        bot = SimpleNamespace(
            environment=environment,
            global_vars={"BETA_ROLE_IDS": configured_roles},
        )
        author = SimpleNamespace(roles=member_roles)
        return SimpleNamespace(bot=bot, guild=guild, author=author)

    def test_role_setting_uses_database_and_ignores_environment(self):
        with patch.dict(
            os.environ,
            {"BETA_ROLE_IDS": "99, 100", "BETA_ROLE_ID": "101"},
            clear=True,
        ):
            configured = SimpleNamespace(
                global_vars={"BETA_ROLE_IDS": ["42", "84"]}
            )
            missing = SimpleNamespace(global_vars={})
            self.assertEqual(get_beta_role_ids(configured), {42, 84})
            self.assertEqual(get_beta_role_ids(missing), set())

    def test_any_configured_role_grants_access_in_any_runtime(self):
        missing_role = self._context(
            configured_roles=["42", "84"],
            member_roles=[],
        )
        allowed = self._context(
            configured_roles=["42", "84"],
            member_roles=[SimpleNamespace(id=84)],
            environment="production",
        )
        self.assertIn("Beta role", beta_access_denial(missing_role))
        self.assertIsNone(beta_access_denial(allowed))

    def test_missing_or_invalid_role_configuration_is_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            missing = self._context(
                configured_roles="",
                member_roles=[],
            )
            invalid = self._context(
                configured_roles="not-an-id",
                member_roles=[],
            )
            self.assertIn("chưa được cấu hình", beta_access_denial(missing))
            self.assertIn("chưa được cấu hình", beta_access_denial(invalid))

    def test_decorator_supports_both_discord_decorator_orders(self):
        @commands.command(name="beta_inner_order")
        @BetaFunction
        async def inner_order(ctx):
            return None

        @BetaFunction
        @commands.command(name="beta_outer_order")
        async def outer_order(ctx):
            return None

        for command in (inner_order, outer_order):
            with self.subTest(command=command.name):
                self.assertTrue(is_beta_function(command))
                self.assertIn(beta_function_check, command.checks)


class TestBetaFunctionCheck(unittest.IsolatedAsyncioTestCase):
    async def test_predicate_allows_role_member_and_raises_safe_denial(self):
        allowed = TestBetaFunction._context(
            configured_roles=["42", "84"],
            member_roles=[SimpleNamespace(id=42)],
        )
        denied = TestBetaFunction._context(
            configured_roles=["42", "84"],
            member_roles=[],
        )

        self.assertTrue(await beta_function_check(allowed))
        with self.assertRaises(BetaFunctionError) as raised:
            await beta_function_check(denied)
        self.assertIn("Beta role", raised.exception.user_message)


class TestCogFlags(unittest.TestCase):
    def test_disabled_cog_patterns(self):
        with patch.dict(
            os.environ,
            {"DISABLED_COGS": "cogs.nsfw.*,cogs.economy.shop"},
            clear=True,
        ):
            self.assertTrue(cog_disabled("cogs.nsfw.gelbooru"))
            self.assertTrue(cog_disabled("cogs.economy.shop"))
            self.assertFalse(cog_disabled("cogs.mod.cases"))


class TestShopHelpers(unittest.TestCase):
    def test_normalize_item_id(self):
        self.assertEqual(normalize_item_id("  Pink_Role "), "pink_role")
        self.assertEqual(normalize_item_id("badge-01"), "badge-01")

    def test_reject_invalid_item_id(self):
        for value in ("", "has space", "@role", "a" * 33):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_item_id(value)

    def test_validate_price_boundaries(self):
        self.assertEqual(validate_price(1), 1)
        self.assertEqual(validate_price(1_000_000_000), 1_000_000_000)
        for value in (0, -1, 1_000_000_001):
            with self.assertRaises(ValueError):
                validate_price(value)

    def test_display_and_price_formatting(self):
        self.assertEqual(clean_display_text("  Pink   Role ", fallback="x", limit=20), "Pink Role")
        self.assertEqual(format_price(1234567), "1,234,567 TC")

    def test_custom_role_reserved_id_and_icon(self):
        self.assertTrue(is_reserved_item_id(CUSTOM_ROLE_ITEM_ID))
        self.assertEqual(item_icon(ITEM_TYPE_CUSTOM_ROLE), "🎨")


class TestModerationCaseHelpers(unittest.TestCase):
    def test_clean_reason(self):
        self.assertEqual(clean_case_reason("  spam\n links  "), "spam links")
        self.assertEqual(clean_case_reason(""), "Không có lý do cụ thể")
        self.assertEqual(len(clean_case_reason("x" * 1200)), 1000)

    def test_case_status(self):
        self.assertEqual(normalize_case_status(" APPEALED "), "appealed")
        with self.assertRaises(ValueError):
            normalize_case_status("deleted")

    def test_audit_reason_limit(self):
        value = format_audit_reason("x" * 1200, "Moderator")
        self.assertEqual(len(value), 512)
        self.assertTrue(value.startswith("x" * 100))
        self.assertTrue(value.endswith(" (Requested by Moderator)"))

    def test_actor_hierarchy(self):
        guild = SimpleNamespace(owner_id=1)
        owner = SimpleNamespace(id=1, guild=guild, top_role=100)
        moderator = SimpleNamespace(id=2, guild=guild, top_role=50)
        member = SimpleNamespace(id=3, guild=guild, top_role=10)
        peer = SimpleNamespace(id=4, guild=guild, top_role=50)
        self.assertTrue(can_moderate(owner, moderator))
        self.assertTrue(can_moderate(moderator, member))
        self.assertFalse(can_moderate(moderator, peer))
        self.assertFalse(can_moderate(moderator, moderator))
        self.assertFalse(can_moderate(moderator, owner))


class TestSetupHelpers(unittest.TestCase):
    def test_parse_discord_id(self):
        self.assertEqual(parse_discord_id(" 123 "), 123)
        for value in (None, True, "", "abc", 0, -1):
            with self.subTest(value=value):
                self.assertIsNone(parse_discord_id(value))

    def test_summarize_checks(self):
        totals = summarize_checks(
            [
                SetupCheck("ok", "one", "done"),
                SetupCheck("warning", "two", "check"),
                SetupCheck("error", "three", "broken"),
                SetupCheck("ok", "four", "done"),
            ]
        )
        self.assertEqual(totals, {"ok": 2, "warning": 1, "error": 1})


class TestLoadableFeatureModules(unittest.TestCase):
    def test_every_public_feature_module_has_async_setup(self):
        paths = [
            "cogs/economy/shop.py",
            "cogs/economy/shop_custom_role.py",
            "cogs/mod/cases.py",
            "cogs/operation/setup_check.py",
        ]
        for relative_path in paths:
            with self.subTest(module=relative_path):
                tree = ast.parse(
                    (ROOT / relative_path).read_text(encoding="utf-8")
                )
                setups = [
                    node
                    for node in tree.body
                    if isinstance(node, ast.AsyncFunctionDef)
                    and node.name == "setup"
                ]
                self.assertEqual(len(setups), 1)


if __name__ == "__main__":
    unittest.main()
