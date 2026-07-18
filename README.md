# noto-fonts

The unified Noto font component for XiaoZhi:

- Noto Sans text fonts in `basic`, `common`, and server-side `full` tiers
- Noto Material Symbols for status and system icons
- Noto Emoji for monochrome character-based emotions
- Noto Color Emoji as 32/64/128 PNG files for the assets partition

## Character sets

The requested `basic` set contains ASCII, Latin-1, and the static strings from
`main/assets/locales/*/language.json`. The device charset contains only characters covered by the
current font sources. Uncovered characters are listed in `build/basic-missing.json`.

The requested common set is `common = basic union deepseek`. The `deepseek` set is decoded from the
core BPE vocabulary of the `deepseek-ai/DeepSeek-V4-Flash` tokenizer; added tokens are excluded.
`charsets/common.json` contains only renderable characters.

The `full` font set is a server distribution bundle. It is not committed to Git and is never downloaded
to a device as a whole. The server extracts missing glyphs from the appropriate CBIN shard using the
size and bpp reported by the device.

The font bundle ID is set explicitly by `bundle_id` in `manifest.json`. Increment it whenever text
font files, profiles, CBIN compatibility, rendering behavior, tokenizer, or character sets change.
Icons and emoji do not affect the text font bundle ID.

## Missing-glyph delivery

The device reports a `text_font` capability in its WebSocket or MQTT hello message. It includes the
font bundle, installed `basic` or `common` charset, size, and bpp. The server may attach a
glyph payload to a TTS `sentence_start` or STT message:

```json
{
  "glyphs": {
    "v": 1,
    "bundle": "noto-...",
    "size": 20,
    "bpp": 4,
    "items": [{
      "codepoint": 171581,
      "adv_w": 320,
      "box_w": 20,
      "box_h": 20,
      "ofs_x": 0,
      "ofs_y": 0,
      "bitmap": "...base64..."
    }]
  }
}
```

A device accepts at most 64 glyphs and 64 KiB of bitmap data in one message. Bundle, size, and
bpp mismatches are rejected. All glyphs in one message are inserted as a batch, followed by one
dynamic fallback-font rebuild.

All devices advertise `glyph_push: 1`. On devices with initialized PSRAM, incoming bitmaps and all
persistent cache, cmap, and glyph-descriptor storage are allocated from PSRAM and retained across
messages subject to the cache limits. A device without PSRAM uses internal RAM only for the current
glyph batch; the next text message clears it, and a new batch replaces it instead of growing a
persistent cache. Clearing a transient batch also releases its reserved internal-RAM capacity.

The assets partition `index.json` also carries the bundle, charset, size, and bpp. Firmware loads a Noto
common CBIN only when they match the firmware. A legacy Puhui font or an asset without this font
information is ignored, and the device continues with its built-in Noto basic font.

Generate a payload for a piece of text from a full bundle with:

```sh
python3 scripts/glyph_provider.py \
  dist/full/noto-*-full/manifest.json 'text to render' \
  --size 20 --bpp 4 --charset common
```

## Generation

Python 3.11+, Node.js, and `rsvg-convert` are required. Install the Python dependencies and run the
desired targets:

```sh
python3 -m pip install -r requirements.txt
python3 scripts/build.py icons
python3 scripts/build.py emoji
python3 scripts/build.py basic
python3 scripts/build.py common
python3 scripts/build.py full --jobs 5
python3 -m unittest discover -s tests -v
```

During font tuning, generate only the profile being changed:

```bash
python3 scripts/build.py basic --profile 14_1
python3 scripts/build.py common --profile 14_1
python3 scripts/build.py icons --profile 14_1
```

Merged text fonts and conversion outputs are cached by the source fonts, profile, charset, format,
and converter version. Unchanged outputs print `cached` and are not regenerated. Cache markers live
under `build/cache/`. Full builds keep completed shards so an interrupted build can resume, but
`full` should only be run after the device profiles have been validated because it intentionally
covers every source glyph.

LVGL C fonts use the pinned npm package `lv_font_conv@1.5.3`. CBIN files use
`78/lv_font_conv@c420999f`, which emits the legacy CBIN memory layout expected by the firmware.

Text profiles are `14_1`, `16_4`, `20_4`, and `30_4`. Icon fonts additionally provide `30_1` for
monochrome OLED displays. The `14_1` text profile uses ExtraLight 200 with font autohinting
disabled. It rasterizes native outlines at 14 pixels without geometric post-scaling, then places
the resulting glyphs in a 16-pixel line box. CJK glyphs typically occupy 12–13 pixels of ink with a
14-pixel advance. The
`14_1` Material Symbols also use native outlines without geometric scaling, preserve the 14-pixel
advance, and retain the font's natural vertical placement in the same 16-pixel line box used by
text.
Generated LVGL and CBIN fonts use the same
16-pixel line box, and glyph offsets are constrained to that box. This allows exactly four rows on a
64-pixel OLED without clipping or label overflow. All other profiles use Regular 400 with default
hinting. Rare full-bundle glyphs whose native outlines are taller than the OLED line box are scaled
individually; their strokes are preserved instead of being clipped.

Commit generated files under `src/`, `include/`, `cbin/`, `png/`, and `charsets/`. The reproducible
intermediate files under `build/` and the server bundle under `dist/full/` are ignored.
