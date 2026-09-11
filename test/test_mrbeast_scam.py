import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

import discord

from cogs.mod.mrbeast_scam import (
    ALERT_CHANNEL_VARIABLE,
    INCIDENTS_COLLECTION,
    LOGS_COLLECTION,
    MrBeastScamCog,
    MrBeastScamDecisionView,
    MrBeastWarningView,
)
from cogs.mod._mrbeast_scam_helpers import (
    ACTION_CHALLENGE,
    ACTION_TIMEOUT,
    ACTION_WATCH,
    SOURCE_PHOTO_DUMP,
    SOURCE_TEXT,
    TIMEOUT_HOURS,
    WindowHit,
    photo_dump_action,
    photo_dump_count,
    compact_brand_text,
    is_photo_dump_candidate,
    is_phishing_host,
    lure_hosts_from,
    normalize_message_text,
    parent_channel_id,
    photo_dump_channel_ids,
    prune_window,
    score_text,
    should_escalate_text,
    timeout_until,
    upsert_window,
)


def utc(year=2026, month=9, day=11, hour=12, minute=0, second=0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def hit(
    *,
    guild_id=1,
    author_id=10,
    parent_channel_id=100,
    channel_id=None,
    message_id=1,
    source=SOURCE_PHOTO_DUMP,
    image_count=4,
    fingerprint="",
    created_at=None,
) -> WindowHit:
    return WindowHit(
        guild_id=guild_id,
        author_id=author_id,
        parent_channel_id=parent_channel_id,
        channel_id=channel_id or parent_channel_id,
        message_id=message_id,
        source=source,
        image_count=image_count,
        fingerprint=fingerprint,
        created_at=created_at or utc(),
    )


class FakeCollection:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    def create_index(self, *args, **kwargs) -> None:
        return None

    def insert_one(self, document: dict) -> SimpleNamespace:
        stored = dict(document)
        stored.setdefault("_id", len(self.documents) + 1)
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    def find_one(self, query: dict | None = None) -> dict | None:
        return next(self._matches(query or {}), None)

    def find(self, query: dict | None = None) -> list[dict]:
        return list(self._matches(query or {}))

    def update_one(self, query: dict, update: dict) -> None:
        document = self.find_one(query)
        if document is not None:
            self._apply(document, update)

    def find_one_and_update(
        self,
        query: dict,
        update: dict,
        **kwargs,
    ) -> dict | None:
        document = self.find_one(query)
        if document is None:
            return None
        self._apply(document, update)
        return dict(document)

    def _matches(self, query: dict):
        for document in self.documents:
            if all(document.get(key) == value for key, value in query.items()):
                yield document

    @staticmethod
    def _apply(document: dict, update: dict) -> None:
        for key, value in update.get("$set", {}).items():
            document[key] = value
        for key, value in update.get("$addToSet", {}).items():
            items = value.get("$each", [value]) if isinstance(value, dict) else [value]
            existing = list(document.get(key) or [])
            for item in items:
                if item not in existing:
                    existing.append(item)
            document[key] = existing


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


class TestMrBeastScamHelpers(unittest.TestCase):
    def test_normalize_folds_homoglyphs_and_zero_width(self) -> None:
        self.assertEqual(
            compact_brand_text("m.r.b.e.a.s.t"),
            "mrbeast",
        )
        self.assertEqual(
            normalize_message_text("mr\u200bbeast"),
            "mrbeast",
        )
        self.assertIn("mrbeast", compact_brand_text("mrbeаst"))  # Cyrillic а

    def test_photo_dump_accepts_one_or_four_images_with_tiny_caption(self) -> None:
        self.assertTrue(is_photo_dump_candidate(4, ""))
        self.assertTrue(is_photo_dump_candidate(1, "https://evil.example/claim"))
        self.assertTrue(is_photo_dump_candidate(2, "  ok  "))
        self.assertFalse(is_photo_dump_candidate(0, ""))
        self.assertFalse(is_photo_dump_candidate(5, ""))
        self.assertFalse(is_photo_dump_candidate(4, "", other_attachment_count=1))
        self.assertFalse(
            is_photo_dump_candidate(4, "x" * 81)
        )

    def test_captioned_meme_without_images_is_not_a_dump(self) -> None:
        self.assertFalse(is_photo_dump_candidate(0, "look at this meme"))

    def test_text_path_ignores_brand_only_and_allowlisted_youtube(self) -> None:
        miss = score_text("MrBeast video mới https://youtu.be/dQw4w9WgXcQ")
        self.assertFalse(miss.hit)
        self.assertIn("brand", miss.signals)
        self.assertEqual(lure_hosts_from("https://youtu.be/dQw4w9WgXcQ"), ())

    def test_text_path_hits_prize_and_lure_or_impersonation(self) -> None:
        hit_score = score_text(
            "MrBeast giveaway click here https://grab-nitro.xyz/free"
        )
        self.assertTrue(hit_score.hit)
        self.assertIn("prize", hit_score.signals)
        self.assertIn("lure_link", hit_score.signals)
        impersonation = score_text(
            "claim your prize https://bit.ly/abc",
            author_names=("MrBeast",),
        )
        self.assertTrue(impersonation.hit)
        self.assertIn("impersonation", impersonation.signals)

    def test_phishing_host_markers(self) -> None:
        self.assertTrue(is_phishing_host("grab-nitro.xyz"))
        self.assertTrue(is_phishing_host("bit.ly"))
        self.assertFalse(is_phishing_host("youtube.com"))
        self.assertFalse(is_phishing_host("cdn.discordapp.com"))

    def test_parent_channel_uses_thread_parent(self) -> None:
        thread = SimpleNamespace(id=9, parent_id=5)
        channel = SimpleNamespace(id=5, parent_id=None)
        self.assertEqual(parent_channel_id(thread), 5)
        self.assertEqual(parent_channel_id(channel), 5)

    def test_window_watches_first_two_dumps(self) -> None:
        first = hit(parent_channel_id=100, message_id=1)
        rows = upsert_window([], first, utc())
        self.assertEqual(photo_dump_action(rows, first), ACTION_WATCH)
        self.assertEqual(photo_dump_count(rows, first), 1)

        second = hit(parent_channel_id=100, message_id=2)
        rows = upsert_window(rows, second, utc())
        self.assertEqual(photo_dump_action(rows, second), ACTION_WATCH)
        self.assertEqual(photo_dump_count(rows, second), 2)
        self.assertEqual(photo_dump_channel_ids(rows, second), {100})

    def test_third_dump_is_challenge_fifth_is_immediate_timeout(self) -> None:
        rows: list[WindowHit] = []
        actions = []
        for index in range(1, 6):
            current = hit(parent_channel_id=100 + index, message_id=index)
            rows = upsert_window(rows, current, utc())
            actions.append(photo_dump_action(rows, current))
        self.assertEqual(
            actions,
            [
                ACTION_WATCH,
                ACTION_WATCH,
                ACTION_CHALLENGE,
                ACTION_CHALLENGE,
                ACTION_TIMEOUT,
            ],
        )

    def test_window_does_not_count_different_authors(self) -> None:
        first = hit(author_id=10, parent_channel_id=100, message_id=1)
        second = hit(author_id=11, parent_channel_id=200, message_id=2)
        rows = upsert_window([first], second, utc())
        self.assertEqual(photo_dump_action(rows, second), ACTION_WATCH)
        self.assertEqual(photo_dump_count(rows, second), 1)

    def test_window_expires_after_two_minutes(self) -> None:
        old = hit(parent_channel_id=100, message_id=1, created_at=utc())
        now = utc(minute=3)
        rows = prune_window([old], now)
        self.assertEqual(rows, [])
        fresh = hit(parent_channel_id=200, message_id=2, created_at=now)
        self.assertEqual(photo_dump_action(rows + [fresh], fresh), ACTION_WATCH)

    def test_text_window_escalates_shared_fingerprint(self) -> None:
        first = hit(
            author_id=10,
            parent_channel_id=100,
            message_id=1,
            source=SOURCE_TEXT,
            fingerprint="grab-nitro.xyz",
            image_count=0,
        )
        second = hit(
            author_id=11,
            parent_channel_id=200,
            message_id=2,
            source=SOURCE_TEXT,
            fingerprint="grab-nitro.xyz",
            image_count=0,
        )
        rows = upsert_window([first], second, utc())
        self.assertTrue(should_escalate_text(rows, second))

    def test_timeout_until_is_24_hours(self) -> None:
        start = utc()
        self.assertEqual(
            timeout_until(start) - start,
            timedelta(hours=TIMEOUT_HOURS),
        )


class FakeRole:
    def __init__(self, position: int) -> None:
        self.position = position

    def __gt__(self, other: "FakeRole") -> bool:
        return self.position > other.position

    def __ge__(self, other: "FakeRole") -> bool:
        return self.position >= other.position


class ImageAttachment:
    def __init__(self, name: str = "raid.png", payload: bytes = b"img") -> None:
        self.filename = name
        self.content_type = "image/png"
        self.size = len(payload)
        self.read = AsyncMock(return_value=payload)


def make_permissions(**flags: bool) -> SimpleNamespace:
    values = {
        "administrator": False,
        "manage_messages": False,
        "moderate_members": True,
        "ban_members": True,
    }
    values.update(flags)
    return SimpleNamespace(**values)


def make_cog(bot: SimpleNamespace) -> MrBeastScamCog:
    cog = object.__new__(MrBeastScamCog)
    cog.bot = bot
    cog.db = bot.db
    cog.logs = bot.db[LOGS_COLLECTION]
    cog.incidents = bot.db[INCIDENTS_COLLECTION]
    cog._window = []
    cog._active_targets = set()
    cog._pending_challenges = {}
    return cog


class TestMrBeastScamCog(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.db = FakeDatabase()
        self.guild_id = 50
        self.author_id = 77
        self.bot_id = 1
        self.alert_id = 900
        self.role_bot = FakeRole(10)
        self.role_member = FakeRole(1)
        self.bot_member = SimpleNamespace(
            id=self.bot_id,
            guild_permissions=make_permissions(),
            top_role=self.role_bot,
        )
        self.alert = SimpleNamespace(
            id=self.alert_id,
            send=AsyncMock(
                return_value=SimpleNamespace(id=321, channel=SimpleNamespace(id=self.alert_id))
            ),
        )
        self.channels: dict[int, SimpleNamespace] = {self.alert_id: self.alert}
        self.guild = SimpleNamespace(
            id=self.guild_id,
            owner_id=2,
            me=self.bot_member,
            get_member=lambda user_id: self.members.get(user_id),
            get_channel_or_thread=lambda channel_id: self.channels.get(channel_id),
            get_channel=lambda channel_id: self.channels.get(channel_id),
            ban=AsyncMock(),
        )
        self.author = SimpleNamespace(
            id=self.author_id,
            bot=False,
            name="raider",
            display_name="raider",
            global_name="raider",
            mention=f"<@{self.author_id}>",
            guild=self.guild,
            guild_permissions=make_permissions(),
            top_role=self.role_member,
            created_at=utc(2026, 1, 1),
            joined_at=utc(2026, 9, 1),
            timeout=AsyncMock(),
            ban=AsyncMock(),
        )
        self.members = {self.author_id: self.author, self.bot_id: self.bot_member}
        self.bot = SimpleNamespace(
            user=SimpleNamespace(id=self.bot_id),
            db=self.db,
            global_vars={ALERT_CHANNEL_VARIABLE: self.alert_id},
            get_channel=lambda channel_id: self.channels.get(channel_id),
            get_cog=lambda name: None,
        )
        self.cog = make_cog(self.bot)
        record_case = patch(
            "cogs.mod.mrbeast_scam.record_case",
            new_callable=AsyncMock,
        )
        record_case.start()
        self.addCleanup(record_case.stop)

    def make_channel(self, channel_id: int) -> SimpleNamespace:
        channel = SimpleNamespace(
            id=channel_id,
            parent_id=None,
            permissions_for=lambda member: make_permissions(manage_messages=True),
            get_partial_message=lambda message_id: SimpleNamespace(delete=AsyncMock()),
            fetch_message=AsyncMock(),
            send=AsyncMock(
                return_value=SimpleNamespace(id=channel_id * 10, edit=AsyncMock())
            ),
        )
        self.channels[channel_id] = channel
        return channel

    def make_message(
        self,
        *,
        channel_id: int,
        message_id: int,
        images: int = 4,
        content: str = "",
        author=None,
        webhook_id=None,
    ) -> SimpleNamespace:
        channel = self.channels.get(channel_id) or self.make_channel(channel_id)
        attachments = [ImageAttachment(f"p{i}.png") for i in range(images)]
        message = SimpleNamespace(
            id=message_id,
            guild=self.guild,
            channel=channel,
            author=author or self.author,
            content=content,
            attachments=attachments,
            embeds=[],
            stickers=[],
            webhook_id=webhook_id,
            mention_everyone=False,
            delete=AsyncMock(),
        )
        channel.fetch_message.return_value = message
        return message

    async def test_skips_self_and_staff(self) -> None:
        self_message = self.make_message(channel_id=10, message_id=1)
        self_message.author = SimpleNamespace(
            id=self.bot_id,
            bot=True,
            guild_permissions=make_permissions(),
        )
        await self.cog.on_message(self_message)
        self_message.delete.assert_not_awaited()

        staff = self.make_message(channel_id=10, message_id=2)
        staff.author = SimpleNamespace(
            id=8,
            bot=False,
            guild_permissions=make_permissions(manage_messages=True),
            display_name="mod",
            name="mod",
            global_name="mod",
        )
        await self.cog.on_message(staff)
        staff.delete.assert_not_awaited()
        self.author.timeout.assert_not_awaited()

    async def dump_times(self, count: int, *, confirm: bool = False):
        async def wait(view: MrBeastWarningView) -> None:
            view.confirmed = confirm

        messages = [
            self.make_message(channel_id=10 + index, message_id=index + 1, images=4)
            for index in range(count)
        ]
        with patch.object(MrBeastWarningView, "wait", wait):
            for message in messages:
                await self.cog.on_message(message)
        return messages

    async def test_unknown_single_channel_dump_is_watch_only(self) -> None:
        message = self.make_message(channel_id=10, message_id=1, images=4)
        await self.cog.on_message(message)
        message.delete.assert_not_awaited()
        self.author.timeout.assert_not_awaited()
        self.alert.send.assert_not_awaited()
        self.assertEqual(len(self.cog._window), 1)

    async def test_two_dumps_are_still_watch_only(self) -> None:
        messages = await self.dump_times(2)
        for message in messages:
            message.delete.assert_not_awaited()
        self.author.timeout.assert_not_awaited()
        self.alert.send.assert_not_awaited()

    async def test_third_dump_warning_click_avoids_timeout(self) -> None:
        messages = await self.dump_times(3, confirm=True)
        messages[2].channel.send.assert_awaited()
        view = messages[2].channel.send.await_args.kwargs["view"]
        self.assertIsInstance(view, MrBeastWarningView)
        self.assertEqual(view.timeout, 30)
        messages[2].delete.assert_not_awaited()
        self.author.timeout.assert_not_awaited()
        self.alert.send.assert_not_awaited()

    async def test_third_dump_missed_click_timeouts(self) -> None:
        messages = await self.dump_times(3, confirm=False)
        messages[2].delete.assert_awaited()
        self.author.timeout.assert_awaited()
        until = self.author.timeout.await_args.args[0]
        remaining = until - datetime.now(timezone.utc)
        self.assertGreater(remaining, timedelta(hours=23, minutes=50))
        self.assertLess(remaining, timedelta(hours=24, minutes=10))
        self.alert.send.assert_awaited_once()
        kwargs = self.alert.send.await_args.kwargs
        self.assertTrue(kwargs["files"])
        self.assertIsInstance(kwargs["view"], MrBeastScamDecisionView)

    async def test_fifth_dump_timeouts_immediately(self) -> None:
        messages = await self.dump_times(5, confirm=True)
        messages[4].delete.assert_awaited()
        self.author.timeout.assert_awaited()
        self.alert.send.assert_awaited()

    async def test_missing_alert_channel_still_timeouts(self) -> None:
        self.bot.global_vars = {}
        await self.dump_times(5, confirm=True)
        self.author.timeout.assert_awaited()
        self.alert.send.assert_not_awaited()
        self.assertEqual(
            self.db[INCIDENTS_COLLECTION].documents[0]["alert_message_id"],
            None,
        )

    async def test_isolated_text_hit_deletes_without_timeout(self) -> None:
        message = self.make_message(
            channel_id=10,
            message_id=3,
            images=0,
            content="MrBeast giveaway click here http://grab-nitro.xyz/free",
        )
        await self.cog.on_message(message)
        message.delete.assert_awaited()
        self.author.timeout.assert_not_awaited()
        self.alert.send.assert_not_awaited()

    async def test_scans_webhook_photo_dumps(self) -> None:
        webhook_author = SimpleNamespace(
            id=88,
            bot=True,
            name="MrBeast",
            display_name="MrBeast",
            global_name="MrBeast",
            mention="<@88>",
            guild=self.guild,
            guild_permissions=None,
        )
        messages = []
        async def wait(view: MrBeastWarningView) -> None:
            view.confirmed = False

        with patch.object(MrBeastWarningView, "wait", wait):
            for index in range(3):
                message = self.make_message(
                    channel_id=10 + index,
                    message_id=index + 1,
                    images=4,
                    author=webhook_author,
                    webhook_id=555,
                )
                messages.append(message)
                await self.cog.on_message(message)
        messages[2].delete.assert_awaited()
        self.author.timeout.assert_not_awaited()
        self.alert.send.assert_awaited()

    async def test_scam_check_reports_dump_without_enforcing(self) -> None:
        target = self.make_message(channel_id=10, message_id=9, images=4)
        ctx = SimpleNamespace(
            message=SimpleNamespace(reference=SimpleNamespace(resolved=target)),
            reply=AsyncMock(),
            guild=self.guild,
        )
        with patch(
            "cogs.mod.mrbeast_scam.fetch_same_channel_reply",
            AsyncMock(return_value=target),
        ):
            await MrBeastScamCog.scam_check.callback(self.cog, ctx)
        ctx.reply.assert_awaited()
        embed = ctx.reply.await_args.kwargs["embed"]
        self.assertIn("Dump ảnh", embed.fields[0].name)
        self.assertIn("Có", embed.fields[0].value)
        target.delete.assert_not_awaited()
        self.author.timeout.assert_not_awaited()


class TestMrBeastScamDecisions(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.db = FakeDatabase()
        self.guild_id = 50
        self.target_id = 77
        self.bot_id = 1
        self.role_bot = FakeRole(10)
        self.role_member = FakeRole(1)
        self.bot_member = SimpleNamespace(
            id=self.bot_id,
            guild_permissions=make_permissions(),
            top_role=self.role_bot,
        )
        self.target = SimpleNamespace(
            id=self.target_id,
            guild=None,
            top_role=self.role_member,
            timeout=AsyncMock(),
            ban=AsyncMock(),
            mention=f"<@{self.target_id}>",
        )
        self.guild = SimpleNamespace(
            id=self.guild_id,
            owner_id=2,
            me=self.bot_member,
            get_member=lambda user_id: self.target if user_id == self.target_id else None,
            ban=AsyncMock(),
        )
        self.target.guild = self.guild
        self.bot = SimpleNamespace(
            user=SimpleNamespace(id=self.bot_id),
            db=self.db,
            get_cog=lambda name: None,
        )
        self.cog = make_cog(self.bot)
        self.incident = {
            "_id": 1,
            "incident_id": "abc123ff",
            "guild_id": self.guild_id,
            "target_id": self.target_id,
            "status": "pending",
            "alert_message_id": 321,
            "timeout_applied": True,
        }
        self.db[INCIDENTS_COLLECTION].documents.append(dict(self.incident))

    def make_interaction(self, **perm_flags: bool):
        user = SimpleNamespace(
            id=9,
            mention="<@9>",
            guild_permissions=make_permissions(**perm_flags),
            guild=self.guild,
            top_role=FakeRole(8),
        )
        embed = discord.Embed(title="Cảnh báo giveaway giả mạo MrBeast")
        message = SimpleNamespace(
            id=321,
            embeds=[embed],
        )
        return SimpleNamespace(
            guild=self.guild,
            user=user,
            message=message,
            response=SimpleNamespace(
                send_message=AsyncMock(),
                edit_message=AsyncMock(),
            ),
        )

    async def test_ban_requires_ban_members_and_cas(self) -> None:
        interaction = self.make_interaction(ban_members=False, manage_messages=True)
        await self.cog.handle_decision(interaction, "banned")
        interaction.response.send_message.assert_awaited()
        self.target.ban.assert_not_awaited()
        self.assertEqual(
            self.db[INCIDENTS_COLLECTION].documents[0]["status"],
            "pending",
        )

        interaction = self.make_interaction(ban_members=True)
        with patch("cogs.mod.mrbeast_scam.record_case", AsyncMock(return_value=3)):
            await self.cog.handle_decision(interaction, "banned")
        self.target.ban.assert_awaited()
        self.assertEqual(
            self.db[INCIDENTS_COLLECTION].documents[0]["status"],
            "banned",
        )
        interaction.response.edit_message.assert_awaited()

    async def test_untimeout_does_not_store_hashes_and_clears_timeout(self) -> None:
        interaction = self.make_interaction(manage_messages=True)
        with patch("cogs.mod.mrbeast_scam.record_case", AsyncMock(return_value=4)):
            await self.cog.handle_decision(interaction, "untimeout")
        self.target.timeout.assert_awaited_with(None, reason=ANY)
        self.assertEqual(
            self.db[INCIDENTS_COLLECTION].documents[0]["status"],
            "untimeout",
        )
        self.assertFalse(any("hash" in key for key in self.db.collections))

    async def test_keep_leaves_timeout_in_place(self) -> None:
        interaction = self.make_interaction(manage_messages=True)
        await self.cog.handle_decision(interaction, "kept")
        self.target.timeout.assert_not_awaited()
        self.target.ban.assert_not_awaited()
        self.assertEqual(
            self.db[INCIDENTS_COLLECTION].documents[0]["status"],
            "kept",
        )


if __name__ == "__main__":
    unittest.main()
