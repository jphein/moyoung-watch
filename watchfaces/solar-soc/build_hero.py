#!/usr/bin/env python3
"""Build the FINAL clean big-SoC "solar-hero3" watch face for the MOY-ERJ3 (MoYoung-v2).

Background is a SOLAR SCENE image (JP's pick): a warm SUN in the top zone over a stylized
SOLAR-PANEL array on the horizon, deep flat dusk sky behind.  (Change #3 revised: no gradient.)

HALO / COMPOSITE DISCIPLINE
  Every value-driven glyph is ONE blob the firmware repositions at render time, so a glyph
  tile must composite seamlessly at *every* X the value can occupy -> the background BEHIND
  each such field must be X-INVARIANT (uniform across X at each Y).  We therefore confine all
  X-variant art (sun disc/rays/glow, panel array) to Y-bands that hold NO repositioning field,
  and keep the field bands FLAT (per-row uniform):
    0-66    SUN zone            (no field)                       -> X-variant art OK
    70-106  hero 0xd8           (center-aligned, repositioned)   -> FLAT sky
    124-137 BATT/DAY_NUM/MONTH  (multi-slot digits)              -> FLAT sky
    153-199 TIME + AM/PM + icons(shared digit blobs @ many x)    -> FLAT sky
    206-254 PANEL band          (only DAY_NAME @ fixed x=12)      -> X-variant art OK
    258-270 HR/KCAL/STEPS       (multi-slot digits)              -> FLAT ground
  Glyph tiles + uniform blanks composite over bg_col(y) (the scene's per-row colour, valid in
  the flat bands).  DAY_NAME is FIXED-position, so it's blanked by copying the ACTUAL scene
  pixels under it -> invisible even over the panels.

The rest of the recipe is unchanged: hero 0xd8 = 24x36 singles / 48x36 double-width units,
compression NONE; secondary numeric fields NONE + blanked; keep all 18 faceData fields; only
faceData edit is the 0xd8 geometry; preview blob 092 shrunk (must stay 140x163 per dawft/fw);
background may be RLE_LINE; target <= 86KB.
"""
import math
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")

W, H = 240, 284

# ---- palette -------------------------------------------------------------
SKY        = (21, 24, 45)     # flat deep dusk indigo (behind hero + clock; X-invariant)
GROUND     = (9, 11, 19)      # dark foreground beneath the panels
SUN_RGB    = (255, 198, 86)   # sun disc
SUN_CORE   = (255, 224, 150)  # sun disc centre
GLOW_RGB   = (255, 150, 60)   # warm radial glow around the sun (confined to y<=66)
HORIZON    = (86, 52, 46)     # thin warm horizon line where sun-light meets the array
PANEL      = (32, 58, 104)    # photovoltaic blue
PANEL_HI   = (120, 160, 220)  # top-edge specular highlight
PANEL_FRM  = (12, 22, 44)     # cell frame lines
PANEL_GLNT = (150, 170, 205)  # sun-glint cells (top-centre, facing the sun)

HERO_RGB  = (255, 205, 92)    # warm solar gold digits (hero)
PCT_RGB   = (245, 180, 70)    # slightly deeper amber for the "%" unit
CLOCK_RGB = (236, 242, 250)   # soft white clock
COLON_RGB = (150, 165, 185)   # muted slate colon

def find_font(name):
    """Resolve a DejaVu TTF across distros: $MOYOUNG_FONT_DIR, common system dirs, else bare
    name (PIL then searches its own font path). Install `fonts-dejavu` (Debian/Ubuntu/HAOS)."""
    for d in (os.environ.get("MOYOUNG_FONT_DIR"),
              "/usr/share/fonts/truetype/dejavu",             # Debian / Ubuntu / HAOS
              "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF",  # Fedora / Arch
              "/Library/Fonts", "/System/Library/Fonts/Supplemental"):  # macOS
        if d and os.path.exists(os.path.join(d, name)):
            return os.path.join(d, name)
    return name

FONT_DIR = os.environ.get("MOYOUNG_FONT_DIR", "/usr/share/fonts/truetype/dejavu")
F_BOLD = find_font("DejaVuSans-Bold.ttf")

# ---- geometry ------------------------------------------------------------
HERO_X, HERO_Y = 120, 72      # 0xd8 anchor: X=120 face centre (firmware centres value); above clock
HERO_DW, HERO_H = 24, 36
HERO_PW = 48                  # double-width unit glyph (= 2*HERO_DW, RULE B)
HERO_FONT = 40
PCT_FONT  = 38
CLOCK_Y, CLOCK_DW, CLOCK_H = 155, 33, 44
CLOCK_FONT = 40

SUN_CX, SUN_CY, SUN_R = 120, 30, 18   # sun (top zone, above the hero band which starts at 70)
GLOW_SIG = 20.0
GLOW_AMP = 0.55
GLOW_YMAX = 66                        # hard clip: no glow contribution at/below this row

PANEL_YTOP, PANEL_YBOT = 233, 256     # solar-array band: BELOW DAY_NAME(211-232), ABOVE HR(258)
                                      # so no glyph field crosses the (X-variant) panels.

def _clamp(v):
    return max(0, min(255, int(round(v))))

def _lerp(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))

# ---- the scene image (built once; bg_col samples it) ---------------------
SCENE = None

def make_background():
    global SCENE
    im = Image.new("RGB", (W, H))
    px = im.load()
    # flat sky above the horizon, flat ground below
    for y in range(H):
        base = SKY if y < PANEL_YTOP - 2 else GROUND
        for x in range(W):
            px[x, y] = base
    d = ImageDraw.Draw(im)

    # --- SUN: soft radial glow (clipped to top zone) + disc + rays ---
    for y in range(0, GLOW_YMAX + 1):
        for x in range(W):
            dist = math.hypot(x - SUN_CX, y - SUN_CY)
            w = GLOW_AMP * math.exp(-(dist * dist) / (2 * GLOW_SIG * GLOW_SIG))
            if w > 0.003:
                cur = px[x, y]
                px[x, y] = tuple(_clamp(cur[i] + (GLOW_RGB[i] - cur[i]) * w) for i in range(3))
    for k in range(12):                       # rays (kept above the hero band)
        ang = k * math.pi / 6
        r0, r1 = SUN_R + 5, SUN_R + 13
        d.line([(SUN_CX + r0 * math.cos(ang), SUN_CY + r0 * math.sin(ang)),
                (SUN_CX + r1 * math.cos(ang), SUN_CY + r1 * math.sin(ang))],
               fill=SUN_RGB, width=2)
    d.ellipse([SUN_CX - SUN_R, SUN_CY - SUN_R, SUN_CX + SUN_R, SUN_CY + SUN_R], fill=SUN_RGB)
    d.ellipse([SUN_CX - SUN_R // 2, SUN_CY - SUN_R // 2, SUN_CX + SUN_R // 2, SUN_CY + SUN_R // 2],
              fill=SUN_CORE)

    # --- horizon line + SOLAR-PANEL array (perspective grid) ---
    d.line([(0, PANEL_YTOP - 3), (W, PANEL_YTOP - 3)], fill=HORIZON, width=2)
    xlt, xrt = 80, 160        # array footprint: narrower at top (far), wider at bottom (near)
    xlb, xrb = 28, 212
    COLS, ROWS = 6, 2
    def edges(y):
        t = (y - PANEL_YTOP) / (PANEL_YBOT - PANEL_YTOP)
        return xlt + (xlb - xlt) * t, xrt + (xrb - xrt) * t
    for ri in range(ROWS):
        yt = PANEL_YTOP + (PANEL_YBOT - PANEL_YTOP) * ri / ROWS
        yb = PANEL_YTOP + (PANEL_YBOT - PANEL_YTOP) * (ri + 1) / ROWS
        xltc, xrtc = edges(yt)
        xlbc, xrbc = edges(yb)
        for ci in range(COLS):
            f0, f1 = ci / COLS, (ci + 1) / COLS
            quad = [(xltc + (xrtc - xltc) * f0, yt), (xltc + (xrtc - xltc) * f1, yt),
                    (xlbc + (xrbc - xlbc) * f1, yb), (xlbc + (xrbc - xlbc) * f0, yb)]
            glint = (ri == 0 and 1 <= ci <= 4)   # top row centre cells catch the sun
            d.polygon(quad, fill=(PANEL_GLNT if glint else PANEL), outline=PANEL_FRM)
    d.line([(xlt, PANEL_YTOP), (xrt, PANEL_YTOP)], fill=PANEL_HI, width=1)  # specular top edge

    im.save(os.path.join(BUILD, "000.bmp"))
    SCENE = im
    _bg_cache.clear()
    bake_colon(im)   # colon is baked AFTER caching-clear; it lives in the clock gap only
    return im

def bake_colon(im):
    """':' baked into the fixed clock gap (x~101, between H2 end ~87 and M1 start 117). No digit
    ever lands at that x, so this X-variant mark doesn't affect any glyph composite."""
    d = ImageDraw.Draw(im)
    cf = ImageFont.truetype(F_BOLD, CLOCK_FONT + 2)
    cx, cyc = 101, CLOCK_Y + CLOCK_H / 2 - 1
    cb = d.textbbox((0, 0), ":", font=cf)
    d.text((cx - (cb[2] - cb[0]) / 2 - cb[0], cyc - (cb[3] - cb[1]) / 2 - cb[1]),
           ":", font=cf, fill=COLON_RGB)
    im.save(os.path.join(BUILD, "000.bmp"))

_bg_cache = {}
def bg_col(y):
    """Scene's per-row colour (sampled at column 0). Valid ONLY in the flat X-invariant bands,
    which is exactly where glyph tiles / uniform blanks composite. Both the background blob and
    the glyph blobs go through dawft's identical RGB565 encode, so they match on-glass."""
    y = max(0, min(H - 1, y))
    if y not in _bg_cache:
        _bg_cache[y] = SCENE.getpixel((0, y))
    return _bg_cache[y]

# ---- halo-free glyph tile over the exact per-row bg ----------------------
def glyph_tile(text, box_w, box_h, y0, font_path, font_size, color, levels=48):
    S = 4
    mask_big = Image.new("L", (box_w * S, box_h * S), 0)
    dm = ImageDraw.Draw(mask_big)
    bf = ImageFont.truetype(font_path, font_size * S)
    bb = dm.textbbox((0, 0), text, font=bf)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    tx = (box_w * S - tw) / 2 - bb[0]
    ty = (box_h * S - th) / 2 - bb[1]
    dm.text((tx, ty), text, font=bf, fill=255)
    mask = mask_big.resize((box_w, box_h), Image.LANCZOS)
    mpx = mask.load()
    out = Image.new("RGB", (box_w, box_h)); opx = out.load()
    step = 255.0 / (levels - 1)
    for j in range(box_h):
        bg = bg_col(y0 + j)
        for i in range(box_w):
            a = int(round(round(mpx[i, j] / step) * step))
            if a <= 0:
                opx[i, j] = bg
            elif a >= 255:
                opx[i, j] = color
            else:
                opx[i, j] = tuple(bg[c] + (color[c] - bg[c]) * a // 255 for c in range(3))
    return out

def blank_tile(box_w, box_h, y0):
    """Solid per-row bg tile -> invisible in a flat (X-invariant) band."""
    out = Image.new("RGB", (box_w, box_h)); opx = out.load()
    for j in range(box_h):
        c = bg_col(y0 + j)
        for i in range(box_w):
            opx[i, j] = c
    return out

def blank_fixed(x0, y0, box_w, box_h):
    """Copy the ACTUAL scene under a FIXED-position field -> invisible even over X-variant art."""
    return SCENE.crop((x0, y0, x0 + box_w, y0 + box_h)).copy()

def save_blob(im, idx):
    im.save(os.path.join(BUILD, f"{idx:03d}.bmp"))
    raw = os.path.join(BUILD, f"{idx:03d}.raw")
    if os.path.exists(raw):
        os.remove(raw)

# ---- build ---------------------------------------------------------------
def build_hero():
    for n in range(10):
        save_blob(glyph_tile(str(n), HERO_DW, HERO_H, HERO_Y, F_BOLD, HERO_FONT, HERO_RGB), 53 + n)
    save_blob(blank_tile(HERO_DW, HERO_H, HERO_Y), 63)                                   # minus slot
    save_blob(glyph_tile("%", HERO_PW, HERO_H, HERO_Y, F_BOLD, PCT_FONT, PCT_RGB), 64)   # unit A
    save_blob(glyph_tile("%", HERO_PW, HERO_H, HERO_Y, F_BOLD, PCT_FONT, PCT_RGB), 65)   # unit B

def build_clock():
    for n in range(10):
        save_blob(glyph_tile(str(n), CLOCK_DW, CLOCK_H, CLOCK_Y, F_BOLD, CLOCK_FONT, CLOCK_RGB), 11 + n)

# uniform-blank sets (all sit in FLAT X-invariant bands): (start, count, w, h, y0)
BLANK_UNIFORM = [
    (1, 10, 13, 17, 178),   # 0x47/0x48 weather-icon slots (clock band, flat sky)
    (21, 10, 9, 12, 258),   # HR_B_CA (flat ground)
    (31, 10, 9, 12, 258),   # KCAL_B_CA / STEPS_B_CA (flat ground)
    (41, 1, 21, 17, 153),   # TIME_AM
    (42, 1, 21, 17, 153),   # TIME_PM
    (43, 10, 9, 12, 125),   # BATT_CA (flat sky)
    (66, 7, 218, 21, 211),  # DAY_NAME (flat sky/horizon band, above the panels -> uniform blank)
    (73, 10, 9, 12, 124),   # DAY_NUM / MONTH_NUM (flat sky)
    (83, 9, 19, 19, 119),   # 0xd6 unknown icon (flat sky)
]

def build_blanks():
    for start, count, w, h, y0 in BLANK_UNIFORM:
        tile = blank_tile(w, h, y0)
        for k in range(count):
            save_blob(tile, start + k)

def build_preview():
    """Carousel preview (LAST blob 092) — MUST be 140x163 (hardcoded in dawft.c:1095/1098 and fw)."""
    pw, ph = 140, 163
    im = Image.new("RGB", (pw, ph)); px = im.load()
    hy = 128   # preview horizon
    for y in range(ph):
        c = SKY if y < hy else GROUND
        for x in range(pw):
            px[x, y] = c
    d = ImageDraw.Draw(im)
    # sun + glow
    scx, scy, sr = pw // 2, 40, 16
    for y in range(0, 70):
        for x in range(pw):
            dist = math.hypot(x - scx, y - scy)
            w = 0.5 * math.exp(-(dist * dist) / (2 * 18.0 * 18.0))
            if w > 0.004:
                cur = px[x, y]; px[x, y] = tuple(_clamp(cur[i] + (GLOW_RGB[i] - cur[i]) * w) for i in range(3))
    d.ellipse([scx - sr, scy - sr, scx + sr, scy + sr], fill=SUN_RGB)
    # panel array
    for ci in range(5):
        x0 = 18 + ci * 21
        d.polygon([(x0 + 3, hy + 4), (x0 + 18, hy + 4), (x0 + 20, hy + 22), (x0 + 1, hy + 22)],
                  fill=PANEL, outline=PANEL_FRM)
    # "78%"
    f = ImageFont.truetype(F_BOLD, 34)
    tb = d.textbbox((0, 0), "78%", font=f)
    d.text((scx - (tb[2] - tb[0]) / 2 - tb[0], 74), "78%", font=f, fill=HERO_RGB)
    im.save(os.path.join(BUILD, "092.bmp"))
    return im

def patch_watchface_txt():
    p = os.path.join(BUILD, "watchface.txt")
    out = []
    for ln in open(p).read().splitlines():
        toks = ln.split()
        if len(toks) >= 7 and toks[0] == "faceData" and toks[1].lower() == "0xd8":
            out.append(f"faceData        0xd8    053    {HERO_X:>3}   {HERO_Y}   {HERO_DW}   {HERO_H}          # WEATHER_TEMP_CA")
        else:
            out.append(ln)
    open(p, "w").write("\n".join(out) + "\n")

if __name__ == "__main__":
    make_background()
    build_hero()
    build_clock()
    build_blanks()
    build_preview()
    patch_watchface_txt()
    print("built into", BUILD)
