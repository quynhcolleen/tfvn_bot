import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs._hash_verification import VERIFICATION_COLLECTION
from cogs.funny_things.cards.femboy_card import FemboyCardCog
from cogs.interaction.marriage import MARRIAGES_COLLECTION


ISSUED_AT = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def _member(*, user_id: int = 20) -> SimpleNamespace:
    role = SimpleNamespace(
        id=40,
        name="Femboy",
        position=5,
        color=discord.Color.from_rgb(255, 105, 180),
    )
    return SimpleNamespace(
        id=user_id,
        name="kien",
        display_name="Kiên",
        mention=f"<@{user_id}>",
        display_avatar=SimpleNamespace(url="https://cdn.example/avatar.png"),
        roles=[role],
    )


def _context(member: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        author=member,
        guild=SimpleNamespace(id=10),
        prefix="!tf ",
        send=AsyncMock(),
    )


def _bot(*, get_cog=None, db=None) -> SimpleNamespace:
    collections = {VERIFICATION_COLLECTION: MagicMock()}
    if db:
        collections.update(db)
    kwargs: dict[str, object] = {
        "FEMBOY_ROLE": ["Femboy"],
        "db": collections,
    }
    if get_cog is not None:
        kwargs["get_cog"] = get_cog
    return SimpleNamespace(**kwargs)


class TestFemboyCardMarriage(unittest.IsolatedAsyncioTestCase):
    async def _issue_card(self, bot, ctx) -> discord.Embed:
        with (
            patch(
                "cogs.funny_things.cards.femboy_card.verification_keyring_from_bot",
                return_value=object(),
            ),
            patch(
                "cogs.funny_things.cards.femboy_card.issue_verification_async",
                new=AsyncMock(return_value="token"),
            ),
            patch(
                "cogs.funny_things.cards.femboy_card.verification_reference_from_token",
                return_value="tfp1_testproof",
            ),
            patch(
                "discord.utils.utcnow",
                return_value=ISSUED_AT,
            ),
        ):
            cog = FemboyCardCog(bot)
            await cog.femboy_card.callback(cog, ctx)
        return ctx.send.await_args.kwargs["embed"]

    @staticmethod
    def _field(embed: discord.Embed, name: str) -> str | None:
        for field in embed.fields:
            if field.name == name:
                return field.value
        return None

    async def test_shows_active_marriage_from_cog(self) -> None:
        marriage_cog = SimpleNamespace(
            find_active_marriage=MagicMock(
                return_value={
                    "user_a": 20,
                    "user_b": 99,
                    "xp": 80,
                    "level": 5,
                    "married_at": datetime(2026, 3, 14, tzinfo=timezone.utc),
                }
            )
        )
        bot = _bot(get_cog=lambda name: marriage_cog if name == "MarriageCog" else None)
        embed = await self._issue_card(bot, _context(_member()))

        value = self._field(embed, "💍 Hôn nhân")
        self.assertIsNotNone(value)
        self.assertIn("<@99>", value)
        self.assertIn("Bạc", value)
        self.assertIn("Level **5**", value)
        self.assertIn("2026-03-14", value)
        self.assertIn("**184 ngày** bên nhau", value)
        marriage_cog.find_active_marriage.assert_called_once_with(10, 20)

    async def test_shows_single_when_unmarried(self) -> None:
        marriage_cog = SimpleNamespace(
            find_active_marriage=MagicMock(return_value=None)
        )
        bot = _bot(get_cog=lambda name: marriage_cog)
        embed = await self._issue_card(bot, _context(_member()))

        self.assertEqual(self._field(embed, "💍 Hôn nhân"), "Độc thân ✨")

    async def test_omits_marriage_when_lookup_unavailable(self) -> None:
        bot = _bot()
        embed = await self._issue_card(bot, _context(_member()))

        self.assertIsNone(self._field(embed, "💍 Hôn nhân"))

    async def test_falls_back_to_marriages_collection(self) -> None:
        marriages = MagicMock()
        marriages.find_one.return_value = {
            "user_a": 7,
            "user_b": 20,
            "xp": 0,
            "level": 1,
            "married_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        }
        bot = _bot(db={MARRIAGES_COLLECTION: marriages})
        embed = await self._issue_card(bot, _context(_member()))

        value = self._field(embed, "💍 Hôn nhân")
        self.assertIn("<@7>", value)
        self.assertIn("Đồng", value)
        marriages.find_one.assert_called_once_with(
            {
                "guild_id": 10,
                "status": "active",
                "partner_ids": 20,
            }
        )


if __name__ == "__main__":
    unittest.main()
