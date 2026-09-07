import unittest
from types import SimpleNamespace

import discord

from cogs.utils._highlight_media import (
    MAX_HIGHLIGHT_EMBEDS,
    MAX_HIGHLIGHT_IMAGES,
    HighlightMedia,
    collect_highlight_media,
    discord_media_url,
    media_url_key,
)


CDN_IMAGE = "https://cdn.discordapp.com/attachments/1/2/photo.png?ex=abc&hm=first"
PROXY_IMAGE = "https://media.discordapp.net/attachments/1/2/photo.png?width=640"
EXTERNAL_PROXY = "https://images-ext-1.discordapp.net/external/hash/https/example.com/a.png"


def make_attachment(filename: str, index: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        filename=filename,
        content_type="image/png",
        url=f"https://cdn.discordapp.com/attachments/1/{index}/{filename}",
        proxy_url=f"https://media.discordapp.net/attachments/1/{index}/{filename}",
    )


def collect_embeds(
    *embeds: discord.Embed,
    attachments: tuple[SimpleNamespace, ...] = (),
) -> HighlightMedia:
    return collect_highlight_media(
        SimpleNamespace(embeds=list(embeds), attachments=list(attachments))
    )


class TestHighlightMediaExtraction(unittest.TestCase):
    def test_rich_embed_preserves_visible_text_and_color(self):
        embed = discord.Embed(
            title="Tiêu đề",
            description="Nội dung **nổi bật**",
            color=discord.Color.from_rgb(42, 99, 180),
        )
        embed.set_author(name="Tác giả")
        embed.set_footer(text="Chú thích")
        embed.add_field(name="Điểm", value="100", inline=True)
        embed.add_field(name="Chi tiết", value="Dòng 1\nDòng 2", inline=False)

        media = collect_embeds(embed)

        self.assertTrue(media.has_content)
        self.assertEqual(len(media.embeds), 1)
        snapshot = media.embeds[0]
        self.assertTrue(snapshot.has_text)
        self.assertEqual(snapshot.title, "Tiêu đề")
        self.assertEqual(snapshot.description, "Nội dung **nổi bật**")
        self.assertEqual(snapshot.author_name, "Tác giả")
        self.assertEqual(snapshot.footer_text, "Chú thích")
        self.assertEqual(snapshot.fields, (("Điểm", "100"), ("Chi tiết", "Dòng 1\nDòng 2")))
        self.assertEqual(snapshot.color_rgb, (42, 99, 180))
        self.assertIsNone(snapshot.image_bytes)
        self.assertIsNone(snapshot.thumbnail_bytes)

    def test_image_and_thumbnail_prefer_discord_proxy_urls(self):
        embed = discord.Embed.from_dict({
            "type": "rich",
            "image": {"url": CDN_IMAGE, "proxy_url": PROXY_IMAGE},
            "thumbnail": {
                "url": "https://example.com/a.png",
                "proxy_url": EXTERNAL_PROXY,
            },
        })

        snapshot = collect_embeds(embed).embeds[0]

        self.assertEqual(snapshot.image_url, PROXY_IMAGE)
        self.assertEqual(snapshot.thumbnail_url, EXTERNAL_PROXY)
        self.assertFalse(snapshot.has_text)

    def test_invalid_proxy_falls_back_to_safe_discord_image_source(self):
        embed = discord.Embed.from_dict({
            "image": {"url": CDN_IMAGE, "proxy_url": "https://example.com/proxy.png"},
        })

        self.assertEqual(collect_embeds(embed).embeds[0].image_url, CDN_IMAGE)

    def test_image_only_embed_uses_safe_top_level_image_url(self):
        embed = discord.Embed.from_dict({"type": "image", "url": CDN_IMAGE})

        media = collect_embeds(embed)

        self.assertTrue(media.has_content)
        self.assertEqual(media.embeds[0].image_url, CDN_IMAGE)
        self.assertFalse(media.embeds[0].has_text)

    def test_thumbnail_only_preview_is_renderable(self):
        embed = discord.Embed.from_dict({
            "type": "image",
            "url": "https://example.com/a.png",
            "thumbnail": {
                "url": "https://example.com/a.png",
                "proxy_url": EXTERNAL_PROXY,
            },
        })

        media = collect_embeds(embed)

        self.assertTrue(media.has_content)
        self.assertEqual(media.embeds[0].thumbnail_url, EXTERNAL_PROXY)

    def test_external_images_without_discord_proxy_are_omitted(self):
        embed = discord.Embed(title="Keep this text")
        embed.set_image(url="https://example.com/image.png")
        embed.set_thumbnail(url="http://127.0.0.1/internal.png")
        image_only = discord.Embed.from_dict({
            "type": "image", "url": "https://example.com/image.png",
        })

        media = collect_embeds(embed, image_only)

        self.assertEqual(len(media.embeds), 1)
        self.assertEqual(media.embeds[0].title, "Keep this text")
        self.assertIsNone(media.embeds[0].image_url)
        self.assertIsNone(media.embeds[0].thumbnail_url)

    def test_arbitrary_message_or_embed_text_does_not_become_image_request(self):
        embed = discord.Embed(title=CDN_IMAGE, description=EXTERNAL_PROXY, url=CDN_IMAGE)
        message = SimpleNamespace(content=CDN_IMAGE, attachments=[], embeds=[embed])

        media = collect_highlight_media(message)

        self.assertEqual(media.attachments, ())
        self.assertEqual(media.embeds[0].title, CDN_IMAGE)
        self.assertIsNone(media.embeds[0].image_url)
        self.assertIsNone(media.embeds[0].thumbnail_url)
        message.embeds = []
        self.assertFalse(collect_highlight_media(message).has_content)

    def test_attachment_image_and_thumbnail_references_resolve_by_filename(self):
        first = make_attachment("first.png")
        second = make_attachment("second.png", 3)
        embed = discord.Embed()
        embed.set_image(url="attachment://second.png")
        embed.set_thumbnail(url="attachment://first.png")

        media = collect_embeds(embed, attachments=(first, second))

        self.assertEqual(media.embeds[0].image_url, second.url)
        self.assertEqual(media.embeds[0].thumbnail_url, first.url)
        self.assertEqual(media.attachments, (first, second))

    def test_unmatched_attachment_reference_is_omitted(self):
        embed = discord.Embed().set_image(url="attachment://missing.png")

        self.assertFalse(collect_embeds(embed).has_content)

    def test_image_attachments_are_filtered_and_limited_to_four(self):
        video = SimpleNamespace(filename="video.mp4", content_type="video/mp4")
        images = [make_attachment(f"image-{index}.png", index) for index in range(6)]

        media = collect_embeds(attachments=(video, *images))

        self.assertEqual(MAX_HIGHLIGHT_IMAGES, 4)
        self.assertEqual(media.attachments, tuple(images[:4]))
        self.assertTrue(media.has_content)

    def test_nonempty_embeds_are_limited_to_four_in_original_order(self):
        embeds = [discord.Embed(title=f"Embed {index}") for index in range(6)]

        media = collect_embeds(discord.Embed(), *embeds)

        self.assertEqual(MAX_HIGHLIGHT_EMBEDS, 4)
        self.assertEqual([embed.title for embed in media.embeds], [
            "Embed 0", "Embed 1", "Embed 2", "Embed 3",
        ])

    def test_empty_message_and_empty_embeds_do_not_count_as_media(self):
        self.assertFalse(collect_highlight_media(SimpleNamespace()).has_content)
        self.assertFalse(collect_embeds(discord.Embed()).has_content)
        self.assertFalse(collect_embeds(discord.Embed(title="  ")).has_content)

    def test_embed_text_and_fields_are_bounded_before_rendering(self):
        embed = discord.Embed.from_dict({
            "title": "t" * 300,
            "description": "d" * 5000,
            "author": {"name": "a" * 300},
            "footer": {"text": "f" * 2500},
            "fields": [{"name": "n" * 300, "value": "v" * 1100}] * 30,
        })

        snapshot = collect_embeds(embed).embeds[0]

        self.assertEqual(len(snapshot.title), 256)
        self.assertEqual(len(snapshot.description), 4096)
        self.assertEqual(len(snapshot.author_name), 256)
        self.assertEqual(len(snapshot.footer_text), 2048)
        self.assertEqual(len(snapshot.fields), 25)
        self.assertTrue(all(len(name) == 256 for name, _ in snapshot.fields))
        self.assertTrue(all(len(value) == 1024 for _, value in snapshot.fields))


class TestHighlightMediaUrls(unittest.TestCase):
    def test_discord_cdn_and_numbered_external_proxy_urls_are_accepted(self):
        for url in (
            CDN_IMAGE,
            PROXY_IMAGE,
            EXTERNAL_PROXY,
            "https://cdn.discordapp.net/attachments/1/2/photo.png",
            "https://media.discordapp.com/attachments/1/2/photo.png",
            "https://images-ext-2.discordapp.net/external/hash/image.gif",
            "https://media.discordapp.net:443/attachments/1/2/photo.png",
        ):
            with self.subTest(url=url):
                self.assertEqual(discord_media_url(url), url)

    def test_external_local_credentialed_and_lookalike_urls_are_rejected(self):
        for url in (
            None,
            42,
            "",
            "https://example.com/image.png",
            "https://localhost/image.png",
            "https://127.0.0.1/image.png",
            "https://[::1]/image.png",
            "file:///C:/private/image.png",
            "http://cdn.discordapp.com/attachments/1/2/photo.png",
            "//cdn.discordapp.com/attachments/1/2/photo.png",
            "https://user:password@cdn.discordapp.com/attachments/1/2/photo.png",
            "https://cdn.discordapp.com@evil.example/image.png",
            "https://evil.example@cdn.discordapp.com/attachments/1/2/photo.png",
            "https://cdn.discordapp.com.evil.example/image.png",
            "https://images-ext-1.discordapp.net.evil.example/image.png",
            "https://images-ext-one.discordapp.net/image.png",
            "https://cdn.discordapp.com:8443/image.png",
            "https://cdn.discordapp.com:invalid/image.png",
            "https://cdn.discordapp.com",
            "https://[invalid/image.png",
        ):
            with self.subTest(url=url):
                self.assertIsNone(discord_media_url(url))

    def test_signed_cdn_and_resized_proxy_references_share_attachment_key(self):
        self.assertEqual(media_url_key(CDN_IMAGE), media_url_key(PROXY_IMAGE))
        self.assertEqual(
            media_url_key(CDN_IMAGE),
            media_url_key(
                "https://cdn.discordapp.com/attachments/1/2/photo.png?ex=def&hm=second"
            ),
        )

    def test_distinct_attachments_and_external_proxy_urls_remain_distinct(self):
        self.assertNotEqual(
            media_url_key(CDN_IMAGE),
            media_url_key("https://cdn.discordapp.com/attachments/1/3/photo.png"),
        )
        self.assertNotEqual(
            media_url_key(EXTERNAL_PROXY),
            media_url_key(EXTERNAL_PROXY.replace("hash", "different")),
        )


if __name__ == "__main__":
    unittest.main()
