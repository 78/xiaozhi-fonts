#!/usr/bin/env python3
"""Minimal HTTP adapter for serving glyphs from a noto-fonts full bundle.

The reusable part is build_glyph_push(). The HTTP server only demonstrates one
possible transport; WebSocket, MQTT, and proprietary protocols can call the
same function before sending a text message to a device.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if __package__:
    from scripts.glyph_provider import FullGlyphProvider
else:
    sys.path.insert(0, str(ROOT / "scripts"))
    from glyph_provider import FullGlyphProvider  # noqa: E402


MAX_REQUEST_BYTES = 1024 * 1024


def build_glyph_push(
    provider: FullGlyphProvider, device_capabilities: dict[str, Any], text: str
) -> dict | None:
    """Build a glyph_push payload from advertised device capabilities and text."""
    features = device_capabilities.get("features")
    capability = device_capabilities.get("text_font")
    if not isinstance(features, dict) or features.get("glyph_push") is not True:
        return None
    if not isinstance(capability, dict):
        return None

    bundle = capability.get("bundle")
    charset = capability.get("charset")
    size = capability.get("size")
    bpp = capability.get("bpp")
    if (
        not isinstance(bundle, str)
        or not isinstance(charset, str)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or not isinstance(bpp, int)
        or isinstance(bpp, bool)
        or not provider.supports(bundle, charset, size, bpp)
    ):
        return None

    return provider.payload_for_text(text, size, bpp, charset)


class GlyphPushHandler(BaseHTTPRequestHandler):
    provider: FullGlyphProvider

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/glyph-push":
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("invalid Content-Length")
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict):
                raise ValueError("request body must be a JSON object")
            device = request.get("device")
            text = request.get("text")
            if not isinstance(device, dict) or not isinstance(text, str):
                raise ValueError("request requires device object and text string")
            payload = build_glyph_push(self.provider, device, text)
            self._send_json({"glyph_push": payload} if payload else {})
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            self._send_json({"error": str(error)}, status=400)

    def _send_json(self, value: dict, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path, help="path to the extracted full-bundle manifest")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    GlyphPushHandler.provider = FullGlyphProvider(args.manifest)
    server = ThreadingHTTPServer((args.host, args.port), GlyphPushHandler)
    print(f"Serving {GlyphPushHandler.provider.bundle} on http://{args.host}:{args.port}/glyph-push")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
