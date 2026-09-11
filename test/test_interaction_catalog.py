import unittest

from assets import nsfw_gifs
from cogs.interaction.user_interaction import SFW_ACTION_SPECS


NEW_SFW_NAMES = (
    "tickle",
    "pinch",
    "wave",
    "blush",
    "highfive",
    "feed",
    "wink",
)

NEW_NSFW_GIF_LISTS = (
    nsfw_gifs.GANGBANG_GIFS,
    nsfw_gifs.RIDE_GIFS,
    nsfw_gifs.FINGERING_GIFS,
    nsfw_gifs.FACESIT_GIFS,
)


class TestInteractionCatalog(unittest.TestCase):
    def test_new_sfw_specs_are_registered_with_gif_pools(self) -> None:
        specs = {spec.name: spec for spec in SFW_ACTION_SPECS}
        self.assertEqual(len(specs), len(SFW_ACTION_SPECS))
        self.assertEqual(len(SFW_ACTION_SPECS), 25)

        for name in NEW_SFW_NAMES:
            with self.subTest(name=name):
                spec = specs[name]
                self.assertTrue(spec.gifs)
                self.assertTrue(all(isinstance(url, str) and url.strip() for url in spec.gifs))

        self.assertEqual(specs["highfive"].aliases, ("brofist",))
        self.assertTrue(specs["tickle"].allow_self)
        self.assertTrue(specs["blush"].allow_self)
        for name in ("pinch", "wave", "highfive", "feed", "wink"):
            self.assertFalse(specs[name].allow_self)

    def test_new_nsfw_gif_pools_are_nonempty(self) -> None:
        for gifs in NEW_NSFW_GIF_LISTS:
            with self.subTest(first=gifs[0]):
                self.assertGreaterEqual(len(gifs), 4)
                self.assertTrue(all(isinstance(url, str) and url.strip() for url in gifs))


if __name__ == "__main__":
    unittest.main()
