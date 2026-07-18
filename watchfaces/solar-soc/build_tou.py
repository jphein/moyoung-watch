#!/usr/bin/env python3
"""Build the THREE PG&E-TOU-period REDESIGNED solar faces from ONE builder (offline, NO flash).

Each face is IDENTICAL in LAYOUT across the three, differing only by palette, and carries JP's
proven redesign (verified on-glass on the PEAK face 2026-07-17):

  solar-offpeak.bin  faceNumber 60002  GREEN   label "OFF-PEAK"   (cheap, relaxed)
  solar-partial.bin  faceNumber 60003  AMBER   label "PART PEAK"  (moderate)
  solar-peak.bin     faceNumber 60004  RED     label "PEAK"       (expensive, conserve)

REDESIGN (all ADDITIVE; proven render config preserved):
  #1 Drop the leading zero on the hour  — TIME_H1 (0x40) gets its OWN 10-frame set at blobs
     092-101 whose frame-0 is a BLANK tile (exact per-row sky). Firmware picks frame = hour-tens
     digit -> in 12h mode hour-tens is 0 for hours 1-9 -> H1 blanks -> "9:05", not "09:05". Only
     frame-1 ('1', for 10/11/12) is a real digit; frames 0 and 2-9 are blank to stay in RLE budget.
  #2 AM/PM — rendered into the already-declared 0x45/0x46 fields (were blank), superscript right
     of the minutes, per-theme accent colour.
  #3 Centre each line — hero is *_CA (firmware-centred); once H1 blanks, the common single-digit
     hour auto-centres at x~121 (margins 54/52) so NO clock geometry changes (preserves the proven
     RLE bg + baked colon exactly). AM/PM sits superscript-right (can't x120-centre without hitting
     the clock; no vertical room for a separate line).

A parallel EXPLORATORY set of TEST faces adds #4 (analog second hand 0xF3 reimagined as an orbiting
spark) with their OWN unique faceNumbers so they cannot collide with or taint the shippable faces:
  solar-offpeak-spark.bin 60010 · solar-partial-spark.bin 60011 · solar-peak-spark.bin 60012
Spark is HIGH RISK: dawft `create` packs opaque RGB565 (drops alpha), so a clean orbit needs the
firmware to colour-key the black field transparent — UNKNOWN. Quarantined by faceNumber.

PROVEN CONFIG PRESERVED (only ADD): 240x284, fileType C / fileID 0x81, unique faceNumber, x-invariant
bg (glyph tiles over exact per-row bg), bg blob RLE / value glyphs NONE (RLE garbles them — the clock
digits 011-020 are the standing RLE exception, which the H1 set inherits), 140x163 preview kept as the
LAST blob (dawft.c: preview == blobCount-1). New blobs APPENDED; preview renumbered to stay last.
"""
import os
import shutil
import subprocess
from PIL import Image, ImageDraw, ImageFont
import build_hero as bh

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.join(HERE, "build5")          # hero5 build dir -> source of watchface.txt
# dawft face-packer (GPL, upstream david47k/dawft — fetch it with ../dawft/get-dawft.sh).
# Resolve in order: $DAWFT env var, `dawft` on PATH, then the vendored build at ../dawft/dawft.
DAWFT = (os.environ.get("DAWFT")
         or shutil.which("dawft")
         or os.path.join(HERE, os.pardir, "dawft", "dawft"))
SHIFT = 18                                    # hero5 sun+hero down-shift

# hero5 field geometry (unchanged across themes; matches build5/watchface.txt)
BATT_X, BATT_Y, BATT_DW, BATT_H, BATT_FONT = 207, 9, 10, 14, 15
DATE_Y = 203
# DATE-DIGIT WHITE-BLOCK FIX (2026-07-18): DNUM_DW was 13 (ODD) -> RGB565 row = 13*2 = 26 bytes,
# NOT 4-byte aligned. This firmware's raw (NONE) blit assumes a 4-byte-aligned scanline stride, so
# an odd-width value-glyph shears into garbage "white blocks" on-glass. Every OTHER visible NONE
# glyph is even-width and renders fine (hero 24, battery 10, weekday 40); the working vendor face
# (dawft example1, fileType C) uses 12-wide date digits. Fix = EVEN width (12, vendor-proven). The
# clock is odd (33) but RLE_LINE, decoded into an aligned buffer, so it's immune. Compression was
# ALREADY NONE (the RLE hypothesis was disproven) — this is purely a stride/alignment fix.
DNUM_DW, DNUM_H, DATE_FONT = 12, 16, 18
MONTH_X, DAY_X = 114, 158
SEP_X0, SEP_X1 = 146, 154
WD_X, WD_W, WD_H, WD_FONT = 54, 40, 16, 15
WEEKDAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]  # 0x60 order -> 066..072

# LABEL banner: baked static text in the clear sky band between hero (ends y126) and clock
# (starts y155). NO repositioning glyph lives here except the fixed 0xd6 icon at x11-30, so
# the centred pill (kept well right of x40) never collides with any glyph composite/blank.
LBL_YC = 141                                  # banner vertical centre
LBL_H = 22
LBL_FONT = 17

# --- redesign field parameters ----------------------------------------------
H1_BASE = 92                                  # dedicated TIME_H1 set 092..101
H1_X, H1_W = 16, 34                           # H1 tens-hour slot @ x16; EVEN width (33->34) so the
                                              # set can be NONE (stride-safe, like the even-width date
                                              # fix that rendered on-glass) with a RAW pixel-exact
                                              # blank frame-0 -> invisible with no RLE-flat decode.
SPARK_IDX = 102                               # HAND_SEC blob (spark variant only)
# AM/PM superscript, just right of M2 (ends x188). Over flat sky -> composites seamlessly.
AMPM_X, AMPM_Y, AMPM_W, AMPM_H, AMPM_FONT = 193, 157, 26, 18, 14
# SPARK hand tile: pivot = bottom-centre = face centre (120,142); spark near the top -> orbits rim.
SPARK_X, SPARK_Y, SPARK_W, SPARK_H = 105, 22, 30, 120
FACE_CX, FACE_CY = 120, 142


THEMES = [
    dict(
        name="offpeak", face="solar-offpeak.bin", facenum=60002, spark_facenum=60010, label="OFF-PEAK",
        desc="GREEN off-peak: cool mint sun+glow, green panel glints & accents, bright mint-white SoC hero",
        SKY=(12, 34, 26), GROUND=(6, 18, 14),
        SUN_RGB=(120, 230, 140), SUN_CORE=(200, 255, 205), GLOW_RGB=(70, 200, 110),
        HORIZON=(34, 78, 50),
        PANEL=(28, 70, 60), PANEL_HI=(130, 220, 170), PANEL_FRM=(10, 30, 24), PANEL_GLNT=(150, 230, 175),
        HERO_RGB=(224, 255, 228), PCT_RGB=(150, 235, 165),
        CLOCK_RGB=(232, 248, 238), COLON_RGB=(120, 175, 140),
        DATE_RGB=(206, 232, 214), WD_RGB=(140, 220, 160), SEP_RGB=(120, 165, 140),
        BATT_RGB=(150, 205, 175), BATT_ICON=(110, 165, 135),
        PILL_FILL=(18, 52, 40), PILL_BORDER=(70, 200, 110), LBL_RGB=(170, 245, 185),
        AMPM_RGB=(205, 245, 215), SPARK_CORE=(235, 255, 240), SPARK_HALO=(90, 225, 140),
    ),
    dict(
        name="partial", face="solar-partial.bin", facenum=60003, spark_facenum=60011, label="PART PEAK",
        desc="AMBER partial-peak: warm orange sun+glow, amber panel glints & accents, pale warm-white SoC hero",
        SKY=(38, 28, 12), GROUND=(20, 14, 6),
        SUN_RGB=(255, 176, 60), SUN_CORE=(255, 214, 130), GLOW_RGB=(245, 140, 40),
        HORIZON=(96, 58, 24),
        PANEL=(86, 62, 30), PANEL_HI=(230, 180, 110), PANEL_FRM=(34, 22, 8), PANEL_GLNT=(235, 190, 120),
        HERO_RGB=(255, 244, 220), PCT_RGB=(250, 190, 90),
        CLOCK_RGB=(248, 240, 226), COLON_RGB=(180, 150, 110),
        DATE_RGB=(236, 226, 210), WD_RGB=(240, 190, 110), SEP_RGB=(180, 150, 110),
        BATT_RGB=(215, 190, 150), BATT_ICON=(170, 145, 110),
        PILL_FILL=(56, 40, 18), PILL_BORDER=(245, 140, 40), LBL_RGB=(255, 200, 110),
        AMPM_RGB=(255, 225, 190), SPARK_CORE=(255, 244, 220), SPARK_HALO=(255, 170, 60),
    ),
    dict(
        name="peak", face="solar-peak.bin", facenum=60004, spark_facenum=60012, label="PEAK",
        desc="RED peak: warm red sun+glow, red panel glints & accents, pale warm-white SoC hero (conserve)",
        SKY=(42, 16, 16), GROUND=(22, 8, 8),
        SUN_RGB=(255, 90, 70), SUN_CORE=(255, 160, 130), GLOW_RGB=(235, 70, 50),
        HORIZON=(98, 34, 28),
        PANEL=(92, 40, 40), PANEL_HI=(235, 140, 120), PANEL_FRM=(36, 14, 14), PANEL_GLNT=(240, 150, 130),
        HERO_RGB=(255, 232, 224), PCT_RGB=(255, 130, 105),
        CLOCK_RGB=(250, 234, 230), COLON_RGB=(185, 120, 110),
        DATE_RGB=(240, 222, 218), WD_RGB=(240, 150, 130), SEP_RGB=(185, 120, 110),
        BATT_RGB=(215, 165, 158), BATT_ICON=(170, 120, 115),
        PILL_FILL=(58, 24, 22), PILL_BORDER=(235, 70, 50), LBL_RGB=(255, 150, 130),
        AMPM_RGB=(255, 205, 195), SPARK_CORE=(255, 244, 226), SPARK_HALO=(255, 96, 74),
    ),
]


def apply_palette(t, build_dir):
    """Push a theme's palette + hero5 geometry into build_hero's module globals."""
    bh.BUILD = build_dir
    # hero5 geometry shift
    bh.SUN_CY = 30 + SHIFT       # 48
    bh.GLOW_YMAX = 66 + SHIFT    # 84 (glow clips 6px above hero band @ 90)
    bh.HERO_Y = 72 + SHIFT       # 90
    # FULL sun-glow (sigma 20.0 = the hero5-proven value) for ALL themes. Earlier builds tightened
    # this for a size budget, but there is NO size cap — the stock ref face erj3_ref_20078.bin is
    # 122 KB and runs fine; chunked upload handles any size — so we restore full quality. Glow
    # hard-clips at GLOW_YMAX=84, 6px above the hero band (y90), so the hero band stays flat / X-invariant.
    bh.GLOW_SIG = 20.0
    # palette
    for k in ("SKY", "GROUND", "SUN_RGB", "SUN_CORE", "GLOW_RGB", "HORIZON",
              "PANEL", "PANEL_HI", "PANEL_FRM", "PANEL_GLNT",
              "HERO_RGB", "PCT_RGB", "CLOCK_RGB", "COLON_RGB"):
        setattr(bh, k, t[k])


def build_scene_and_motifs(t):
    """Themed shifted-sun scene + baked motifs (battery outline, date dash, LABEL banner)."""
    bh.make_background()                 # writes <build>/000.bmp (theme sun/glow/panels + colon)
    im = bh.SCENE
    d = ImageDraw.Draw(im)

    # battery outline motif, LEFT of the (centre-aligned) battery digits (digits @188-226)
    bx0, by0, bx1, by1 = 170, BATT_Y + 1, 184, BATT_Y + 11
    d.rectangle([bx0, by0, bx1, by1], outline=t["BATT_ICON"])
    d.rectangle([bx1 + 1, by0 + 3, bx1 + 2, by1 - 3], fill=t["BATT_ICON"])

    # date '-' separator baked in the flat month/day gap (no digit lands at x146-154)
    sep_yc = DATE_Y + DNUM_H // 2
    d.rectangle([SEP_X0, sep_yc - 1, SEP_X1, sep_yc + 1], fill=t["SEP_RGB"])

    # LABEL banner (baked static). Centred at x120; width from text so it never reaches the
    # 0xd6 icon zone (x11-30). Lives in the no-glyph sky band y130-152.
    f = ImageFont.truetype(bh.F_BOLD, LBL_FONT)
    tb = d.textbbox((0, 0), t["label"], font=f)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pad_x = 16
    pw = tw + pad_x * 2
    px0 = 120 - pw // 2
    px1 = px0 + pw
    py0 = LBL_YC - LBL_H // 2
    py1 = py0 + LBL_H
    d.rounded_rectangle([px0, py0, px1, py1], radius=LBL_H // 2,
                        fill=t["PILL_FILL"], outline=t["PILL_BORDER"], width=1)
    d.text((120 - tw / 2 - tb[0], LBL_YC - th / 2 - tb[1]), t["label"], font=f, fill=t["LBL_RGB"])

    im.save(os.path.join(bh.BUILD, "000.bmp"))
    bh._bg_cache.clear()                 # bg_col samples col-0 (sky) -> unaffected by centred motifs


def build_batt(t):
    for n in range(10):
        bh.save_blob(bh.glyph_tile(str(n), BATT_DW, BATT_H, BATT_Y, bh.F_BOLD, BATT_FONT, t["BATT_RGB"]), 43 + n)


def build_datenums(t):
    for n in range(10):                  # shared DAY_NUM(0x30)+MONTH_NUM(0x11) digits 073..082
        bh.save_blob(bh.glyph_tile(str(n), DNUM_DW, DNUM_H, DATE_Y, bh.F_BOLD, DATE_FONT, t["DATE_RGB"]), 73 + n)


def build_weekdays(t):
    for i, wd in enumerate(WEEKDAYS):    # 066..072
        bh.save_blob(bh.glyph_tile(wd, WD_W, WD_H, DATE_Y, bh.F_BOLD, WD_FONT, t["WD_RGB"]), 66 + i)


# --- redesign builders (folded in from build_peak_redesign.py) --------------
def build_ampm(t):
    """Render AM/PM into the existing 0x45/0x46 fields (was blank). Over flat-sky bg -> seamless."""
    bh.save_blob(bh.glyph_tile("AM", AMPM_W, AMPM_H, AMPM_Y, bh.F_BOLD, AMPM_FONT, t["AMPM_RGB"]), 41)
    bh.save_blob(bh.glyph_tile("PM", AMPM_W, AMPM_H, AMPM_Y, bh.F_BOLD, AMPM_FONT, t["AMPM_RGB"]), 42)


def build_h1_set(t):
    """Dedicated TIME_H1 set (hour-tens digit). Frame0 BLANK -> leading zero vanishes; it is the EXACT
    scene crop under the H1 slot (blank_fixed) so it's a raw, pixel-perfect copy of the background ->
    invisible on-glass regardless of theme (the earlier col-0 blank_tile + RLE showed as a static
    block). Frames 1 & 2 are real digits ('1' for 10/11/12 in 12h; '2' for 20-23 in 24h) so BOTH time
    modes render the tens digit. Frames 3-9 stay blank. Built at EVEN width (H1_W) so the whole set is
    NONE (stride-safe) with no RLE. Clock font/colour so '10:42' / '21:07' read uniform."""
    blank = bh.blank_fixed(H1_X, bh.CLOCK_Y, H1_W, bh.CLOCK_H)
    for n in range(10):
        if n in (1, 2):
            bh.save_blob(bh.glyph_tile(str(n), H1_W, bh.CLOCK_H, bh.CLOCK_Y,
                                       bh.F_BOLD, bh.CLOCK_FONT, t["CLOCK_RGB"]), H1_BASE + n)
        else:
            bh.save_blob(blank, H1_BASE + n)


def build_spark(t):
    """EXPLORATORY HAND_SEC spark. Opaque RGB565 tile: BLACK field (transparency-key bet) with a
    bright spark near the top so the firmware's per-second rotation traces it around the rim."""
    im = Image.new("RGB", (SPARK_W, SPARK_H), (0, 0, 0))
    d = ImageDraw.Draw(im)
    cx = SPARK_W // 2
    cy = 16                                   # spark centre near the top of the tile (the tip)
    for r, a in [(13, 0.14), (10, 0.28), (7, 0.5), (5, 0.78)]:   # halo rings -> fade to black
        col = tuple(int(t["SPARK_HALO"][i] * a) for i in range(3))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    d.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=t["SPARK_CORE"])  # hot core
    im.save(os.path.join(bh.BUILD, f"{SPARK_IDX:03d}.bmp"))


def build_preview(t, idx):
    """FULL-quality 140x163 carousel preview (MUST be 140x163, MUST be the last blob): flat sky/ground
    + sun with core + panel array (outlined) + '78%' hero + the period label. No size budget, so the
    thumbnail carries the full themed scene + period identity."""
    pw, ph = 140, 163
    im = Image.new("RGB", (pw, ph))
    px = im.load()
    hy = 122
    for y in range(ph):
        c = t["SKY"] if y < hy else t["GROUND"]
        for x in range(pw):
            px[x, y] = c
    d = ImageDraw.Draw(im)
    scx = pw // 2
    d.ellipse([scx - 15, 34, scx + 15, 64], fill=t["SUN_RGB"])              # sun
    d.ellipse([scx - 7, 42, scx + 7, 56], fill=t["SUN_CORE"])              # sun core
    for ci in range(5):
        x0 = 24 + ci * 19
        d.rectangle([x0, hy + 8, x0 + 15, hy + 26], fill=t["PANEL"], outline=t["PANEL_FRM"])
    f = ImageFont.truetype(bh.F_BOLD, 30)
    tb = d.textbbox((0, 0), "78%", font=f)
    d.text((scx - (tb[2] - tb[0]) / 2 - tb[0], 74), "78%", font=f, fill=t["HERO_RGB"])
    lf = ImageFont.truetype(bh.F_BOLD, 13)                                  # period label
    lb = d.textbbox((0, 0), t["label"], font=lf)
    d.text((scx - (lb[2] - lb[0]) / 2 - lb[0], 112 - (lb[3] - lb[1]) / 2 - lb[1]),
           t["label"], font=lf, fill=t["LBL_RGB"])
    im.save(os.path.join(bh.BUILD, f"{idx:03d}.bmp"))


def write_watchface(build_dir, facenum, datacount, blobcount, spark, preview_idx):
    out = []
    for ln in open(os.path.join(SEED, "watchface.txt")).read().splitlines():
        tk = ln.split()
        if tk[:1] == ["dataCount"]:
            out.append(f"dataCount       {datacount}")
        elif tk[:1] == ["blobCount"]:
            out.append(f"blobCount       {blobcount}")
        elif tk[:1] == ["faceNumber"]:
            out.append(f"faceNumber     {facenum}")
        elif tk[:2] == ["faceData", "0x40"]:
            out.append(f"faceData        0x40    092     {H1_X}  155   {H1_W}   44          # TIME_H1 (dedicated blank-frame0 set -> drops leading zero)")
        elif tk[:2] == ["faceData", "0x45"]:
            out.append(f"faceData        0x45    041    {AMPM_X}  {AMPM_Y}   {AMPM_W}   {AMPM_H}          # TIME_AM")
        elif tk[:2] == ["faceData", "0x46"]:
            out.append(f"faceData        0x46    042    {AMPM_X}  {AMPM_Y}   {AMPM_W}   {AMPM_H}          # TIME_PM")
        elif tk[:2] == ["faceData", "0x30"]:   # DAY_NUM  — emit current (even) dims so faceData == blob size
            out.append(f"faceData        0x30    073    {DAY_X}  {DATE_Y}   {DNUM_DW}   {DNUM_H}          # DAY_NUM")
        elif tk[:2] == ["faceData", "0x11"]:   # MONTH_NUM — even-width date-digit fix
            out.append(f"faceData        0x11    073    {MONTH_X}  {DATE_Y}   {DNUM_DW}   {DNUM_H}          # MONTH_NUM")
        elif tk[:2] == ["faceData", "0xd6"]:
            out.append(ln)
            if spark:
                out.append(f"faceData        0xF3    {SPARK_IDX:03d}    {SPARK_X}   {SPARK_Y}   {SPARK_W}  {SPARK_H}          # HAND_SEC (EXPLORATORY spark; pivot=face-centre {FACE_CX},{FACE_CY})")
        elif tk[:1] == ["blobCompression"] and tk[1] == "092":
            continue                          # drop old preview line; re-emitted below
        else:
            out.append(ln)
    # append new blob compression lines (ascending), preview LAST
    for i in range(92, 102):
        out.append(f"blobCompression {i:03d}  NONE")           # H1 set: even width -> NONE (stride-safe), raw exact blank
    if spark:
        out.append(f"blobCompression {SPARK_IDX:03d}  RLE_LINE")  # spark: mostly-black flat rows crush under RLE
    out.append(f"blobCompression {preview_idx:03d}  RLE_LINE") # preview (last blob)
    open(os.path.join(build_dir, "watchface.txt"), "w").write("\n".join(out) + "\n")


def build_theme(t, spark=False):
    tag = "-spark" if spark else ""
    name = t["name"] + ("_spark" if spark else "")
    facenum = t["spark_facenum"] if spark else t["facenum"]
    datacount = 19 if spark else 18
    blobcount = 104 if spark else 103
    preview_idx = 103 if spark else 102
    out_face = t["face"].replace(".bin", f"{tag}.bin")

    build_dir = os.path.join(HERE, f"build_{name}")
    if os.path.exists(build_dir):
        shutil.rmtree(build_dir)
    shutil.copytree(SEED, build_dir)     # seed watchface.txt + placeholder bmps (overwritten below)
    apply_palette(t, build_dir)

    build_scene_and_motifs(t)            # 000 (theme scene + motifs + label)
    bh.build_blanks()                    # 001-052, 066-091 (041/042 overwritten below)
    bh.build_hero()                      # 053-065 hero glyphs (HERO_Y=90)
    bh.build_clock()                     # 011-020 shared H2/M1/M2 digits
    build_batt(t)                        # 043-052 battery digits
    build_datenums(t)                    # 073-082 date digits
    build_weekdays(t)                    # 066-072 weekday
    build_ampm(t)                        # 041/042 -> AM/PM       (redesign #2)
    build_preview(t, preview_idx)        # lean preview at the last index
    build_h1_set(t)                      # 092-101 dedicated H1   (redesign #1)
    if spark:
        build_spark(t)                   # 102 HAND_SEC           (redesign #4, exploratory)
    write_watchface(build_dir, facenum, datacount, blobcount, spark, preview_idx)

    out_bin = os.path.join(HERE, out_face)
    if os.path.exists(out_bin):
        os.remove(out_bin)
    r = subprocess.run([DAWFT, "create", f"folder={build_dir}", out_bin],
                       capture_output=True, text=True)
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()
    sz = os.path.getsize(out_bin)
    print(f"[{name}] {out_face} facenum={facenum} size={sz} ({tail})")
    return out_bin


if __name__ == "__main__":
    import sys
    want_spark = "--spark" in sys.argv or "--all" in sys.argv
    for t in THEMES:
        build_theme(t, spark=False)
    if want_spark:
        for t in THEMES:
            build_theme(t, spark=True)
    print("done")
