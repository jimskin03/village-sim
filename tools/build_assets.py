"""Build Lantern Bay's grid art.

One locked style: three-quarter top-down, evening light from the upper left,
dark outlines, warm plaster and thatch, cool water. West-facing villagers are
mirrors of the east view. Run from the repo root:

    python tools/build_assets.py

Writes village_sim/static/assets/ and a preview in /tmp.
"""

from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from village_sim.world import World, inside  # noqa: E402

OUTLINE = (32, 26, 22, 255)
GRASS = (72, 111, 74, 255)
GRASS_D = (63, 100, 66, 255)
GRASS_L = (88, 127, 79, 255)
PATH = (152, 125, 88, 255)
PATH_D = (134, 107, 76, 255)
SAND = (201, 181, 137, 255)
SAND_D = (188, 164, 122, 255)
WATER = (
    (18, 64, 94, 255),
    (22, 76, 108, 255),
    (20, 70, 100, 255),
    (28, 86, 118, 255),
)
WOOD = (132, 86, 48, 255)
WOOD_D = (96, 60, 34, 255)
WOOD_L = (164, 112, 66, 255)
THATCH = (178, 118, 58, 255)
THATCH_D = (142, 86, 40, 255)
THATCH_L = (206, 148, 80, 255)
WALL = (232, 214, 180, 255)
WALL_D = (186, 160, 126, 255)
TIMBER = (96, 62, 40, 255)
DOOR = (112, 68, 40, 255)
STONE = (132, 136, 132, 255)
STONE_D = (90, 94, 92, 255)
STONE_L = (176, 178, 170, 255)
LEAF = (48, 112, 60, 255)
LEAF_D = (32, 80, 46, 255)
LEAF_L = (86, 146, 78, 255)
SKIN = (230, 186, 148, 255)
SKIN_D = (196, 148, 112, 255)
INK = (42, 32, 28, 255)
FOAM = (220, 234, 236, 255)
FLAME = (255, 168, 56, 255)
FLAME_L = (255, 220, 128, 255)
FRESH = (72, 150, 190, 255)
MURK = (86, 120, 72, 255)
LANTERN = (255, 198, 96, 255)
FISH = (186, 198, 206, 255)
ROPE = (150, 124, 86, 255)

# Tunic colors are the villager colors from the simulation, so the map and the
# cards name the same person. Light comes from the upper left; the right side
# of every form is the shade.
PEOPLE = {
    "mara": {"tunic": (245, 184, 107), "hair": (62, 40, 32), "style": "bun", "hat": "straw", "role": "fisher"},
    "tom": {"tunic": (145, 199, 203), "hair": (92, 62, 40), "style": "short", "hat": None, "role": "boat"},
    "lin": {"tunic": (186, 163, 229), "hair": (32, 28, 36), "style": "long", "hat": None, "role": "garden"},
    "ada": {"tunic": (231, 151, 158), "hair": (48, 32, 36), "style": "short", "hat": "scarf", "role": "fisher"},
    "ivo": {"tunic": (167, 199, 140), "hair": (70, 74, 64), "style": "hood", "hat": None, "role": "garden"},
    "nell": {"tunic": (216, 165, 207), "hair": (120, 72, 48), "style": "braid", "hat": None, "role": "boat"},
    "ren": {"tunic": (211, 204, 135), "hair": (40, 32, 28), "style": "short", "hat": "cap", "role": "fisher"},
    "bea": {"tunic": (135, 183, 226), "hair": (88, 52, 36), "style": "bun", "hat": None, "role": "garden"},
    "visitor": {"tunic": (223, 201, 155), "hair": (60, 48, 40), "style": "hood", "hat": None, "role": "visitor"},
}

LINKS = (
    ("guest_home", "fire"),
    ("fire", "food_center"),
    ("food_center", "mara_home"),
    ("mara_home", "rain_barrel"),
    ("rain_barrel", "nets"),
    ("nets", "well"),
    ("well", "garden"),
    ("garden", "lin_home"),
    ("garden", "tom_home"),
    ("nets", "bench"),
    ("bench", "shore"),
    ("shore", "water_center"),
    ("water_center", "fire"),
    ("bench", "jetty"),
)


def mix(color, factor):
    return tuple(max(0, min(255, int(channel * factor))) if index < 3 else color[3]
                 for index, channel in enumerate(color))


def opaque(color):
    return (*color[:3], 255) if len(color) == 3 else color


def noise(x, y, seed=0):
    """Stable scattered detail without a visible modulo stripe pattern."""
    n = (x * 374761393 + y * 668265263 + seed * 1442695041) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return (n ^ (n >> 16)) & 0xFFFF


class Pix:
    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.px = bytearray(w * h * 4)

    def get(self, x, y):
        if x < 0 or y < 0 or x >= self.w or y >= self.h:
            return (0, 0, 0, 0)
        i = (y * self.w + x) * 4
        return tuple(self.px[i:i + 4])

    def set(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h and color[3]:
            i = (y * self.w + x) * 4
            self.px[i:i + 4] = bytes(opaque(color))

    def rect(self, x, y, w, h, color):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.set(xx, yy, color)

    def hline(self, x0, x1, y, color):
        if x1 < x0:
            x0, x1 = x1, x0
        for x in range(x0, x1 + 1):
            self.set(x, y, color)

    def vline(self, x, y0, y1, color):
        if y1 < y0:
            y0, y1 = y1, y0
        for y in range(y0, y1 + 1):
            self.set(x, y, color)

    def ellipse(self, cx, cy, rx, ry, color):
        if rx <= 0 or ry <= 0:
            self.set(cx, cy, color)
            return
        for y in range(cy - ry, cy + ry + 1):
            for x in range(cx - rx, cx + rx + 1):
                if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1:
                    self.set(x, y, color)

    def blit(self, src, dx, dy):
        for y in range(src.h):
            ty = dy + y
            if ty < 0 or ty >= self.h:
                continue
            for x in range(src.w):
                tx = dx + x
                if tx < 0 or tx >= self.w:
                    continue
                i = (y * src.w + x) * 4
                if src.px[i + 3] == 0:
                    continue
                j = (ty * self.w + tx) * 4
                self.px[j:j + 4] = src.px[i:i + 4]

    def fill(self, color):
        self.rect(0, 0, self.w, self.h, color)


def outline(p, color=OUTLINE):
    src = bytes(p.px)
    w, h = p.w, p.h

    def alpha(x, y):
        if x < 0 or y < 0 or x >= w or y >= h:
            return 0
        return src[(y * w + x) * 4 + 3]

    for y in range(h):
        for x in range(w):
            if alpha(x, y):
                continue
            if alpha(x - 1, y) or alpha(x + 1, y) or alpha(x, y - 1) or alpha(x, y + 1):
                p.set(x, y, color)


def flip(p):
    out = Pix(p.w, p.h)
    for y in range(p.h):
        for x in range(p.w):
            out.set(p.w - 1 - x, y, p.get(x, y))
    return out


def shade_right(p, color, x0):
    """Darken the right portion of whatever is already drawn, for the upper-left light."""
    dark = mix(color, 0.78)
    for y in range(p.h):
        for x in range(max(0, x0), p.w):
            if p.get(x, y)[3]:
                p.set(x, y, dark)


def write_png(path, pix):
    raw = b"".join(b"\x00" + bytes(pix.px[y * pix.w * 4:(y + 1) * pix.w * 4]) for y in range(pix.h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", pix.w, pix.h, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    )


def assert_clear_border(p, name):
    for x in range(p.w):
        if p.get(x, 0)[3] or p.get(x, p.h - 1)[3]:
            raise SystemExit(f"{name} touches the top or bottom border")
    for y in range(p.h):
        if p.get(0, y)[3] or p.get(p.w - 1, y)[3]:
            raise SystemExit(f"{name} touches the side border")
    if not any(p.px[i + 3] for i in range(0, len(p.px), 4)):
        raise SystemExit(f"{name} is empty")


def assert_solid_border(p, color, name):
    for x in range(p.w):
        if p.get(x, 0) != color or p.get(x, p.h - 1) != color:
            raise SystemExit(f"{name} border does not tile")
    for y in range(p.h):
        if p.get(0, y) != color or p.get(p.w - 1, y) != color:
            raise SystemExit(f"{name} border does not tile")


# --- tiles -----------------------------------------------------------------

def grass_tile(kind):
    p = Pix(32, 32)
    p.fill(GRASS)
    for y in range(2, 30):
        for x in range(2, 30):
            n = noise(x, y, kind) % 90
            if n < 3:
                p.set(x, y, GRASS_D)
            elif n == 3:
                p.set(x, y, GRASS_L)
    for x, y in ((7 + kind * 2, 10), (23 - kind, 24)):
        p.set(x, y, GRASS_L)
        p.set(x - 1, y - 1, GRASS_L)
        p.set(x + 1, y - 2, GRASS_L)
        p.set(x + 1, y + 1, GRASS_D)
    if kind == 2:
        p.set(14, 17, LEAF_D)
        p.set(14, 16, (234, 167, 147, 255))
        p.set(15, 16, (241, 212, 146, 255))
        p.set(22, 7, (224, 227, 186, 255))
    return p


def path_tile(variant):
    p = Pix(32, 32)
    p.fill(PATH)
    for y in range(2, 30):
        for x in range(2, 30):
            n = noise(x, y, 7 + variant) % 80
            if n < 2:
                p.set(x, y, PATH_D)
            elif n == 2:
                p.set(x, y, (173, 147, 107, 255))
    for x, y in ((8 + variant * 7, 9), (23 - variant * 4, 24)):
        p.hline(x, x + 2, y, (181, 157, 115, 255))
        p.hline(x + 1, x + 3, y + 1, PATH_D)
    return p


def sand_tile(variant):
    p = Pix(32, 32)
    p.fill(SAND)
    for y in range(2, 30):
        for x in range(2, 30):
            n = noise(x, y, 11 + variant) % 100
            if n < 2:
                p.set(x, y, SAND_D)
            elif n == 2:
                p.set(x, y, (221, 202, 160, 255))
    if variant == 1:
        p.set(20, 18, (236, 230, 220, 255))
        p.set(21, 18, (236, 230, 220, 255))
    return p


def water_tile(shift):
    p = Pix(32, 32)
    p.fill((22, 78, 102, 255))
    for x, y, length in ((5, 9, 7), (20, 25, 5), (2, 29, 3)):
        p.hline(x + shift, x + length + shift, y, (27, 86, 109, 255))
        p.hline(x + 2 + shift, x + length - 1 + shift, y + 1, (24, 81, 105, 255))
    return p


def pier_tile():
    p = Pix(32, 32)
    for y in range(32):
        band = y % 8
        color = WOOD_D if band in (6, 7) else WOOD_L if band == 0 else WOOD
        p.hline(0, 31, y, color)
    for x in (6, 16, 26):
        for y in range(2, 30, 8):
            p.set(x, y + 2, WOOD_D)
    for y in range(3, 32, 8):
        p.hline(2 + y % 5, 12 + y % 6, y, (144, 97, 57, 255))
        p.hline(19, 26, y + 1, (116, 76, 45, 255))
    return p


def foam_tile(side):
    p = Pix(32, 32)
    for i in (2, 3, 10, 11, 19, 25, 26):
        if side == "n":
            p.set(i, 2, FOAM)
        elif side == "s":
            p.set(i, 29, FOAM)
        elif side == "w":
            p.set(2, i, FOAM)
        else:
            p.set(29, i, FOAM)
    return p


# --- buildings -------------------------------------------------------------

def thatch(p, peak_y, eave_y, left, right):
    span = max(1, eave_y - peak_y)
    width = right - left
    for y in range(peak_y, eave_y + 1):
        t = (y - peak_y) / span
        half = int(4 + t * (width / 2 - 4))
        cx = (left + right) // 2
        color = THATCH_D if (y - peak_y) % 6 == 5 else THATCH_L if (y - peak_y) % 6 == 0 else THATCH
        p.hline(cx - half, cx + half, y, color)
        p.hline(cx + half // 3, cx + half, y, mix(color, 0.79))
        for x in range(cx - half + 2, cx + half - 1):
            if noise(x, y, 4) % 8 == 0:
                p.set(x, y, mix(color, 1.12 if x < cx else 0.9))
    p.hline(left, right, eave_y, (101, 66, 40, 255))
    for x in range(left + 2, right - 1, 3):
        p.set(x, eave_y + 1, THATCH_D)


def framed_window(p, x, y, width=8, height=9):
    p.rect(x - 1, y - 1, width + 2, height + 2, TIMBER)
    p.rect(x, y, width, height, (239, 166, 75, 255))
    p.rect(x, y, width - 1, height // 2, (255, 220, 137, 255))
    p.vline(x + width // 2, y, y + height - 1, TIMBER)
    p.hline(x, x + width - 1, y + height // 2, TIMBER)
    p.hline(x - 2, x + width + 1, y + height + 1, WOOD_L)


def masonry(p, left, right, y, rows=1):
    for row in range(rows):
        p.hline(left, right, y + row * 3, STONE_D)
        p.hline(left, right, y + row * 3 + 1, STONE)
        for x in range(left + row * 3, right, 7):
            p.set(x, y + row * 3 + 1, STONE_D)
            p.set(x + 1, y + row * 3 + 2, STONE_L)


def cottage(flowers=False, vines=False, oar=False):
    p = Pix(64, 72)
    p.rect(44, 36, 12, 28, WALL_D)  # right-hand wall
    p.rect(14, 34, 32, 32, WALL)
    p.rect(15, 39, 30, 4, (208, 186, 150, 255))
    p.rect(46, 39, 10, 4, (156, 128, 97, 255))
    for x, y in ((16, 57), (19, 60), (42, 54), (25, 45), (49, 61), (52, 48)):
        p.hline(x, x + 2, y, WALL_D if x < 46 else TIMBER)
    masonry(p, 14, 55, 63)
    p.vline(14, 34, 65, TIMBER)
    p.vline(45, 34, 65, TIMBER)
    p.hline(14, 45, 34, TIMBER)
    p.vline(55, 39, 63, TIMBER)
    framed_window(p, 19, 44)
    p.rect(30, 48, 10, 18, DOOR)
    p.rect(30, 47, 10, 2, TIMBER)
    p.vline(33, 50, 65, WOOD)
    p.vline(37, 50, 65, WOOD_D)
    p.hline(31, 38, 61, WOOD_D)
    p.set(38, 57, LANTERN)
    p.rect(28, 65, 14, 3, STONE)
    p.hline(28, 41, 65, STONE_L)
    if flowers:
        p.rect(17, 55, 12, 3, WOOD_D)
        p.hline(17, 28, 55, WOOD_L)
        for index, color in enumerate(((220, 86, 104, 255), (240, 200, 90, 255), (230, 120, 170, 255))):
            p.set(19 + index * 3, 53, color)
            p.set(18 + index * 3, 54, LEAF)
    if vines:
        for y in range(40, 62, 3):
            p.set(43, y, LEAF_D)
            p.hline(41, 43, y + 1, LEAF)
            p.set(42, y, LEAF_L)
    if oar:
        p.vline(50, 40, 64, WOOD_L)
        p.rect(48, 38, 5, 3, WOOD)
    thatch(p, 16, 40, 8, 56)
    p.rect(46, 8, 6, 16, STONE_D)
    p.rect(47, 8, 4, 4, STONE_L)
    for y in (14, 19):
        p.hline(47, 50, y, STONE)
    p.rect(45, 7, 8, 3, STONE_D)
    p.hline(46, 51, 7, STONE_L)
    p.vline(42, 42, 45, TIMBER)
    p.rect(40, 45, 4, 6, TIMBER)
    p.rect(41, 46, 2, 4, LANTERN)
    p.set(41, 46, FLAME_L)
    outline(p)
    return p


def guest_cottage():
    p = Pix(52, 64)
    p.rect(36, 32, 10, 24, WALL_D)
    p.rect(12, 30, 26, 28, WALL)
    p.rect(13, 34, 24, 3, (208, 186, 150, 255))
    p.vline(12, 34, 57, TIMBER)
    p.vline(37, 34, 57, TIMBER)
    masonry(p, 12, 45, 55)
    framed_window(p, 16, 39, 6, 7)
    p.rect(24, 44, 8, 14, DOOR)
    p.vline(26, 46, 57, WOOD)
    p.set(30, 51, LANTERN)
    p.rect(22, 57, 12, 3, STONE)
    p.hline(22, 33, 57, STONE_L)
    thatch(p, 14, 34, 6, 46)
    p.rect(34, 8, 5, 12, STONE_D)
    p.hline(34, 38, 8, STONE_L)
    p.hline(35, 38, 13, STONE)
    p.rect(7, 51, 4, 6, (163, 99, 67, 255))
    p.ellipse(9, 50, 3, 3, LEAF)
    p.set(8, 48, (234, 167, 147, 255))
    outline(p)
    return p


def jetty_post():
    p = Pix(36, 48)
    p.rect(14, 18, 8, 22, WOOD_D)
    p.rect(16, 18, 3, 22, WOOD_L)
    p.rect(10, 16, 16, 4, WOOD)
    p.rect(11, 28, 14, 2, ROPE)
    p.rect(11, 33, 14, 2, ROPE)
    p.vline(22, 12, 20, WOOD_D)
    p.rect(20, 18, 5, 6, LANTERN)
    outline(p)
    return p


def boats(leaking):
    """Two side-view boats. The sail rises above the shore point; the hulls sit in the water."""
    p = Pix(72, 48)
    sail = (236, 230, 216, 255)
    wet = (42, 92, 118, 255)

    def hull(ox, oy, length, height, with_sail, leak):
        bow = ox + length
        for row in range(height):
            inset = 0 if row < 2 else row - 1
            left = ox + max(0, inset - 1)
            right = bow - inset * 2
            if right - left < 4:
                break
            color = WOOD_L if row < 2 else WOOD_D if row >= height - 2 else WOOD
            p.hline(left, right, oy + row, color)
        p.set(bow + 1, oy + 1, WOOD_D)
        p.hline(ox + 3, bow - 4, oy + 2, WOOD_D)
        if with_sail:
            mast = ox + length // 2
            p.vline(mast, oy - 16, oy + 1, WOOD_D)
            for index, span in enumerate(range(3, 15, 2)):
                p.hline(mast + 1, mast + span, oy - 14 + index, sail)
            p.hline(mast, mast + 12, oy - 1, WOOD)
        else:
            p.hline(ox + 6, ox + length - 6, oy - 1, WOOD_L)
            p.vline(ox + 6, oy - 3, oy - 1, WOOD_L)
            p.vline(ox + length - 6, oy - 3, oy - 1, WOOD_D)
        if leak:
            p.hline(ox + 5, ox + 12, oy + height - 3, wet)
            p.hline(ox + 6, ox + 11, oy + height - 2, wet)

    hull(6, 20, 28, 9, True, False)
    hull(38, 30, 22, 8, False, leaking)
    outline(p)
    return p


def net_rack(torn):
    p = Pix(44, 48)
    p.rect(6, 12, 3, 30, WOOD_D)
    p.rect(34, 12, 3, 30, WOOD_D)
    p.rect(6, 12, 31, 3, WOOD)
    for y in range(18, 40, 4):
        if not (torn and 26 <= y <= 34):
            p.hline(9, 34, y, (78, 104, 92, 255))
    for x in range(12, 34, 4):
        stop = 28 if torn and 16 <= x <= 26 else 40
        p.vline(x, 16, stop, (78, 104, 92, 255))
    if torn:
        p.vline(22, 30, 42, (78, 104, 92, 255))
    outline(p)
    return p


def garden():
    p = Pix(60, 44)
    p.rect(6, 14, 48, 24, (92, 64, 40, 255))
    p.rect(8, 16, 20, 20, (70, 48, 32, 255))
    p.rect(32, 16, 20, 20, (70, 48, 32, 255))
    for x in range(10, 50, 8):
        if 26 <= x <= 30:
            continue
        for y in (21, 29):
            p.hline(x - 1, x + 3, y + 4, (105, 78, 48, 255))
            p.vline(x, y, y + 4, LEAF_D)
            p.ellipse(x - 1, y, 2, 2, LEAF)
            p.ellipse(x + 2, y - 1, 2, 2, LEAF_L)
            if x > 30:
                p.set(x, y + 1, (166, 151, 188, 255))
    p.rect(4, 12, 52, 3, WOOD)
    p.hline(5, 54, 12, WOOD_L)
    p.rect(4, 36, 52, 3, WOOD)
    p.rect(4, 12, 3, 27, WOOD_D)
    p.rect(52, 12, 3, 27, WOOD_D)
    p.hline(5, 54, 36, WOOD_L)
    outline(p)
    return p


def hearth(lit):
    p = Pix(44, 44)
    p.ellipse(22, 28, 16, 8, STONE_D)
    p.ellipse(22, 27, 12, 5, STONE)
    p.ellipse(22, 26, 7, 3, (54, 36, 30, 255))
    for x, y in ((9, 27), (14, 23), (23, 22), (32, 25), (35, 29), (28, 33), (18, 34), (11, 31)):
        p.ellipse(x, y, 3, 2, STONE)
        p.hline(x - 1, x + 1, y - 1, STONE_L)
    p.rect(12, 24, 16, 3, WOOD_D)
    p.rect(14, 21, 12, 3, WOOD)
    if lit:
        p.hline(16, 26, 25, (197, 77, 41, 255))
        p.ellipse(22, 18, 4, 7, FLAME)
        p.ellipse(22, 16, 2, 4, FLAME_L)
        p.set(22, 12, (255, 244, 210, 255))
        p.set(18, 22, FLAME_L)
        p.set(26, 21, FLAME_L)
    outline(p)
    return p


def flower_bench():
    p = Pix(48, 32)
    p.rect(6, 16, 36, 4, WOOD)
    p.rect(8, 20, 3, 7, WOOD_D)
    p.rect(37, 20, 3, 7, WOOD_D)
    p.rect(8, 8, 32, 6, (64, 108, 62, 255))
    for index, color in enumerate(((220, 86, 104, 255), (240, 200, 90, 255), (230, 120, 170, 255), (250, 250, 250, 255), (120, 170, 220, 255))):
        p.ellipse(12 + index * 5, 9, 2, 2, color)
    outline(p)
    return p


def food_stall():
    p = Pix(56, 52)
    p.rect(8, 22, 40, 22, WOOD)
    p.rect(40, 26, 8, 18, WOOD_D)
    p.rect(12, 25, 30, 9, (65, 48, 35, 255))
    for x in range(12, 47, 7):
        p.vline(x, 38, 43, WOOD_D)
    p.rect(10, 34, 36, 4, WOOD)
    p.hline(10, 45, 34, WOOD_L)
    for y in range(12, 22):
        left, right = 11 - (y - 12) // 3, 47 + (y - 12) // 3
        for x in range(left, right + 1):
            stripe = (x // 6) % 2
            color = (174, 77, 63, 255) if stripe else (232, 204, 151, 255)
            p.set(x, y, mix(color, 1.08 if y < 16 else 1))
    for x in range(8, 51):
        p.vline(x, 22, 23 + (x % 6 not in (0, 5)), (144, 63, 52, 255) if (x // 6) % 2 else (206, 175, 120, 255))
    p.vline(8, 16, 46, WOOD_D)
    p.vline(46, 16, 46, WOOD_D)
    outline(p)
    return p


def water_center():
    p = Pix(56, 48)
    p.rect(6, 16, 44, 24, STONE)
    p.rect(42, 20, 8, 20, STONE_D)
    p.rect(10, 20, 14, 12, (36, 44, 50, 255))
    p.rect(28, 20, 12, 12, (36, 44, 50, 255))
    p.rect(4, 14, 48, 4, WOOD)
    p.rect(4, 38, 48, 3, WOOD_D)
    for y in (33, 36):
        p.hline(7, 49, y, STONE_D)
    p.hline(5, 50, 14, WOOD_L)
    for x in (12, 24, 36):
        p.vline(x, 34, 35, STONE_D)
    p.rect(43, 24, 5, 6, WOOD_L)
    p.set(45, 25, FRESH)
    outline(p)
    return p


def well(state):
    p = Pix(40, 48)
    p.vline(10, 16, 40, WOOD_D)
    p.vline(28, 16, 40, WOOD_D)
    thatch(p, 8, 20, 6, 34)
    p.ellipse(20, 36, 12, 6, STONE)
    p.ellipse(20, 32, 12, 5, STONE_L)
    p.hline(9, 30, 37, STONE_D)
    for x in (12, 20, 27):
        p.vline(x, 38, 40, STONE_D)
    hole = FRESH if state == "ready" else MURK if state == "fouled" else (36, 40, 44, 255)
    p.ellipse(20, 35, 6, 3, hole)
    if state == "ready":
        p.hline(17, 21, 34, (154, 209, 214, 255))
    p.hline(11, 28, 24, WOOD_L)
    p.vline(20, 23, 32, ROPE)
    p.rect(30, 28, 5, 4, WOOD)
    p.vline(32, 22, 28, ROPE)
    outline(p)
    return p


def rain_barrel(state):
    p = Pix(32, 40)
    p.ellipse(16, 14, 8, 4, WOOD_L)
    p.rect(8, 14, 16, 18, WOOD)
    for x in (11, 15, 19, 23):
        p.vline(x, 16, 31, WOOD_D)
    p.vline(9, 16, 30, WOOD_L)
    p.hline(8, 23, 18, WOOD_D)
    p.hline(8, 23, 26, WOOD_D)
    p.ellipse(16, 32, 8, 3, WOOD_D)
    water = FRESH if state == "ready" else MURK if state == "fouled" else (42, 36, 30, 255)
    p.ellipse(16, 14, 6, 3, water)
    if state == "ready":
        p.hline(12, 17, 13, (156, 208, 213, 255))
    outline(p)
    return p


def fish_crates(level):
    p = Pix(32, 24)
    p.rect(4, 8, 24, 12, WOOD)
    p.rect(6, 6, 20, 4, WOOD_L)
    p.hline(4, 27, 14, WOOD_D)
    if level == "full":
        spots = ((8, 10), (14, 9), (20, 11), (16, 13))
    elif level == "half":
        spots = ((10, 11), (18, 10))
    else:
        spots = ()
        p.rect(8, 10, 16, 6, (78, 52, 32, 255))
    for x, y in spots:
        p.ellipse(x, y, 3, 2, FISH)
        p.set(x + 2, y, (90, 110, 120, 255))
    outline(p)
    return p


def herb_bed(level):
    p = Pix(32, 28)
    count = 6 if level == "full" else 3 if level == "half" else 0
    for index in range(6):
        x = 8 + (index % 3) * 8
        y = 8 + (index // 3) * 9
        p.vline(x, y, min(24, y + 6), WOOD_D)
        if index < count:
            p.ellipse(x, y, 2, 2, LEAF if index % 2 == 0 else LEAF_L)
    outline(p)
    return p


def water_store(level):
    p = Pix(28, 20)
    p.ellipse(14, 10, 10, 5, STONE_D)
    if level == "full":
        p.ellipse(14, 10, 8, 3, FRESH)
    elif level == "half":
        p.ellipse(14, 11, 5, 2, FRESH)
    else:
        p.ellipse(14, 10, 6, 2, (36, 40, 44, 255))
    outline(p)
    return p


def pantry_goods(level):
    p = Pix(36, 28)
    p.rect(4, 18, 28, 4, WOOD_D)
    if level != "empty":
        p.rect(6, 10, 10, 8, WOOD)
        p.ellipse(12, 10, 4, 2, (150, 118, 70, 255))
    if level == "full":
        p.rect(18, 8, 12, 10, WOOD_L)
        p.ellipse(28, 14, 4, 5, (120, 86, 150, 255))
        p.ellipse(10, 12, 3, 2, FISH)
    outline(p)
    return p


# --- scenery ---------------------------------------------------------------

def tree(variant):
    p = Pix(40, 68)
    p.rect(17, 34, 7, 28, WOOD_D)
    p.rect(18, 37, 3, 25, WOOD)
    p.vline(19, 42, 58, WOOD_L)
    p.hline(14, 26, 62, WOOD_D)
    p.hline(15, 24, 61, WOOD)
    for x, y in ((15, 44), (14, 43), (23, 47), (24, 46)):
        p.rect(x, y, 3, 3, WOOD_D)
    # Overlapping irregular crowns give each tree a leafy silhouette and volume.
    dark = (35, 76, 55, 255) if variant else (41, 83, 52, 255)
    middle = (49, 104, 65, 255) if variant else (60, 111, 61, 255)
    light = (81, 137, 79, 255) if variant else (99, 145, 78, 255)
    crowns = ((13, 32, 10, 11), (27, 33, 9, 10), (21, 21, 12, 14), (11, 23, 8, 9), (27, 19, 7, 9))
    for x, y, rx, ry in crowns:
        p.ellipse(x, y, rx, ry, dark)
    for x, y, rx, ry in crowns:
        p.ellipse(x - 1, y - 3, rx - 2, ry - 3, middle)
    for x, y, rx, ry in ((11, 19, 5, 4), (19, 12, 5, 4), (27, 15, 4, 4), (10, 29, 5, 4), (22, 26, 5, 4)):
        p.ellipse(x, y, rx, ry, light)
    for y in range(8, 42):
        for x in range(4, 36):
            color = p.get(x, y)
            if color in (dark, middle, light) and noise(x, y, variant + 3) % 17 == 0:
                p.hline(x, x + 1, y, mix(color, 1.12))
    outline(p, (32, 61, 44, 255))
    return p


def rocks():
    p = Pix(36, 28)
    p.ellipse(12, 16, 8, 6, STONE)
    p.ellipse(22, 18, 10, 6, STONE_D)
    p.ellipse(16, 14, 5, 3, STONE_L)
    p.hline(9, 15, 13, (192, 193, 176, 255))
    p.hline(20, 27, 18, STONE)
    p.vline(18, 16, 20, STONE_D)
    p.hline(7, 13, 21, (72, 95, 64, 255))
    p.set(8, 20, GRASS_L)
    outline(p)
    return p


def bush():
    p = Pix(32, 26)
    p.ellipse(10, 15, 7, 6, LEAF_D)
    p.ellipse(22, 14, 7, 6, LEAF_D)
    p.ellipse(16, 10, 8, 7, LEAF)
    p.ellipse(10, 11, 5, 4, LEAF)
    p.ellipse(23, 12, 4, 3, LEAF)
    p.ellipse(14, 7, 4, 3, LEAF_L)
    for x, y in ((7, 13), (12, 10), (21, 8), (24, 15), (17, 16)):
        p.set(x, y, (220, 144, 139, 255))
        p.set(x + 1, y, (239, 182, 154, 255))
    outline(p, (32, 61, 44, 255))
    return p


def reed():
    p = Pix(16, 32)
    for x, top in ((5, 12), (8, 8), (11, 14)):
        p.vline(x, top, 26, (70, 120, 78, 255))
        p.ellipse(x, top, 2, 3, (120, 150, 90, 255))
    outline(p)
    return p


# --- villagers -------------------------------------------------------------

def tunic_colors(spec):
    base = opaque(spec["tunic"])
    return base, mix(base, 0.72), mix(base, 1.08)


def draw_hair(p, spec, x, y, front):
    hair = opaque(spec["hair"])
    style = spec["style"]
    if style == "hood":
        hood, hood_d, _ = tunic_colors(spec)
        p.rect(x - 7, y - 2, 14, 8, hood_d)
        p.rect(x - 8, y + 2, 4, 12, hood)
        p.rect(x + 5, y + 2, 4, 12, hood_d)
        if front:
            p.rect(x - 5, y + 2, 10, 8, SKIN)
        return
    p.rect(x - 6, y, 12, 5, hair)
    if style == "bun":
        p.ellipse(x + 4, y, 3, 2, hair)
    elif style == "long":
        p.rect(x - 7, y + 4, 3, 12, hair)
        p.rect(x + 5, y + 4, 3, 14, hair)
    elif style == "braid":
        p.vline(x + 6, y + 4, y + 16, hair)
        p.set(x + 6, y + 17, (200, 160, 70, 255))
    if spec["hat"] == "straw":
        p.ellipse(x, y + 1, 8, 2, (214, 170, 80, 255))
        p.rect(x - 3, y - 2, 6, 3, (196, 148, 64, 255))
    elif spec["hat"] == "cap":
        p.rect(x - 6, y - 1, 12, 3, (48, 72, 96, 255))
        p.rect(x - 3, y - 3, 6, 3, (48, 72, 96, 255))
    elif spec["hat"] == "scarf":
        p.rect(x - 4, y + 12, 8, 3, (176, 56, 62, 255))


def draw_role_mark(p, spec, x, y):
    if spec["role"] == "fisher":
        p.vline(x + 8, y, y + 8, WOOD_D)
    elif spec["role"] == "boat":
        p.hline(x - 4, x + 4, y + 4, WOOD_D)
    elif spec["role"] == "garden":
        p.set(x - 7, y + 2, LEAF_L)
        p.set(x - 6, y + 1, LEAF)
    elif spec["role"] == "visitor":
        p.rect(x + 5, y + 2, 4, 6, (96, 64, 40, 255))


def draw_front(p, spec, action):
    """South (toward the camera) or the caller covers the face for north."""
    tunic, tunic_d, tunic_l = tunic_colors(spec)
    step = 2 if action == "walk" else 0
    x, foot = 16, 44
    p.rect(x - 6, foot - 3, 4, 3, INK)
    p.rect(x + 2, foot - 3 - step * 2, 4, 3, INK)
    p.rect(x - 5, foot - 11, 4, 8, (54, 46, 44, 255))
    p.rect(x + 1, foot - 11 - step * 2, 4, 8, (42, 36, 34, 255))
    p.rect(x - 7, foot - 24, 14, 14, tunic)
    p.rect(x + 4, foot - 24, 3, 14, tunic_d)
    p.rect(x - 3, foot - 24, 6, 2, tunic_l)
    if action == "carry":
        p.rect(x - 10, foot - 20, 3, 8, tunic_d)
        p.rect(x + 7, foot - 20, 3, 8, tunic_d)
        p.rect(x - 5, foot - 18, 10, 7, WOOD)
        p.hline(x - 5, x + 4, foot - 15, WOOD_L)
    else:
        p.rect(x - 10, foot - 22 + step, 3, 9, tunic)
        p.rect(x - 10, foot - 14 + step, 3, 2, SKIN)
        p.rect(x + 7, foot - 22 - step, 3, 9, tunic_d)
        p.rect(x + 7, foot - 14 - step, 3, 2, SKIN_D)
    p.rect(x - 2, foot - 27, 4, 3, SKIN)
    p.rect(x - 6, foot - 38, 12, 12, SKIN)
    p.rect(x + 3, foot - 38, 3, 12, SKIN_D)
    draw_hair(p, spec, x, foot - 38, True)
    p.rect(x - 4, foot - 32, 2, 2, INK)
    p.rect(x + 1, foot - 32, 2, 2, INK)
    draw_role_mark(p, spec, x, foot - 22)


def draw_back(p, spec, action):
    draw_front(p, spec, action)
    tunic, tunic_d, _ = tunic_colors(spec)
    hair = opaque(spec["hair"])
    x, foot = 16, 44
    # Cover the face. A hood keeps the hood opening closed from behind.
    if spec["style"] == "hood":
        p.rect(x - 5, foot - 34, 10, 10, mix(tunic, 0.72))
    else:
        p.rect(x - 6, foot - 38, 12, 14, hair)
        if spec["style"] == "bun":
            p.ellipse(x + 4, foot - 36, 2, 2, hair)
        if spec["hat"] == "straw":
            p.ellipse(x, foot - 37, 8, 2, (214, 170, 80, 255))
        elif spec["hat"] == "cap":
            p.rect(x - 6, foot - 40, 12, 4, (48, 72, 96, 255))
    p.rect(x - 7, foot - 24, 11, 14, tunic)
    p.rect(x + 4, foot - 24, 3, 14, tunic_d)


def draw_side(p, spec, action):
    tunic, tunic_d, _ = tunic_colors(spec)
    hair = opaque(spec["hair"])
    step = 4 if action == "walk" else 0
    x, foot = 14, 44
    p.rect(x - 1, foot - 3, 5, 3, INK)
    p.rect(x + 2 + step, foot - 3, 5, 3, INK)
    p.rect(x, foot - 11, 5, 8, (54, 46, 44, 255))
    p.rect(x - 3, foot - 24, 10, 14, tunic)
    p.rect(x + 5, foot - 24, 2, 14, tunic_d)
    if action == "carry":
        p.rect(x + 4, foot - 18, 8, 6, WOOD)
        p.rect(x + 6, foot - 20, 3, 6, tunic_d)
    else:
        p.rect(x + 4, foot - 22, 3, 9, tunic_d)
        p.rect(x + 5, foot - 14, 2, 2, SKIN_D)
    p.rect(x - 3, foot - 36, 10, 12, SKIN)
    p.rect(x + 6, foot - 30, 2, 2, SKIN_D)  # nose, facing east
    p.rect(x + 3, foot - 32, 2, 2, INK)
    p.rect(x - 5, foot - 36, 4, 12, hair)
    p.rect(x - 3, foot - 40, 10, 5, hair)
    if spec["style"] == "hood":
        hood_d = mix(tunic, 0.72)
        p.rect(x - 5, foot - 40, 12, 8, hood_d)
        p.rect(x - 5, foot - 34, 3, 10, hood_d)
    elif spec["style"] == "braid":
        p.vline(x - 4, foot - 28, foot - 16, hair)
    elif spec["style"] == "bun":
        p.ellipse(x - 2, foot - 38, 3, 3, hair)
    if spec["hat"] == "straw":
        p.hline(x - 6, x + 8, foot - 36, (214, 170, 80, 255))
        p.rect(x - 1, foot - 40, 6, 4, (196, 148, 64, 255))
    elif spec["hat"] == "cap":
        p.rect(x - 4, foot - 40, 10, 3, (48, 72, 96, 255))
    if spec["role"] == "visitor":
        p.rect(x - 6, foot - 22, 4, 6, (96, 64, 40, 255))
    elif spec["role"] == "fisher":
        p.vline(x + 8, foot - 28, foot - 16, WOOD_D)


def draw_collapsed(p, spec, view):
    tunic, tunic_d, _ = tunic_colors(spec)
    hair = opaque(spec["hair"])
    # Head points east or south in those views, and the other way for north and west.
    if view in ("e", "s"):
        p.rect(6, 34, 14, 8, tunic)
        p.rect(6, 38, 14, 4, tunic_d)
        p.rect(4, 41, 4, 3, INK)
        p.rect(20, 32, 8, 9, SKIN)
        p.rect(20, 30, 8, 4, hair)
        if view == "s":
            p.rect(22, 35, 2, 2, INK)
            p.rect(25, 35, 2, 2, INK)
        else:
            p.rect(26, 35, 2, 2, SKIN_D)
    else:
        p.rect(12, 34, 14, 8, tunic)
        p.rect(12, 38, 14, 4, tunic_d)
        p.rect(24, 41, 4, 3, INK)
        p.rect(4, 32, 8, 9, SKIN)
        p.rect(4, 30, 8, 4, hair)
        if view == "n":
            p.rect(6, 35, 2, 2, INK)
            p.rect(9, 35, 2, 2, INK)


def villager(spec, action, view):
    if view == "w":
        return flip(villager(spec, action, "e"))
    p = Pix(32, 48)
    if action == "collapsed":
        draw_collapsed(p, spec, view)
    elif view == "e":
        draw_side(p, spec, action)
    elif view == "n":
        draw_back(p, spec, action)
    else:
        draw_front(p, spec, action)
    if action != "collapsed":
        tunic, tunic_d, tunic_l = tunic_colors(spec)
        hair = opaque(spec["hair"])
        # A lit seam, tiny cloth folds and a belt keep silhouettes readable at 1×.
        for y in range(22, 33):
            for x in range(8, 24):
                if p.get(x, y) == tunic and p.get(x - 1, y) != tunic:
                    p.set(x, y, tunic_l)
                    break
        for x in range(9, 24):
            if p.get(x, 31) in (tunic, tunic_d):
                p.set(x, 31, (105, 75, 51, 255))
        for x, y in ((12, 26), (12, 27), (19, 29), (19, 30)):
            if p.get(x, y) == tunic:
                p.set(x, y, mix(tunic, 0.88))
        if view == "s":
            p.set(15, 31, (223, 190, 119, 255))
            p.set(15, 16, (173, 112, 89, 255))
            if p.get(11, 14) == SKIN:
                p.set(11, 14, (228, 155, 127, 255))
            if p.get(16, 22) in (tunic, tunic_l):
                p.set(16, 23, (232, 216, 182, 255))
                p.set(16, 25, (232, 216, 182, 255))
        elif view == "e":
            if p.get(19, 17) == SKIN:
                p.set(19, 17, (173, 112, 89, 255))
        for x, y in ((12, 7), (13, 7), (14, 6), (12, 9), (11, 13)):
            if p.get(x, y) == hair:
                p.set(x, y, mix(hair, 1.3))
    outline(p)
    return p


# --- terrain ----------------------------------------------------------------

def build_terrain(world: World):
    land, pier = world.config["walkable_polygons"]
    width, height = world.config["width"], world.config["height"]
    blocked = world.config["blocked_rectangles"]

    def land_at(x, y):
        return 0 <= x < width and 0 <= y < height and inside((x, y), land)

    def pier_at(x, y):
        return 0 <= x < width and 0 <= y < height and inside((x, y), pier)

    paths = set()
    for start, goal in LINKS:
        for point in world.path(world.point(start), world.point(goal)):
            paths.add(point)
        paths.add(world.point(start))
        paths.add(world.point(goal))

    terrain = []
    foam = []
    for y in range(height):
        for x in range(width):
            here_pier = pier_at(x, y)
            here_land = land_at(x, y)
            watery = []
            for dx, dy, bit in ((0, -1, 1), (1, 0, 2), (0, 1, 4), (-1, 0, 8)):
                nx, ny = x + dx, y + dy
                # Foam belongs on the water edge that faces land or the pier.
                if land_at(nx, ny) or pier_at(nx, ny):
                    watery.append(bit)
            beach = False
            if here_land and not here_pier:
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx or dy:
                            if not land_at(x + dx, y + dy) and not pier_at(x + dx, y + dy):
                                beach = True
            if here_pier:
                tile = "pier"
            elif not here_land:
                tile = "water"
            elif beach:
                tile = "sand_b" if (x + y) % 2 else "sand"
            elif (x, y) in paths:
                tile = "path_b" if (x * 3 + y) % 2 else "path"
            else:
                tile = ("grass_flower", "grass_b", "grass")[(x * 5 + y * 3) % 3]
            terrain.append(tile)
            foam.append(sum(watery) if tile == "water" else 0)

    places = {(place["x"], place["y"]) for place in world.config["places"]}
    clear = set()
    for x, y in places:
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                clear.add((x + dx, y + dy))
    scenery = []
    for x0, y0, x1, y1 in blocked:
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                if (x, y) in clear or not land_at(x, y):
                    continue
                kind = "tree" if y <= 8 else "bush" if y <= 10 else "rock"
                if kind == "tree" and (x + y) % 2:
                    continue
                scenery.append((kind, x, y))
    for y in range(height):
        for x in range(width):
            if terrain[y * width + x] in ("sand", "sand_b") and (x * 7 + y * 5) % 9 == 0 and (x, y) not in clear:
                scenery.append(("reed", x, y))
    return terrain, foam, scenery


def rel(folder, name):
    return f"{folder}/{name}.png"


def save(root, folder, name, pix, store):
    path = rel(folder, name)
    assert_clear_border(pix, path)
    store[path] = pix
    write_png(root / path, pix)
    return path


def main():
    root = ROOT / "village_sim" / "static" / "assets"
    for folder in ("tiles", "buildings", "resources", "characters"):
        path = root / folder
        path.mkdir(parents=True, exist_ok=True)
        for old in path.glob("*"):
            if old.is_file():
                old.unlink()

    store = {}
    tiles = {
        "grass": grass_tile(0),
        "grass_b": grass_tile(1),
        "grass_flower": grass_tile(2),
        "path": path_tile(0),
        "path_b": path_tile(1),
        "sand": sand_tile(0),
        "sand_b": sand_tile(1),
        "water": water_tile(0),
        "water_b": water_tile(1),
        "pier": pier_tile(),
        "foam_n": foam_tile("n"),
        "foam_e": foam_tile("e"),
        "foam_s": foam_tile("s"),
        "foam_w": foam_tile("w"),
    }
    for name in ("grass", "grass_b", "grass_flower"):
        assert_solid_border(tiles[name], GRASS, name)
    for name in ("path", "path_b"):
        assert_solid_border(tiles[name], PATH, name)
    for name in ("sand", "sand_b"):
        assert_solid_border(tiles[name], SAND, name)
    for name, pix in tiles.items():
        store[rel("tiles", name)] = pix
        write_png(root / "tiles" / f"{name}.png", pix)
    for name, pix in {
        "tree_a": tree(0),
        "tree_b": tree(1),
        "rock": rocks(),
        "bush": bush(),
        "reed": reed(),
    }.items():
        store[rel("tiles", name)] = pix
        write_png(root / "tiles" / f"{name}.png", pix)
        assert_clear_border(pix, name)

    world = World()
    terrain, foam, scenery_plan = build_terrain(world)
    buildings = []

    def add_building(place, anchor, blocked, states, default):
        painted = {}
        size = None
        for state, pix in states.items():
            file = save(root, "buildings", f"building_{place}_{state}", pix, store)
            painted[state] = file
            size = [pix.w, pix.h]
        buildings.append({
            "id": place,
            "file": painted[default],
            "size": size,
            "anchor": list(anchor),
            "blocked": blocked,
            "states": painted,
        })

    add_building("mara_home", (32, 69), [[0, -1]], {"ready": cottage(flowers=True)}, "ready")
    add_building("tom_home", (32, 69), [[0, -1]], {"ready": cottage(oar=True)}, "ready")
    add_building("lin_home", (32, 69), [[0, -1]], {"ready": cottage(flowers=True, vines=True)}, "ready")
    add_building("guest_home", (26, 61), [[0, -1]], {"ready": guest_cottage()}, "ready")
    add_building("jetty", (18, 44), [], {"ready": jetty_post()}, "ready")
    add_building("shore", (32, 14), [], {"sound": boats(False), "leaking": boats(True)}, "sound")
    add_building("nets", (22, 44), [], {"sound": net_rack(False), "torn": net_rack(True)}, "sound")
    add_building("garden", (30, 40), [], {"ready": garden()}, "ready")
    add_building("fire", (22, 40), [], {"lit": hearth(True), "resting": hearth(False)}, "lit")
    add_building("bench", (24, 28), [], {"ready": flower_bench()}, "ready")
    add_building("food_center", (28, 48), [], {"ready": food_stall()}, "ready")
    add_building("water_center", (28, 44), [], {"ready": water_center()}, "ready")
    add_building("well", (20, 44), [], {
        "ready": well("ready"), "empty": well("empty"), "fouled": well("fouled"),
    }, "ready")
    add_building("rain_barrel", (16, 36), [], {
        "ready": rain_barrel("ready"), "empty": rain_barrel("empty"), "fouled": rain_barrel("fouled"),
    }, "ready")

    resources = []

    def add_resource(rid, place, anchor, states):
        painted = {}
        size = None
        for state, pix in states.items():
            file = save(root, "resources", f"resource_{rid}_{state}", pix, store)
            painted[state] = file
            size = [pix.w, pix.h]
        resources.append({
            "id": rid,
            "place": place,
            "file": painted["full"],
            "size": size,
            "anchor": list(anchor),
            "blocked": [],
            "states": painted,
        })

    add_resource("fish", "jetty", (8, 20), {level: fish_crates(level) for level in ("full", "half", "empty")})
    add_resource("herbs", "garden", (16, 26), {level: herb_bed(level) for level in ("full", "half", "empty")})
    add_resource("water", "water_center", (18, 26), {level: water_store(level) for level in ("full", "half", "empty")})
    add_resource("pantry", "food_center", (28, 40), {level: pantry_goods(level) for level in ("full", "half", "empty")})

    characters = {}
    for pid, spec in PEOPLE.items():
        actions = {}
        for action in ("idle", "walk", "carry", "collapsed"):
            actions[action] = {}
            for view in ("n", "e", "s", "w"):
                pix = villager(spec, action, view)
                actions[action][view] = save(root, "characters", f"char_{pid}_{action}_{view}", pix, store)
        characters[pid] = {
            "size": [32, 48],
            "anchor": [16, 44],
            "actions": actions,
        }

    scenery = []
    files = {"tree": "tiles/tree_a.png", "bush": "tiles/bush.png", "rock": "tiles/rock.png", "reed": "tiles/reed.png"}
    anchors = {"tree": [20, 64], "bush": [16, 22], "rock": [18, 24], "reed": [8, 28]}
    for index, (kind, x, y) in enumerate(scenery_plan):
        file = "tiles/tree_b.png" if kind == "tree" and (x + y) % 4 == 0 else files[kind]
        pix = store[file]
        scenery.append({
            "id": f"{kind}-{x}-{y}",
            "file": file,
            "tile": [x, y],
            "size": [pix.w, pix.h],
            "anchor": anchors[kind],
        })

    manifest = {
        "version": 1,
        "view": "three-quarter",
        "tile_size": 32,
        "map": {"width": world.config["width"], "height": world.config["height"], "canvas": [1280, 720]},
        "style": "evening lantern bay, upper-left light, outlined three-quarter sprites",
        "tiles": {name: rel("tiles", name) for name in tiles},
        "terrain": terrain,
        "foam": foam,
        "buildings": buildings,
        "resources": resources,
        "characters": characters,
        "scenery": scenery,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    preview(manifest, store, world)
    print(f"Wrote {len(store)} assets and {root / 'manifest.json'}")


def preview(manifest, store, world):
    canvas = Pix(1280, 720)
    width = manifest["map"]["width"]
    for index, tile in enumerate(manifest["terrain"]):
        x, y = (index % width) * 32, (index // width) * 32
        canvas.blit(store[manifest["tiles"][tile]], x, y)
        mask = manifest["foam"][index]
        for bit, name in ((1, "foam_n"), (2, "foam_e"), (4, "foam_s"), (8, "foam_w")):
            if mask & bit:
                canvas.blit(store[manifest["tiles"][name]], x, y)
    places = {place["id"]: place for place in world.config["places"]}
    items = []
    for item in manifest["scenery"]:
        tx, ty = item["tile"]
        items.append(((ty + 0.5) * 32, 0, item["file"], (tx + 0.5) * 32, (ty + 0.5) * 32, item["anchor"]))
    for item in manifest["buildings"]:
        place = places[item["id"]]
        items.append(((place["y"] + 0.5) * 32, 1, item["file"], (place["x"] + 0.5) * 32, (place["y"] + 0.5) * 32, item["anchor"]))
    for item in manifest["resources"]:
        place = places[item["place"]]
        items.append(((place["y"] + 0.5) * 32, 2, item["states"]["half"], (place["x"] + 0.5) * 32, (place["y"] + 0.5) * 32, item["anchor"]))
    for pid, home in (("mara", "mara_home"), ("tom", "tom_home"), ("lin", "garden"), ("ada", "shore")):
        place = places[home]
        actor = manifest["characters"][pid]
        items.append((
            (place["y"] + 0.5) * 32, 3, actor["actions"]["idle"]["s"],
            (place["x"] + 0.5) * 32, (place["y"] + 0.5) * 32, actor["anchor"],
        ))
    for foot_y, _z, file, x, y, anchor in sorted(items):
        pix = store[file]
        canvas.blit(pix, round(x - anchor[0]), round(y - anchor[1]))
    write_png(Path("/tmp/lantern-bay-map.png"), canvas)
    crop = Pix(27 * 32, 17 * 32)
    for y in range(crop.h):
        for x in range(crop.w):
            crop.set(x, y, canvas.get(x + 3 * 32, y + 3 * 32))
    write_png(Path("/tmp/lantern-bay-zoom.png"), scale(crop, 3))

    sheet = Pix(1100, 720)
    sheet.fill((32, 48, 52, 255))
    x = 16
    for item in manifest["buildings"]:
        pix = store[item["file"]]
        scaled = scale(pix, 3)
        sheet.blit(scaled, x, 16)
        x += scaled.w + 12
        if x > 1000:
            break
    y = 280
    x = 16
    for pid in PEOPLE:
        pix = store[manifest["characters"][pid]["actions"]["idle"]["s"]]
        sheet.blit(scale(pix, 4), x, y)
        x += 32 * 4 + 8
    x = 16
    mara = manifest["characters"]["mara"]["actions"]
    for key in ("idle", "walk", "carry", "collapsed"):
        sheet.blit(scale(store[mara[key]["s"]], 4), x, 500)
        x += 32 * 4 + 8
    for view in ("n", "e", "s", "w"):
        sheet.blit(scale(store[mara["walk"][view]], 4), x, 500)
        x += 32 * 4 + 8
    write_png(Path("/tmp/lantern-bay-sheet.png"), sheet)
    glyphs = {"water": "~", "pier": "=", "sand": ".", "sand_b": ",", "path": "+", "path_b": "+",
              "grass": "#", "grass_b": "#", "grass_flower": "*"}
    width = manifest["map"]["width"]
    rows = []
    for y in range(manifest["map"]["height"]):
        rows.append("".join(glyphs[tile] for tile in manifest["terrain"][y * width:(y + 1) * width]))
    print("\n".join(rows))


def scale(pix, factor):
    out = Pix(pix.w * factor, pix.h * factor)
    for y in range(pix.h):
        for x in range(pix.w):
            color = pix.get(x, y)
            if not color[3]:
                continue
            for dy in range(factor):
                for dx in range(factor):
                    out.set(x * factor + dx, y * factor + dy, color)
    return out


if __name__ == "__main__":
    main()
