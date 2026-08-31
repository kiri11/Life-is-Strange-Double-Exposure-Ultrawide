#!/usr/bin/env python3
"""Generate LiSUltrawidePatcher.ico - no third-party imaging libraries.

The mark: an ultrawide screen (white rounded bar) with two arrows pushing
outwards from a 16:9 centre - the fix's whole idea in one shape. Drawn with
4x4 supersampling, written as PNG-compressed ICO entries (Vista+).

    python tools/make_icon.py
"""
import os
import struct
import zlib

SIZES = (16, 24, 32, 48, 64, 128, 256)

VIOLET = (0x6D, 0x4A, 0xFF)
TEAL = (0x00, 0xC2, 0xA8)
INK = (0x14, 0x0C, 0x2E)
WHITE = (0xFF, 0xFF, 0xFF)


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def rounded_rect(px, py, hw, hh, r):
    """True if (px,py) is inside a rounded rectangle centred on the origin."""
    dx = abs(px) - (hw - r)
    dy = abs(py) - (hh - r)
    if dx <= 0 or dy <= 0:
        return abs(px) <= hw and abs(py) <= hh
    return dx * dx + dy * dy <= r * r


def triangle(px, py, ax, ay, bx, by, cx, cy):
    def side(x1, y1, x2, y2):
        return (px - x2) * (y1 - y2) - (x1 - x2) * (py - y2)
    d1 = side(ax, ay, bx, by)
    d2 = side(bx, by, cx, cy)
    d3 = side(cx, cy, ax, ay)
    neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (neg and pos)


def shade(px, py):
    """-> (r, g, b, a) for a point in normalised [-1, 1] space."""
    # background plate
    if not rounded_rect(px, py, 0.94, 0.94, 0.30):
        return (0, 0, 0, 0)
    t = max(0.0, min(1.0, (px + py + 2.0) / 4.0))
    bg = lerp(VIOLET, TEAL, t)

    # the ultrawide screen
    if rounded_rect(px, py, 0.66, 0.30, 0.09):
        # inner bevel so the shape still reads at 16 px
        if not rounded_rect(px, py, 0.60, 0.24, 0.06):
            return WHITE + (255,)
        # arrows pushing outwards from the 16:9 centre
        for direction in (-1, 1):
            tipx = direction * 0.52
            basex = direction * 0.28
            if triangle(px, py, tipx, 0.0, basex, -0.155, basex, 0.155):
                return bg + (255,)
        # the 16:9 frame left behind in the middle (landscape, not portrait)
        if rounded_rect(px, py, 0.15, 0.084, 0.025):
            return INK + (255,)
        return WHITE + (255,)
    return bg + (255,)


def render(size, ss=4):
    """Supersampled RGBA bytes for one square icon size."""
    rows = []
    n = size * ss
    inv = 2.0 / n
    for y in range(size):
        row = bytearray()
        for x in range(size):
            r = g = b = a = 0
            for sy in range(ss):
                py = (y * ss + sy + 0.5) * inv - 1.0
                for sx in range(ss):
                    px = (x * ss + sx + 0.5) * inv - 1.0
                    pr, pg, pb, pa = shade(px, py)
                    r += pr * pa
                    g += pg * pa
                    b += pb * pa
                    a += pa
            total = ss * ss
            if a:
                row += bytes((r // a, g // a, b // a, a // total))
            else:
                row += b"\0\0\0\0"
        rows.append(bytes(row))
    return rows


def png(size, rows):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = b"".join(b"\0" + r for r in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def main():
    images = []
    for size in SIZES:
        print("  rendering {0}x{0}...".format(size))
        images.append((size, png(size, render(size))))

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                               len(data), offset)
        blobs += data
        offset += len(data)

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "LiSUltrawidePatcher.ico")
    with open(out, "wb") as f:
        f.write(header + entries + blobs)
    print("wrote {} ({} bytes, {} sizes)".format(out, os.path.getsize(out),
                                                 len(images)))


if __name__ == "__main__":
    main()
