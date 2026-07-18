#!/usr/bin/env python3
"""Build solar-hero5.bin — iterate on solar-hero3 (offline, NO flash). Three changes:

  1) MOVE DOWN ~18px: SUN (bg blob 000) + SoC HERO (0xd8 WEATHER_TEMP_CA) shift down 18px
     (SUN_CY 30->48, GLOW_YMAX 66->84, HERO_Y 72->90). The band directly behind the shifted,
     center-aligned hero stays FLAT / X-invariant sky, so the repositioned glyphs still
     composite seamlessly (sun above, clean sky directly behind the number, panels below).
     NOTE: hero glyph tiles composite over bg_col(y); bg_col(72)==bg_col(90)==flat SKY, so the
     hero BLOBS are byte-identical at either Y -> only the 0xd8 Y coordinate changes.

  2) ADD DATE (watch-native, auto from the watch RTC — no HA injection): enable DAY_NAME
     (weekday, 0x60) + MONTH_NUM (0x11) + DAY_NUM (0x30) as a clean line under the clock,
     format "WED 07-18" (weekday + MM-DD, '-' baked in the bg gap). Left-aligned numerics
     (per dawft print_types + example1: MONTH_NUM/DAY_NUM are left-aligned, 2px digit spacing,
     ~41px slot pitch -> designed for 2-digit fields). Weekday + digits = compression NONE.

  3) ADD WATCH BATTERY % (BATT_CA, 0xd3, watch-native, auto) top-right with a subtle
     battery-outline motif baked in the bg. Center-aligned digits, compression NONE.

KEEP (proven recipe): 18 faceData; hero 24x36 single digits + last-2 double-width % NONE;
ALL value glyphs (SoC/battery/date-numbers/weekday) compression NONE (RLE garbles them);
halo-free composite over per-row bg; preview blob 092 = 140x163 (flattened to reclaim budget);
faceNumber 60002, 240x284, sun+solar-panels bg, clock on TIME digits. Target <= 86KB.
"""
import os
from PIL import Image, ImageDraw, ImageFont
import build_hero as bh

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD5 = os.path.join(HERE, "build5")

# ---- change #1: shift sun + hero DOWN ~18px --------------------------------
SHIFT = 18
bh.BUILD = BUILD5
bh.SUN_CY = 30 + SHIFT       # 48
bh.GLOW_YMAX = 66 + SHIFT    # 84 (glow hard-clips 6px above the shifted hero band @ 90)
bh.HERO_Y = 72 + SHIFT       # 90

F_BOLD = bh.F_BOLD
W = bh.W

# ---- new field geometry ----------------------------------------------------
# WATCH BATTERY (0xd3 BATT_CA) — top-right, center-aligned, subtle slate, small.
BATT_X, BATT_Y, BATT_DW, BATT_H = 207, 9, 10, 14
BATT_FONT = 15
BATT_RGB = (150, 170, 205)     # cool slate — distinct from the warm gold SoC hero
BATT_ICON = (120, 140, 175)    # battery outline motif

# DATE line, under the clock (clock ends y199). Left-aligned numerics per firmware.
DATE_Y = 203
DNUM_DW, DNUM_H = 13, 16        # single date-digit tile (shared by DAY_NUM + MONTH_NUM)
DATE_FONT = 18
MONTH_X = 114                   # 0x11 left-aligned -> digits @114,129  (extent 114-142)
DAY_X = 158                     # 0x30 left-aligned -> digits @158,173  (extent 158-186)
SEP_X0, SEP_X1 = 146, 154       # '-' baked in bg, sits in the 142..158 gap (no digit lands here)
SEP_YC = DATE_Y + DNUM_H // 2
WD_X, WD_W, WD_H = 54, 40, 16   # DAY_NAME weekday image (fits widest "WED")
WD_FONT = 15
DATE_RGB = (206, 216, 232)      # soft slate-white — secondary to the clock
WD_RGB = (226, 186, 112)        # muted warm amber — ties the weekday to the sun/hero
SEP_RGB = (150, 165, 185)       # muted slate dash

WEEKDAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]  # 0x60 order -> blobs 066..072


def build_scene_and_motifs():
    """Shifted-sun scene (change #1) + baked static motifs (battery outline, date dash)."""
    bh.make_background()          # writes build5/000.bmp with sun @ SUN_CY=48, glow clip @84; bakes colon
    im = bh.SCENE
    d = ImageDraw.Draw(im)
    # battery outline motif, LEFT of the (center-aligned) number. Icon @170-184; number center 207
    # (2-3 digit extent 188-226) never reaches the icon -> no glyph composites over this X-variant art.
    bx0, by0, bx1, by1 = 170, BATT_Y + 1, 184, BATT_Y + 11
    d.rectangle([bx0, by0, bx1, by1], outline=BATT_ICON)          # battery body
    d.rectangle([bx1 + 1, by0 + 3, bx1 + 2, by1 - 3], fill=BATT_ICON)  # + terminal nub
    # date '-' separator baked in the flat month/day gap (X-variant art, but no repositioned digit here)
    d.rectangle([SEP_X0, SEP_YC - 1, SEP_X1, SEP_YC + 1], fill=SEP_RGB)
    im.save(os.path.join(BUILD5, "000.bmp"))
    bh._bg_cache.clear()          # bg_col samples col-0 (SKY, unaffected by the motifs) — clear to be safe


def build_batt():
    for n in range(10):
        bh.save_blob(bh.glyph_tile(str(n), BATT_DW, BATT_H, BATT_Y, F_BOLD, BATT_FONT, BATT_RGB), 43 + n)


def build_datenums():
    # shared digit set for DAY_NUM (0x30) + MONTH_NUM (0x11), blobs 073..082
    for n in range(10):
        bh.save_blob(bh.glyph_tile(str(n), DNUM_DW, DNUM_H, DATE_Y, F_BOLD, DATE_FONT, DATE_RGB), 73 + n)


def build_weekdays():
    for i, wd in enumerate(WEEKDAYS):    # 066..072
        bh.save_blob(bh.glyph_tile(wd, WD_W, WD_H, DATE_Y, F_BOLD, WD_FONT, WD_RGB), 66 + i)


def build_preview_flat():
    """Carousel preview (blob 092) — MUST be 140x163. FLAT (solid sun, no glow gradient, solid
    panels) so RLE_LINE crushes it -> reclaims the budget the NONE weekday/date glyphs consume."""
    pw, ph = 140, 163
    im = Image.new("RGB", (pw, ph))
    px = im.load()
    hy = 122
    for y in range(ph):
        c = bh.SKY if y < hy else bh.GROUND
        for x in range(pw):
            px[x, y] = c
    d = ImageDraw.Draw(im)
    scx = pw // 2
    d.ellipse([scx - 15, 34, scx + 15, 64], fill=bh.SUN_RGB)          # SOLID sun (RLE-friendly)
    for ci in range(5):                                               # simple panel row
        x0 = 24 + ci * 19
        d.rectangle([x0, hy + 8, x0 + 15, hy + 26], fill=bh.PANEL, outline=bh.PANEL_FRM)
    f = ImageFont.truetype(F_BOLD, 30)
    tb = d.textbbox((0, 0), "78%", font=f)
    d.text((scx - (tb[2] - tb[0]) / 2 - tb[0], 76), "78%", font=f, fill=bh.HERO_RGB)
    im.save(os.path.join(BUILD5, "092.bmp"))


# fields whose geometry we rewrite: type -> (X, Y, W, H)
GEOM = {
    "0xd8": (120, bh.HERO_Y, 24, 36),         # SoC hero — moved down 18px
    "0xd3": (BATT_X, BATT_Y, BATT_DW, BATT_H),  # watch battery -> top-right
    "0x11": (MONTH_X, DATE_Y, DNUM_DW, DNUM_H),  # MONTH_NUM -> date line
    "0x30": (DAY_X, DATE_Y, DNUM_DW, DNUM_H),    # DAY_NUM  -> date line
    "0x60": (WD_X, DATE_Y, WD_W, WD_H),          # DAY_NAME -> weekday, date line
}
NONE_BLOBS = set(range(66, 73))   # weekday 066..072 must switch RLE_LINE -> NONE


def patch_watchface_txt():
    p = os.path.join(BUILD5, "watchface.txt")
    out = []
    for ln in open(p).read().splitlines():
        t = ln.split()
        if len(t) >= 7 and t[0] == "faceData" and t[1].lower() in GEOM:
            x, y, w, h = GEOM[t[1].lower()]
            comment = ln.split("#", 1)[1].strip() if "#" in ln else ""
            out.append(f"faceData        {t[1].lower()}    {t[2]}    {x:>3}  {y:>3}   {w:>3}  {h:>3}          # {comment}")
        elif len(t) >= 2 and t[0] == "blobCompression" and t[1].isdigit() and int(t[1]) in NONE_BLOBS:
            out.append(f"blobCompression {int(t[1]):03d}  NONE")
        else:
            out.append(ln)
    open(p, "w").write("\n".join(out) + "\n")


if __name__ == "__main__":
    build_scene_and_motifs()   # change #1 scene + baked motifs (overwrites 000)
    build_batt()               # change #3 (043-052)
    build_datenums()           # change #2 numerics (073-082)
    build_weekdays()           # change #2 weekday (066-072)
    build_preview_flat()       # 092 (flattened for budget)
    patch_watchface_txt()      # geometry (0xd8/0xd3/0x11/0x30/0x60) + compression (066-072 NONE)
    # hero blobs (053-065), clock (011-020), and untouched blanks stay from the hero3 seed.
    print("built solar-hero5 into", BUILD5)
