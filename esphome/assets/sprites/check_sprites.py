"""Check the BOX-3 mascot sprite strips (HIMMEL-7815).

Fails unless every <agent>_<state>.png strip exists next to this script, all strips share
one square frame size with 2 to 4 frames side by side, every pixel is a colour from
palette.gpl, and every palette colour survives an RGB565 round trip. Stdlib only:

  python3 -I check_sprites.py
"""

import pathlib
import struct
import sys
import zlib

HERE = pathlib.Path(__file__).resolve().parent
AGENTS = ("claude", "codex", "grokbot")
STATES = ("idle", "listen", "think", "speak")
MAX_COLOURS = 32


def read_palette(path):
    colours = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 3 and all(p.isdigit() for p in parts[:3]):
            colours.append(tuple(int(p) for p in parts[:3]))
    return colours


def rgb565_round_trip(rgb):
    r, g, b = rgb
    word = (r >> 3) << 11 | (g >> 2) << 5 | b >> 3
    return ((word >> 11) & 0x1F) << 3, ((word >> 5) & 0x3F) << 2, (word & 0x1F) << 3


def unfilter(raw, width, height, bpp):
    stride = width * bpp
    rows, prev, pos = [], bytearray(stride), 0
    for _ in range(height):
        kind, line = raw[pos], bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if kind == 1:
                line[i] = (line[i] + a) & 0xFF
            elif kind == 2:
                line[i] = (line[i] + b) & 0xFF
            elif kind == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
            elif kind != 0:
                raise ValueError(f"unknown PNG filter {kind}")
        rows.append(bytes(line))
        prev = line
    return rows


def read_png(path):
    """Return (width, height, set of opaque RGB colours) for an 8-bit RGB, RGBA or indexed PNG."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, plte = 8, b"", None
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if data[pos + 8 + length:pos + 12 + length] != struct.pack(">I", zlib.crc32(kind + body)):
            raise ValueError(f"bad CRC on {kind.decode(errors='replace')} chunk")
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            plte = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif kind == b"tRNS":
            raise ValueError("transparency chunk present; strips are opaque")
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    else:
        raise ValueError("no IEND chunk; the file is truncated")
    if not width or not height:
        raise ValueError(f"empty image {width}x{height}")
    if depth != 8 or interlace or ctype not in (2, 3, 6):
        raise ValueError(f"unsupported PNG (depth {depth}, colour type {ctype}, interlace {interlace})")
    bpp = {2: 3, 3: 1, 6: 4}[ctype]
    colours = set()
    for row in unfilter(zlib.decompress(idat), width, height, bpp):
        for i in range(0, len(row), bpp):
            px = row[i:i + bpp]
            if ctype == 3:
                colours.add(plte[px[0]])
            else:
                if ctype == 6 and px[3] != 255:
                    raise ValueError("pixel with alpha below 255")
                colours.add(tuple(px[:3]))
    return width, height, colours


def main():
    errors = []
    palette_path = HERE / "palette.gpl"
    palette = read_palette(palette_path) if palette_path.exists() else []
    if not palette:
        errors.append("palette.gpl missing or empty")
    if len(palette) > MAX_COLOURS:
        errors.append(f"palette has {len(palette)} colours, more than {MAX_COLOURS}")
    for rgb in palette:
        if rgb565_round_trip(rgb) != rgb:
            errors.append(f"palette colour {rgb} is not exact in RGB565")
    frame_sizes = set()
    for agent in AGENTS:
        for state in STATES:
            path = HERE / f"{agent}_{state}.png"
            if not path.exists():
                errors.append(f"{path.name} missing")
                continue
            try:
                width, height, colours = read_png(path)
            except (ValueError, IndexError, TypeError, NameError, zlib.error, struct.error) as exc:
                # A truncated or malformed PNG fails this strip and the check goes on.
                errors.append(f"{path.name}: {exc}")
                continue
            frames, rest = divmod(width, height)
            if rest or not 2 <= frames <= 4:
                errors.append(f"{path.name}: {width}x{height} is not 2 to 4 square frames")
            frame_sizes.add(height)
            stray = colours - set(palette)
            if stray:
                errors.append(f"{path.name}: {len(stray)} colours not in palette.gpl, e.g. {sorted(stray)[0]}")
    if len(frame_sizes) > 1:
        errors.append(f"strips use different frame sizes: {sorted(frame_sizes)}")
    for line in errors:
        print(f"FAIL {line}")
    if errors:
        return 1
    print(f"PASS {len(AGENTS) * len(STATES)} strips, frame {frame_sizes.pop()}px, {len(palette)} RGB565-exact colours")
    return 0


if __name__ == "__main__":
    sys.exit(main())
