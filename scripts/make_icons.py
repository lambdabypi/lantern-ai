"""Draw Lantern's app icons (PNG, standard library only): a lantern with a warm glow on dark teal.

    python scripts/make_icons.py      -> app/web/icons/icon-{192,512}.png, maskable-512.png, apple-touch-icon.png
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app" / "web" / "icons"
BG = (18, 32, 27)          # dark teal
FRAME = (233, 226, 208)    # warm off-white
GLOW = (255, 190, 90)      # amber
CORE = (255, 236, 190)


def _png(size: int, pixels: list[bytes]) -> bytes:
    raw = b"".join(b"\x00" + row for row in pixels)
    chunk = lambda tag, data: (struct.pack(">I", len(data)) + tag + data  # noqa: E731
                               + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw(size: int, padding: float, rounded: bool) -> bytes:
    """padding: share of the icon kept clear around the lantern (maskable icons need more)."""
    s = size
    scale = 1 - 2 * padding
    cx = s / 2
    rows = []
    for y in range(s):
        row = bytearray()
        for x in range(s):
            # background: rounded square (or full square for maskable icons)
            r_corner = s * 0.22 if rounded else 0
            dx, dy = max(r_corner - x, x - (s - 1 - r_corner), 0), max(r_corner - y, y - (s - 1 - r_corner), 0)
            if rounded and math.hypot(dx, dy) > r_corner:
                row += bytes((0, 0, 0, 0))
                continue
            u, v = (x - cx) / (s * scale), (y - s / 2) / (s * scale)   # -0.5..0.5 inside the drawing area
            color = BG
            glow = math.exp(-((u ** 2) / 0.06 + ((v - 0.06) ** 2) / 0.09) * 1.6)
            color = _mix(color, GLOW, min(glow * 0.55, 0.55))
            body = abs(u) < 0.17 and -0.16 < v < 0.28                 # glass
            if body:
                t = 1 - min(1, math.hypot(u / 0.17, (v - 0.06) / 0.22))
                color = _mix(GLOW, CORE, t ** 0.7)
            frame_side = 0.17 <= abs(u) < 0.21 and -0.16 < v < 0.28
            cap = abs(u) < 0.24 and -0.23 < v <= -0.16                # top cap
            base = abs(u) < 0.25 and 0.28 <= v < 0.34                 # base
            handle = abs(math.hypot(u, v + 0.27) - 0.11) < 0.025 and v < -0.24
            if frame_side or cap or base or handle:
                color = FRAME
            row += bytes((*color, 255))
        rows.append(bytes(row))
    return _png(s, rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "icon-192.png").write_bytes(draw(192, 0.10, rounded=True))
    (OUT / "icon-512.png").write_bytes(draw(512, 0.10, rounded=True))
    (OUT / "maskable-512.png").write_bytes(draw(512, 0.20, rounded=False))
    (OUT / "apple-touch-icon.png").write_bytes(draw(180, 0.12, rounded=False))  # iOS rounds the corners itself
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
