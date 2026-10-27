"""Check the BOX-3 mascot animations (HIMMEL-7815).

Fails unless mascots.yaml loads every <agent>_<state>.gif next to this script as an
RGB565 animation with an fps, every GIF decodes to opaque square frames of one shared
size, has the frames its state needs, never repeats a frame back to back (Pillow would
merge the pair, and ESPHome plays every frame for the same time), uses only colours from
palette.gpl, and every palette colour survives an RGB565 round trip. The clips are stored
in flash as raw RGB565, so their total must fit FLASH_BUDGET. Stdlib only:

  python3 -I check_sprites.py
"""

import math
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
AGENTS = ("claude", "codex", "grokbot")
# state -> the least frames that reads as motion (idle breathes and blinks: 8 to 12)
STATES = {"idle": (8, 12), "listen": (4, 16), "think": (4, 16), "speak": (4, 16), "error": (2, 8)}
FRAME_SIZES = range(96, 129)
MAX_COLOURS = 32
FLASH_BUDGET = 1_500_000  # bytes of RGB565 across every frame of every clip


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


def lzw_decode(data, min_size, count):
    clear, end = 1 << min_size, (1 << min_size) + 1
    table = [bytes([i]) for i in range(clear)] + [b"", b""]
    size, prev, out = min_size + 1, None, bytearray()
    bits, nbits, pos = int.from_bytes(data, "little"), len(data) * 8, 0
    while True:
        if pos + size > nbits:
            raise ValueError("image data has no end code")
        code = (bits >> pos) & ((1 << size) - 1)
        pos += size
        if code == clear:
            table, size, prev = table[:clear + 2], min_size + 1, None
            continue
        if code == end:
            break
        if prev is None:
            entry = table[code]
        elif code < len(table):
            entry = table[code]
            table.append(prev + entry[:1])
        elif code == len(table):
            entry = prev + prev[:1]
            table.append(entry)
        else:
            raise ValueError(f"bad LZW code {code}")
        out += entry
        prev = entry
        if len(table) == 1 << size and size < 12:
            size += 1
    if len(out) != count:
        raise ValueError(f"image data holds {len(out)} pixels, want {count}")
    return bytes(out)


def sub_blocks(data, pos):
    body = bytearray()
    while True:
        n = data[pos]
        pos += 1
        if n == 0:
            return bytes(body), pos
        body += data[pos:pos + n]
        pos += n


def colour_table(data, pos, flags):
    n = 2 << (flags & 7)
    return [tuple(data[pos + 3 * i:pos + 3 * i + 3]) for i in range(n)], pos + 3 * n


def read_gif(path):
    """Return (width, height, frames), each frame a tuple of RGB tuples after compositing."""
    data = path.read_bytes()
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError("not a GIF")
    width, height = int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if not width or not height:
        raise ValueError(f"empty image {width}x{height}")
    flags, pos = data[10], 13
    global_table = None
    if flags & 0x80:
        global_table, pos = colour_table(data, pos, flags)
    canvas, frames = [None] * (width * height), []
    disposal, transparent = 0, None
    while True:
        kind = data[pos]
        pos += 1
        if kind == 0x3B:
            break
        if kind == 0x21:
            label = data[pos]
            body, pos = sub_blocks(data, pos + 1)
            if label == 0xF9:
                disposal = (body[0] >> 2) & 7
                transparent = body[3] if body[0] & 1 else None
            continue
        if kind != 0x2C:
            raise ValueError(f"unknown GIF block 0x{kind:02x}")
        left, top, w, h = (int.from_bytes(data[pos + i:pos + i + 2], "little") for i in (0, 2, 4, 6))
        flags = data[pos + 8]
        pos += 9
        table = global_table
        if flags & 0x80:
            table, pos = colour_table(data, pos, flags)
        if flags & 0x40:
            raise ValueError("interlaced frame")
        if table is None:
            raise ValueError("frame without a colour table")
        if left + w > width or top + h > height:
            raise ValueError("frame outside the image")
        min_size = data[pos]
        body, pos = sub_blocks(data, pos + 1)
        before = list(canvas)
        indices = lzw_decode(body, min_size, w * h)
        for y in range(h):
            for x in range(w):
                index = indices[y * w + x]
                if index != transparent:
                    canvas[(top + y) * width + left + x] = table[index]
        if None in canvas:
            raise ValueError(f"frame {len(frames) + 1} has transparent pixels")
        frames.append(tuple(canvas))
        if disposal == 2:
            for y in range(top, top + h):
                canvas[y * width + left:y * width + left + w] = [None] * w
        elif disposal == 3:
            canvas = before
        disposal, transparent = 0, None
    if not frames:
        raise ValueError("no frames")
    return width, height, frames


def read_package(path):
    """Return ({name: value} substitutions, {id: (platform, file, type)} images) from mascots.yaml."""
    subs, images, section, current = {}, {}, None, None
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            section = line.rstrip(":").strip()
            continue
        if section == "substitutions":
            match = re.fullmatch(r"\s+(\w+):\s*\"?([^\"]*)\"?\s*", line)
            if match:
                subs[match[1]] = match[2]
        elif section == "image":
            match = re.fullmatch(r"\s+(-\s+)?(\w+):\s*(\S+)\s*", line)
            if not match:
                continue
            if match[1]:
                current = {}
            current[match[2]] = match[3]
            if "id" in current:
                images[current["id"]] = current
    return subs, images


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
    package = HERE / "mascots.yaml"
    subs, images = read_package(package) if package.exists() else ({}, {})
    if not package.exists():
        errors.append("mascots.yaml missing")
    for key in ("mascot_x", "mascot_y"):
        if not subs.get(key, "").isdigit():
            errors.append(f"mascots.yaml: substitution {key} missing or not a number")
    sizes, total = set(), 0
    for agent in AGENTS:
        for state, (least, most) in STATES.items():
            name = f"{agent}_{state}"
            try:
                fps = float(subs.get(f"fps_{name}", ""))
                if not math.isfinite(fps) or fps <= 0:
                    raise ValueError
            except ValueError:
                errors.append(f"mascots.yaml: fps_{name} missing or not a positive number")
            want = {"id": name, "platform": "animation", "file": f"assets/sprites/{name}.gif", "type": "RGB565"}
            if images.get(name) != want:
                errors.append(f"mascots.yaml: image {name} is {images.get(name)}, want {want}")
            path = HERE / f"{name}.gif"
            if not path.exists():
                errors.append(f"{path.name} missing")
                continue
            try:
                width, height, frames = read_gif(path)
            except (ValueError, IndexError, TypeError) as exc:
                # A truncated or malformed GIF fails this clip and the check goes on.
                errors.append(f"{path.name}: {exc}")
                continue
            sizes.add((width, height))
            total += len(frames) * width * height * 2
            if width != height or width not in FRAME_SIZES:
                errors.append(f"{path.name}: frame {width}x{height}, want square {FRAME_SIZES[0]} to {FRAME_SIZES[-1]}")
            if not least <= len(frames) <= most:
                errors.append(f"{path.name}: {len(frames)} frames, want {least} to {most}")
            for i, frame in enumerate(frames):
                if len(frames) > 1 and frame == frames[(i + 1) % len(frames)]:
                    errors.append(f"{path.name}: frames {i + 1} and {(i + 1) % len(frames) + 1} are identical")
            stray = set().union(*map(set, frames)) - set(palette)
            if stray:
                errors.append(f"{path.name}: {len(stray)} colours not in palette.gpl, e.g. {sorted(stray)[0]}")
    if len(sizes) > 1:
        errors.append(f"clips use different frame sizes: {sorted(sizes)}")
    if total > FLASH_BUDGET:
        errors.append(f"clips take {total} bytes of RGB565, over the {FLASH_BUDGET} byte budget")
    for line in errors:
        print(f"FAIL {line}")
    if errors:
        return 1
    size = sizes.pop()[0]
    print(f"PASS {len(AGENTS) * len(STATES)} clips, frame {size}px, {total} of {FLASH_BUDGET} RGB565 bytes, "
          f"{len(palette)} RGB565-exact colours")
    return 0


if __name__ == "__main__":
    sys.exit(main())
