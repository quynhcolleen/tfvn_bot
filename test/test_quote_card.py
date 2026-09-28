import unittest
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from PIL import Image, ImageFont, features

from cogs.utils._quote_card import (
    BUNDLED_EMOJI_FONT_PATH,
    BUNDLED_FONT_PATH,
    BUNDLED_SYMBOL_FONT_PATH,
    BUNDLED_SYMBOLS2_FONT_PATH,
    CARD_HEIGHT,
    CARD_WIDTH,
    MAX_NORMALIZED_TEXT_LENGTH,
    TEXT_AREA_HEIGHT,
    _FallbackFont,
    _truncate_to_width,
    _fit_quote_lines,
    _font_path,
    _load_fallback_font,
    _load_font,
    _safe_accent,
    _text_block_height,
    normalize_quote_text,
    render_quote_card,
    wrap_quote_text,
)
from cogs.utils.quote import (
    QuoteCog,
    QuoteLookupError,
    parse_quote_request,
    prepare_embed_quote_text,
)


class FixedWidthFont:
    def getlength(self, text: str) -> float:
        return float(len(text))


class TestQuoteModes(unittest.TestCase):
    def test_default_mode_is_text_embed(self):
        self.assertEqual(parse_quote_request(None), (False, None))
        self.assertEqual(
            parse_quote_request("123456789012345678"),
            (False, "123456789012345678"),
        )

    def test_image_keyword_selects_png_mode(self):
        self.assertEqual(parse_quote_request("image"), (True, None))
        self.assertEqual(
            parse_quote_request("IMAGE\t123456789012345678"),
            (True, "123456789012345678"),
        )

    def test_embed_text_preserves_unicode_emoji(self):
        self.assertEqual(
            prepare_embed_quote_text("  Xin chào ✨  "),
            "Xin chào ✨",
        )

    def test_embed_text_is_bounded(self):
        prepared = prepare_embed_quote_text("x" * 5_000)
        self.assertEqual(len(">>> " + prepared), 4_096)
        self.assertTrue(prepared.endswith("…"))

    def test_embed_text_rejects_empty_message(self):
        with self.assertRaises(ValueError):
            prepare_embed_quote_text(" \n\t ")


class TestQuoteText(unittest.TestCase):
    def test_normalizes_whitespace_and_custom_emoji(self):
        self.assertEqual(
            normalize_quote_text(
                "  Xin   chào ✨  \n\n<a:waving:123456>  bạn "
            ),
            "Xin chào ✨\n\n:waving: bạn",
        )

    def test_demojizes_only_complex_emoji_sequences(self):
        self.assertEqual(
            normalize_quote_text(
                "😀 ✨ ❤️ 👍🏽 🇻🇳 1️⃣ 👨‍👩‍👧‍👦"
            ),
            (
                "😀 ✨ ❤️ :thumbs_up_medium_skin_tone: :Vietnam: "
                ":keycap_1: :family_man_woman_girl_boy:"
            ),
        )

    def test_demojizes_emoji_missing_from_bundled_font(self):
        self.assertEqual(
            normalize_quote_text("Newest emoji: 🪉"),
            "Newest emoji: :harp:",
        )

    def test_preserves_decorative_symbols_for_font_fallback(self):
        self.assertEqual(
            normalize_quote_text("♡  ★  ♪"),
            "♡ ★ ♪",
        )

    def test_rejects_empty_text(self):
        for content in ("", "   ", "\n\t\n"):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    normalize_quote_text(content)

    def test_wraps_words_and_long_tokens(self):
        font = FixedWidthFont()
        self.assertEqual(
            wrap_quote_text("one two three", font, 7),
            ["one two", "three"],
        )
        self.assertEqual(
            wrap_quote_text("abcdefgh", font, 3),
            ["abc", "def", "gh"],
        )

    def test_preserves_one_blank_line_between_paragraphs(self):
        lines = wrap_quote_text("first\n\nsecond", FixedWidthFont(), 20)
        self.assertEqual(lines, ["first", "", "second"])

    def test_width_truncation_only_adds_ellipsis_when_needed(self):
        font = FixedWidthFont()
        self.assertEqual(_truncate_to_width("short", font, 10), "short")
        self.assertEqual(_truncate_to_width("too long", font, 5), "too…")
        self.assertEqual(
            _truncate_to_width("short", font, 10, force_suffix=True),
            "short…",
        )

    def test_bundled_font_is_present(self):
        for path in (
            BUNDLED_FONT_PATH,
            BUNDLED_EMOJI_FONT_PATH,
            BUNDLED_SYMBOL_FONT_PATH,
            BUNDLED_SYMBOLS2_FONT_PATH,
        ):
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file())
        self.assertEqual(_font_path(False), str(BUNDLED_FONT_PATH))
        self.assertEqual(_font_path(True), str(BUNDLED_FONT_PATH))
        self.assertEqual(_load_font(24, bold=True).getname()[1], "Bold")

    def test_fallback_fonts_replace_tofu_glyphs(self):
        font = _load_fallback_font(40)
        runs = [
            (text, run_font.getname()[0])
            for text, run_font in font._font_runs("A♡♪😀𐙚")
        ]
        self.assertEqual(
            runs,
            [
                ("A", "Noto Sans"),
                ("♡", "Noto Sans Symbols 2"),
                ("♪", "Noto Sans Symbols"),
                ("😀", "Noto Emoji"),
                ("�", "Noto Sans"),
            ],
        )
        self.assertEqual(
            "".join(text for text, _ in font._font_runs("Kiên")),
            "Kiên",
        )
        self.assertEqual(
            "".join(text for text, _ in font._font_runs("1️⃣")),
            "1",
        )

    def test_fallback_modifiers_are_consistent_across_layout_engines(self):
        engines = [ImageFont.Layout.BASIC]
        if features.check_feature("raqm"):
            engines.append(ImageFont.Layout.RAQM)
        for engine in engines:
            with self.subTest(engine=engine):
                primary = ImageFont.truetype(
                    str(BUNDLED_FONT_PATH), size=40, layout_engine=engine,
                )
                font = _FallbackFont(primary, ())
                runs = font._font_runs("Kie\u0302n 1\ufe0f\u20e3")
                self.assertEqual("".join(text for text, _ in runs), "Kie\u0302n 1")
                self.assertTrue(all(run_font is primary for _, run_font in runs))
                self.assertEqual(primary.layout_engine, engine)

    def test_shaped_missing_modifiers_do_not_count_as_supported_glyphs(self):
        primary = ImageFont.truetype(
            str(BUNDLED_FONT_PATH), size=40, layout_engine=ImageFont.Layout.BASIC,
        )
        getmask = primary.getmask

        def shaped_mask(character):
            # Model RAQM suppressing selectors and adding a dotted circle to
            # isolated marks, so neither looks like the missing-glyph probe.
            if character == "\ufe0f":
                return getmask("")
            if character == "\u20e3":
                return getmask("\u25cc\u20e3")
            return getmask(character)

        with (
            patch.object(primary, "layout_engine", ImageFont.Layout.RAQM),
            patch.object(primary, "getmask", side_effect=shaped_mask),
        ):
            font = _FallbackFont(primary, ())
            self.assertEqual(font._font_runs("1\ufe0f\u20e3"), [("1", primary)])
            self.assertEqual(font._font_runs("Kie\u0302n"), [("Kie\u0302n", primary)])

    def test_normalized_text_is_bounded(self):
        normalized = normalize_quote_text("😀" * 4_000)
        self.assertLessEqual(len(normalized), MAX_NORMALIZED_TEXT_LENGTH)
        self.assertTrue(normalized.endswith("…"))

    def test_long_quote_fits_text_area(self):
        text = normalize_quote_text("từ " * 2_000)
        font, lines, spacing = _fit_quote_lines(text)
        self.assertLessEqual(
            _text_block_height(lines, font, spacing),
            TEXT_AREA_HEIGHT,
        )
        self.assertTrue(lines[-1].endswith("…"))

    def test_dark_role_color_is_lightened_for_contrast(self):
        red, green, blue = _safe_accent((40, 50, 70))
        luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
        self.assertGreaterEqual(luminance, 134)


class TestQuoteCardRendering(unittest.TestCase):
    def test_renders_png_with_expected_dimensions(self):
        avatar_buffer = BytesIO()
        Image.new("RGB", (64, 64), (220, 80, 120)).save(
            avatar_buffer,
            format="PNG",
        )

        output = render_quote_card(
            avatar_bytes=avatar_buffer.getvalue(),
            display_name="Người dùng ♡ 😀",
            username="tester♪",
            quote_text=(
                "Xin chào ✨ Đây là một câu quote tiếng Việt ★"
            ),
            context_label="#general ☾ • 01/08/2026 10:00 UTC",
            accent_rgb=(120, 90, 220),
        )

        with Image.open(BytesIO(output)) as card:
            self.assertEqual(card.format, "PNG")
            self.assertEqual(card.size, (CARD_WIDTH, CARD_HEIGHT))

    def test_missing_avatar_uses_placeholder(self):
        output = render_quote_card(
            avatar_bytes=None,
            display_name="Kien",
            username="kien",
            quote_text="A short quote.",
            context_label="#general",
        )
        self.assertTrue(output.startswith(b"\x89PNG\r\n\x1a\n"))


class TestQuoteAvatarSelection(unittest.TestCase):
    def test_prefers_server_avatar(self):
        server_avatar = object()
        display_avatar = object()
        author = SimpleNamespace(
            guild_avatar=server_avatar,
            display_avatar=display_avatar,
        )
        self.assertIs(QuoteCog._server_avatar(author), server_avatar)

    def test_falls_back_to_display_avatar(self):
        display_avatar = object()
        author = SimpleNamespace(
            guild_avatar=None,
            display_avatar=display_avatar,
        )
        self.assertIs(QuoteCog._server_avatar(author), display_avatar)


class TestTextQuoteSending(unittest.IsolatedAsyncioTestCase):
    async def test_embed_contains_original_text_and_server_avatar(self):
        server_avatar = SimpleNamespace(url="https://cdn.example/avatar.png")
        author = SimpleNamespace(
            guild_avatar=server_avatar,
            display_avatar=SimpleNamespace(url="https://cdn.example/global.png"),
            display_name="Kiên",
            name="kien",
        )
        message = SimpleNamespace(
            author=author,
            jump_url="https://discord.com/channels/1/2/3",
            created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            channel=SimpleNamespace(name="general"),
        )
        ctx = SimpleNamespace(send=AsyncMock())

        await QuoteCog(SimpleNamespace())._send_text_quote(
            ctx,
            message,
            "Xin chào ✨",
        )

        embed = ctx.send.await_args.kwargs["embed"]
        self.assertEqual(embed.description, ">>> Xin chào ✨")
        self.assertEqual(embed.author.icon_url, server_avatar.url)
        self.assertEqual(embed.url, message.jump_url)

    async def test_embed_includes_verification_proof_and_command(self):
        verification_proof = "tfp1_" + "a" * 26
        author = SimpleNamespace(
            guild_avatar=None,
            display_avatar=SimpleNamespace(url="https://cdn.example/avatar.png"),
            display_name="Kiên",
            name="kien",
        )
        message = SimpleNamespace(
            author=author,
            jump_url="https://discord.com/channels/1/2/3",
            created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            channel=SimpleNamespace(name="general"),
        )
        ctx = SimpleNamespace(send=AsyncMock(), prefix="!tf ")

        await QuoteCog(SimpleNamespace())._send_text_quote(
            ctx,
            message,
            "Xin chào ✨",
            verification_proof=verification_proof,
        )

        embed = ctx.send.await_args.kwargs["embed"]
        verification_field = next(
            field
            for field in embed.fields
            if field.name == "🔐 Mã proof TFVN"
        )
        self.assertIn(verification_proof, verification_field.value)
        self.assertIn("!tf hash_verify", verification_field.value)
        self.assertEqual(verification_field.value.count(verification_proof), 1)
        self.assertLessEqual(len(verification_field.value), 1_024)

    async def test_plain_fallback_stays_within_message_limit_with_proof(self):
        verification_proof = "tfp1_" + "c" * 26
        author = SimpleNamespace(
            guild_avatar=None,
            display_avatar=SimpleNamespace(url="https://cdn.example/avatar.png"),
            display_name="Kiên",
            name="kien",
        )
        message = SimpleNamespace(
            author=author,
            jump_url="https://discord.com/channels/1/2/3",
            created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            channel=SimpleNamespace(name="general"),
        )
        response = SimpleNamespace(status=500, reason="Server Error")
        ctx = SimpleNamespace(
            send=AsyncMock(
                side_effect=[
                    discord.HTTPException(response, "failed"),
                    None,
                ]
            ),
            prefix="!tf ",
        )

        await QuoteCog(SimpleNamespace())._send_text_quote(
            ctx,
            message,
            "x" * 4_000,
            verification_proof=verification_proof,
        )

        fallback = ctx.send.await_args_list[1].args[0]
        self.assertLessEqual(len(fallback), 2_000)
        self.assertIn(verification_proof, fallback)
        self.assertEqual(fallback.count(verification_proof), 1)
        self.assertTrue(
            fallback.endswith(f"hash_verify {verification_proof}`")
        )


class AsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class TestQuoteCommandModes(unittest.IsolatedAsyncioTestCase):
    verification_token = "tfv1.hidden-token"
    verification_proof = "tfp1_" + "b" * 26

    def _message(self):
        return SimpleNamespace(
            id=123456789012345678,
            clean_content="Xin chào ✨",
            author=SimpleNamespace(
                display_name="Kiên",
                name="kien",
            ),
            channel=SimpleNamespace(name="general"),
            created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            jump_url="https://discord.com/channels/1/2/3",
        )

    async def test_default_command_uses_text_embed(self):
        cog = QuoteCog(SimpleNamespace())
        cog._issue_quote_proof = AsyncMock(
            return_value=self.verification_token
        )
        message = self._message()
        cog._resolve_message = AsyncMock(return_value=message)
        cog._send_text_quote = AsyncMock()
        cog._avatar_bytes = AsyncMock()
        ctx = SimpleNamespace(send=AsyncMock(), prefix="!tf ")

        with patch(
            "cogs.utils.quote.verification_reference_from_token",
            return_value=self.verification_proof,
        ):
            await cog.quote.callback(cog, ctx, message_reference=None)

        cog._resolve_message.assert_awaited_once_with(ctx, None)
        cog._send_text_quote.assert_awaited_once_with(
            ctx,
            message,
            "Xin chào ✨",
            verification_proof=self.verification_proof,
        )
        cog._issue_quote_proof.assert_awaited_once_with(
            ctx,
            message,
            "Xin chào ✨",
        )
        cog._avatar_bytes.assert_not_awaited()

    async def test_image_keyword_uses_png_mode(self):
        cog = QuoteCog(SimpleNamespace())
        cog._issue_quote_proof = AsyncMock(
            return_value=self.verification_token
        )
        message = self._message()
        cog._resolve_message = AsyncMock(return_value=message)
        cog._send_text_quote = AsyncMock()
        cog._avatar_bytes = AsyncMock(return_value=None)
        ctx = SimpleNamespace(
            send=AsyncMock(),
            typing=lambda: AsyncContext(),
            prefix="!tf ",
        )

        with patch(
            "cogs.utils.quote.asyncio.to_thread",
            AsyncMock(return_value=b"png"),
        ) as render_thread, patch(
            "cogs.utils.quote.verification_reference_from_token",
            return_value=self.verification_proof,
        ):
            await cog.quote.callback(
                cog,
                ctx,
                message_reference="image",
            )

        cog._resolve_message.assert_awaited_once_with(ctx, None)
        cog._send_text_quote.assert_not_awaited()
        self.assertEqual(
            render_thread.await_args.kwargs["context_label"],
            "01/08/2026 00:00 UTC",
        )
        self.assertIn("file", ctx.send.await_args.kwargs)
        self.assertIn(self.verification_proof, ctx.send.await_args.args[0])
        self.assertEqual(
            ctx.send.await_args.args[0].count(self.verification_proof),
            1,
        )

    async def test_image_render_failure_falls_back_to_embed(self):
        cog = QuoteCog(SimpleNamespace())
        cog._issue_quote_proof = AsyncMock(
            return_value=self.verification_token
        )
        message = self._message()
        cog._resolve_message = AsyncMock(return_value=message)
        cog._send_text_quote = AsyncMock()
        cog._avatar_bytes = AsyncMock(return_value=None)
        ctx = SimpleNamespace(
            send=AsyncMock(),
            typing=lambda: AsyncContext(),
            prefix="!tf ",
        )

        with (
            patch(
                "cogs.utils.quote.asyncio.to_thread",
                AsyncMock(side_effect=RuntimeError("render failed")),
            ),
            patch(
                "cogs.utils.quote.verification_reference_from_token",
                return_value=self.verification_proof,
            ),
            patch("cogs.utils.quote.logger.exception") as log_exception,
        ):
            await cog.quote.callback(
                cog,
                ctx,
                message_reference="image",
            )

        cog._send_text_quote.assert_awaited_once_with(
            ctx,
            message,
            "Xin chào ✨",
            verification_proof=self.verification_proof,
        )
        log_exception.assert_called_once_with("Failed to render quote card")


class TestQuoteMessageResolution(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = QuoteCog(SimpleNamespace())
        self.guild = SimpleNamespace(id=10)
        self.channel = SimpleNamespace(
            id=20,
            fetch_message=AsyncMock(),
        )

    def _context(self, reference=None):
        return SimpleNamespace(
            message=SimpleNamespace(reference=reference),
            guild=self.guild,
            channel=self.channel,
            prefix="!tf ",
        )

    def _message(self, *, guild=None, channel=None):
        return SimpleNamespace(
            guild=guild or self.guild,
            channel=channel or self.channel,
        )

    async def test_resolves_cached_replied_message(self):
        message = self._message()
        reference = SimpleNamespace(
            message_id=30,
            channel_id=self.channel.id,
            resolved=None,
            cached_message=message,
        )
        result = await self.cog._resolve_message(
            self._context(reference),
            None,
        )
        self.assertIs(result, message)

    async def test_resolves_explicit_message_reference(self):
        message = self._message()
        self.channel.fetch_message.return_value = message
        result = await self.cog._resolve_message(
            self._context(),
            "123456789012345678",
        )
        self.assertIs(result, message)
        self.channel.fetch_message.assert_awaited_once_with(
            123456789012345678
        )

    async def test_rejects_cross_channel_link_before_fetch(self):
        reference = (
            "https://discord.com/channels/10/21/123456789012345678"
        )
        with self.assertRaisesRegex(QuoteLookupError, "kênh hiện tại"):
            await self.cog._resolve_message(self._context(), reference)
        self.channel.fetch_message.assert_not_awaited()

    async def test_requires_reply_or_explicit_reference(self):
        with self.assertRaisesRegex(QuoteLookupError, "reply"):
            await self.cog._resolve_message(self._context(), None)

    async def test_rejects_message_from_another_channel(self):
        other_channel = SimpleNamespace(id=21)
        message = self._message(channel=other_channel)
        reference = SimpleNamespace(
            message_id=30,
            channel_id=self.channel.id,
            resolved=None,
            cached_message=message,
        )
        with self.assertRaisesRegex(QuoteLookupError, "kênh hiện tại"):
            await self.cog._resolve_message(
                self._context(reference),
                None,
            )


if __name__ == "__main__":
    unittest.main()
