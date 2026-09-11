import base64
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.ext import commands
from pymongo.errors import DuplicateKeyError

from cogs._hash_verification import load_verification_keyring
from cogs.operation import _doctor as doctor
from cogs.utils._softotp_helpers import (
    SOFTOTP_ACCOUNT_MISMATCH_MESSAGE,
    SOFTOTP_COLLECTION,
    SOFTOTP_KEY_MESSAGE,
    SoftOtpError,
    compute_otp_code,
    confirm_softotp_token,
    format_softotp_token,
    issue_softotp_token,
    normalize_challenge,
    parse_softotp_token,
    persist_softotp_issuance,
    resolve_softotp_issuance,
)
from cogs.utils._softotp_ui import (
    GetOtpModal,
    SoftOtpRevealView,
    SoftOtpView,
    VerifyOtpModal,
    can_verify_softotp,
)
from cogs.utils.softotp import SoftOtpCog

TOKEN_PATTERN = r"`tfotp1\.[A-Za-z0-9_-]+\.[1-9][0-9]*\.[0-9A-HJKMNP-TV-Z]{8}`"


GUILD_ID = 10
AUTHOR_ID = 20
OTHER_USER_ID = 21
OLD_KEY = bytes(range(32))
NEW_KEY = bytes(range(32, 64))
ISSUED_AT = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)


def encoded_key(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def keyring(*, active: str = "2026-08", keys: dict[str, bytes] | None = None):
    configured = keys or {"2026-08": OLD_KEY}
    return load_verification_keyring(
        json.dumps({kid: encoded_key(value) for kid, value in configured.items()}),
        active,
    )


TEST_KEYRING = keyring()


class MemoryIssuances:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}

    def create_index(self, *args: object, **kwargs: object) -> str:
        return "ok"

    def find_one(self, query: dict) -> dict | None:
        if "_id" in query:
            document = self.documents.get(query["_id"])
            return dict(document) if document is not None else None
        for document in self.documents.values():
            if all(document.get(key) == value for key, value in query.items()):
                return dict(document)
        return None

    def insert_one(self, document: dict) -> None:
        if document["_id"] in self.documents:
            raise DuplicateKeyError("duplicate")
        slot = (document["guild_id"], document["user_id"], document["challenge"])
        for existing in self.documents.values():
            if (
                existing["guild_id"],
                existing["user_id"],
                existing["challenge"],
            ) == slot:
                raise DuplicateKeyError("duplicate")
        self.documents[document["_id"]] = dict(document)

    def update_one(self, query: dict, update: dict) -> None:
        current = self.find_one(query)
        if current is None:
            return
        self.documents[current["_id"]].update(update.get("$set", {}))

    def delete_one(self, query: dict) -> None:
        current = self.find_one(query)
        if current is not None:
            del self.documents[current["_id"]]


def persist(
    *,
    user_id: int = AUTHOR_ID,
    challenge: str = "123456",
    guild_id: int = GUILD_ID,
    signing_keyring=TEST_KEYRING,
    collection: MemoryIssuances | None = None,
) -> tuple[str, MemoryIssuances]:
    issuances = collection or MemoryIssuances()
    token = persist_softotp_issuance(
        issuances,
        signing_keyring,
        guild_id=guild_id,
        user_id=user_id,
        challenge=challenge,
        issued_at=ISSUED_AT,
    )
    return token, issuances


def http_error(status: int = 403) -> discord.HTTPException:
    return discord.HTTPException(
        SimpleNamespace(status=status, reason="Forbidden"),
        "Missing Access",
    )


def make_interaction(
    *,
    user_id: int = AUTHOR_ID,
    guild_id: int | None = GUILD_ID,
    administrator: bool = False,
    manage_guild: bool = False,
    guild_name: str = "TFVN",
) -> SimpleNamespace:
    acknowledged = {"done": False}

    async def acknowledge(*args: object, **kwargs: object) -> None:
        acknowledged["done"] = True

    permissions = discord.Permissions(
        administrator=administrator,
        manage_guild=manage_guild,
    )
    guild = None
    if guild_id is not None:
        guild = SimpleNamespace(
            id=guild_id,
            name=guild_name,
            get_member=lambda member_id: None,
            fetch_member=AsyncMock(side_effect=http_error(404)),
        )
    return SimpleNamespace(
        guild_id=guild_id,
        guild=guild,
        user=SimpleNamespace(id=user_id, guild_permissions=permissions),
        response=SimpleNamespace(
            is_done=lambda: acknowledged["done"],
            send_message=AsyncMock(side_effect=acknowledge),
            send_modal=AsyncMock(side_effect=acknowledge),
            defer=AsyncMock(side_effect=acknowledge),
            edit_message=AsyncMock(side_effect=acknowledge),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


class TestSoftOtpHelpers(unittest.TestCase):
    def test_different_challenges_issue_opaque_tokens_without_user_ids(self) -> None:
        first = issue_softotp_token(
            TEST_KEYRING,
            guild_id=GUILD_ID,
            user_id=AUTHOR_ID,
            challenge="123456",
            issued_at=ISSUED_AT,
        )
        second = issue_softotp_token(
            TEST_KEYRING,
            guild_id=GUILD_ID,
            user_id=AUTHOR_ID,
            challenge="jd3s1s",
            issued_at=ISSUED_AT,
        )

        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("tfotp1.2026-08."))
        self.assertIn(f".{int(ISSUED_AT.timestamp())}.", first)
        self.assertNotIn(f".{AUTHOR_ID}.", first)
        self.assertEqual(len(parse_softotp_token(first).code), 8)

    def test_persist_lets_verify_name_the_member_without_scanning(self) -> None:
        token, issuances = persist(challenge="123456")
        self.assertEqual(
            resolve_softotp_issuance(
                issuances,
                TEST_KEYRING,
                guild_id=GUILD_ID,
                challenge="123456",
                token=token,
            ),
            AUTHOR_ID,
        )
        other, _ = persist(challenge="jd3s1s", collection=issuances)
        with self.assertRaises(SoftOtpError):
            resolve_softotp_issuance(
                issuances,
                TEST_KEYRING,
                guild_id=GUILD_ID,
                challenge="123456",
                token=other,
            )

    def test_guessed_discord_id_tokens_are_rejected(self) -> None:
        token, issuances = persist()
        code = parse_softotp_token(token).code
        guessed = f"tfotp1-{AUTHOR_ID}-{code}"

        with self.assertRaises(SoftOtpError):
            parse_softotp_token(guessed)
        with self.assertRaises(SoftOtpError):
            resolve_softotp_issuance(
                issuances,
                TEST_KEYRING,
                guild_id=GUILD_ID,
                challenge="123456",
                token=guessed,
            )
        hmac_only = issue_softotp_token(
            TEST_KEYRING,
            guild_id=GUILD_ID,
            user_id=AUTHOR_ID,
            challenge="123456",
            issued_at=ISSUED_AT,
        )
        with self.assertRaises(SoftOtpError):
            resolve_softotp_issuance(
                MemoryIssuances(),
                TEST_KEYRING,
                guild_id=GUILD_ID,
                challenge="123456",
                token=hmac_only,
            )

    def test_same_challenge_is_stable_and_case_insensitive(self) -> None:
        first, issuances = persist(challenge="Jd3S1S")
        second, _ = persist(challenge="  `jd3s1s`  ", collection=issuances)

        self.assertEqual(first, second)
        self.assertEqual(normalize_challenge("Jd3S1S"), "jd3s1s")
        self.assertEqual(
            resolve_softotp_issuance(
                issuances,
                TEST_KEYRING,
                guild_id=GUILD_ID,
                challenge="JD3S1S",
                token=f"`{first}`",
            ),
            AUTHOR_ID,
        )

    def test_other_users_and_guilds_cannot_reuse_a_code(self) -> None:
        token, issuances = persist(challenge="form-2026")
        other_user, _ = persist(
            user_id=OTHER_USER_ID,
            challenge="form-2026",
            collection=issuances,
        )
        other_guild, _ = persist(
            challenge="form-2026",
            guild_id=GUILD_ID + 1,
            collection=issuances,
        )

        self.assertNotEqual(token, other_user)
        self.assertNotEqual(token, other_guild)
        with self.assertRaises(SoftOtpError):
            resolve_softotp_issuance(
                issuances,
                TEST_KEYRING,
                guild_id=GUILD_ID + 1,
                challenge="form-2026",
                token=token,
            )

    def test_rotating_active_key_invalidates_outstanding_otps(self) -> None:
        token, issuances = persist(challenge="123456")
        rotated = keyring(
            active="new",
            keys={"2026-08": OLD_KEY, "new": NEW_KEY},
        )

        with self.assertRaises(SoftOtpError) as raised:
            resolve_softotp_issuance(
                issuances,
                rotated,
                guild_id=GUILD_ID,
                challenge="123456",
                token=token,
            )
        self.assertEqual(str(raised.exception), SOFTOTP_KEY_MESSAGE)
        with self.assertRaises(SoftOtpError) as confirm_error:
            confirm_softotp_token(
                rotated,
                guild_id=GUILD_ID,
                user_id=AUTHOR_ID,
                challenge="123456",
                token=token,
            )
        self.assertEqual(str(confirm_error.exception), SOFTOTP_KEY_MESSAGE)

        replacement, _ = persist(
            challenge="123456",
            signing_keyring=rotated,
            collection=issuances,
        )
        self.assertNotEqual(replacement, token)
        self.assertTrue(replacement.startswith("tfotp1.new."))
        self.assertEqual(
            resolve_softotp_issuance(
                issuances,
                rotated,
                guild_id=GUILD_ID,
                challenge="123456",
                token=replacement,
            ),
            AUTHOR_ID,
        )

    def test_forged_or_malformed_inputs_are_rejected(self) -> None:
        token, issuances = persist()
        parsed = parse_softotp_token(token)
        flipped = "0" if parsed.code[-1] != "0" else "1"
        forged = format_softotp_token(
            parsed.kid, parsed.issued_at, parsed.code[:-1] + flipped
        )

        with self.assertRaises(SoftOtpError):
            resolve_softotp_issuance(
                issuances,
                TEST_KEYRING,
                guild_id=GUILD_ID,
                challenge="123456",
                token=forged,
            )
        with self.assertRaises(SoftOtpError):
            parse_softotp_token("847291")
        with self.assertRaises(SoftOtpError):
            normalize_challenge("")
        with self.assertRaises(SoftOtpError):
            normalize_challenge("hello world")
        with self.assertRaises(SoftOtpError):
            normalize_challenge("x" * 65)
        with self.assertRaises(SoftOtpError):
            normalize_challenge("bad`challenge")

    def test_tampered_issuance_record_fails_hmac(self) -> None:
        token, issuances = persist()
        stored = next(iter(issuances.documents.values()))
        stored["user_id"] = OTHER_USER_ID

        with self.assertRaises(SoftOtpError):
            resolve_softotp_issuance(
                issuances,
                TEST_KEYRING,
                guild_id=GUILD_ID,
                challenge="123456",
                token=token,
            )

    def test_claimed_member_rejects_another_members_otp(self) -> None:
        alice, _ = persist(user_id=AUTHOR_ID, challenge="form-2026")
        bob, _ = persist(user_id=OTHER_USER_ID, challenge="form-2026")

        self.assertEqual(
            confirm_softotp_token(
                TEST_KEYRING,
                guild_id=GUILD_ID,
                user_id=AUTHOR_ID,
                challenge="form-2026",
                token=alice,
            ),
            AUTHOR_ID,
        )
        with self.assertRaises(SoftOtpError) as raised:
            confirm_softotp_token(
                TEST_KEYRING,
                guild_id=GUILD_ID,
                user_id=AUTHOR_ID,
                challenge="form-2026",
                token=bob,
            )
        self.assertEqual(str(raised.exception), SOFTOTP_ACCOUNT_MISMATCH_MESSAGE)

    def test_later_issue_time_changes_the_code(self) -> None:
        first = issue_softotp_token(
            TEST_KEYRING,
            guild_id=GUILD_ID,
            user_id=AUTHOR_ID,
            challenge="123456",
            issued_at=ISSUED_AT,
        )
        later = issue_softotp_token(
            TEST_KEYRING,
            guild_id=GUILD_ID,
            user_id=AUTHOR_ID,
            challenge="123456",
            issued_at=ISSUED_AT + timedelta(minutes=1),
        )
        self.assertNotEqual(first, later)
        self.assertEqual(parse_softotp_token(first).kid, "2026-08")
        self.assertGreater(
            parse_softotp_token(later).issued_at,
            parse_softotp_token(first).issued_at,
        )

    def test_compute_otp_uses_five_byte_crockford_codes(self) -> None:
        code = compute_otp_code(
            OLD_KEY,
            GUILD_ID,
            AUTHOR_ID,
            "jd3s1s",
            kid="2026-08",
            issued_at=int(ISSUED_AT.timestamp()),
        )
        self.assertEqual(len(code), 8)
        self.assertTrue(code.isalnum())
        self.assertEqual(code, code.upper())


class TestSoftOtpCog(unittest.IsolatedAsyncioTestCase):
    def _cog(
        self,
        *,
        configured: bool = True,
        issuances: MemoryIssuances | None = None,
    ) -> SoftOtpCog:
        store = issuances or MemoryIssuances()
        bot = SimpleNamespace(
            verification_keyring=TEST_KEYRING if configured else None,
            CONTENT_VERIFICATION_KEYS_JSON=None,
            CONTENT_VERIFICATION_ACTIVE_KEY_ID=None,
            db={SOFTOTP_COLLECTION: store},
        )
        if not configured:
            bot.verification_keyring = object()
        cog = SoftOtpCog(bot)
        if not configured:
            cog.verification_keyring = None
        self.addAsyncCleanup(cog.cog_unload)
        return cog

    def _context(
        self,
        *,
        manage_guild: bool = False,
        administrator: bool = False,
        guild_id: int | None = GUILD_ID,
        user_id: int = AUTHOR_ID,
    ) -> SimpleNamespace:
        permissions = discord.Permissions(
            administrator=administrator,
            manage_guild=manage_guild,
        )
        member = SimpleNamespace(
            id=AUTHOR_ID,
            mention=f"<@{AUTHOR_ID}>",
        )
        guild = None
        if guild_id is not None:
            guild = SimpleNamespace(
                id=guild_id,
                name="TFVN",
                get_member=lambda target_id: member if target_id == AUTHOR_ID else None,
                fetch_member=AsyncMock(side_effect=http_error(404)),
            )
        author = SimpleNamespace(
            id=user_id,
            guild_permissions=permissions,
            send=AsyncMock(),
        )
        posted = SimpleNamespace(id=99, edit=AsyncMock())
        return SimpleNamespace(
            guild=guild,
            author=author,
            prefix="!tf ",
            clean_prefix="!tf ",
            send=AsyncMock(return_value=posted),
            reply=AsyncMock(return_value=posted),
        )

    async def test_group_and_get_are_open_verify_requires_manage_guild(self) -> None:
        cog = self._cog()
        member_ctx = self._context()
        staff_ctx = self._context(manage_guild=True)
        dm_ctx = self._context(guild_id=None, manage_guild=True)

        for command in (cog.softotp_group, cog.softotp_get):
            with self.subTest(command=command.qualified_name):
                self.assertTrue(
                    await discord.utils.async_all(
                        check(member_ctx) for check in command.checks
                    )
                )
                with self.assertRaises(commands.NoPrivateMessage):
                    await discord.utils.async_all(
                        check(dm_ctx) for check in command.checks
                    )

        self.assertTrue(
            await discord.utils.async_all(
                check(staff_ctx) for check in cog.softotp_verify.checks
            )
        )
        with self.assertRaises(commands.MissingPermissions):
            await discord.utils.async_all(
                check(member_ctx) for check in cog.softotp_verify.checks
            )
        admin_only = self._context(administrator=True)
        self.assertTrue(
            await discord.utils.async_all(
                check(admin_only) for check in cog.softotp_verify.checks
            )
        )

    async def test_group_opens_ui_without_verify_for_members(self) -> None:
        cog = self._cog()
        ctx = self._context()

        await cog.softotp_group.callback(cog, ctx)

        ctx.send.assert_not_awaited()
        ctx.reply.assert_awaited_once()
        self.assertEqual(
            ctx.reply.await_args.args[0], "||Chỉ bạn thấy bảng này.||"
        )
        kwargs = ctx.reply.await_args.kwargs
        view = kwargs["view"]
        self.assertIsInstance(view, SoftOtpView)
        self.assertFalse(view.can_verify)
        self.assertFalse(kwargs["mention_author"])
        self.assertIs(view.message, ctx.reply.return_value)
        labels = [
            item.label
            for item in view.children
            if isinstance(item, discord.ui.Button)
        ]
        self.assertEqual(labels, ["Lấy OTP", "Đóng"])
        self.assertFalse(
            any(isinstance(item, discord.ui.UserSelect) for item in view.children)
        )
        self.assertIn("Google Form", kwargs["embed"].description)
        self.assertEqual(
            kwargs["allowed_mentions"].to_dict(),
            discord.AllowedMentions.none().to_dict(),
        )

    async def test_staff_panel_includes_verify_control(self) -> None:
        cog = self._cog()
        ctx = self._context(manage_guild=True)

        await cog.softotp_group.callback(cog, ctx)

        ctx.send.assert_not_awaited()
        self.assertEqual(
            ctx.reply.await_args.args[0], "||Chỉ bạn thấy bảng này.||"
        )
        view = ctx.reply.await_args.kwargs["view"]
        labels = {
            item.label
            for item in view.children
            if isinstance(item, discord.ui.Button)
        }
        self.assertEqual(labels, {"Lấy OTP", "Xác minh", "Đóng"})
        selects = [
            item
            for item in view.children
            if isinstance(item, discord.ui.UserSelect)
        ]
        self.assertEqual(len(selects), 1)
        self.assertEqual(selects[0].min_values, 0)
        self.assertIn("softotp verify", ctx.reply.await_args.kwargs["embed"].description)
        self.assertFalse(ctx.reply.await_args.kwargs["mention_author"])

    async def test_get_sends_otp_privately_and_not_in_channel(self) -> None:
        cog = self._cog()
        ctx = self._context()

        await cog.softotp_get.callback(cog, ctx, "123456")

        dm_embed = ctx.author.send.await_args.kwargs["embed"]
        channel_text = ctx.send.await_args.args[0]
        rendered = "\n".join(field.value for field in dm_embed.fields)
        self.assertIn("`123456`", rendered)
        self.assertRegex(rendered, TOKEN_PATTERN)
        self.assertNotRegex(rendered, r"`tfotp1-\d+-")
        self.assertIn("tin nhắn riêng", channel_text)
        self.assertNotIn("tfotp1.", channel_text)

    async def test_get_falls_back_to_reveal_button_when_dm_fails(self) -> None:
        cog = self._cog()
        ctx = self._context()
        ctx.author.send.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"),
            "Cannot send messages to this user",
        )

        await cog.softotp_get.callback(cog, ctx, "jd3s1s")

        ctx.send.assert_not_awaited()
        kwargs = ctx.reply.await_args.kwargs
        self.assertIn("Không gửi được tin nhắn riêng", ctx.reply.await_args.args[0])
        self.assertIsInstance(kwargs["view"], SoftOtpRevealView)
        self.assertEqual(kwargs["view"].challenge, "jd3s1s")
        self.assertFalse(kwargs["mention_author"])

    async def test_verify_identifies_the_bound_member_from_storage(self) -> None:
        cog = self._cog()
        ctx = self._context(manage_guild=True)
        token = await cog.issue_token(GUILD_ID, AUTHOR_ID, "jd3s1s")

        await cog.softotp_verify.callback(cog, ctx, "JD3S1S", token)

        embed = ctx.send.await_args.kwargs["embed"]
        rendered = "\n".join(field.value for field in embed.fields)
        self.assertEqual(embed.title, "✅ Soft OTP hợp lệ")
        self.assertIn(f"<@{AUTHOR_ID}>", rendered)
        self.assertIn("`jd3s1s`", rendered)

    async def test_verify_claimed_member_rejects_another_otp(self) -> None:
        cog = self._cog()
        ctx = self._context(manage_guild=True)
        bob_token = await cog.issue_token(GUILD_ID, OTHER_USER_ID, "form-2026")
        claimed = SimpleNamespace(id=AUTHOR_ID, mention=f"<@{AUTHOR_ID}>")

        await cog.softotp_verify.callback(
            cog, ctx, "form-2026", bob_token, claimed
        )

        self.assertIn("tài khoản đã chọn", ctx.send.await_args.args[0])
        self.assertNotIn("embed", ctx.send.await_args.kwargs)

        alice_token = await cog.issue_token(GUILD_ID, AUTHOR_ID, "form-2026")
        await cog.softotp_verify.callback(
            cog, ctx, "form-2026", alice_token, claimed
        )
        embed = ctx.send.await_args.kwargs["embed"]
        self.assertIn("tài khoản đã chọn", embed.description)

    async def test_verify_rejects_wrong_challenge_and_missing_keyring(self) -> None:
        cog = self._cog()
        ctx = self._context(manage_guild=True)
        token = await cog.issue_token(GUILD_ID, AUTHOR_ID, "123456")

        await cog.softotp_verify.callback(cog, ctx, "jd3s1s", token)
        self.assertIn("không khớp", ctx.send.await_args.args[0].casefold())

        unconfigured = self._cog(configured=False)
        empty_ctx = self._context()
        await unconfigured.softotp_get.callback(unconfigured, empty_ctx, "123456")
        self.assertIn("chưa được cấu hình", empty_ctx.send.await_args.args[0])

    async def test_syntax_error_shows_usage(self) -> None:
        cog = self._cog()
        ctx = self._context()

        await cog.cog_command_error(ctx, commands.TooManyArguments())

        self.assertIn("softotp get <challenge>", ctx.send.await_args.args[0])
        self.assertIn("softotp verify <challenge> <otp>", ctx.send.await_args.args[0])


class TestSoftOtpUI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.issuances = MemoryIssuances()
        self.cog = SoftOtpCog(
            SimpleNamespace(
                verification_keyring=TEST_KEYRING,
                db={SOFTOTP_COLLECTION: self.issuances},
            )
        )
        self.addAsyncCleanup(self.cog.cog_unload)

    def _panel(self, *, can_verify: bool = True) -> SoftOtpView:
        view = SoftOtpView(
            self.cog,
            guild_id=GUILD_ID,
            author_id=AUTHOR_ID,
            prefix="!tf ",
            can_verify=can_verify,
        )
        view.message = SimpleNamespace(edit=AsyncMock())
        self.addCleanup(view.stop)
        return view

    def assert_private_reply(self, interaction: SimpleNamespace) -> str:
        replies = (
            interaction.response.send_message.await_args_list
            + interaction.followup.send.await_args_list
        )
        self.assertTrue(replies, "Interaction did not receive a private response")
        contents: list[str] = []
        for reply in replies:
            self.assertTrue(reply.kwargs.get("ephemeral"))
            self.assertEqual(
                reply.kwargs["allowed_mentions"].to_dict(),
                discord.AllowedMentions.none().to_dict(),
            )
            if reply.args:
                contents.append(str(reply.args[0]))
            embed = reply.kwargs.get("embed")
            if embed is not None:
                contents.append(embed.title or "")
                contents.append(embed.description or "")
                contents.extend(field.value for field in embed.fields)
            if "content" in reply.kwargs and reply.kwargs["content"]:
                contents.append(str(reply.kwargs["content"]))
        return "\n".join(contents)

    async def test_panel_embed_stays_within_api_limits(self) -> None:
        view = self._panel()
        embed = view.build_embed()
        self.assertLessEqual(len(embed), 6000)
        self.assertLessEqual(len(embed.title or ""), 256)
        self.assertLessEqual(len(embed.description or ""), 4096)
        self.assertLessEqual(len(embed.fields), 25)
        for field in embed.fields:
            self.assertLessEqual(len(field.name), 256)
            self.assertLessEqual(len(field.value), 1024)
        for item in view.children:
            self.assertLessEqual(len(item.custom_id or ""), 100)

    async def test_only_opening_member_in_original_guild_passes_check(self) -> None:
        view = self._panel()
        self.assertTrue(await view.interaction_check(make_interaction()))
        for args in (
            {"user_id": AUTHOR_ID + 1},
            {"guild_id": GUILD_ID + 1},
            {"guild_id": None},
        ):
            with self.subTest(args=args):
                interaction = make_interaction(**args)
                self.assertFalse(await view.interaction_check(interaction))
                self.assert_private_reply(interaction)

    async def test_get_button_opens_modal_for_the_owner(self) -> None:
        view = self._panel(can_verify=False)
        get_button = next(
            item for item in view.children
            if isinstance(item, discord.ui.Button) and item.label == "Lấy OTP"
        )
        interaction = make_interaction()

        await get_button.callback(interaction)

        interaction.response.send_modal.assert_awaited_once()
        modal = interaction.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, GetOtpModal)

    async def test_verify_button_rechecks_live_manage_guild(self) -> None:
        view = self._panel(can_verify=True)
        verify_button = next(
            item for item in view.children
            if isinstance(item, discord.ui.Button) and item.label == "Xác minh"
        )
        denied = make_interaction()
        allowed = make_interaction(manage_guild=True)

        await verify_button.callback(denied)
        self.assertIn("Manage Server", self.assert_private_reply(denied))
        denied.response.send_modal.assert_not_awaited()

        await verify_button.callback(allowed)
        allowed.response.send_modal.assert_awaited_once()
        self.assertIsInstance(
            allowed.response.send_modal.await_args.args[0],
            VerifyOtpModal,
        )

    async def test_get_modal_returns_private_otp_for_the_challenge(self) -> None:
        view = self._panel()
        modal = GetOtpModal(view)
        modal.challenge_input._value = "123456"
        self.addCleanup(modal.stop)
        interaction = make_interaction()

        await modal.on_submit(interaction)

        rendered = self.assert_private_reply(interaction)
        self.assertIn("Soft OTP của bạn", rendered)
        self.assertIn("`123456`", rendered)
        self.assertRegex(rendered, TOKEN_PATTERN)
        self.assertNotRegex(rendered, r"`tfotp1-\d+-")

    async def test_verify_modal_identifies_account_privately(self) -> None:
        view = self._panel()
        token = await self.cog.issue_token(GUILD_ID, AUTHOR_ID, "jd3s1s")
        modal = VerifyOtpModal(view)
        modal.challenge_input._value = "jd3s1s"
        modal.otp_input._value = token
        self.addCleanup(modal.stop)
        member = SimpleNamespace(mention=f"<@{AUTHOR_ID}>", id=AUTHOR_ID)
        interaction = make_interaction(manage_guild=True)
        interaction.guild.get_member = (
            lambda user_id: member if user_id == AUTHOR_ID else None
        )

        await modal.on_submit(interaction)

        rendered = self.assert_private_reply(interaction)
        self.assertIn("Soft OTP hợp lệ", rendered)
        self.assertIn(f"<@{AUTHOR_ID}>", rendered)
        self.assertIn("`jd3s1s`", rendered)

    async def test_verify_modal_rejects_another_members_otp_when_claimed(self) -> None:
        view = self._panel()
        view.claimed_user_id = AUTHOR_ID
        view.claimed_user_label = f"<@{AUTHOR_ID}> (`{AUTHOR_ID}`)"
        bob_token = await self.cog.issue_token(GUILD_ID, OTHER_USER_ID, "form-2026")
        modal = VerifyOtpModal(view)
        modal.challenge_input._value = "form-2026"
        modal.otp_input._value = bob_token
        self.addCleanup(modal.stop)
        interaction = make_interaction(manage_guild=True)

        await modal.on_submit(interaction)

        rendered = self.assert_private_reply(interaction)
        self.assertIn("tài khoản đã chọn", rendered)
        self.assertNotIn("Soft OTP hợp lệ", rendered)

    async def test_reveal_button_shows_otp_only_to_owner(self) -> None:
        view = SoftOtpRevealView(
            self.cog,
            guild_id=GUILD_ID,
            author_id=AUTHOR_ID,
            prefix="!tf ",
            challenge="jd3s1s",
        )
        self.addCleanup(view.stop)
        reveal = next(
            item for item in view.children if isinstance(item, discord.ui.Button)
        )
        stranger = make_interaction(user_id=AUTHOR_ID + 1)
        owner = make_interaction()

        await reveal.callback(stranger)
        self.assertNotIn("tfotp1.", self.assert_private_reply(stranger))

        await reveal.callback(owner)
        rendered = self.assert_private_reply(owner)
        self.assertRegex(rendered, TOKEN_PATTERN)
        self.assertNotRegex(rendered, r"`tfotp1-\d+-")
        self.assertIn("`jd3s1s`", rendered)

    def test_permission_helper_accepts_admin_or_manage_guild(self) -> None:
        self.assertTrue(
            can_verify_softotp(
                SimpleNamespace(guild_permissions=discord.Permissions(administrator=True))
            )
        )
        self.assertTrue(
            can_verify_softotp(
                SimpleNamespace(guild_permissions=discord.Permissions(manage_guild=True))
            )
        )
        self.assertFalse(
            can_verify_softotp(
                SimpleNamespace(guild_permissions=discord.Permissions.none())
            )
        )


class TestSoftOtpDoctorHook(unittest.TestCase):
    def test_softotp_uses_the_content_verification_keyring_check(self) -> None:
        self.assertIn("cogs.utils.softotp", doctor.PROOF_MODULES)
