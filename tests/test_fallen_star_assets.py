import re
import struct
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
ASSETS = WEB / "assets"


class FallenStarAssetTests(unittest.TestCase):
    def test_fallen_star_art_is_transparent_square_png(self):
        art = ASSETS / "fish" / "fallen_star.png"
        payload = art.read_bytes()
        self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
        width, height = struct.unpack(">II", payload[16:24])
        self.assertEqual((width, height), (440, 440))
        self.assertEqual(payload[25], 6, "PNG must use RGBA color type")

    def test_bundle_maps_fallen_star_name_and_art(self):
        bundle = (ASSETS / "index-AxS0zlpM.js").read_text(encoding="utf-8")
        self.assertIn('qp={落星:"fallen_star",', bundle)
        self.assertIn('Yp=new Set(["copper_bream","fallen_star",', bundle)

    def test_enhancement_has_catch_trigger_and_local_preview(self):
        enhancement = (ASSETS / "rainholm-enhancements.js").read_text(encoding="utf-8")
        self.assertIn('result.fish === "落星"', enhancement)
        self.assertIn('url.searchParams.get("preview") === "fallen-star"', enhancement)
        self.assertIn('headers.set("X-Rainholm-Preview", "fallen-star")', enhancement)
        self.assertIn('window.addEventListener("rainholm:fallen-star"', enhancement)

    def test_fallen_star_lore_hex_decodes_to_public_easter_egg_note(self):
        enhancement = (ASSETS / "rainholm-enhancements.js").read_text(encoding="utf-8")
        match = re.search(r'FALLEN_STAR_LORE_HEX = "([0-9a-f]+)"', enhancement)
        self.assertIsNotNone(match)
        lore = bytes.fromhex(match.group(1)).decode("utf-8")
        self.assertEqual(
            lore,
            "星河三角洲里有一颗塘主埋的彩蛋，彩蛋的起源是克霖给苏晚的一封表白信，"
            "埋进了这颗落星之中，如果你也想做一样的表白，你可以自行修改这个落星的表白描述。",
        )

    def test_index_cache_versions_include_fallen_star_release(self):
        page = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn("rainholm-enhancements.js?v=18", page)
        self.assertIn("rainholm-enhancements.css?v=18", page)
        self.assertIn("index-AxS0zlpM.js?v=4", page)

    def test_catch_card_wave_is_clipped_without_clipping_badges(self):
        stylesheet = (ASSETS / "rainholm-enhancements.css").read_text(encoding="utf-8")
        self.assertIn(".card-in.parchment > svg:last-child", stylesheet)
        self.assertIn("clip-path: inset(0 round 0 0 1rem 1rem)", stylesheet)

    def test_starry_delta_replaces_petals_with_accessible_meteors(self):
        enhancement = (ASSETS / "rainholm-enhancements.js").read_text(encoding="utf-8")
        stylesheet = (ASSETS / "rainholm-enhancements.css").read_text(encoding="utf-8")
        self.assertIn('starryDeltaSpot = /\\/assets\\/spot_starry_delta', enhancement)
        self.assertIn('mapLayer.classList.toggle("rainholm-starry-delta", isStarryDelta)', enhancement)
        self.assertIn('shower.setAttribute("aria-hidden", "true")', enhancement)
        self.assertIn('.rainholm-starry-delta .petal', stylesheet)
        self.assertIn('height: 34%', stylesheet)
        self.assertIn('@media (prefers-reduced-motion: reduce)', stylesheet)
        self.assertIn('.rainholm-meteor-shower', stylesheet)


if __name__ == "__main__":
    unittest.main()
