import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from assets.gifs import BANNED_GIF, GOODBYE_GIF, WELCOME_GIF
from cogs.announcement._media import announcement_gif_url
from cogs.announcement.goodbye import DepartureKind, GoodbyeCog
from cogs.announcement.welcome import WelcomeCog


def make_member(guild, member_id=42):
    return SimpleNamespace(
        id=member_id,
        guild=guild,
        mention=f"<@{member_id}>",
        name="Member",
        display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
    )


class TestGuildWelcome(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.guilds = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
        self.channels = {
            channel_id: SimpleNamespace(id=channel_id, guild=guild, send=AsyncMock())
            for guild in self.guilds
            for channel_id in (guild.id * 10, guild.id * 10 + 1, guild.id * 10 + 2)
        }
        self.bot = SimpleNamespace(
            get_channel=self.channels.get,
            guild_vars={
                guild.id: {
                    "JOIN_CHANNEL": guild.id * 10,
                    "RULE_CHANNEL": guild.id * 10 + 1,
                    "ROLE_CHANNEL": guild.id * 10 + 2,
                }
                for guild in self.guilds
            },
        )
        self.cog = WelcomeCog(self.bot)

    async def test_guilds_have_independent_welcome_channels_and_links(self):
        for guild in self.guilds:
            await self.cog.on_member_join(make_member(guild))
            embed = self.channels[guild.id * 10].send.await_args.kwargs["embed"]
            self.assertIn(f"<#{guild.id * 10 + 1}>", embed.description)
            self.assertIn(f"<#{guild.id * 10 + 2}>", embed.description)
            self.channels[guild.id * 10].send.assert_awaited_once()

        self.bot.guild_vars[1]["JOIN_CHANNEL"] = 20
        await self.cog.on_member_join(make_member(self.guilds[0]))
        self.channels[20].send.assert_awaited_once()

    async def test_missing_guild_configuration_skips_welcome(self):
        bot = SimpleNamespace(guild_vars={2: {"JOIN_CHANNEL": 20}}, get_channel=Mock())
        await WelcomeCog(bot).on_member_join(make_member(SimpleNamespace(id=1)))
        bot.get_channel.assert_not_called()

    async def test_welcome_image_override_is_local_and_updates_immediately(self):
        self.bot.global_vars = {"WELCOME_GIF_URL": "https://example.com/shared.gif"}
        self.bot.guild_vars[1]["WELCOME_GIF_URL"] = "https://example.com/first.gif"
        for guild in self.guilds:
            await self.cog.on_member_join(make_member(guild))
        self.assertEqual(
            self.channels[10].send.await_args.kwargs["embed"].image.url,
            "https://example.com/first.gif",
        )
        self.assertEqual(
            self.channels[20].send.await_args.kwargs["embed"].image.url,
            WELCOME_GIF,
        )
        self.bot.guild_vars[1]["WELCOME_GIF_URL"] = "https://example.com/updated.gif"
        await self.cog.on_member_join(make_member(self.guilds[0]))
        self.assertEqual(
            self.channels[10].send.await_args.kwargs["embed"].image.url,
            "https://example.com/updated.gif",
        )


class TestGuildDepartureImages(unittest.IsolatedAsyncioTestCase):
    async def test_each_departure_kind_uses_only_the_current_guild_image(self):
        bot = SimpleNamespace(guild_vars={1: {
            "GOODBYE_GIF_URL": "https://example.com/goodbye.gif",
            "BANNED_GIF_URL": "https://example.com/banned.gif",
        }})
        cog = GoodbyeCog(bot)
        for guild_id in (1, 2):
            guild = SimpleNamespace(id=guild_id)
            member = make_member(guild)
            for kind in DepartureKind:
                with self.subTest(guild=guild_id, kind=kind):
                    channel = SimpleNamespace(guild=guild, send=AsyncMock())
                    await cog.send_departure(member, channel, kind)
                    embed = channel.send.await_args.kwargs["embed"]
                    if guild_id == 1:
                        expected = (
                            "https://example.com/banned.gif" if kind is DepartureKind.BAN
                            else "https://example.com/goodbye.gif"
                        )
                    else:
                        expected = BANNED_GIF if kind is DepartureKind.BAN else GOODBYE_GIF
                    self.assertEqual(embed.image.url, expected)


class TestAnnouncementGifUrls(unittest.TestCase):
    def test_invalid_overrides_fall_back_to_bundled_image(self):
        for value in (
            None, [], 42, "", "file:///tmp/image.gif", "javascript:alert(1)",
            "https://", "https://[", "https://example.com:bad/image.gif",
            "https://example.com/image with spaces.gif",
        ):
            with self.subTest(value=value):
                bot = SimpleNamespace(guild_vars={1: {"WELCOME_GIF_URL": value}})
                self.assertEqual(
                    announcement_gif_url(bot, 1, "WELCOME_GIF_URL", WELCOME_GIF),
                    WELCOME_GIF,
                )

    def test_http_and_https_overrides_are_accepted(self):
        for scheme in ("http", "https"):
            value = f"{scheme}://example.com/image.gif"
            bot = SimpleNamespace(guild_vars={1: {"WELCOME_GIF_URL": f" {value} "}})
            self.assertEqual(
                announcement_gif_url(bot, 1, "WELCOME_GIF_URL", WELCOME_GIF), value
            )
