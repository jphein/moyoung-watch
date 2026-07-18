"""MoYoung / Da Fit binary watch-face format: embeddable field types + a header parser.

The field-type table mirrors david47k/dawft (``dataTypes[]`` in dawft.c) — the authoritative
list of what a face can *embed*. Each field is a live value the firmware paints for you (time,
steps, heart rate, battery, weather, analog hands, animations, …); the face file supplies the
bitmap glyphs and their placement, and the watch fills in the current value.

Header / faceData layout (also from dawft):

    byte 0      fileID        0x04 = Type A ; 0x81 / 0x84 = Type B / C
    byte 1      dataCount     number of faceData entries
    byte 2      blobCount     number of bitmap blobs
    bytes 3-4   faceNumber    design id (u16, little-endian)
    byte 5+     faceData      Type A: 32 x 6-byte [type,x,y,w,h,oidx] (u8 fields)
                              Type B/C: 39 x 10-byte [type,oidx,x,y,w,h] (x/y/w/h u16 LE)
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Dict, List, Optional

FILE_TYPE_A = 0x04
FILE_TYPE_BC = (0x81, 0x84)


@dataclass(frozen=True)
class FieldType:
    code: int
    name: str
    count: int    # how many frames/glyphs the face must supply for this field
    group: str
    desc: str


# (code, name, count, group, description) — grouped for the field-reference site.
_FIELDS = [
    (0x00, "BACKGROUNDS",     10, "Background",   "Background in 10 strips of 240x24 (Type A). May include example time, overwritten."),
    (0x01, "BACKGROUND",       1, "Background",   "Full-screen background image (Type B & C)."),

    (0x40, "TIME_H1",         10, "Time",         "Hour, tens digit (H_:mm)."),
    (0x41, "TIME_H2",         10, "Time",         "Hour, ones digit (_H:mm)."),
    (0x43, "TIME_M1",         10, "Time",         "Minute, tens digit (hh:M_)."),
    (0x44, "TIME_M2",         10, "Time",         "Minute, ones digit (hh:_M)."),
    (0x45, "TIME_AM",          1, "Time",         "'AM' indicator."),
    (0x46, "TIME_PM",          1, "Time",         "'PM' indicator."),
    (0xF0, "SEPERATOR",        1, "Time",         "Static date/time separator glyph, e.g. ':' or '/'."),

    (0x10, "MONTH_NAME",      12, "Date",         "Month name: JAN..DEC."),
    (0x11, "MONTH_NUM",       10, "Date",         "Month as digits."),
    (0x12, "YEAR",            10, "Date",         "Year, 2 digits, left aligned."),
    (0x30, "DAY_NUM",         10, "Date",         "Day of month, digits."),
    (0x60, "DAY_NAME",         7, "Date",         "Weekday: SUN..SAT."),
    (0x61, "DAY_NAME_CN",      7, "Date",         "Weekday with Chinese symbol option."),
    (0x6B, "MONTH_NUM_B",     10, "Date",         "Month digits, alternate glyph set."),
    (0x6C, "DAY_NUM_B",       10, "Date",         "Day-of-month digits, alternate glyph set."),

    (0x62, "STEPS",           10, "Steps",        "Step count, left aligned, digits."),
    (0x63, "STEPS_CA",        10, "Steps",        "Step count, centre aligned."),
    (0x64, "STEPS_RA",        10, "Steps",        "Step count, right aligned."),
    (0x70, "STEPS_PROGBAR",   11, "Steps",        "Steps progress bar, 11 frames (0,10,..100%)."),
    (0x71, "STEPS_LOGO",       1, "Steps",        "Steps static logo/icon."),
    (0x72, "STEPS_B",         10, "Steps",        "Step count, left aligned, alternate glyphs."),
    (0x73, "STEPS_B_CA",      10, "Steps",        "Step count, centre aligned, alternate glyphs."),
    (0x74, "STEPS_B_RA",      10, "Steps",        "Step count, right aligned, alternate glyphs."),
    (0x76, "STEPS_GOAL",      10, "Steps",        "Step goal, left aligned, digits."),

    (0x65, "HR",              10, "Heart rate",   "Heart rate, left aligned, digits."),
    (0x66, "HR_CA",           10, "Heart rate",   "Heart rate, centre aligned."),
    (0x67, "HR_RA",           10, "Heart rate",   "Heart rate, right aligned."),
    (0x80, "HR_PROGBAR",      11, "Heart rate",   "Heart-rate progress bar, 11 frames."),
    (0x81, "HR_LOGO",          1, "Heart rate",   "Heart-rate static logo/icon."),
    (0x82, "HR_B",            10, "Heart rate",   "Heart rate, left aligned, alternate glyphs."),
    (0x83, "HR_B_CA",         10, "Heart rate",   "Heart rate, centre aligned, alternate glyphs."),
    (0x84, "HR_B_RA",         10, "Heart rate",   "Heart rate, right aligned, alternate glyphs."),

    (0x68, "KCAL",            10, "Calories",     "Calories, left aligned, digits."),
    (0x90, "KCAL_PROGBAR",    11, "Calories",     "Calories progress bar, 11 frames."),
    (0x91, "KCAL_LOGO",        1, "Calories",     "Calories static logo/icon."),
    (0x92, "KCAL_B",          10, "Calories",     "Calories, left aligned, alternate glyphs."),
    (0x93, "KCAL_B_CA",       10, "Calories",     "Calories, centre aligned, alternate glyphs."),
    (0x94, "KCAL_B_RA",       10, "Calories",     "Calories, right aligned, alternate glyphs."),

    (0xA0, "DIST_PROGBAR",    11, "Distance",     "Distance progress bar, 11 frames."),
    (0xA1, "DIST_LOGO",        1, "Distance",     "Distance static logo/icon."),
    (0xA2, "DIST",            10, "Distance",     "Distance, left aligned, digits (has a decimal point)."),
    (0xA3, "DIST_CA",         10, "Distance",     "Distance, centre aligned."),
    (0xA4, "DIST_RA",         10, "Distance",     "Distance, right aligned."),
    (0xA5, "DIST_KM",          1, "Distance",     "Distance unit 'KM'."),
    (0xA6, "DIST_MI",          1, "Distance",     "Distance unit 'MI'."),

    (0xCE, "BATT_IMG",         1, "Battery",      "Battery level image."),
    (0xD0, "BATT_IMG_B",       1, "Battery",      "Battery level image, alternate."),
    (0xD1, "BATT_IMG_C",       1, "Battery",      "Battery level image, alternate."),
    (0xDA, "BATT_IMG_D",       1, "Battery",      "Battery level image, alternate."),
    (0xD2, "BATT",            10, "Battery",      "Battery %, left aligned, digits."),
    (0xD3, "BATT_CA",         10, "Battery",      "Battery %, centre aligned."),
    (0xD4, "BATT_RA",         10, "Battery",      "Battery %, right aligned."),

    (0xD7, "WEATHER_TEMP",    13, "Weather",      "Temperature, left aligned; 11 digits (0-9,-) + degC/degF glyphs."),
    (0xD8, "WEATHER_TEMP_CA", 13, "Weather",      "Temperature, centre aligned."),
    (0xD9, "WEATHER_TEMP_RA", 13, "Weather",      "Temperature, right aligned."),

    (0xC0, "BTLINK_UP",        1, "Connectivity", "Bluetooth connected indicator."),
    (0xC1, "BTLINK_DOWN",      1, "Connectivity", "Bluetooth disconnected indicator."),

    (0xF1, "HAND_HOUR",        1, "Analog",       "Analog hour hand (drawn at the 12:00 position)."),
    (0xF2, "HAND_MINUTE",      1, "Analog",       "Analog minute hand (at 12:00)."),
    (0xF3, "HAND_SEC",         1, "Analog",       "Analog second hand (at 12:00)."),
    (0xF4, "HAND_PIN_UPPER",   1, "Analog",       "Top half of the analog centre pin."),
    (0xF5, "HAND_PIN_LOWER",   1, "Analog",       "Bottom half of the analog centre pin."),

    (0xF6, "TAP_TO_CHANGE",    1, "Animation",    "Tap-to-change image series; frame count set by animationFrames."),
    (0xF7, "ANIMATION",        1, "Animation",    "Animation; frame count set by animationFrames."),
    (0xF8, "ANIMATION_F8",     1, "Animation",    "Animation (alternate); frame count set by animationFrames."),
]

FIELDS: Dict[int, FieldType] = {
    code: FieldType(code, name, count, group, desc)
    for (code, name, count, group, desc) in _FIELDS
}

# Display order for grouping the field reference.
GROUP_ORDER = [
    "Background", "Time", "Date", "Steps", "Heart rate", "Calories",
    "Distance", "Battery", "Weather", "Connectivity", "Analog", "Animation",
]


def fields_by_group() -> Dict[str, List[FieldType]]:
    """Return the field table grouped, in GROUP_ORDER."""
    out: Dict[str, List[FieldType]] = {g: [] for g in GROUP_ORDER}
    for ft in FIELDS.values():
        out.setdefault(ft.group, []).append(ft)
    for g in out:
        out[g].sort(key=lambda f: f.code)
    return out


# ------------------------------------------------------------------ header parsing
@dataclass
class FaceEntry:
    code: int
    oidx: int          # index into the blob offset table
    x: int
    y: int
    w: int
    h: int

    @property
    def field(self) -> Optional[FieldType]:
        return FIELDS.get(self.code)


@dataclass
class FaceFile:
    file_id: int
    file_type: str     # 'A' or 'B/C'
    data_count: int
    blob_count: int
    face_number: int
    entries: List[FaceEntry]

    def field_names(self) -> List[str]:
        names = []
        for e in self.entries:
            f = e.field
            names.append(f.name if f else f"UNKNOWN(0x{e.code:02x})")
        return names


def parse_header(buf: bytes) -> FaceFile:
    """Parse a MoYoung face .bin header. Reads only the first ``dataCount`` entries."""
    if len(buf) < 5:
        raise ValueError("file too small to be a watch face")
    file_id = buf[0]
    data_count = buf[1]
    blob_count = buf[2]
    face_number = struct.unpack_from("<H", buf, 3)[0]

    entries: List[FaceEntry] = []
    idx = 5
    if file_id == FILE_TYPE_A:
        file_type = "A"
        n = min(data_count, 32)
        for _ in range(n):
            code = buf[idx]
            x, y, w, h = buf[idx + 1], buf[idx + 2], buf[idx + 3], buf[idx + 4]
            oidx = buf[idx + 5]
            entries.append(FaceEntry(code, oidx, x, y, w, h))
            idx += 6
    elif file_id in FILE_TYPE_BC:
        file_type = "B/C"
        n = min(data_count, 39)
        for _ in range(n):
            code = buf[idx]
            oidx = buf[idx + 1]
            x, y, w, h = struct.unpack_from("<HHHH", buf, idx + 2)
            entries.append(FaceEntry(code, oidx, x, y, w, h))
            idx += 10
    else:
        raise ValueError(f"unrecognised fileID 0x{file_id:02x} (expected 0x04, 0x81 or 0x84)")

    return FaceFile(file_id, file_type, data_count, blob_count, face_number, entries)
