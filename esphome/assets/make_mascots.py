"""Draw the three BOX-3 mascots as pixel art (HIMMEL-7815).

Each mascot is an original character drawn on a 32x32 grid in one shared palette whose
colours are exact in RGB565, then scaled 4x (nearest) to 128x128. Output, next to this
script: <mascot>.gif with 8 frames in the order the firmware expects (idle 0-1,
listen 2-3, think 4-5, speak 6-7), palette.gpl, and mascots.png, a preview sheet with
one row per mascot.

  python3 -I make_mascots.py      # needs Pillow
"""

import pathlib

from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
GRID = 32
SCALE = 4
STATES = ("idle", "listen", "think", "speak")

# Every channel is on the RGB565 grid (red and blue multiples of 8, green of 4), so the
# firmware shows exactly these colours. "." is the screen background.
PALETTE = {
    ".": ("background", (16, 20, 32)),
    "k": ("outline", (8, 8, 16)),
    "w": ("white", (248, 252, 248)),
    "a": ("amber", (248, 200, 72)),
    "p": ("cheek", (240, 136, 152)),
    "s": ("sound", (120, 200, 248)),
    "o": ("clay light", (240, 164, 120)),
    "O": ("clay", (216, 120, 88)),
    "q": ("clay shade", (160, 80, 56)),
    "c": ("cream", (248, 228, 192)),
    "g": ("case light", (216, 220, 216)),
    "G": ("case shade", (136, 140, 144)),
    "d": ("screen", (24, 44, 40)),
    "e": ("terminal green", (80, 224, 136)),
    "m": ("slate light", (120, 124, 136)),
    "M": ("slate", (72, 76, 88)),
    "v": ("visor", (32, 24, 48)),
    "V": ("visor glow", (176, 132, 248)),
    "u": ("antenna tip", (232, 92, 120)),
}


def blank():
    return [["."] * GRID for _ in range(GRID)]


def put(grid, x, y, rows):
    """Stamp an ASCII patch with its top-left at (x, y); a space leaves the pixel alone."""
    for dy, row in enumerate(rows):
        for dx, ch in enumerate(row):
            if ch != " ":
                # A negative index would wrap to the far edge instead of failing.
                assert 0 <= x + dx < GRID and 0 <= y + dy < GRID, (x + dx, y + dy)
                grid[y + dy][x + dx] = ch


def rect(grid, x0, y0, x1, y1, ch, over=None):
    """Fill a rectangle; with `over`, only the pixels that hold that colour."""
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            if over is None or grid[y][x] == over:
                grid[y][x] = ch


def blob(grid, cx, cy, rx, ry, fill, edge):
    """A filled ellipse on the pixel grid with a one-pixel outline."""
    inside = set()
    for y in range(GRID):
        for x in range(GRID):
            if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0:
                inside.add((x, y))
    for x, y in inside:
        border = any((x + dx, y + dy) not in inside for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
        grid[y][x] = edge if border else fill


# Effects shared by all three mascots, drawn beside the body.
ARC = ["  s", " s ", "s  ", "s  ", "s  ", " s ", "  s"]  # bulges left; mirrored on the right


def sound_arcs(grid, frame, left, right, y):
    """One sound arc each side of the body, and a second one further out on frame 1."""
    outer = ["  s", " s ", "s  ", "s  ", "s  ", "s  ", "s  ", " s ", "  s"]  # two rows taller
    for x0, x1, top, arc in ((left - 2, right, y, ARC), (left - 4, right + 2, y - 1, outer))[:frame + 1]:
        put(grid, x0, top, arc)
        put(grid, x1, top, [r[::-1] for r in arc])


def think_dots(grid, frame, x, y):
    """Three thought dots that fill in one by one."""
    for i in range(3):
        on = i <= frame + 1
        put(grid, x + 3 * i, y - i, ["aa", "aa"] if on else ["k ", "  "])


# --- Clay: a round clay sprite with a leaf-like spark on its head (Claude) -------------

def clay(state, frame):
    g = blank()
    sprout = ["  a  ", " aoa ", "aoOoa", " aoa ", "  a  "]
    put(g, 13, 2 - (1 if state == "listen" else 0), sprout)
    blob(g, 15.5, 17, 10, 9, "O", "k")
    rect(g, 9, 11, 13, 11, "o", over="O")  # highlight
    rect(g, 8, 12, 10, 13, "o", over="O")
    blob(g, 15.5, 19, 6, 4, "c", "c")  # face patch
    put(g, 9, 25, ["kqk", "kqk"])
    put(g, 20, 25, ["kqk", "kqk"])
    if state == "idle" and frame == 1:
        eyes = ["kk    kk"]  # blink
        put(g, 12, 18, eyes)
    elif state == "think":
        put(g, 13, 16, ["kw   kw", "kk   kk"])  # looking up
    elif state == "listen":
        put(g, 12, 16, ["kk    kk", "kw    kw", "kk    kk"])
    else:
        put(g, 12, 17, ["kw    kw", "kk    kk"])
    put(g, 10, 20, ["p"])
    put(g, 21, 20, ["p"])
    if state == "speak":
        put(g, 14, 21, ["kkkk", "kppk", " kk "] if frame == 0 else ["kkkk"])
    elif state == "think":
        put(g, 15, 21, ["kk"])
    else:
        put(g, 14, 21, ["k  k", " kk "])
    if state == "listen":
        sound_arcs(g, frame, 4, 27, 13)
    if state == "think":
        think_dots(g, frame, 22, 6)
    return g


# --- Term: a little terminal robot with a prompt for a face (Codex) -------------------

def term(state, frame):
    g = blank()
    tip = "a" if state == "listen" and frame == 0 else "u"
    put(g, 15, 2, [tip + tip, "kk", "kk"])
    rect(g, 6, 5, 25, 23, "g")
    rect(g, 6, 5, 25, 5, "k")
    rect(g, 6, 23, 25, 23, "k")
    rect(g, 6, 5, 6, 23, "k")
    rect(g, 25, 5, 25, 23, "k")
    rect(g, 24, 6, 24, 22, "G")
    rect(g, 7, 22, 24, 22, "G")
    rect(g, 9, 8, 22, 19, "d")  # screen
    rect(g, 9, 8, 22, 8, "k")
    rect(g, 9, 8, 9, 19, "k")
    put(g, 20, 21, ["ee"])  # power light
    put(g, 9, 24, ["kGk", "kGk"])
    put(g, 20, 24, ["kGk", "kGk"])
    put(g, 7, 26, ["kkkkk"])
    put(g, 18, 26, ["kkkkk"])
    if state == "idle" and frame == 1:
        put(g, 11, 12, ["eee   eee"])
    elif state == "think":
        put(g, 12, 11, ["ee  ee", "ee  ee"])
    else:
        put(g, 11, 11, ["e      e", " e    e ", "e      e"] if state == "listen" else
            ["ee    ee", "ee    ee"])
    if state == "speak":
        bars = ["e e e e", "eeeeeee"] if frame == 0 else ["eeeeeee"]
        put(g, 12, 16, bars)
    elif state == "think":
        put(g, 12, 16, ["e e e" if frame == 0 else " e e "])
    else:
        cursor = ["eeee"] if not (state == "idle" and frame == 1) else ["    "]
        put(g, 14, 17, cursor)
    if state == "listen":
        sound_arcs(g, frame, 4, 27, 10)
    if state == "think":
        think_dots(g, frame, 22, 3)
    return g


# --- Visor: a slate dome robot with one wide glowing visor (Grokbot) -------------------

def visor(state, frame):
    g = blank()
    put(g, 21, 1, ["uu", "uu", " k", " k"] if not (state == "listen" and frame == 1) else
        ["aa", "aa", " k", " k"])
    blob(g, 15.5, 16, 10, 10, "M", "k")
    rect(g, 9, 10, 12, 10, "m", over="M")
    rect(g, 8, 11, 9, 13, "m", over="M")
    rect(g, 8, 14, 23, 19, "v")  # visor band
    rect(g, 7, 14, 7, 19, "k")
    rect(g, 24, 14, 24, 19, "k")
    glow = "V"
    if state == "think":
        x = 10 + (frame * 6)
        put(g, x, 16, [glow * 4, glow * 4])  # scanning
    elif state == "idle" and frame == 1:
        put(g, 10, 17, [glow * 12])
    else:
        put(g, 10, 15, [glow * 4 + "    " + glow * 4, glow * 4 + "    " + glow * 4])
        if state == "listen":
            put(g, 10, 17, ["w" * 4 + "    " + "w" * 4])
    if state == "speak":
        put(g, 12, 21, ["kkkkkkk", "kvvvvvk", "kkkkkkk"] if frame == 0 else ["kkkkkkk"])
    else:
        put(g, 13, 22, ["kkkkk", "    k"])  # a lopsided grin
    put(g, 9, 26, ["kmk", "kmk"])
    put(g, 20, 26, ["kmk", "kmk"])
    if state == "listen":
        sound_arcs(g, frame, 4, 27, 12)
    if state == "think":
        think_dots(g, frame, 2, 4)
    return g


MASCOTS = {"claude": clay, "codex": term, "grokbot": visor}


def frames(draw):
    return [draw(state, f) for state in STATES for f in (0, 1)]


def to_image(grid):
    img = Image.new("RGB", (GRID, GRID))
    img.putdata([PALETTE[ch][1] for row in grid for ch in row])
    return img.resize((GRID * SCALE, GRID * SCALE), Image.Resampling.NEAREST)


def main():
    lines = ["GIMP Palette", "Name: himmel-voice BOX-3 mascots (RGB565-exact)", "Columns: 4", "#"]
    lines += [f"{r:3d} {g:3d} {b:3d}\t{name}" for name, (r, g, b) in PALETTE.values()]
    (HERE / "palette.gpl").write_text("\n".join(lines) + "\n")
    sheet = Image.new("RGB", (8 * GRID * SCALE, len(MASCOTS) * GRID * SCALE))
    for row, (name, draw) in enumerate(MASCOTS.items()):
        imgs = [to_image(f) for f in frames(draw)]
        for col, img in enumerate(imgs):
            sheet.paste(img, (col * GRID * SCALE, row * GRID * SCALE))
        imgs[0].save(HERE / f"{name}.gif", save_all=True, append_images=imgs[1:], duration=400,
                     loop=0, optimize=False, disposal=1)
    sheet.save(HERE / "mascots.png", optimize=True)


if __name__ == "__main__":
    main()
