import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.funny_things.meters import titansize


def make_member(user_id: int = 42) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        name="Tester",
        mention=f"<@{user_id}>",
        display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
    )


class TestTitanSizeHelpers(unittest.TestCase):
    def test_daily_measurement_is_deterministic_and_supported(self) -> None:
        day = datetime.date(2026, 8, 27)

        first = titansize.get_daily_measurement(42, day)
        second = titansize.get_daily_measurement(42, day)

        self.assertEqual(first, second)
        size_cm, cup_size = first
        self.assertGreaterEqual(size_cm, titansize.MIN_SIZE_CM)
        self.assertLessEqual(size_cm, titansize.MAX_SIZE_CM)
        self.assertEqual(size_cm, round(size_cm, 1))
        self.assertIn(cup_size, titansize.CUP_SIZES)

    def test_each_supported_cup_size_has_a_tease(self) -> None:
        for cup_size in titansize.CUP_SIZES:
            with self.subTest(cup_size=cup_size):
                tease = titansize.describe_cup_size(cup_size)
                self.assertTrue(tease)

    def test_unsupported_cup_size_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported cup size"):
            titansize.describe_cup_size("Z")


class TestTitanSizeCommand(unittest.IsolatedAsyncioTestCase):
    async def test_defaults_to_author_and_renders_fictional_cup_result(self) -> None:
        author = make_member()
        loading_message = SimpleNamespace(edit=AsyncMock())
        ctx = SimpleNamespace(author=author)
        cog = titansize.TitanSizeCog(SimpleNamespace())

        with (
            patch.object(
                titansize,
                "fake_loading",
                AsyncMock(return_value=loading_message),
            ) as fake_loading,
            patch.object(
                titansize,
                "get_daily_measurement",
                return_value=(92.4, "DD"),
            ),
        ):
            await cog.titan_size.callback(cog, ctx, member=None)

        fake_loading.assert_awaited_once()
        loading_message.edit.assert_awaited_once()
        kwargs = loading_message.edit.await_args.kwargs
        embed = kwargs["embed"]
        rendered = "\n".join(
            (
                embed.title or "",
                embed.description or "",
                *(field.value for field in embed.fields),
                embed.footer.text or "",
            )
        )

        self.assertEqual(cog.titan_size.name, "titansize")
        self.assertEqual(cog.titan_size.aliases, [])
        self.assertIn("Tit-an Size Meter", rendered)
        self.assertIn(author.mention, rendered)
        self.assertIn("（ ͜.人 ͜.）", rendered)
        self.assertNotIn("Số đo và cup hư cấu của", rendered)
        self.assertIn("92.4 cm", rendered)
        self.assertIn("Cup:** DD", rendered)
        self.assertIn("hư cấu", rendered)
        self.assertIn("không phải số đo thật", rendered)
        self.assertNotIn("chiều cao", rendered.casefold())
        self.assertNotRegex(
            rendered.casefold(),
            r"\d+(?:[.,]\d+)?\s*m\b",
        )


if __name__ == "__main__":
    unittest.main()
