#!/usr/bin/env python3
"""Generate assets for the "Solar SoC" MoYoung watch face (MOY-ERJ3, 240x280, Type C).

Three stacked elements on a dark solar theme (top -> bottom):
  1. HERO   : big solar-battery State-of-Charge %, injected via WEATHER_TEMP_CA (0xD8),
              driven by HA `moyoung.weather` temp. The field's two "unit" glyphs (normally
              degC/degF) are both drawn as "%", so the injected value renders as e.g. "78%".
  2. CLOCK  : HH:MM from the watch's own TIME fields (kept synced by moyoung.set_time).
  3. RATE   : current electricity price in cents/kWh, injected via STEPS_GOAL (0x76, driven by
              `moyoung.set_goal`), with a baked "¢/kWh" label beside it.
Plus a small baked sun motif crowning the hero.

Design constraint that shapes everything:
    dawft composites every glyph over the BACKGROUND at build time, but the firmware
    REPOSITIONS value-driven fields at render time (centre-aligned hero shifts with digit
    count; clock/rate slots are fixed). A pre-baked tile only blends seamlessly if the
    background behind the field is X-INVARIANT. So the background is a purely vertical gradient
    + a horizontal amber "sun-horizon" glow (both functions of Y only) wherever a field sits.
    X-variant decoration (sun motif, clock colon, accent line, "¢/kWh" label) is confined to
    gap bands / gaps that no shifting field overlaps. Each glyph tile is built on the exact
    per-row background colour, guaranteeing a seamless composite at any X the firmware chooses.

Outputs into ./glyphs/ : background000.bmp, wt000..012 (hero), ta000..009 (clock digits),
rg000..009 (rate digits), and watchface.txt.  Also writes ./preview.png (3x design mock).
"""
import math
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "glyphs")
os.makedirs(OUT, exist_ok=True)

W, H = 240, 284   # MOY-ERJ3 face-template 60 is 240x284 (verified vs the real CDN face 20078).
                  # We were 240x280 — a 4px shortfall that desynced the per-scanline RLE decode
                  # partway down the framebuffer → "top renders, lower half = colored garbage".

# ---- palette -------------------------------------------------------------
BG_TOP    = (13, 19, 34)     # deep navy (y=0)
BG_BOT    = (5, 8, 15)       # near-black (y=H)
GLOW_RGB  = (255, 170, 62)   # amber sun-horizon
GLOW_Y    = 150              # glow centre
GLOW_SIG  = 46.0             # glow spread (px)
GLOW_AMP  = 0.30             # peak glow strength
# STEPS_GOAL (0x76) renders as corrupted pixels on the MOY-ERJ3 firmware (verified on-glass
# 2026-07-16) — it's the only 2nd injectable numeric slot, so the ¢/kWh rate can't render on
# the face and lives on the HA dashboard instead. Set True only if a firmware/field fix is found.
INCLUDE_RATE = False
# LEAN mode was an attempt to shrink the chunk count, but on-glass it rendered GARBLED even after
# a verified clean direct transfer (102/102 + ack) — so the hard 2-level posterize / flat bg / tiny
# glyphs are mis-rendered by the MOY-ERJ3 firmware. The earlier POSTERIZE-8 + gradient config
# demonstrably rendered SoC+clock cleanly (JP saw "78% and the time"). So LEAN is OFF: use the
# proven-render config. (Transfer reliability is solved separately via a held direct katana flash.)
LEAN = False
POSTERIZE = 2 if LEAN else 8   # glyph edge alpha levels (fewer = smaller RLE, harder edges)
BG_LEAN = (11, 16, 28)         # solid dark navy backing when LEAN (trivially x-invariant)

HERO_RGB  = (255, 201, 74)   # warm solar gold digits (hero)
PCT_RGB   = (245, 176, 62)   # slightly deeper amber for the "%" unit
CLOCK_RGB = (238, 244, 251)  # soft white clock
RATE_RGB  = (240, 200, 120)  # pale gold rate digits
LABEL_RGB = (132, 149, 174)  # muted slate for the ¢/kWh label
SUN_RGB   = (255, 186, 84)   # sun motif

# DejaVu fonts (install `fonts-dejavu`). Override the dir with $MOYOUNG_FONT_DIR if needed.
FONT_DIR = os.environ.get("MOYOUNG_FONT_DIR", "/usr/share/fonts/truetype/dejavu")
F_BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")
F_COND = os.path.join(FONT_DIR, "DejaVuSansCondensed-Bold.ttf")

# ---- geometry (must match watchface.txt) ---------------------------------
if LEAN:                          # tighter glyphs → fewer chunks
    HERO_H, HERO_DW, HERO_PW = 66, 42, 52
    CLOCK_H, CLOCK_DW = 30, 18
    CLOCK_X = [68, 88, 128, 148]
    COLON_CX = 117
    HERO_Y, CLOCK_Y, SUN_CY = 86, 184, 34
else:
    HERO_H, HERO_DW, HERO_PW = 88, 50, 64
    CLOCK_H, CLOCK_DW = 46, 28
    CLOCK_X = [52, 82, 128, 158]  # H1, H2, M1, M2 (colon baked between H2 and M1)
    COLON_CX = 119
    if INCLUDE_RATE:              # 3-element layout (SoC / clock / rate)
        HERO_Y, CLOCK_Y, SUN_CY = 48, 162, 26
    else:                         # 2-element layout (SoC / clock), centred + roomier
        HERO_Y, CLOCK_Y, SUN_CY = 68, 196, 32
RATE_Y, RATE_H, RATE_DW = 226, 36, 22
RATE_X = 70                       # STEPS_GOAL is left-aligned; digits start here
RATE_LABEL_X = 122                # baked "¢/kWh" begins here (right of the 2 digits)
SUN_CX, SUN_R = 120, 9
HERO_FONT = 62 if LEAN else 78
PCT_FONT  = 46 if LEAN else 62
CLOCK_FONT = 26 if LEAN else 42

# ---- x-invariant background base colour, per row -------------------------
def _clamp(v):
    return max(0, min(255, int(round(v))))

def bg_col(y):
    """Base background colour at row y: vertical gradient + horizontal glow. X-invariant.
    In LEAN mode it's a solid dark navy — trivially x-invariant and compresses to almost nothing,
    and makes every glyph tile's backing uniform (best-case RLE)."""
    if LEAN:
        return BG_LEAN
    t = y / (H - 1)
    r = BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t
    g = BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t
    b = BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t
    w = GLOW_AMP * pow(2.718281828, -((y - GLOW_Y) ** 2) / (2 * GLOW_SIG ** 2))
    r += (GLOW_RGB[0] - r) * w
    g += (GLOW_RGB[1] - g) * w
    b += (GLOW_RGB[2] - b) * w
    return (_clamp(r), _clamp(g), _clamp(b))

def make_background():
    im = Image.new("RGB", (W, H))
    px = im.load()
    for y in range(H):
        c = bg_col(y)
        for x in range(W):
            px[x, y] = c
    d = ImageDraw.Draw(im)

    # --- x-variant decoration, ONLY in gap bands (no shifting field overlaps these) ---
    if not LEAN:   # LEAN drops baked decoration to minimise chunks (flat bg + colon only)
        # sun motif crowning the hero (top gap band)
        d.ellipse([SUN_CX - SUN_R, SUN_CY - SUN_R, SUN_CX + SUN_R, SUN_CY + SUN_R], fill=SUN_RGB)
        for k in range(12):
            ang = k * math.pi / 6
            r0, r1 = SUN_R + 3, SUN_R + 7
            d.line([(SUN_CX + r0 * math.cos(ang), SUN_CY + r0 * math.sin(ang)),
                    (SUN_CX + r1 * math.cos(ang), SUN_CY + r1 * math.sin(ang))],
                   fill=SUN_RGB, width=2)
        # thin amber accent line under the hero (gap band between hero and clock)
        ly = (HERO_Y + HERO_H + CLOCK_Y) // 2
        d.line([(72, ly), (168, ly)], fill=(206, 136, 52))
        d.line([(72, ly + 1), (168, ly + 1)], fill=(150, 96, 40))

    # clock colon ':' baked between the fixed H2/M1 slots (that x-gap never holds a digit)
    col_font = ImageFont.truetype(F_BOLD, CLOCK_FONT + 4)
    cb = d.textbbox((0, 0), ":", font=col_font)
    colon_cy = CLOCK_Y + CLOCK_H / 2 - 1
    d.text((COLON_CX - (cb[2] - cb[0]) / 2 - cb[0],
            colon_cy - (cb[3] - cb[1]) / 2 - cb[1]), ":", font=col_font, fill=CLOCK_RGB)

    # "¢/kWh" label baked beside the (fixed, left-aligned) rate digits
    if INCLUDE_RATE:
        lab_font = ImageFont.truetype(F_COND, 15)
        lb = d.textbbox((0, 0), "¢/kWh", font=lab_font)
        d.text((RATE_LABEL_X, RATE_Y + RATE_H / 2 - (lb[3] + lb[1]) / 2),
               "¢/kWh", font=lab_font, fill=LABEL_RGB)

    im.save(os.path.join(OUT, "background000.bmp"))
    return im

# ---- glyph tile: glyph composited over the EXACT per-row bg base (x-invariant) --------
def glyph_tile(text, box_w, box_h, y0, font, color, dy=0, levels=POSTERIZE):
    """Return an RGB tile whose backing == the exact background at absolute rows y0..y0+box_h
    (so it composites seamlessly wherever the firmware repositions it), with `text` centred.

    The glyph is a supersampled ALPHA MASK, downsampled for smooth edges, its alpha posterized
    to `levels` steps, then composited over the exact per-row bg colour. Posterizing the *mask*
    keeps alpha=0 pixels byte-identical to the real background (no tile seams) while collapsing
    anti-aliased edges to a few colours per row -> long RLE_LINE runs -> a much smaller .bin."""
    S = 4
    mask_big = Image.new("L", (box_w * S, box_h * S), 0)
    dm = ImageDraw.Draw(mask_big)
    bf = ImageFont.truetype(font.path, font.size * S)
    bb = dm.textbbox((0, 0), text, font=bf)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    tx = (box_w * S - tw) / 2 - bb[0]
    ty = (box_h * S - th) / 2 - bb[1] + dy * S
    dm.text((tx, ty), text, font=bf, fill=255)
    mask = mask_big.resize((box_w, box_h), Image.LANCZOS)
    mpx = mask.load()

    out = Image.new("RGB", (box_w, box_h))
    opx = out.load()
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
                opx[i, j] = (bg[0] + (color[0] - bg[0]) * a // 255,
                             bg[1] + (color[1] - bg[1]) * a // 255,
                             bg[2] + (color[2] - bg[2]) * a // 255)
    return out

class F:
    def __init__(self, path, size):
        self.path = path; self.size = size

def save(im, name):
    im.save(os.path.join(OUT, name))

def build_glyphs():
    hero_f, pct_f = F(F_BOLD, HERO_FONT), F(F_BOLD, PCT_FONT)
    for n in range(10):
        save(glyph_tile(str(n), HERO_DW, HERO_H, HERO_Y, hero_f, HERO_RGB), f"wt{n:03d}.bmp")
    save(glyph_tile(" ", HERO_DW, HERO_H, HERO_Y, hero_f, HERO_RGB), "wt010.bmp")  # minus slot: blank (never shown for SoC 0-100; compresses to ~nothing)
    save(glyph_tile("%", HERO_PW, HERO_H, HERO_Y, pct_f, PCT_RGB), "wt011.bmp")    # unit glyph A (degC slot)
    save(glyph_tile("%", HERO_PW, HERO_H, HERO_Y, pct_f, PCT_RGB), "wt012.bmp")    # unit glyph B (degF slot)

    clock_f = F(F_BOLD, CLOCK_FONT)
    for n in range(10):
        save(glyph_tile(str(n), CLOCK_DW, CLOCK_H, CLOCK_Y, clock_f, CLOCK_RGB), f"ta{n:03d}.bmp")

    if INCLUDE_RATE:
        rate_f = F(F_BOLD, 32)
        for n in range(10):
            save(glyph_tile(str(n), RATE_DW, RATE_H, RATE_Y, rate_f, RATE_RGB), f"rg{n:03d}.bmp")

# ---- watchface.txt -------------------------------------------------------
def write_watchface_txt():
    n_blobs = 64 if INCLUDE_RATE else 54
    n_data = 7 if INCLUDE_RATE else 6
    lines = [
        "fileType       C",
        "fileID         0x81",
        f"dataCount      {n_data}",
        f"blobCount      {n_blobs}",
        "faceNumber     60002",
        "",
        "#              TYPE  INDEX      X    Y    W    H    FILENAME",
        "faceData       0x01    000      0    0  240  284    background000.bmp   # BACKGROUND",
        f"faceData       0xD8    001    120   {HERO_Y}   {HERO_DW}   {HERO_H}    wt000.bmp           # SoC%% hero (WEATHER_TEMP_CA)",
        f"faceData       0x40    014    {CLOCK_X[0]:>3}  {CLOCK_Y}   {CLOCK_DW}   {CLOCK_H}    ta000.bmp           # TIME_H1",
        f"faceData       0x41    024    {CLOCK_X[1]:>3}  {CLOCK_Y}   {CLOCK_DW}   {CLOCK_H}    ta000.bmp           # TIME_H2",
        f"faceData       0x43    034    {CLOCK_X[2]:>3}  {CLOCK_Y}   {CLOCK_DW}   {CLOCK_H}    ta000.bmp           # TIME_M1",
        f"faceData       0x44    044    {CLOCK_X[3]:>3}  {CLOCK_Y}   {CLOCK_DW}   {CLOCK_H}    ta000.bmp           # TIME_M2",
    ]
    if INCLUDE_RATE:
        lines.append(f"faceData       0x76    054    {RATE_X:>3}  {RATE_Y}   {RATE_DW}   {RATE_H}    rg000.bmp           # RATE cents (STEPS_GOAL)")
    lines += [
        "",
        "# Compress every blob (dawft's C initializer only marks blob 0; the rest default to NONE).",
        "#             INDEX  CTYPE",
    ]
    for i in range(n_blobs):
        lines.append(f"blobCompression {i:03d}  TRY_RLE")
    lines.append("")
    with open(os.path.join(OUT, "watchface.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")

# ---- design preview (independent of dawft) -------------------------------
def make_preview(bg):
    im = bg.copy()
    # hero "78%" centred on x=120
    dig, pf = F(F_BOLD, HERO_FONT), F(F_BOLD, PCT_FONT)
    parts = [("7", HERO_DW, dig, HERO_RGB), ("8", HERO_DW, dig, HERO_RGB), ("%", HERO_PW, pf, PCT_RGB)]
    total = sum(p[1] for p in parts) + 2 * (len(parts) - 1)
    x = int(120 - total / 2)
    for txt, bw, fnt, col in parts:
        im.paste(glyph_tile(txt, bw, HERO_H, HERO_Y, fnt, col), (x, HERO_Y)); x += bw + 2
    # clock 18:46 at fixed slots
    cf = F(F_BOLD, CLOCK_FONT)
    for txt, cx in zip(["1", "8", "4", "6"], CLOCK_X):
        im.paste(glyph_tile(txt, CLOCK_DW, CLOCK_H, CLOCK_Y, cf, CLOCK_RGB), (cx, CLOCK_Y))
    # rate "54" (current summer peak ¢/kWh) left-aligned at RATE_X
    if INCLUDE_RATE:
        rf = F(F_BOLD, 32)
        for k, txt in enumerate(["5", "4"]):
            im.paste(glyph_tile(txt, RATE_DW, RATE_H, RATE_Y, rf, RATE_RGB), (RATE_X + k * (RATE_DW + 2), RATE_Y))
    im.resize((W * 3, H * 3), Image.NEAREST).save(os.path.join(HERE, "preview.png"))

if __name__ == "__main__":
    bg = make_background()
    build_glyphs()
    write_watchface_txt()
    make_preview(bg)
    print("assets written to", OUT)
