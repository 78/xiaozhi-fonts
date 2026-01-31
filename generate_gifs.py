'''
Download animated Noto Emoji GIFs from https://googlefonts.github.io/noto-emoji-animation/
and resize with gifsicle to 32/64/128 for use as emoji pack.

- Source: https://fonts.gstatic.com/s/e/notoemoji/latest/{codepoint}/512.gif (512x512)
- Process: gifsicle -U --resize-fit NxN --loopcount=0 --lossy=30 -O2 input.gif -o output.gif
- Output: gif/noto-emoji_32/, gif/noto-emoji_64/, gif/noto-emoji_128/ (same 21 names as emoji_mapping)

Requires: pip install requests, and system gifsicle (apt install gifsicle / brew install gifsicle).
'''

import os
import sys
import subprocess
import shutil

import requests

# Same emoji mapping as font_emoji.py (21 emojis)
emoji_mapping = {
    "neutral": 0x1f636,     # 😶
    "happy": 0x1f642,       # 🙂
    "laughing": 0x1f606,    # 😆
    "funny": 0x1f602,       # 😂
    "sad": 0x1f614,         # 😔
    "angry": 0x1f620,       # 😠
    "crying": 0x1f62d,      # 😭
    "loving": 0x1f60d,      # 😍
    "embarrassed": 0x1f633,  # 😳
    "surprised": 0x1f62f,   # 😯
    "shocked": 0x1f631,     # 😱
    "thinking": 0x1f914,    # 🤔
    "winking": 0x1f609,     # 😉
    "cool": 0x1f60e,        # 😎
    "relaxed": 0x1f60c,     # 😌
    "delicious": 0x1f924,   # 🤤
    "kissy": 0x1f618,       # 😘
    "confident": 0x1f60f,   # 😏
    "sleepy": 0x1f634,      # 😴
    "silly": 0x1f61c,       # 😜
    "confused": 0x1f644,    # 🙄
}

# Output sizes (same structure as png: twemoji_32, twemoji_64, twemoji_128)
GIF_SIZES = [32, 64, 128]
BUILD_GIF_DIR = "./build/gif"
GIF_BASE_URL = "https://fonts.gstatic.com/s/e/notoemoji/latest"
GIF_OUT_DIR = "./gif"


def check_gifsicle():
    """Ensure gifsicle is available."""
    if shutil.which("gifsicle") is None:
        print("Error: gifsicle is required. Install with: apt install gifsicle (or brew install gifsicle)")
        sys.exit(1)


def get_emoji_gif_512(name: str, emoji_hex: str) -> str:
    """Download 512x512 GIF from Noto Emoji animation if not present. Returns path to local file."""
    os.makedirs(BUILD_GIF_DIR, exist_ok=True)
    local_path = os.path.join(BUILD_GIF_DIR, f"{name}.gif")
    if os.path.exists(local_path):
        return local_path
    url = f"{GIF_BASE_URL}/{emoji_hex}/512.gif"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(r.content)
        print(f"  Downloaded: {name} <- {url}")
    except requests.RequestException as e:
        print(f"  Failed to download {name}: {e}")
        raise
    return local_path


def resize_gif(input_path: str, output_path: str, size: int) -> None:
    """Resize GIF with gifsicle: -U --resize-fit NxN --loopcount=0 --lossy=30 -O2."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cmd = [
        "gifsicle",
        "-U",
        "--resize-fit", f"{size}x{size}",
        "--loopcount=0",
        "--lossy=0",
        "-O2",
        input_path,
        "-o", output_path,
    ]
    subprocess.run(cmd, check=True)


def main():
    check_gifsicle()
    print("Generating Noto Emoji GIFs (32, 64, 128)...")
    for name, code in emoji_mapping.items():
        emoji_hex = format(code, "x")
        try:
            gif_512 = get_emoji_gif_512(name, emoji_hex)
        except requests.RequestException:
            continue
        for size in GIF_SIZES:
            out_dir = os.path.join(GIF_OUT_DIR, f"noto-emoji_{size}")
            out_path = os.path.join(out_dir, f"{name}.gif")
            if os.path.exists(out_path):
                print(f"  Skip (exists): {out_path}")
                continue
            resize_gif(gif_512, out_path, size)
            print(f"  Generated: {out_path}")
    print("Done.")


if __name__ == "__main__":
    main()
