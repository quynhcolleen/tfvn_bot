import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from cogs.utils._highlight_helpers import (
    HIGHLIGHT_MIN_INTERVAL_SECONDS,
    HIGHLIGHT_THRESHOLD,
    SKULL_EMOJI,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_POSTED,
    STATUS_POSTING,
    HighlightConfigError,
    channel_is_nsfw,
    collect_skull_voter_ids,
    count_unique_skull_voters,
    first_image_attachment,
    format_highlight_caption,
    format_highlight_congrats,
    has_renderable_content,
    ignore_reaction_user,
    is_skull_emoji,
    parse_highlight_channel_id,
    seconds_until_highlight_slot,
    should_post_highlight,
)
from cogs.utils.highlight import HighlightCog


BOT_ID = 111
GUILD_ID = 10
CHANNEL_ID = 20
HIGHLIGHT_CHANNEL_ID = 99
MESSAGE_ID = 123456789012345678
SECOND_MESSAGE_ID = 123456789012345679
NOW = datetime(2026, 9, 7, 14, 30, tzinfo=timezone.utc)


class AsyncIterator:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


class FakeCollection:
    def __init__(self):
        self.docs: list[dict] = []
        self._next_id = 1

    def create_index(self, *args, **kwargs):
        return None

    def _match(self, doc: dict, query: dict) -> bool:
        for key, expected in query.items():
            actual = doc.get(key)
            if isinstance(expected, dict):
                if "$in" in expected and actual not in expected["$in"]:
                    return False
                elif "$ne" in expected and actual == expected["$ne"]:
                    return False
                elif "$in" not in expected and "$ne" not in expected:
                    return False
            elif actual != expected:
                return False
        return True

    def find_one(self, query, sort=None):
        matches = [dict(doc) for doc in self.docs if self._match(doc, query)]
        if sort:
            for key, direction in reversed(list(sort)):
                reverse = direction in (-1, DESCENDING)
                matches.sort(
                    key=lambda document, field=key: (
                        document.get(field) is None,
                        document.get(field),
                    ),
                    reverse=reverse,
                )
        return dict(matches[0]) if matches else None

    def find(self, query):
        return [dict(doc) for doc in self.docs if self._match(doc, query)]

    def insert_one(self, document):
        for doc in self.docs:
            if (
                doc.get("guild_id") == document.get("guild_id")
                and doc.get("source_message_id") == document.get("source_message_id")
            ):
                raise DuplicateKeyError("duplicate highlight")
        stored = dict(document)
        stored["_id"] = stored.get("_id", self._next_id)
        self._next_id += 1
        self.docs.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    def _apply(self, doc, update):
        updated = dict(doc)
        if "$set" in update:
            updated.update(update["$set"])
        if "$addToSet" in update:
            for key, value in update["$addToSet"].items():
                current = list(updated.get(key) or [])
                if value not in current:
                    current.append(value)
                updated[key] = current
        if "$pull" in update:
            for key, value in update["$pull"].items():
                current = list(updated.get(key) or [])
                updated[key] = [item for item in current if item != value]
        return updated

    def update_one(self, query, update):
        for index, doc in enumerate(self.docs):
            if self._match(doc, query):
                self.docs[index] = self._apply(doc, update)
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    def find_one_and_update(self, query, update, return_document=None):
        for index, doc in enumerate(self.docs):
            if self._match(doc, query):
                before = dict(doc)
                updated = self._apply(doc, update)
                self.docs[index] = updated
                if return_document == ReturnDocument.AFTER:
                    return dict(updated)
                return before
        return None


class TestHighlightHelpers(unittest.IsolatedAsyncioTestCase):
    def test_skull_emoji_detection(self):
        self.assertTrue(is_skull_emoji(SKULL_EMOJI))
        self.assertTrue(is_skull_emoji(SimpleNamespace(name=SKULL_EMOJI, id=None)))
        self.assertFalse(is_skull_emoji("☠️"))
        self.assertFalse(is_skull_emoji("✅"))

    def test_unique_voters_exclude_bot_and_duplicates(self):
        self.assertEqual(
            count_unique_skull_voters(
                [1, 1, 2, BOT_ID, 3, 4],
                bot_user_id=BOT_ID,
            ),
            4,
        )
        self.assertFalse(
            should_post_highlight(STATUS_PENDING, max(0, HIGHLIGHT_THRESHOLD - 1))
        )
        self.assertTrue(
            should_post_highlight(STATUS_PENDING, HIGHLIGHT_THRESHOLD)
        )
        self.assertFalse(
            should_post_highlight(STATUS_POSTED, HIGHLIGHT_THRESHOLD)
        )

    def test_ignore_bot_reaction_users(self):
        self.assertTrue(
            ignore_reaction_user(user_id=BOT_ID, bot_user_id=BOT_ID, member=None)
        )
        self.assertTrue(
            ignore_reaction_user(
                user_id=5,
                bot_user_id=BOT_ID,
                member=SimpleNamespace(bot=True),
            )
        )
        self.assertFalse(
            ignore_reaction_user(
                user_id=5,
                bot_user_id=BOT_ID,
                member=SimpleNamespace(bot=False),
            )
        )

    def test_nsfw_and_channel_helpers(self):
        self.assertTrue(channel_is_nsfw(SimpleNamespace(is_nsfw=lambda: True)))
        self.assertFalse(channel_is_nsfw(SimpleNamespace(nsfw=False)))
        self.assertIsInstance(HIGHLIGHT_THRESHOLD, int)
        self.assertGreaterEqual(HIGHLIGHT_THRESHOLD, 1)
        self.assertIsInstance(HIGHLIGHT_MIN_INTERVAL_SECONDS, int)
        self.assertGreaterEqual(HIGHLIGHT_MIN_INTERVAL_SECONDS, 0)

    def test_first_image_attachment_and_renderable(self):
        image = SimpleNamespace(content_type="image/png", filename="a.png")
        video = SimpleNamespace(content_type="video/mp4", filename="a.mp4")
        self.assertIs(first_image_attachment([video, image]), image)
        self.assertIsNone(first_image_attachment([video]))
        self.assertTrue(has_renderable_content(text="", has_image=True))
        self.assertFalse(has_renderable_content(text="  ", has_image=False))

    def test_missing_highlight_channel_names_the_setting(self):
        with self.assertRaisesRegex(HighlightConfigError, "HIGHLIGHT_CHANNEL"):
            parse_highlight_channel_id(None)
        self.assertEqual(parse_highlight_channel_id("99"), 99)

    def test_caption_is_source_link_without_skull(self):
        jump = "https://discord.com/channels/1/2/3"
        caption = format_highlight_caption(jump)
        self.assertEqual(caption, f"🔗 [Tin nhắn gốc]({jump})")
        self.assertNotIn("💀", caption)

    def test_congrats_is_vietnamese_tv_reply(self):
        jump = "https://discord.com/channels/1/99/777"
        text = format_highlight_congrats("<@8>", jump)
        self.assertIn("Chúc mừng", text)
        self.assertIn("<@8>", text)
        self.assertIn("TV", text)
        self.assertIn(jump, text)
        self.assertEqual(
            format_highlight_congrats("<@8>"),
            "📺 Chúc mừng <@8>, tin nhắn này đã lên TV!",
        )

    def test_spacing_helper_zero_when_never_posted(self):
        self.assertEqual(
            seconds_until_highlight_slot(None, now=NOW),
            0.0,
        )

    def test_spacing_helper_remaining_and_ready(self):
        elapsed = max(1, HIGHLIGHT_MIN_INTERVAL_SECONDS // 2)
        last = NOW - timedelta(seconds=elapsed)
        remaining = seconds_until_highlight_slot(last, now=NOW)
        self.assertEqual(
            remaining,
            float(HIGHLIGHT_MIN_INTERVAL_SECONDS - elapsed),
        )
        ready = seconds_until_highlight_slot(
            NOW - timedelta(seconds=HIGHLIGHT_MIN_INTERVAL_SECONDS),
            now=NOW,
        )
        self.assertEqual(ready, 0.0)

    async def test_collect_skull_voters_skips_bots_and_other_emoji(self):
        skull = SimpleNamespace(
            emoji=SKULL_EMOJI,
            users=lambda: AsyncIterator(
                [
                    SimpleNamespace(id=1, bot=False),
                    SimpleNamespace(id=BOT_ID, bot=True),
                    SimpleNamespace(id=1, bot=False),
                    SimpleNamespace(id=2, bot=False),
                ]
            ),
        )
        other = SimpleNamespace(
            emoji="🔥",
            users=lambda: AsyncIterator([SimpleNamespace(id=9, bot=False)]),
        )
        message = SimpleNamespace(reactions=[other, skull])
        voters = await collect_skull_voter_ids(message, bot_user_id=BOT_ID)
        self.assertEqual(voters, [1, 2])


class TestHighlightAutomation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.collection = FakeCollection()
        self.highlight_channel = SimpleNamespace(
            id=HIGHLIGHT_CHANNEL_ID,
            send=AsyncMock(
                return_value=SimpleNamespace(
                    id=777,
                    jump_url=(
                        f"https://discord.com/channels/{GUILD_ID}/"
                        f"{HIGHLIGHT_CHANNEL_ID}/777"
                    ),
                )
            ),
            is_nsfw=lambda: False,
        )
        self.source_channel = SimpleNamespace(
            id=CHANNEL_ID,
            name="general",
            is_nsfw=lambda: False,
            fetch_message=AsyncMock(),
        )
        self.guild = SimpleNamespace(id=GUILD_ID, get_channel=Mock(return_value=None))
        self.bot = SimpleNamespace(
            db={"highlight_nominations": self.collection},
            global_vars={"HIGHLIGHT_CHANNEL": str(HIGHLIGHT_CHANNEL_ID)},
            user=SimpleNamespace(id=BOT_ID),
            get_channel=Mock(side_effect=self._get_channel),
            get_guild=Mock(return_value=self.guild),
            fetch_channel=AsyncMock(side_effect=discord.NotFound(Mock(), "missing")),
            is_ready=lambda: False,
        )
        self.cog = HighlightCog(self.bot)
        self.author = SimpleNamespace(
            id=8,
            mention="<@8>",
            name="src",
            display_name="Src",
            color=SimpleNamespace(value=0, to_rgb=lambda: (88, 101, 242)),
            guild_avatar=None,
            display_avatar=SimpleNamespace(
                with_size=lambda size: SimpleNamespace(
                    read=AsyncMock(return_value=b"ava")
                )
            ),
        )
        self.message = self._make_message(MESSAGE_ID)
        self.source_channel.fetch_message.side_effect = self._fetch_source

    def _get_channel(self, channel_id):
        return {
            HIGHLIGHT_CHANNEL_ID: self.highlight_channel,
            CHANNEL_ID: self.source_channel,
        }.get(channel_id)

    def _make_message(self, message_id: int):
        return SimpleNamespace(
            id=message_id,
            guild=self.guild,
            channel=self.source_channel,
            author=self.author,
            clean_content="Tin nhắn hài",
            content="Tin nhắn hài",
            attachments=[],
            reactions=[],
            jump_url=(
                f"https://discord.com/channels/{GUILD_ID}/"
                f"{CHANNEL_ID}/{message_id}"
            ),
            created_at=NOW,
            reply=AsyncMock(),
        )

    async def _fetch_source(self, message_id):
        if message_id == self.message.id:
            return self.message
        raise discord.NotFound(Mock(status=404, reason="Not Found"), "missing")

    def _payload(self, user_id: int, *, message_id: int = MESSAGE_ID, emoji=SKULL_EMOJI):
        return SimpleNamespace(
            guild_id=GUILD_ID,
            channel_id=CHANNEL_ID,
            message_id=message_id,
            user_id=user_id,
            member=SimpleNamespace(bot=False, id=user_id),
            emoji=emoji,
        )

    async def _mark_posted(self, nomination: dict) -> None:
        self.collection.update_one(
            {"_id": nomination["_id"], "status": STATUS_POSTING},
            {
                "$set": {
                    "status": STATUS_POSTED,
                    "posted_at": NOW,
                    "highlight_message_id": 777,
                }
            },
        )

    def _set_skulls(self, user_ids: list[int]) -> None:
        bound = list(user_ids)
        self.message.reactions = [
            SimpleNamespace(
                emoji=SKULL_EMOJI,
                users=lambda ids=bound: AsyncIterator(
                    [SimpleNamespace(id=item, bot=False) for item in ids]
                ),
            )
        ]

    def _below_threshold_users(self) -> list[int]:
        return list(range(1, HIGHLIGHT_THRESHOLD))

    def _threshold_users(self) -> list[int]:
        return list(range(1, HIGHLIGHT_THRESHOLD + 1))

    async def _add_skulls(self, user_ids: list[int]) -> None:
        for index, user_id in enumerate(user_ids, start=1):
            self._set_skulls(user_ids[:index])
            await self.cog.on_raw_reaction_add(
                self._payload(user_id, message_id=self.message.id)
            )
            await self._await_idle_flush()

    async def _await_idle_flush(self) -> None:
        task = self.cog._flush_tasks.get(GUILD_ID)
        if task is None:
            return
        await asyncio.wait({task}, timeout=0.2)
        if task.done():
            await task

    async def asyncTearDown(self):
        tasks = [task for task in self.cog._flush_tasks.values() if task is not None]
        self.cog.cog_unload()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_four_skulls_do_not_post(self):
        self.cog._publish_highlight = AsyncMock()
        await self._add_skulls(self._below_threshold_users())
        self.cog._publish_highlight.assert_not_awaited()
        self.assertEqual(self.collection.docs, [])

    async def test_fifth_unique_skull_posts_once(self):
        self.cog._publish_highlight = AsyncMock(side_effect=self._mark_posted)
        await self._add_skulls(self._threshold_users())
        self.cog._publish_highlight.assert_awaited_once()
        self.assertEqual(self.collection.docs[0]["status"], STATUS_POSTED)

        self.cog._publish_highlight.reset_mock()
        self._set_skulls(self._threshold_users() + [HIGHLIGHT_THRESHOLD + 1])
        await self.cog.on_raw_reaction_add(
            self._payload(HIGHLIGHT_THRESHOLD + 1)
        )
        await self._await_idle_flush()
        self.cog._publish_highlight.assert_not_awaited()

    async def test_bot_skull_does_not_count(self):
        self.cog._publish_highlight = AsyncMock()
        await self._add_skulls(self._below_threshold_users())
        self._set_skulls(self._below_threshold_users() + [BOT_ID])
        payload = self._payload(BOT_ID)
        payload.member = SimpleNamespace(bot=True, id=BOT_ID)
        await self.cog.on_raw_reaction_add(payload)
        await self._await_idle_flush()
        self.cog._publish_highlight.assert_not_awaited()
        self.assertEqual(self.collection.docs, [])

    async def test_non_skull_reaction_is_ignored(self):
        self.cog._publish_highlight = AsyncMock()
        self._set_skulls(self._threshold_users())
        await self.cog.on_raw_reaction_add(
            self._payload(HIGHLIGHT_THRESHOLD, emoji="🔥")
        )
        await self._await_idle_flush()
        self.cog._publish_highlight.assert_not_awaited()

    async def test_source_in_highlight_channel_ignored(self):
        self.cog._publish_highlight = AsyncMock()
        self.message.channel = self.highlight_channel
        self._set_skulls(self._threshold_users())
        await self.cog.on_raw_reaction_add(
            self._payload(HIGHLIGHT_THRESHOLD)
        )
        await self._await_idle_flush()
        self.cog._publish_highlight.assert_not_awaited()
        self.assertEqual(self.collection.docs, [])

    async def test_nsfw_channel_reactions_and_messages_are_ignored(self):
        self.cog._publish_highlight = AsyncMock()
        self.source_channel.is_nsfw = lambda: True
        await self._add_skulls(self._threshold_users())
        self.cog._publish_highlight.assert_not_awaited()
        self.assertEqual(self.collection.docs, [])
        await self.cog.on_raw_reaction_remove(
            self._payload(HIGHLIGHT_THRESHOLD)
        )
        self.assertEqual(self.collection.docs, [])

    async def test_missing_highlight_channel_stays_pending(self):
        self.bot.global_vars = {}
        with patch("cogs.utils.highlight.logger.exception"):
            await self._add_skulls(self._threshold_users())
        self.assertEqual(self.collection.docs[0]["status"], STATUS_PENDING)
        self.highlight_channel.send.assert_not_awaited()

    async def test_second_cas_does_not_publish_twice(self):
        await self._add_skulls(self._below_threshold_users())
        self._set_skulls(self._threshold_users())
        self.cog._publish_highlight = AsyncMock(side_effect=self._mark_posted)
        await self.cog.on_raw_reaction_add(
            self._payload(HIGHLIGHT_THRESHOLD)
        )
        await self._await_idle_flush()
        await self.cog._flush_guild(GUILD_ID)
        self.cog._publish_highlight.assert_awaited_once()

    async def test_spacing_holds_second_qualified_message(self):
        first = self.message
        second = self._make_message(SECOND_MESSAGE_ID)
        messages = {MESSAGE_ID: first, SECOND_MESSAGE_ID: second}

        async def fetch(message_id):
            return messages[message_id]

        self.source_channel.fetch_message.side_effect = fetch
        self.cog._publish_highlight = AsyncMock(side_effect=self._mark_posted)

        with patch(
            "cogs.utils.highlight.discord.utils.utcnow",
            return_value=NOW,
        ):
            self.message = first
            await self._add_skulls(self._threshold_users())
            self.assertEqual(self.cog._publish_highlight.await_count, 1)

            self.message = second
            await self._add_skulls(self._threshold_users())
            self.assertEqual(self.cog._publish_highlight.await_count, 1)
            self.assertEqual(
                sum(1 for doc in self.collection.docs if doc["status"] == STATUS_PENDING),
                1,
            )

        later = NOW + timedelta(seconds=HIGHLIGHT_MIN_INTERVAL_SECONDS)
        with patch(
            "cogs.utils.highlight.discord.utils.utcnow",
            return_value=later,
        ):
            await self.cog._flush_guild(GUILD_ID)
        self.assertEqual(self.cog._publish_highlight.await_count, 2)

    async def test_remove_below_threshold_during_wait_skips_flush(self):
        first = self.message
        second = self._make_message(SECOND_MESSAGE_ID)
        messages = {MESSAGE_ID: first, SECOND_MESSAGE_ID: second}

        async def fetch(message_id):
            return messages[message_id]

        self.source_channel.fetch_message.side_effect = fetch
        self.cog._publish_highlight = AsyncMock(side_effect=self._mark_posted)

        with patch(
            "cogs.utils.highlight.discord.utils.utcnow",
            return_value=NOW,
        ):
            self.message = first
            await self._add_skulls(self._threshold_users())
            self.message = second
            await self._add_skulls(self._threshold_users())

        bound = self._below_threshold_users()
        second.reactions = [
            SimpleNamespace(
                emoji=SKULL_EMOJI,
                users=lambda ids=bound: AsyncIterator(
                    [SimpleNamespace(id=item, bot=False) for item in ids]
                ),
            )
        ]
        await self.cog.on_raw_reaction_remove(
            self._payload(HIGHLIGHT_THRESHOLD, message_id=SECOND_MESSAGE_ID)
        )
        later = NOW + timedelta(seconds=HIGHLIGHT_MIN_INTERVAL_SECONDS)
        with patch(
            "cogs.utils.highlight.discord.utils.utcnow",
            return_value=later,
        ):
            await self.cog._flush_guild(GUILD_ID)
        self.assertEqual(self.cog._publish_highlight.await_count, 1)
        pending = [
            doc
            for doc in self.collection.docs
            if doc["source_message_id"] == SECOND_MESSAGE_ID
        ][0]
        self.assertEqual(pending["status"], STATUS_PENDING)

    async def test_deleted_source_marks_failed(self):
        self.collection.insert_one(
            {
                "guild_id": GUILD_ID,
                "source_channel_id": CHANNEL_ID,
                "source_message_id": MESSAGE_ID,
                "status": STATUS_PENDING,
                "voter_ids": list(range(1, HIGHLIGHT_THRESHOLD + 1)),
                "qualified_at": NOW,
                "created_at": NOW,
            }
        )
        self.source_channel.fetch_message.side_effect = discord.NotFound(
            Mock(status=404, reason="Not Found"),
            "missing",
        )
        await self.cog._flush_guild(GUILD_ID)
        self.assertEqual(self.collection.docs[0]["status"], STATUS_FAILED)

    async def test_failed_publish_reverts_to_pending(self):
        self.cog._publish_highlight = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("cogs.utils.highlight.logger.exception"):
            await self._add_skulls(self._threshold_users())
        self.assertEqual(self.collection.docs[0]["status"], STATUS_PENDING)

    async def test_remove_after_posted_is_ignored(self):
        self.collection.insert_one(
            {
                "guild_id": GUILD_ID,
                "source_message_id": MESSAGE_ID,
                "status": STATUS_POSTED,
                "voter_ids": list(range(1, HIGHLIGHT_THRESHOLD + 1)),
            }
        )
        await self.cog.on_raw_reaction_remove(
            self._payload(HIGHLIGHT_THRESHOLD)
        )
        self.assertEqual(
            self.collection.docs[0]["voter_ids"],
            list(range(1, HIGHLIGHT_THRESHOLD + 1)),
        )

    def _posting_nomination(self) -> dict:
        self.collection.insert_one(
            {
                "guild_id": GUILD_ID,
                "source_channel_id": CHANNEL_ID,
                "source_message_id": MESSAGE_ID,
                "status": STATUS_POSTING,
                "voter_ids": self._threshold_users(),
                "qualified_at": NOW,
                "created_at": NOW,
            }
        )
        return dict(self.collection.docs[0])

    async def test_publish_replies_vietnamese_congrats_on_source(self):
        nomination = self._posting_nomination()
        with patch(
            "cogs.utils.highlight.render_highlight_card",
            return_value=b"\x89PNG\r\n\x1a\nxxxx",
        ):
            await self.cog._publish_highlight(nomination)
        self.message.reply.assert_awaited_once()
        content = self.message.reply.await_args.args[0]
        self.assertIn("Chúc mừng", content)
        self.assertIn("<@8>", content)
        self.assertIn("TV", content)
        self.assertIn("777", content)
        self.assertTrue(self.message.reply.await_args.kwargs["mention_author"])
        self.assertEqual(self.collection.docs[0]["status"], STATUS_POSTED)

    async def test_congrats_reply_failure_still_marks_posted(self):
        self.message.reply = AsyncMock(
            side_effect=discord.Forbidden(
                Mock(status=403, reason="Forbidden"),
                "missing send permission",
            )
        )
        nomination = self._posting_nomination()
        with patch(
            "cogs.utils.highlight.render_highlight_card",
            return_value=b"\x89PNG\r\n\x1a\nxxxx",
        ), patch("cogs.utils.highlight.logger.warning"):
            await self.cog._publish_highlight(nomination)
        self.assertEqual(self.collection.docs[0]["status"], STATUS_POSTED)

    async def test_bot_authored_source_skips_congrats(self):
        self.author.id = BOT_ID
        self.author.mention = f"<@{BOT_ID}>"
        nomination = self._posting_nomination()
        with patch(
            "cogs.utils.highlight.render_highlight_card",
            return_value=b"\x89PNG\r\n\x1a\nxxxx",
        ):
            await self.cog._publish_highlight(nomination)
        self.message.reply.assert_not_awaited()
        self.assertEqual(self.collection.docs[0]["status"], STATUS_POSTED)


if __name__ == "__main__":
    unittest.main()
