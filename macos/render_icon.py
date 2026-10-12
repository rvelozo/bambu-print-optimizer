"""Draw the launcher's icon as a PNG: a dark build plate with three brass layers.

Same picture as slicer_optimizer/web/icon.svg, laid out on macOS's icon grid
(an 824 px rounded square on a 1024 px canvas). Standard library only.

    python3 render_icon.py OUT.png [SIZE]
"""

import math
import struct
import sys
import zlib

PLATE = (0x1E, 0x1B, 0x16)
GRID = (0x2C, 0x28, 0x20)
BRASS = (0xC8, 0xA0, 0x4A)


def png(path: str, size: int, rows: list[bytes]) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + r for r in rows)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def render(size: int) -> list[bytes]:
    k = size / 1024.0
    x0, x1, r = 100 * k, 924 * k, 185 * k           # the rounded square
    unit = 824 * k / 58                               # the SVG's 64-unit grid, 3..61 across the square
    to = lambda u: x0 + (u - 3) * unit
    layers = [(to(14), to(50), to(46), 1.0), (to(18), to(46), to(36), 0.8), (to(23), to(41), to(26), 0.6)]
    half = 2.5 * unit                                 # stroke width 5 in SVG units
    pitch = 46 * k
    rows = []
    for py in range(size):
        y = py + 0.5
        row = bytearray()
        for px in range(size):
            x = px + 0.5
            # signed distance to the rounded square
            qx = max(abs(x - 512 * k) - (412 * k - r), 0.0)
            qy = max(abs(y - 512 * k) - (412 * k - r), 0.0)
            inside = max(abs(x - 512 * k) - (412 * k - r), abs(y - 512 * k) - (412 * k - r))
            d = math.hypot(qx, qy) + min(inside, 0.0) - r
            a = min(max(0.5 - d, 0.0), 1.0)
            if a <= 0:
                row += b"\x00\x00\x00\x00"
                continue
            c = list(PLATE)
            gx = abs(((x - 512 * k) % pitch) - pitch / 2)
            gy = abs(((y - 512 * k) % pitch) - pitch / 2)
            g = max(min(max(pitch / 2 - gx - (pitch / 2 - 1.2 * k), 0), 1), min(max(pitch / 2 - gy - (pitch / 2 - 1.2 * k), 0), 1))
            if g > 0:
                c = [c[i] + (GRID[i] - c[i]) * g for i in range(3)]
            for lx0, lx1, ly, op in layers:
                cx = min(max(x, lx0), lx1)
                dl = math.hypot(x - cx, y - ly) - half
                cov = min(max(0.5 - dl, 0.0), 1.0) * op
                if cov > 0:
                    c = [c[i] + (BRASS[i] - c[i]) * cov for i in range(3)]
            row += bytes((round(c[0]), round(c[1]), round(c[2]), round(255 * a)))
        rows.append(bytes(row))
    return rows


if __name__ == "__main__":
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 1024
    png(sys.argv[1], size, render(size))
