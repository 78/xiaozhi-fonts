import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("noto_font_build", ROOT / "scripts" / "build.py")
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


class BuildTest(unittest.TestCase):
    def test_bundle_id_is_explicit(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(BUILD.font_bundle_id(manifest), "noto-v1")

    def test_bundle_id_rejects_invalid_names(self):
        with self.assertRaises(ValueError):
            BUILD.font_bundle_id({"bundle_id": "Noto v1"})

    def test_generated_bundle_header_matches_manifest(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        header = (ROOT / "include" / "noto_font_bundle.h").read_text(encoding="utf-8")
        self.assertIn(f'#define NOTO_FONT_BUNDLE_ID "{manifest["bundle_id"]}"', header)
        self.assertNotIn("CHARSET_ID", header)

    def test_oled_text_profile_has_extra_light_source_for_every_script(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        profile = next(item for item in manifest["text_profiles"] if item["name"] == "14_1")
        self.assertEqual(profile["variant"], "extra_light")
        self.assertEqual(profile["autohint"], "off")
        self.assertEqual(profile["size"], 14)
        for source in manifest["text_sources"]:
            self.assertIn("extra_light_path", source)
            self.assertTrue((ROOT / source["extra_light_path"]).is_file(), source["id"])

    def test_oled_icon_profile_uses_natural_placement(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        profile = next(item for item in manifest["icon_profiles"] if item["name"] == "14_1")
        self.assertEqual((profile["line_height"], profile["base_line"]), (16, 2))

    def test_profile_selection_uses_size_and_bpp(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            BUILD.profile_names_for_size(manifest, ["text_profiles"], 14, 1),
            {"14_1"},
        )
        self.assertEqual(
            BUILD.profile_names_for_size(manifest, ["text_profiles"], 14, 4),
            set(),
        )

    def test_generated_oled_icons_fit_fourteen_pixel_advance(self):
        source = (ROOT / "src" / "font_material_symbols_14_1.c").read_text(
            encoding="utf-8"
        )
        descriptors = re.findall(
            r"\.adv_w = (\d+), \.box_w = (\d+), \.box_h = \d+, "
            r"\.ofs_x = (-?\d+)",
            source,
        )
        self.assertGreater(len(descriptors), 1)
        for adv_w, box_w, ofs_x in descriptors[1:]:
            advance = (int(adv_w) + 15) // 16
            self.assertGreaterEqual(int(ofs_x), 0)
            self.assertLessEqual(int(ofs_x) + int(box_w), advance)
        vertical = re.findall(
            r"\.box_h = (\d+), \.ofs_x = -?\d+, \.ofs_y = (-?\d+)", source
        )
        self.assertEqual(len(vertical), len(descriptors))
        for box_h, ofs_y in vertical[1:]:
            height = int(box_h)
            top = 16 - 2 - height - int(ofs_y)
            self.assertGreaterEqual(top, 0)
            self.assertLessEqual(top + height, 16)

    def test_postprocess_lvgl_font(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "font.c"
            output.write_text(
                "{.box_h = 16, .ofs_y = 3},\n.line_height = 17,\n.base_line = -2,\n",
                encoding="utf-8",
            )

            BUILD.postprocess_lvgl_font(
                output,
                {"line_height": 16, "base_line": -2, "fit_line_box": True},
            )

            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "{.box_h = 16, .ofs_y = 2},\n.line_height = 16,\n.base_line = -2,\n",
            )

    def test_fitted_glyph_rejects_too_tall_bitmap(self):
        with self.assertRaises(ValueError):
            BUILD.fitted_glyph_ofs_y(17, 0, 16, 2)

if __name__ == "__main__":
    unittest.main()
