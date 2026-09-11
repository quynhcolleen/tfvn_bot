import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from cogs.help import (
    HELP_SELECT_CUSTOM_ID,
    HELP_SELECT_PLACEHOLDER,
    HELP_TOPICS,
    HELP_TOPIC_MAP,
    HelpCog,
    HelpView,
    available_help_topics,
    build_help_embed,
    resolve_help_topic,
)
from cogs.interaction.nsfw_interaction import NSFWInteractionCog
from cogs.interaction.user_interaction import SFW_ACTION_SPECS
from cogs.mod.slowmode import SlowmodeCog


class TestHelpTopicData(unittest.TestCase):
    def test_every_topic_embed_and_dropdown_option_stays_within_api_limits(
        self,
    ) -> None:
        self.assertLessEqual(len(HELP_TOPICS), 25)
        self.assertEqual(len(HELP_TOPIC_MAP), len(HELP_TOPICS))
        self.assertEqual(
            len({topic.key for topic in HELP_TOPICS}),
            len(HELP_TOPICS),
        )

        for topic in HELP_TOPICS:
            with self.subTest(topic=topic.key):
                self.assertLessEqual(len(topic.key), 100)
                self.assertLessEqual(len(topic.label), 100)
                self.assertLessEqual(len(topic.option_description), 100)

                embed = build_help_embed(topic.key, "!tf ")
                self.assertLessEqual(len(embed), 6000)
                self.assertLessEqual(len(embed.title or ""), 256)
                self.assertLessEqual(len(embed.description or ""), 4096)
                self.assertLessEqual(len(embed.fields), 25)
                self.assertLessEqual(len(embed.footer.text or ""), 2048)
                for field in embed.fields:
                    self.assertLessEqual(len(field.name), 256)
                    self.assertLessEqual(len(field.value), 1024)

        view = make_help_view()
        select = view.topic_select
        self.assertLessEqual(len(select.options), 25)
        self.assertLessEqual(len(select.custom_id), 100)
        self.assertLessEqual(len(select.placeholder or ""), 150)
        self.assertEqual(select.min_values, 1)
        self.assertEqual(select.max_values, 1)
        self.assertEqual(select.custom_id, HELP_SELECT_CUSTOM_ID)
        self.assertEqual(select.placeholder, HELP_SELECT_PLACEHOLDER)

    def test_command_rendering_preserves_exact_prefix_spacing(self) -> None:
        embed = build_help_embed("economy", "!tf ")
        rendered = "\n".join(field.value for field in embed.fields)

        self.assertIn("`!tf daily`", rendered)
        self.assertIn("`!tf user_transactions`", rendered)
        self.assertNotIn("`!tf  ", rendered)
        self.assertNotIn("`!tfuser_transactions`", rendered)
        self.assertEqual(embed.footer.text, "Prefix: !tf • Menu chỉ dành cho người đã gọi lệnh")

        compact_prefix = build_help_embed("economy", "!")
        compact_rendered = "\n".join(
            field.value for field in compact_prefix.fields
        )
        self.assertIn("`!daily`", compact_rendered)
        self.assertNotIn("`! daily`", compact_rendered)

    def test_representative_command_signatures_match_registered_callbacks(
        self,
    ) -> None:
        rendered_topics = {
            key: "\n".join(
                field.value for field in build_help_embed(key, "!tf ").fields
            )
            for key in ("fun", "utilities", "moderation", "nsfw")
        }

        self.assertIn("`!tf femboycard`", rendered_topics["fun"])
        self.assertNotIn("femboycard [@user]", rendered_topics["fun"])
        self.assertIn(
            "`!tf custom_role <#RRGGBB[,#RRGGBB]> <tên role>`",
            rendered_topics["utilities"],
        )
        self.assertIn(
            "`!tf update_custom_role <#RRGGBB[,#RRGGBB]> <tên role>`",
            rendered_topics["utilities"],
        )
        self.assertIn(
            "`!tf nickchange [@user] [nickname] · reply + nickchange`",
            rendered_topics["moderation"],
        )
        self.assertIn(
            "`!tf roleroll [@user] [tên role] · reply + roleroll`",
            rendered_topics["moderation"],
        )
        self.assertIn(
            "`!tf roleunroll [@user] [tên role] · reply + roleunroll`",
            rendered_topics["moderation"],
        )
        self.assertIn(
            "`!tf rolecopy [@source] [@target] [lý do] · reply + rolecopy`",
            rendered_topics["moderation"],
        )
        self.assertIn(
            "`!tf setup` (alias: `diagnose`)",
            rendered_topics["moderation"],
        )
        self.assertIn("`!tf mrank <tháng> <năm>`", rendered_topics["nsfw"])
        self.assertIn("`!tf softotp`", rendered_topics["utilities"])
        self.assertIn("`!tf softotp get <challenge>`", rendered_topics["utilities"])
        self.assertIn(
            "`!tf softotp verify <challenge> <otp> [@user]`",
            rendered_topics["utilities"],
        )

    def test_every_source_command_and_alias_is_documented(self) -> None:
        source_commands, source_aliases, beta_commands = source_command_inventory()
        documented_commands = {
            usage.command_name
            for topic in HELP_TOPICS
            for section in topic.sections
            for entry in section.entries
            for usage in entry.usages
        }
        documented_aliases: dict[str, set[str]] = {}
        for topic in HELP_TOPICS:
            for section in topic.sections:
                for entry in section.entries:
                    if not entry.aliases:
                        continue
                    command_names = {usage.command_name for usage in entry.usages}
                    self.assertEqual(
                        len(command_names),
                        1,
                        "Aliases must be attached to one unambiguous command.",
                    )
                    documented_aliases.setdefault(command_names.pop(), set()).update(
                        entry.aliases
                    )

        expected_aliases = {
            command_name: aliases
            for command_name, aliases in source_aliases.items()
            if command_name not in beta_commands and aliases
        }

        self.assertEqual(
            source_commands - beta_commands,
            documented_commands,
            "Every canonical non-Beta command must have a static help entry.",
        )
        self.assertEqual(
            expected_aliases,
            documented_aliases,
            "Every alias must be shown with the command that registers it.",
        )

    def test_beta_commands_render_in_overview_when_supplied(self) -> None:
        rendered = "\n".join(
            field.value
            for field in build_help_embed(
                "overview",
                "!tf ",
                beta_commands=("beta_preview",),
            ).fields
        )

        self.assertIn("`!tf beta_preview`", rendered)
        self.assertIn("Beta role", rendered)

    def test_functions_quick_index_matches_source_commands(self) -> None:
        source_commands, _, _ = source_command_inventory()
        functions_path = Path(__file__).resolve().parents[1] / "FUNCTIONS.md"
        functions_text = functions_path.read_text(encoding="utf-8")
        quick_index = functions_text.split(
            "## Quick index by prefix (flat list)",
            1,
        )[1].split("```", 2)[1]
        documented_commands = {
            command.strip()
            for line in quick_index.splitlines()
            for command in line.split(",")
            if command.strip()
        }

        self.assertEqual(
            source_commands,
            documented_commands,
            "FUNCTIONS.md's flat index must cover every registered command.",
        )

    def test_resolve_topic_aliases_and_unknown_fallback(self) -> None:
        aliases = {
            " general ": "overview",
            "TỔNG_QUAN": "overview",
            "TRAP_COIN": "economy",
            " TrÒ   ChƠi ": "games",
            "booster": "utilities",
            "TU_TIEN": "cultivation",
            "TỰ_ĐỘNG": "automation",
            "ADMIN": "moderation",
            "nsfw": "nsfw",
        }
        for value, expected in aliases.items():
            with self.subTest(value=value):
                self.assertEqual(resolve_help_topic(value), expected)

        for value in (None, "", "not-a-topic"):
            with self.subTest(value=value):
                self.assertIsNone(resolve_help_topic(value))

        fallback = build_help_embed("removed-topic", "!tf ")
        overview = build_help_embed("overview", "!tf ")
        self.assertEqual(fallback.to_dict(), overview.to_dict())

    def test_available_topics_support_full_catalog_and_nsfw_gate(
        self,
    ) -> None:
        available_commands = frozenset({"daily", "kick", "r34"})

        public_keys = topic_keys(
            available_help_topics(
                available_commands,
                allow_nsfw=False,
            )
        )
        self.assertEqual(
            public_keys,
            ("overview", "economy", "automation", "moderation"),
        )

        nsfw_keys = topic_keys(
            available_help_topics(
                available_commands,
                allow_nsfw=True,
            )
        )
        self.assertEqual(
            nsfw_keys,
            ("overview", "economy", "automation", "moderation", "nsfw"),
        )

        full_catalog_keys = topic_keys(
            available_help_topics(None, allow_nsfw=False)
        )
        self.assertEqual(
            full_catalog_keys,
            tuple(topic.key for topic in HELP_TOPICS if topic.key != "nsfw"),
        )

        forced_keys = topic_keys(
            available_help_topics(
                frozenset(),
                allow_nsfw=False,
                always_include=("games", "moderation", "nsfw"),
            )
        )
        self.assertEqual(
            forced_keys,
            ("overview", "games", "automation", "moderation"),
        )


class TestHelpCogCommand(unittest.IsolatedAsyncioTestCase):
    async def test_initial_help_command_replies_with_overview_and_dropdown(
        self,
    ) -> None:
        bot = SimpleNamespace(
            command_prefix="!tf ",
            global_vars={},
            walk_commands=lambda: (),
        )
        cog = HelpCog(bot)
        sent_message = SimpleNamespace(id=123)
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=42, guild_permissions=None),
            bot=bot,
            channel=SimpleNamespace(is_nsfw=lambda: False),
            clean_prefix="!tf ",
            guild=None,
            permissions=None,
            send=AsyncMock(),
            reply=AsyncMock(return_value=sent_message),
        )

        await cog.custom_help.callback(cog, ctx, topic=None)

        ctx.reply.assert_awaited_once()
        kwargs = ctx.reply.await_args.kwargs
        self.assertIsInstance(kwargs["embed"], discord.Embed)
        self.assertTrue(kwargs["embed"].title.startswith("📖"))
        self.assertIsInstance(kwargs["view"], HelpView)
        view = kwargs["view"]
        self.assertEqual(view.selected_topic_key, "overview")
        self.assertIs(view.message, sent_message)
        self.assertEqual(len(view.children), 1)
        self.assertIs(view.children[0], view.topic_select)
        self.assertEqual(
            tuple(option.value for option in view.topic_select.options),
            (
                "overview",
                "community",
                "economy",
                "cultivation",
                "games",
                "fun",
                "utilities",
                "automation",
                "moderation",
            ),
        )
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        self.assertFalse(kwargs["allowed_mentions"].users)
        self.assertFalse(kwargs["allowed_mentions"].roles)
        self.assertTrue(kwargs["mention_author"])
        ctx.send.assert_not_awaited()

    async def test_mod_shortcut_is_readable_without_command_permissions(self) -> None:
        bot = SimpleNamespace(command_prefix="!tf ", global_vars={})
        cog = HelpCog(bot)
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=42),
            bot=bot,
            channel=SimpleNamespace(is_nsfw=lambda: False),
            clean_prefix="!tf ",
            guild=None,
            reply=AsyncMock(return_value=SimpleNamespace(id=123)),
        )

        self.assertEqual(cog.mod_help.checks, [])
        await cog.mod_help.callback(cog, ctx)

        view = ctx.reply.await_args.kwargs["view"]
        self.assertEqual(view.selected_topic_key, "moderation")

    async def test_slowmode_group_links_to_complete_moderation_help(self) -> None:
        cog = SlowmodeCog(SimpleNamespace())
        ctx = SimpleNamespace(clean_prefix="!tf ", send=AsyncMock())

        await cog.slowmode.callback(cog, ctx)

        ctx.send.assert_awaited_once()
        self.assertIn("`!tf help moderation`", ctx.send.await_args.args[0])

    async def test_nsfw_rule_uses_clean_prefix_and_real_cooldown(self) -> None:
        cog = object.__new__(NSFWInteractionCog)
        cog.bot = SimpleNamespace()
        cog._nsfw_guard = AsyncMock(return_value=True)
        ctx = SimpleNamespace(clean_prefix="!tf ", send=AsyncMock())

        await cog.nsfw_rule.callback(cog, ctx)

        ctx.send.assert_awaited_once()
        embed = ctx.send.await_args.kwargs["embed"]
        rendered = "\n".join(field.value for field in embed.fields)
        self.assertIn("`!tf <lệnh> @tên_thành_viên`", rendered)
        self.assertIn("cooldown) là 3 giây", rendered)
        self.assertNotIn("15 giây", rendered)
        self.assertNotIn("`!!tf", rendered)


class TestHelpViewInteractions(unittest.IsolatedAsyncioTestCase):
    async def test_only_menu_owner_can_interact(self) -> None:
        view = make_help_view()
        owner = make_interaction(user_id=42)
        stranger = make_interaction(user_id=99)

        self.assertTrue(await view.interaction_check(owner))
        owner.response.send_message.assert_not_awaited()

        self.assertFalse(await view.interaction_check(stranger))
        stranger.response.send_message.assert_awaited_once_with(
            "Chỉ người đã mở menu trợ giúp mới có thể đổi chủ đề.",
            ephemeral=True,
        )

    async def test_dropdown_selection_edits_original_message_with_same_view(
        self,
    ) -> None:
        view = make_help_view()
        interaction = make_interaction(user_id=42)
        view.topic_select._values = ["economy"]

        await view.topic_select.callback(interaction)

        interaction.response.edit_message.assert_awaited_once()
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertIs(kwargs["view"], view)
        self.assertEqual(kwargs["embed"].title, "🪙 Trap Coin & cửa hàng")
        self.assertEqual(view.selected_topic_key, "economy")
        defaults = {
            option.value: option.default for option in view.topic_select.options
        }
        self.assertTrue(defaults["economy"])
        self.assertFalse(defaults["overview"])
        interaction.response.send_message.assert_not_awaited()

    async def test_invalid_topic_is_denied_without_editing(self) -> None:
        view = make_help_view()
        interaction = make_interaction(user_id=42)

        await view.select_topic("deleted-topic", interaction)

        interaction.response.send_message.assert_awaited_once_with(
            "Chủ đề trợ giúp này không còn khả dụng. Hãy mở lại menu.",
            ephemeral=True,
        )
        interaction.response.edit_message.assert_not_awaited()
        self.assertEqual(view.selected_topic_key, "overview")

    async def test_moderation_reference_is_visible_without_permissions(self) -> None:
        view = make_help_view()
        interaction = make_interaction(
            user_id=42,
            permissions=SimpleNamespace(
                administrator=False,
                moderate_members=False,
            ),
        )

        await view.select_topic("moderation", interaction)

        interaction.response.edit_message.assert_awaited_once()
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertTrue(kwargs["embed"].title.startswith("🛡️"))
        interaction.response.send_message.assert_not_awaited()

    async def test_nsfw_topic_rechecks_current_channel(self) -> None:
        view = make_help_view(allow_nsfw=True)
        moved_channel = make_interaction(user_id=42, is_nsfw=False)

        await view.select_topic("nsfw", moved_channel)

        moved_channel.response.send_message.assert_awaited_once_with(
            "Chủ đề NSFW chỉ khả dụng trong kênh được đánh dấu NSFW.",
            ephemeral=True,
        )
        moved_channel.response.edit_message.assert_not_awaited()

        allowed = make_interaction(user_id=42, is_nsfw=True)
        await view.select_topic("nsfw", allowed)
        allowed.response.edit_message.assert_awaited_once()


def source_command_inventory() -> tuple[
    set[str],
    dict[str, set[str]],
    set[str],
]:
    """Read command decorators so new cogs cannot silently miss the help catalog."""
    command_names: set[str] = set()
    aliases: dict[str, set[str]] = {}
    beta_commands: set[str] = set()
    cogs_path = Path(__file__).resolve().parents[1] / "cogs"

    def decorator_parts(
        decorator: ast.expr,
    ) -> tuple[ast.expr, ast.Call | None]:
        if isinstance(decorator, ast.Call):
            return decorator.func, decorator
        return decorator, None

    def keyword_string(call: ast.Call | None, name: str) -> str | None:
        if call is None:
            return None
        for keyword in call.keywords:
            if (
                keyword.arg == name
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                return keyword.value.value
        return None

    def keyword_strings(call: ast.Call | None, name: str) -> set[str]:
        if call is None:
            return set()
        for keyword in call.keywords:
            if keyword.arg != name or not isinstance(
                keyword.value,
                (ast.List, ast.Tuple, ast.Set),
            ):
                continue
            return {
                item.value
                for item in keyword.value.elts
                if isinstance(item, ast.Constant)
                and isinstance(item.value, str)
            }
        return set()

    for path in cogs_path.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        group_specs: dict[str, tuple[str | None, str]] = {}

        for function in functions:
            for decorator in function.decorator_list:
                target, call = decorator_parts(decorator)
                if not isinstance(target, ast.Attribute) or not isinstance(
                    target.value, ast.Name
                ):
                    continue
                if target.value.id == "commands" and target.attr == "group":
                    parent = None
                elif target.attr == "group":
                    parent = target.value.id
                else:
                    continue
                group_specs[function.name] = (
                    parent,
                    keyword_string(call, "name") or function.name,
                )

        resolved_groups: dict[str, str] = {}

        def resolve_group(function_name: str) -> str | None:
            if function_name in resolved_groups:
                return resolved_groups[function_name]
            spec = group_specs.get(function_name)
            if spec is None:
                return None
            parent, public_name = spec
            if parent is None:
                qualified = public_name
            else:
                parent_name = resolve_group(parent)
                if parent_name is None:
                    return None
                qualified = f"{parent_name} {public_name}"
            resolved_groups[function_name] = qualified
            return qualified

        for group_function in group_specs:
            resolve_group(group_function)

        for function in functions:
            is_beta = any(
                isinstance(decorator_parts(decorator)[0], ast.Name)
                and decorator_parts(decorator)[0].id == "BetaFunction"
                for decorator in function.decorator_list
            )
            for decorator in function.decorator_list:
                target, call = decorator_parts(decorator)
                if not (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                ):
                    continue

                public_name = keyword_string(call, "name") or function.name
                qualified_name = None
                if (
                    target.value.id == "commands"
                    and target.attr in {"command", "group"}
                ):
                    qualified_name = public_name
                elif target.attr in {"command", "group"}:
                    parent_name = resolve_group(target.value.id)
                    if parent_name is not None:
                        qualified_name = f"{parent_name} {public_name}"

                if qualified_name is None:
                    continue
                command_names.add(qualified_name)
                registered_aliases = keyword_strings(call, "aliases")
                if " " in qualified_name:
                    parent_name = qualified_name.rsplit(" ", 1)[0]
                    registered_aliases = {
                        f"{parent_name} {alias}" for alias in registered_aliases
                    }
                aliases.setdefault(qualified_name, set()).update(registered_aliases)
                if is_beta:
                    beta_commands.add(qualified_name)

    for spec in SFW_ACTION_SPECS:
        command_names.add(spec.name)
        aliases.setdefault(spec.name, set()).update(spec.aliases)
    return command_names, aliases, beta_commands


def make_help_view(
    *,
    allow_nsfw: bool = True,
) -> HelpView:
    return HelpView(
        author_id=42,
        topics=HELP_TOPICS,
        selected_topic_key="overview",
        prefix="!tf ",
        available_commands=None,
        beta_commands=(),
        allow_nsfw=allow_nsfw,
    )


def make_interaction(
    *,
    user_id: int,
    permissions: object | None = None,
    is_nsfw: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        permissions=permissions,
        channel=SimpleNamespace(is_nsfw=lambda: is_nsfw),
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
        ),
    )


def topic_keys(topics: tuple) -> tuple[str, ...]:
    return tuple(topic.key for topic in topics)


if __name__ == "__main__":
    unittest.main()
