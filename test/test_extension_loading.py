import ast
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock, call

from discord.ext import commands


MAIN_PATH = Path(__file__).resolve().parents[1] / "main.py"


def load_loader_function(
    bot: SimpleNamespace, **overrides: Any
) -> tuple[Callable[[], Awaitable[None]], dict[str, Any]]:
    """Execute only the loader, without importing startup services or credentials."""
    tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
    loader = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "load_cogs"
    )
    namespace = {
        "bot": bot,
        "environment": "production",
        "COG_PROFILE_FILES": {"development": "dev_cogs.txt"},
        "os": SimpleNamespace(path=SimpleNamespace(exists=Mock(return_value=True))),
        "get_cogs_from_path": Mock(return_value=[]),
        "get_cogs_from_profile": Mock(return_value=[]),
        "cog_disabled": Mock(return_value=False),
        "commands": commands,
        "print": Mock(),
    }
    namespace.update(overrides)
    exec(
        compile(ast.Module(body=[loader], type_ignores=[]), str(MAIN_PATH), "exec"),
        namespace,
    )
    return namespace["load_cogs"], namespace


class TestExtensionLoading(unittest.IsolatedAsyncioTestCase):
    async def test_selection_excludes_disabled_and_loads_settings_first(self) -> None:
        feature = "cogs.utils.highlight"
        disabled = "cogs.interaction.cat"
        settings = "cogs.settings.variable_setting"
        bot = SimpleNamespace(load_extension=AsyncMock())
        load_cogs, namespace = load_loader_function(
            bot,
            get_cogs_from_path=Mock(return_value=[feature, disabled, settings]),
            cog_disabled=lambda module: module == disabled,
        )

        await load_cogs()

        self.assertEqual(bot.selected_extensions, {feature, settings})
        self.assertEqual(bot.extension_load_failures, {})
        self.assertEqual(
            bot.load_extension.await_args_list, [call(settings), call(feature)]
        )
        namespace["get_cogs_from_path"].assert_called_once_with("cogs")
        namespace["get_cogs_from_profile"].assert_not_called()

    async def test_missing_development_profile_initializes_diagnostics(self) -> None:
        bot = SimpleNamespace(
            load_extension=AsyncMock(),
            selected_extensions={"cogs.previously_selected"},
        )
        load_cogs, namespace = load_loader_function(
            bot,
            environment="development",
            os=SimpleNamespace(path=SimpleNamespace(exists=Mock(return_value=False))),
        )

        await load_cogs()

        self.assertEqual(bot.selected_extensions, set())
        self.assertEqual(bot.extension_load_failures, {})
        bot.load_extension.assert_not_awaited()
        namespace["get_cogs_from_path"].assert_not_called()
        namespace["get_cogs_from_profile"].assert_not_called()

    async def test_development_selection_uses_profile(self) -> None:
        module = "cogs.operation.operation_dashboard"
        bot = SimpleNamespace(load_extension=AsyncMock())
        load_cogs, namespace = load_loader_function(
            bot,
            environment="development",
            get_cogs_from_profile=Mock(return_value=[module]),
        )

        await load_cogs()

        self.assertEqual(bot.selected_extensions, {module})
        namespace["get_cogs_from_profile"].assert_called_once_with("dev_cogs.txt")
        namespace["get_cogs_from_path"].assert_not_called()
        bot.load_extension.assert_awaited_once_with(module)

    async def test_failure_records_safe_original_type_and_keeps_loading(self) -> None:
        broken = "cogs.utils.highlight"
        healthy = "cogs.operation.operation_dashboard"
        sensitive_detail = "private-connection-string-and-credential"
        failure = commands.ExtensionFailed(broken, ValueError(sensitive_detail))
        bot = SimpleNamespace(load_extension=AsyncMock(side_effect=[failure, None]))
        load_cogs, namespace = load_loader_function(
            bot, get_cogs_from_path=Mock(return_value=[broken, healthy])
        )

        await load_cogs()

        self.assertEqual(bot.selected_extensions, {broken, healthy})
        self.assertEqual(bot.extension_load_failures, {broken: "ValueError"})
        self.assertEqual(
            bot.load_extension.await_args_list, [call(broken), call(healthy)]
        )
        self.assertNotIn(sensitive_detail, repr(bot.extension_load_failures))
        self.assertNotIn(sensitive_detail, repr(namespace["print"].call_args_list))

    async def test_successful_retry_clears_failure_and_preserves_loaded_state(self) -> None:
        module = "cogs.utils.highlight"
        existing_extensions = {"cogs.operation.operation_dashboard": object()}
        bot = SimpleNamespace(
            load_extension=AsyncMock(side_effect=[RuntimeError("private detail"), None]),
            extensions=existing_extensions,
        )
        load_cogs, namespace = load_loader_function(
            bot, get_cogs_from_path=Mock(return_value=[module])
        )

        await load_cogs()
        self.assertEqual(bot.extension_load_failures, {module: "RuntimeError"})
        await load_cogs()

        self.assertEqual(bot.selected_extensions, {module})
        self.assertEqual(bot.extension_load_failures, {})
        self.assertIs(bot.extensions, existing_extensions)
        self.assertNotIn("private detail", repr(namespace["print"].call_args_list))


if __name__ == "__main__":
    unittest.main()
