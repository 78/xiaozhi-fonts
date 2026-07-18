#!/usr/bin/env python3
"""Reproducible Noto font and emoji asset builder for XiaoZhi."""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from fontTools import subset
from fontTools.merge import Merger
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.scaleUpem import scale_upem

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parents[1]
BUILD = ROOT / "build"
SRC = ROOT / "src"
INCLUDE = ROOT / "include"
CBIN = ROOT / "cbin"
PNG = ROOT / "png"
CHARSETS = ROOT / "charsets"
FULL_DIST = ROOT / "dist" / "full"
LV_FONT_CONV_VERSION = "1.5.3"
LV_FONT_CONV = ["npx", "--yes", f"lv_font_conv@{LV_FONT_CONV_VERSION}"]
CBIN_CONVERTER_COMMIT = "c420999fe79adb0bc2a480c4a64fd33fc6e34519"
FONT_OUTPUT_CACHE_VERSION = 3


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def run(command: list[str], cwd: Path | None = None) -> None:
    display = " ".join(command)
    print("+", display if len(display) <= 1000 else display[:1000] + " ...")
    subprocess.run(command, check=True, cwd=cwd)


def cbin_converter() -> list[str]:
    destination = BUILD / "tools" / "lv_font_conv"
    marker = destination / ".installed-commit"
    if not marker.exists() or marker.read_text(encoding="utf-8").strip() != CBIN_CONVERTER_COMMIT:
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--quiet", "https://github.com/78/lv_font_conv.git", str(destination)])
        run(["git", "-C", str(destination), "checkout", "--quiet", CBIN_CONVERTER_COMMIT])
        run(["npm", "ci", "--omit=dev", "--ignore-scripts"], cwd=destination)
        marker.write_text(CBIN_CONVERTER_COMMIT + "\n", encoding="utf-8")
    return ["node", str(destination / "lv_font_conv.js")]


def utf8_literal(codepoint: int) -> str:
    return "".join(f"\\x{byte:02x}" for byte in chr(codepoint).encode("utf-8"))


def valid_text_codepoint(codepoint: int) -> bool:
    if codepoint < 0x20 or 0xD800 <= codepoint <= 0xDFFF or codepoint > 0x10FFFF:
        return False
    category = unicodedata.category(chr(codepoint))
    return category not in {"Cc", "Cs", "Cn"}


def collect_strings(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "".join(collect_strings(item) for item in value.values())
    if isinstance(value, list):
        return "".join(collect_strings(item) for item in value)
    return ""


def build_basic_charset(locale_dir: Path) -> set[int]:
    codepoints = set(range(0x20, 0x7F)) | set(range(0xA1, 0x100))
    for path in sorted(locale_dir.glob("*/language.json")):
        payload = load_json(path)
        text = collect_strings(payload.get("strings", payload))
        codepoints.update(ord(char) for char in text if valid_text_codepoint(ord(char)))
    return codepoints


def bytes_to_unicode() -> dict[int, str]:
    values = list(range(ord("!"), ord("~") + 1))
    values += list(range(ord("¡"), ord("¬") + 1))
    values += list(range(ord("®"), ord("ÿ") + 1))
    chars = values[:]
    extra = 0
    for byte in range(256):
        if byte not in values:
            values.append(byte)
            chars.append(256 + extra)
            extra += 1
    return dict(zip(values, map(chr, chars)))


def download_tokenizer(config: dict) -> Path:
    target = BUILD / "tokenizer" / "deepseek-v4-flash-tokenizer.json"
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {config['url']}")
    urllib.request.urlretrieve(config["url"], target)
    return target


def build_deepseek_charset(tokenizer_path: Path) -> set[int]:
    payload = load_json(tokenizer_path)
    vocab = payload["model"]["vocab"]
    inverse = {char: byte for byte, char in bytes_to_unicode().items()}
    codepoints: set[int] = set()
    for token, _token_id in sorted(vocab.items(), key=lambda item: item[1]):
        if token.startswith("<｜"):
            continue
        try:
            raw = bytes(inverse[char] for char in token)
        except KeyError:
            continue
        decoded = raw.decode("utf-8", errors="ignore")
        codepoints.update(ord(char) for char in decoded if valid_text_codepoint(ord(char)))
    return codepoints


def save_charset(name: str, codepoints: set[int], metadata: dict) -> None:
    payload = {**metadata, "count": len(codepoints), "codepoints": sorted(codepoints)}
    write_text(CHARSETS / f"{name}.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def font_codepoints(path: Path) -> set[int]:
    font = TTFont(path, lazy=True)
    result = set(font.getBestCmap() or {})
    font.close()
    return result


def profile_variant(profile: dict) -> str:
    return profile.get("variant", "regular")


def text_source_path(source: dict, variant: str = "regular") -> Path:
    key = "path" if variant == "regular" else f"{variant}_path"
    if key not in source:
        raise KeyError(f"Text source {source['id']} does not define {key}")
    return ROOT / source[key]


def assign_sources(
    manifest: dict, requested: set[int] | None, variant: str = "regular"
) -> tuple[list[tuple[dict, set[int]]], set[int]]:
    assigned: list[tuple[dict, set[int]]] = []
    covered: set[int] = set()
    for source in manifest["text_sources"]:
        path = text_source_path(source, variant)
        available = {cp for cp in font_codepoints(path) if valid_text_codepoint(cp)}
        owned = available - covered
        if requested is not None:
            owned &= requested
        if owned:
            assigned.append((source, owned))
            covered.update(owned)
    return assigned, covered


def make_font_deterministic(font: TTFont) -> None:
    font.recalcTimestamp = False
    if "head" in font:
        font["head"].modified = 2082844800  # 1970-01-01 in the OpenType epoch


def subset_font(source: Path, destination: Path, codepoints: set[int]) -> None:
    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = [0, 1, 2, 3, 4, 5, 6]
    options.name_legacy = True
    options.name_languages = [0x409]
    font = subset.load_font(str(source), options)
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=sorted(codepoints))
    subsetter.subset(font)
    if font["head"].unitsPerEm != 1000:
        scale_upem(font, 1000)
    # LVGL only consumes horizontal metrics. CJK fonts carry vertical tables
    # while the Latin/Arabic/Thai fonts do not, which also makes fontTools merge
    # reject the otherwise compatible subset fonts.
    for tag in ("vhea", "vmtx"):
        if tag in font:
            del font[tag]
    make_font_deterministic(font)
    destination.parent.mkdir(parents=True, exist_ok=True)
    subset.save_font(font, str(destination), options)


def cap_glyph_heights(
    source: Path, destination: Path, codepoints: set[int], max_height_units: int
) -> None:
    font = TTFont(source)
    cmap = font.getBestCmap() or {}
    glyph_names = {cmap[codepoint] for codepoint in codepoints if codepoint in cmap}
    glyph_set = font.getGlyphSet()
    replacements = {}
    for glyph_name in glyph_names:
        bounds_pen = BoundsPen(glyph_set)
        glyph_set[glyph_name].draw(bounds_pen)
        if bounds_pen.bounds is None:
            continue
        glyph_height = bounds_pen.bounds[3] - bounds_pen.bounds[1]
        if glyph_height <= max_height_units:
            continue
        y_scale = max_height_units / glyph_height
        recording_pen = DecomposingRecordingPen(glyph_set)
        glyph_set[glyph_name].draw(recording_pen)
        pen = TTGlyphPen(None)
        recording_pen.replay(TransformPen(pen, (1, 0, 0, y_scale, 0, 0)))
        replacements[glyph_name] = pen.glyph()
    for glyph_name, glyph in replacements.items():
        font["glyf"][glyph_name] = glyph
    make_font_deterministic(font)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    font.save(temporary)
    temporary.replace(destination)


def merge_text_font(
    charset: str, manifest: dict, requested: set[int], variant: str = "regular"
) -> tuple[Path, set[int]]:
    assigned, covered = assign_sources(manifest, requested, variant)
    merged_destination = BUILD / "ttf" / f"noto-sans-{charset}-{variant}.ttf"
    source_fingerprint = source_files_fingerprint([
        text_source_path(source, variant) for source in manifest["text_sources"]
    ])
    digest = hashlib.sha256()
    digest.update(str(FONT_OUTPUT_CACHE_VERSION).encode())
    digest.update(charset.encode())
    digest.update(variant.encode())
    digest.update(source_fingerprint.encode())
    for codepoint in sorted(covered):
        digest.update(codepoint.to_bytes(4, "little"))
    cache_marker = BUILD / "cache" / "text_merges" / f"{merged_destination.name}.sha256"
    fingerprint = digest.hexdigest()
    if (
        merged_destination.exists()
        and cache_marker.exists()
        and cache_marker.read_text(encoding="utf-8").strip() == fingerprint
    ):
        print(f"= cached {merged_destination}")
        return merged_destination, covered

    subset_paths: list[Path] = []
    for source, codepoints in assigned:
        subset_destination = BUILD / "subsets" / charset / variant / f"{source['id']}.ttf"
        subset_font(text_source_path(source, variant), subset_destination, codepoints)
        subset_paths.append(subset_destination)
    if not subset_paths:
        raise RuntimeError(f"No fonts cover the {charset} charset")
    merged_destination.parent.mkdir(parents=True, exist_ok=True)
    if len(subset_paths) == 1:
        shutil.copyfile(subset_paths[0], merged_destination)
    else:
        font = Merger().merge([str(path) for path in subset_paths])
        make_font_deterministic(font)
        font.save(merged_destination)
    write_text(cache_marker, fingerprint + "\n")
    return merged_destination, covered


def compact_ranges(codepoints: set[int]) -> str:
    values = sorted(codepoints)
    if not values:
        raise ValueError("empty codepoint set")
    ranges: list[str] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(hex(start) if start == previous else f"{hex(start)}-{hex(previous)}")
        start = previous = value
    ranges.append(hex(start) if start == previous else f"{hex(start)}-{hex(previous)}")
    return ",".join(ranges)


def range_pairs(codepoints: set[int]) -> list[list[int]]:
    values = sorted(codepoints)
    if not values:
        return []
    result: list[list[int]] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        result.append([start, previous])
        start = previous = value
    result.append([start, previous])
    return result


def profiles_for(manifest: dict, key: str, selected: set[str] | None) -> list[dict]:
    profiles = manifest[key]
    if selected is None:
        return profiles
    return [profile for profile in profiles if profile["name"] in selected]


def profile_names_for_size(
    manifest: dict, keys: list[str], size: int, bpp: int
) -> set[str]:
    return {
        profile["name"]
        for key in keys
        for profile in manifest[key]
        if profile["size"] == size and profile["bpp"] == bpp
    }


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_files_fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(file_digest(path).encode())
    return digest.hexdigest()


def font_output_fingerprint(
    font: Path, profile: dict, codepoints: set[int], fmt: str, symbol: str,
    source_fingerprint: str | None = None,
) -> str:
    digest = hashlib.sha256()
    digest.update(str(FONT_OUTPUT_CACHE_VERSION).encode())
    digest.update(
        (CBIN_CONVERTER_COMMIT if fmt == "cbin" else f"lv_font_conv@{LV_FONT_CONV_VERSION}").encode()
    )
    digest.update(fmt.encode())
    digest.update(symbol.encode())
    digest.update(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode())
    digest.update((source_fingerprint or file_digest(font)).encode())
    for codepoint in sorted(codepoints):
        digest.update(codepoint.to_bytes(4, "little"))
    return digest.hexdigest()


def font_output_cache_marker(output: Path) -> Path:
    try:
        identity = str(output.relative_to(ROOT))
    except ValueError:
        identity = str(output)
    name = hashlib.sha256(identity.encode()).hexdigest() + ".sha256"
    return BUILD / "cache" / "font_outputs" / name


def lv_font_conv(
    font: Path, output: Path, profile: dict, codepoints: set[int], fmt: str, symbol: str,
    source_fingerprint: str | None = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = font_output_fingerprint(
        font, profile, codepoints, fmt, symbol, source_fingerprint
    )
    marker = font_output_cache_marker(output)
    if output.exists() and marker.exists() and marker.read_text(encoding="utf-8").strip() == fingerprint:
        print(f"= cached {output}")
        return
    converter = cbin_converter() if fmt == "cbin" else LV_FONT_CONV
    command = converter + [
        "--no-compress", "--no-prefilter", "--force-fast-kern-format",
        "--font", str(font),
    ]
    autohint = profile.get("autohint")
    if autohint == "off":
        command.append("--autohint-off")
    elif autohint is not None:
        raise ValueError(f"Unsupported autohint mode: {autohint}")
    command += [
        "--format", fmt, "--bpp", str(profile["bpp"]),
        "--size", str(profile["size"]), "-r", compact_ranges(codepoints),
        "-o", str(output),
    ]
    if fmt == "lvgl":
        command += ["--lv-include", "lvgl.h", "--lv-font-name", symbol]
    run(command)
    if fmt == "lvgl":
        postprocess_lvgl_font(output, profile)
    elif fmt == "cbin":
        postprocess_cbin_font(output, profile)
    write_text(marker, fingerprint + "\n")


def fitted_glyph_ofs_y(box_h: int, ofs_y: int, line_height: int, base_line: int) -> int:
    if box_h > line_height:
        raise ValueError(f"Glyph height {box_h} exceeds the {line_height}-pixel line box")
    minimum = -base_line
    maximum = line_height - base_line - box_h
    return min(max(ofs_y, minimum), maximum)


def postprocess_lvgl_font(output: Path, profile: dict) -> None:
    overrides = {
        "line_height": profile.get("line_height"),
        "base_line": profile.get("base_line"),
    }
    fit_line_box = profile.get("fit_line_box", False)
    if all(value is None for value in overrides.values()) and not fit_line_box:
        return
    source = output.read_text(encoding="utf-8")
    if fit_line_box:
        line_height = overrides["line_height"]
        base_line = overrides["base_line"]
        if line_height is None or base_line is None:
            raise ValueError("glyph positioning requires line_height and base_line")
        pattern = re.compile(
            r"(?P<prefix>\.box_h\s*=\s*(?P<height>\d+)[^{}]*?\.ofs_y\s*=\s*)"
            r"(?P<offset>-?\d+)"
        )

        def fit_offset(match: re.Match) -> str:
            box_h = int(match.group("height"))
            fitted = fitted_glyph_ofs_y(
                box_h, int(match.group("offset")), line_height, base_line
            )
            return match.group("prefix") + str(fitted)

        source, count = pattern.subn(fit_offset, source)
        if count == 0:
            raise RuntimeError(f"No glyph descriptors found in {output}")
    for field, value in overrides.items():
        if value is None:
            continue
        source, count = re.subn(rf"(\.{field}\s*=\s*)-?\d+", rf"\g<1>{value}", source)
        if count != 1:
            raise RuntimeError(f"Expected one {field} field in {output}, found {count}")
    output.write_text(source, encoding="utf-8")


def postprocess_cbin_font(output: Path, profile: dict) -> None:
    line_height = profile.get("line_height")
    base_line = profile.get("base_line")
    fit_line_box = profile.get("fit_line_box", False)
    if line_height is None and base_line is None and not fit_line_box:
        return
    if fit_line_box and (line_height is None or base_line is None):
        raise ValueError("glyph positioning requires line_height and base_line")
    data = bytearray(output.read_bytes())
    if len(data) < 28:
        raise RuntimeError(f"Invalid CBIN font header in {output}")
    if line_height is not None:
        struct.pack_into("<i", data, 12, line_height)
    if base_line is not None:
        struct.pack_into("<i", data, 16, base_line)
    if fit_line_box:
        descriptor = struct.unpack_from("<I", data, 24)[0]
        _, glyph_offset, cmap_offset = struct.unpack_from("<III", data, descriptor)
        glyph_start = descriptor + glyph_offset
        glyph_end = descriptor + cmap_offset
        if glyph_start > glyph_end or (glyph_end - glyph_start) % 16 != 0:
            raise RuntimeError(f"Invalid CBIN glyph descriptor table in {output}")
        for offset in range(glyph_start, glyph_end, 16):
            box_h = struct.unpack_from("<H", data, offset + 10)[0]
            ofs_y = struct.unpack_from("<h", data, offset + 14)[0]
            fitted = fitted_glyph_ofs_y(box_h, ofs_y, line_height, base_line)
            struct.pack_into("<h", data, offset + 14, fitted)
    output.write_bytes(data)


def build_text_tier(
    charset: str, manifest: dict, requested: set[int], selected: set[str] | None = None
) -> None:
    _, covered = assign_sources(manifest, requested)
    missing = requested - covered
    save_charset(
        charset,
        covered,
        {"charset": charset, "requested": len(requested), "missing": len(missing)},
    )
    if missing:
        write_text(BUILD / f"{charset}-missing.json", json.dumps(sorted(missing)) + "\n")
    merged_fonts: dict[str, tuple[Path, set[int]]] = {}
    source_fingerprints: dict[str, str] = {}
    for profile in profiles_for(manifest, "text_profiles", selected):
        variant = profile_variant(profile)
        if variant not in source_fingerprints:
            source_fingerprints[variant] = source_files_fingerprint([
                text_source_path(source, variant) for source in manifest["text_sources"]
            ])
        if variant not in merged_fonts:
            merged_fonts[variant] = merge_text_font(charset, manifest, requested, variant)
        font, profile_covered = merged_fonts[variant]
        if profile_covered != covered:
            raise RuntimeError(f"Text coverage differs for the {variant} variant")
        name = f"font_noto_sans_{charset}_{profile['name']}"
        if charset == "basic":
            lv_font_conv(
                font, SRC / f"{name}.c", profile, profile_covered, "lvgl", name,
                source_fingerprints[variant],
            )
        else:
            lv_font_conv(
                font, CBIN / f"{name}.bin", profile, profile_covered, "cbin", name,
                source_fingerprints[variant],
            )


def generate_lookup_header(prefix: str, typedef: str, function: str, mapping: dict[str, str]) -> str:
    guard = f"{prefix}_H"
    lines = [
        f"#ifndef {guard}", f"#define {guard}", "", "#include <stddef.h>", "#include <string.h>", "",
        f"typedef struct {{ const char* name; const char* utf8_string; }} {typedef};", "",
    ]
    for name, value in mapping.items():
        lines.append(f'#define {prefix}_{name.upper()} "{utf8_literal(int(value, 16))}"')
    lines += [
        "", f"extern const {typedef} {function}_symbols[];",
        f"extern const size_t {function}_symbol_count;", "",
        f"static inline const char* {function}_get_utf8(const char* name) {{",
        "    if (name == NULL) return NULL;",
        f"    for (size_t i = 0; i < {function}_symbol_count; ++i) {{",
        f"        if (strcmp({function}_symbols[i].name, name) == 0) return {function}_symbols[i].utf8_string;",
        "    }", "    return NULL;", "}", "", f"#endif  // {guard}", "",
    ]
    return "\n".join(lines)


def generate_lookup_source(header: str, typedef: str, function: str, prefix: str, mapping: dict[str, str]) -> str:
    lines = [f'#include "{header}"', "", f"const {typedef} {function}_symbols[] = {{"]
    lines += [f'    {{"{name}", {prefix}_{name.upper()}}},' for name in mapping]
    lines += ["};", f"const size_t {function}_symbol_count = sizeof({function}_symbols) / sizeof({function}_symbols[0]);", ""]
    return "\n".join(lines)


def build_material_symbols(manifest: dict, selected: set[str] | None = None) -> None:
    mapping = load_json(ROOT / "mappings" / "material_symbols.json")
    prefix = "MATERIAL_SYMBOLS"
    write_text(INCLUDE / "material_symbols.h", generate_lookup_header(prefix, "material_symbol_t", "material_symbols", mapping))
    write_text(SRC / "material_symbols.c", generate_lookup_source("material_symbols.h", "material_symbol_t", "material_symbols", prefix, mapping))
    codepoints = {int(value, 16) for value in mapping.values()}
    for profile in profiles_for(manifest, "icon_profiles", selected):
        variant = profile_variant(profile)
        font = ROOT / manifest["material_fonts"][variant]
        source_fingerprint = source_files_fingerprint([font])
        name = f"font_material_symbols_{profile['name']}"
        lv_font_conv(
            font, SRC / f"{name}.c", profile, codepoints, "lvgl", name, source_fingerprint
        )


def build_mono_emoji(manifest: dict, selected: set[str] | None = None) -> None:
    mapping = load_json(ROOT / "mappings" / "emoji.json")
    prefix = "NOTO_EMOJI"
    write_text(INCLUDE / "noto_emoji.h", generate_lookup_header(prefix, "noto_emoji_symbol_t", "noto_emoji", mapping))
    write_text(SRC / "noto_emoji.c", generate_lookup_source("noto_emoji.h", "noto_emoji_symbol_t", "noto_emoji", prefix, mapping))
    codepoints = {int(value, 16) for value in mapping.values()}
    font = ROOT / manifest["mono_emoji_font"]
    source_fingerprint = source_files_fingerprint([font])
    for profile in profiles_for(manifest, "emoji_font_profiles", selected):
        name = f"font_noto_emoji_{profile['name']}"
        lv_font_conv(
            font, SRC / f"{name}.c", profile, codepoints, "lvgl", name, source_fingerprint
        )


def svg_for_glyph(font: TTFont, codepoint: int) -> bytes:
    glyph_name = font.getBestCmap()[codepoint]
    glyph_id = font.getGlyphID(glyph_name)
    document = next(item for item in font["SVG "].docList if item.startGlyphID <= glyph_id <= item.endGlyphID)
    source_root = ET.fromstring(document.data)
    target = next((node for node in source_root.iter() if node.attrib.get("id") == f"glyph{glyph_id}"), None)
    if target is None:
        raise RuntimeError(f"SVG glyph group not found for U+{codepoint:04X}")
    namespace = "http://www.w3.org/2000/svg"
    ET.register_namespace("", namespace)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    advance = font["hmtx"][glyph_name][0]
    ascent = font["hhea"].ascent
    descent = font["hhea"].descent
    output_root = ET.Element(f"{{{namespace}}}svg", {
        "viewBox": f"0 {-ascent} {advance} {ascent - descent}",
        "preserveAspectRatio": "xMidYMid meet",
    })
    for node in source_root:
        if node.tag.endswith("defs"):
            output_root.append(copy.deepcopy(node))
    output_root.append(copy.deepcopy(target))
    return ET.tostring(output_root, encoding="utf-8", xml_declaration=True)


def build_color_emoji(manifest: dict) -> None:
    mapping = load_json(ROOT / "mappings" / "emoji.json")
    font = TTFont(ROOT / manifest["color_emoji_font"])
    svg_dir = BUILD / "emoji-svg"
    svg_dir.mkdir(parents=True, exist_ok=True)
    for name, value in mapping.items():
        codepoint = int(value, 16)
        svg_path = svg_dir / f"{name}.svg"
        svg_path.write_bytes(svg_for_glyph(font, codepoint))
        for size in manifest["color_emoji_sizes"]:
            output = PNG / f"noto-color-emoji_{size}" / f"{name}.png"
            output.parent.mkdir(parents=True, exist_ok=True)
            run(["rsvg-convert", "--width", str(size), "--height", str(size), "--keep-aspect-ratio", "-o", str(output), str(svg_path)])
    font.close()


def font_bundle_id(manifest: dict) -> str:
    bundle_id = manifest.get("bundle_id")
    if not isinstance(bundle_id, str) or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", bundle_id) is None:
        raise ValueError("manifest bundle_id must be a lowercase identifier of at most 64 characters")
    return bundle_id


def write_font_bundle_header(manifest: dict) -> None:
    bundle_id = font_bundle_id(manifest)
    content = f'''#ifndef NOTO_FONT_BUNDLE_H
#define NOTO_FONT_BUNDLE_H

#define NOTO_FONT_BUNDLE_ID "{bundle_id}"

#endif
'''
    write_text(INCLUDE / "noto_font_bundle.h", content)


def build_full(manifest: dict, selected: set[str] | None = None, jobs: int = 1) -> None:
    assigned, covered = assign_sources(manifest, None)
    bundle_id = font_bundle_id(manifest)
    destination = FULL_DIST / f"{bundle_id}-full"
    # Keep completed shards so an interrupted full build can resume and hit the
    # per-output cache on the next invocation.
    destination.mkdir(parents=True, exist_ok=True)
    profiles = profiles_for(manifest, "text_profiles", selected)
    shards = []
    tasks = []
    source_fingerprints = {}
    for source, codepoints in assigned:
        shard = {"id": source["id"], "count": len(codepoints), "ranges": range_pairs(codepoints), "profiles": {}}
        for profile in profiles:
            output = destination / profile["name"] / f"{source['id']}.cbin"
            variant = profile_variant(profile)
            font = text_source_path(source, variant)
            fingerprint_key = (source["id"], variant)
            if fingerprint_key not in source_fingerprints:
                source_fingerprints[fingerprint_key] = source_files_fingerprint([font])
            tasks.append((source, codepoints, profile, output, font, source_fingerprints[fingerprint_key]))
            shard["profiles"][profile["name"]] = str(output.relative_to(destination))
        shards.append(shard)

    def build_shard_profile(task: tuple) -> None:
        source, codepoints, profile, output, font, source_fingerprint = task
        max_height_units = profile.get("full_max_glyph_height_units")
        if max_height_units is not None:
            capped_font = BUILD / "ttf" / "full" / profile["name"] / f"{source['id']}.ttf"
            cap_glyph_heights(font, capped_font, codepoints, max_height_units)
            font = capped_font
        lv_font_conv(
            font, output, profile, codepoints, "cbin", source["id"], source_fingerprint
        )

    if jobs > 1:
        # Ensure the pinned converter is installed before workers access it.
        cbin_converter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
            list(executor.map(build_shard_profile, tasks))
    else:
        for task in tasks:
            build_shard_profile(task)
    common_path = CHARSETS / "common.json"
    basic_path = CHARSETS / "basic.json"
    payload = {
        "version": 1,
        "bundle_id": bundle_id,
        "profiles": profiles,
        "coverage_count": len(covered),
        "charsets": {
            "basic": load_json(basic_path) if basic_path.exists() else None,
            "common": load_json(common_path) if common_path.exists() else None,
        },
        "shards": shards,
    }
    write_text(destination / "manifest.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    archive = shutil.make_archive(str(destination), "gztar", destination)
    print(f"Full bundle: {archive}")


def build_charsets(manifest: dict, locale_dir: Path) -> tuple[set[int], set[int]]:
    basic = build_basic_charset(locale_dir)
    tokenizer_path = download_tokenizer(manifest["tokenizer"])
    deepseek = build_deepseek_charset(tokenizer_path)
    common = basic | deepseek
    _, basic_covered = assign_sources(manifest, basic)
    _, common_covered = assign_sources(manifest, common)
    save_charset(
        "basic-requested", basic, {"charset": "basic", "locale_dir": str(locale_dir)}
    )
    save_charset("deepseek", deepseek, {"model": manifest["tokenizer"]["model"], "core_vocab_only": True})
    save_charset(
        "common-requested",
        common,
        {"charset": "common", "formula": "basic union deepseek"},
    )
    print(
        f"basic={len(basic)} (covered={len(basic_covered)}) "
        f"deepseek={len(deepseek)} common={len(common)} (covered={len(common_covered)})"
    )
    return basic, common


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=["charsets", "basic", "common", "icons", "emoji", "full", "all"])
    parser.add_argument("--locale-dir", type=Path, default=PROJECT_ROOT / "main" / "assets" / "locales")
    parser.add_argument(
        "--size", type=int,
        help="generate only the profile with this pixel size; requires --bpp",
    )
    parser.add_argument(
        "--bpp", type=int, choices=(1, 4),
        help="generate only the profile with this bit depth; requires --size",
    )
    parser.add_argument(
        "--jobs", type=int, default=1,
        help="number of concurrent full-bundle conversion jobs (default: 1)",
    )
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    if args.jobs != 1 and args.target not in {"full", "all"}:
        parser.error("--jobs is only supported for the full and all targets")
    if (args.size is None) != (args.bpp is None):
        parser.error("--size and --bpp must be used together")
    if args.size is not None and args.size <= 0:
        parser.error("--size must be positive")
    manifest = load_json(ROOT / "manifest.json")
    selected = None
    if args.size is not None:
        if args.target in {"charsets", "emoji", "all"}:
            parser.error(f"--size and --bpp are not supported for the {args.target} target")
        profile_keys = (
            ["icon_profiles", "emoji_font_profiles"]
            if args.target == "icons"
            else ["text_profiles"]
        )
        selected = profile_names_for_size(manifest, profile_keys, args.size, args.bpp)
        if not selected:
            parser.error(f"no profile matches size={args.size}, bpp={args.bpp}")
    write_font_bundle_header(manifest)
    BUILD.mkdir(exist_ok=True)
    if args.target in {"icons", "all"}:
        build_material_symbols(manifest, selected)
        build_mono_emoji(manifest, selected)
    if args.target in {"emoji", "all"}:
        build_color_emoji(manifest)
    if args.target in {"charsets", "basic", "common", "full", "all"}:
        basic, common = build_charsets(manifest, args.locale_dir)
        if args.target in {"basic", "all"}:
            build_text_tier("basic", manifest, basic, selected)
        if args.target in {"common", "full", "all"}:
            build_text_tier("common", manifest, common, selected)
        if args.target in {"full", "all"}:
            build_full(manifest, selected, args.jobs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
