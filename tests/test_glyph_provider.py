import base64
import json
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from glyph_provider import CbinFont, FullGlyphProvider  # noqa: E402


class GlyphProviderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cbin_path = ROOT / "cbin" / "font_noto_sans_common_14_1.bin"
        if not cls.cbin_path.exists():
            raise unittest.SkipTest("generate common fonts before running provider tests")

    def test_cbin_extracts_wire_bitmap(self):
        font = CbinFont.from_file(self.cbin_path)
        font.assert_wire_compatible()
        glyph = font.glyph(ord("A"))
        self.assertIsNotNone(glyph)
        expected = (glyph["box_w"] * glyph["box_h"] * font.bpp + 7) // 8
        self.assertEqual(len(base64.b64decode(glyph["bitmap"])), expected)

    def test_oled_cbin_glyphs_fit_sixteen_pixel_line(self):
        font = CbinFont.from_file(self.cbin_path)
        self.assertEqual((font.line_height, font.base_line), (16, 2))
        glyph_count = (font.cmap_abs - font.glyph_dsc_abs) // 16
        for glyph_id in range(1, glyph_count):
            offset = font.glyph_dsc_abs + glyph_id * 16
            box_h, ofs_y = struct.unpack_from("<Hxxh", font.data, offset + 10)
            top = font.line_height - font.base_line - box_h - ofs_y
            bottom = font.line_height - font.base_line - ofs_y - 1
            self.assertGreaterEqual(top, 0)
            self.assertLess(bottom, font.line_height)

    def test_oled_cjk_uses_native_raster_metrics(self):
        font = CbinFont.from_file(self.cbin_path)
        expected = {
            "早": (12, 12),
            "别": (12, 12),
            "嘛": (12, 13),
            "聆": (12, 13),
        }
        for character, dimensions in expected.items():
            glyph = font.glyph(ord(character))
            self.assertIsNotNone(glyph)
            self.assertEqual(glyph["adv_w"], 14 * 16)
            self.assertEqual((glyph["box_w"], glyph["box_h"]), dimensions)

    def test_provider_filters_installed_charset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(self.cbin_path, root / "latin.cbin")
            manifest = {
                "bundle_id": "test-bundle",
                "charsets": {
                    "basic": {"codepoints": [ord("A")]},
                    "common": {"codepoints": []},
                },
                "shards": [{
                    "id": "latin",
                    "ranges": [[0x20, 0x7E]],
                    "profiles": {"14_1": "latin.cbin"},
                }],
            }
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            provider = FullGlyphProvider(path)

            self.assertIsNone(provider.payload_for_text("A", 14, 1, "basic"))
            payload = provider.payload_for_text("AA", 14, 1, "common")
            self.assertEqual(payload["size"], 14)
            self.assertEqual(payload["bpp"], 1)
            self.assertNotIn("profile", payload)
            self.assertEqual([item["codepoint"] for item in payload["items"]], [ord("A")])

if __name__ == "__main__":
    unittest.main()
