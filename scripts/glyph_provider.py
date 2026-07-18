#!/usr/bin/env python3
"""Extract server-pushed glyphs from a noto-fonts full bundle."""

from __future__ import annotations

import argparse
import base64
import bisect
import json
import struct
import threading
from pathlib import Path


CMAP_FORMAT0_FULL = 0
CMAP_SPARSE_FULL = 1
CMAP_FORMAT0_TINY = 2
CMAP_SPARSE_TINY = 3
BITMAP_FORMAT_PLAIN = 0
MAX_GLYPHS = 64
MAX_BITMAP_BYTES = 64 * 1024


class CbinFont:
    def __init__(self, data: bytes):
        self.data = data
        self.line_height = struct.unpack_from("<i", data, 12)[0]
        self.base_line = struct.unpack_from("<i", data, 16)[0]
        dsc = struct.unpack_from("<I", data, 24)[0]
        bitmap_offset, glyph_dsc_offset, cmap_offset = struct.unpack_from("<III", data, dsc)
        bitfield = struct.unpack_from("<H", data, dsc + 18)[0]
        self.bitmap_abs = dsc + bitmap_offset
        self.glyph_dsc_abs = dsc + glyph_dsc_offset
        self.cmap_abs = dsc + cmap_offset
        self.cmap_count = bitfield & 0x1FF
        self.bpp = (bitfield >> 9) & 0xF
        self.bitmap_format = (bitfield >> 14) & 0x3
        self.stride = data[dsc + 20]
        self.cmaps = self._parse_cmaps()

    @classmethod
    def from_file(cls, path: Path) -> "CbinFont":
        return cls(path.read_bytes())

    def _parse_cmaps(self) -> list[dict]:
        result = []
        pointer = self.cmap_abs
        for _ in range(self.cmap_count):
            start, length, glyph_start, unicode_offset, glyph_offset, count, kind = struct.unpack_from(
                "<IHHIIHBx", self.data, pointer
            )
            result.append({
                "start": start,
                "length": length,
                "glyph_start": glyph_start,
                "unicode": self.cmap_abs + unicode_offset if unicode_offset else 0,
                "glyph_offset": self.cmap_abs + glyph_offset if glyph_offset else 0,
                "count": count,
                "kind": kind,
            })
            pointer += 20
        return result

    def _sparse_index(self, cmap: dict, relative: int) -> int:
        values = [struct.unpack_from("<H", self.data, cmap["unicode"] + index * 2)[0]
                  for index in range(cmap["count"])]
        index = bisect.bisect_left(values, relative)
        return index if index < len(values) and values[index] == relative else -1

    def glyph_id(self, codepoint: int) -> int:
        for cmap in self.cmaps:
            relative = codepoint - cmap["start"]
            if relative < 0 or relative >= cmap["length"]:
                continue
            if cmap["kind"] == CMAP_FORMAT0_TINY:
                return cmap["glyph_start"] + relative
            if cmap["kind"] == CMAP_FORMAT0_FULL:
                offset = self.data[cmap["glyph_offset"] + relative] if cmap["glyph_offset"] else relative
                return cmap["glyph_start"] + offset
            index = self._sparse_index(cmap, relative)
            if index < 0:
                continue
            if cmap["kind"] == CMAP_SPARSE_TINY:
                return cmap["glyph_start"] + index
            if cmap["kind"] == CMAP_SPARSE_FULL:
                offset = (struct.unpack_from("<H", self.data, cmap["glyph_offset"] + index * 2)[0]
                          if cmap["glyph_offset"] else index)
                return cmap["glyph_start"] + offset
        return 0

    def glyph(self, codepoint: int) -> dict | None:
        glyph_id = self.glyph_id(codepoint)
        if glyph_id == 0:
            return None
        offset = self.glyph_dsc_abs + glyph_id * 16
        bitmap_index, advance, width, height, ofs_x, ofs_y = struct.unpack_from("<IIHHhh", self.data, offset)
        byte_count = (width * height * self.bpp + 7) // 8
        bitmap = self.data[self.bitmap_abs + bitmap_index:self.bitmap_abs + bitmap_index + byte_count]
        return {
            "codepoint": codepoint,
            "adv_w": advance,
            "box_w": width,
            "box_h": height,
            "ofs_x": ofs_x,
            "ofs_y": ofs_y,
            "bitmap": base64.b64encode(bitmap).decode("ascii"),
        }

    def assert_wire_compatible(self) -> None:
        if self.bpp not in (1, 4) or self.bitmap_format != BITMAP_FORMAT_PLAIN or self.stride != 0:
            raise ValueError(
                f"incompatible CBIN: bpp={self.bpp}, bitmap_format={self.bitmap_format}, stride={self.stride}"
            )


class FullGlyphProvider:
    def __init__(self, manifest_path: Path):
        self.root = manifest_path.parent
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.bundle = self.manifest["bundle_id"]
        self.charsets = {
            name: set(value["codepoints"])
            for name, value in self.manifest["charsets"].items() if value is not None
        }
        self.profiles = {
            (profile["size"], profile["bpp"])
            for profile in self.manifest["profiles"]
        }
        self.fonts: dict[tuple[str, str], CbinFont] = {}
        self._font_lock = threading.Lock()

    def supports(self, bundle: str, charset: str, size: int, bpp: int) -> bool:
        """Return whether a device capability can use this full bundle."""
        return (
            bundle == self.bundle
            and charset in self.charsets
            and (size, bpp) in self.profiles
        )

    @staticmethod
    def _in_ranges(codepoint: int, ranges: list[list[int]]) -> bool:
        return any(start <= codepoint <= end for start, end in ranges)

    def _font_for(self, codepoint: int, size: int, bpp: int) -> CbinFont | None:
        profile = f"{size}_{bpp}"
        for shard in self.manifest["shards"]:
            if not self._in_ranges(codepoint, shard["ranges"]):
                continue
            key = (shard["id"], profile)
            if key not in self.fonts:
                with self._font_lock:
                    if key not in self.fonts:
                        font = CbinFont.from_file(self.root / shard["profiles"][profile])
                        font.assert_wire_compatible()
                        self.fonts[key] = font
            return self.fonts[key]
        return None

    def payload_for_text(
        self, text: str, size: int, bpp: int, charset: str = "common"
    ) -> dict | None:
        if charset not in self.charsets:
            raise ValueError(f"unknown device charset: {charset}")
        if size <= 0 or bpp not in (1, 4):
            raise ValueError("size must be positive and bpp must be 1 or 4")
        if (size, bpp) not in self.profiles:
            raise ValueError(f"unsupported font profile: {size}_{bpp}")
        device_charset = self.charsets[charset]
        glyphs = []
        seen = set()
        total_bitmap_bytes = 0
        for char in text:
            codepoint = ord(char)
            if codepoint < 0x20 or codepoint in seen or codepoint in device_charset:
                continue
            seen.add(codepoint)
            font = self._font_for(codepoint, size, bpp)
            if font is None:
                continue
            if font.bpp != bpp:
                raise ValueError(f"font bpp mismatch: expected {bpp}, found {font.bpp}")
            glyph = font.glyph(codepoint)
            if glyph is not None:
                bitmap_bytes = (glyph["box_w"] * glyph["box_h"] * bpp + 7) // 8
                if (
                    len(glyphs) >= MAX_GLYPHS
                    or total_bitmap_bytes + bitmap_bytes > MAX_BITMAP_BYTES
                ):
                    break
                glyphs.append(glyph)
                total_bitmap_bytes += bitmap_bytes
        if not glyphs:
            return None
        return {"v": 1, "bundle": self.bundle, "size": size, "bpp": bpp, "glyphs": glyphs}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("text")
    parser.add_argument("--size", required=True, type=int)
    parser.add_argument("--bpp", required=True, type=int, choices=(1, 4))
    parser.add_argument("--charset", choices=("basic", "common"), default="common")
    args = parser.parse_args()
    provider = FullGlyphProvider(args.manifest)
    print(
        json.dumps(
            provider.payload_for_text(args.text, args.size, args.bpp, args.charset), ensure_ascii=False
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
