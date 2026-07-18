"""MoYoung device-settings payloads: SET builders, QUERY payloads, and response decoders.

Every setting is a (SET, QUERY) opcode pair (a few are set-only). SET builders return the raw
payload to hand to :func:`commands.build_packet`; QUERY payloads are almost always empty. The
``decode_*`` helpers parse the watch's reply for the matching QUERY.

Encodings are lifted from Gadgetbridge's ``MoyoungSetting*`` classes + ``MoyoungEnum*`` +
``AbstractMoyoungDeviceCoordinator`` (AGPLv3, krzys_h) — interoperability facts, no code copied.

Two documented reshapes vs. the literal GB code (see scratch/gb-port/coverage.md):
  * TimeRange query responses are decoded **big-endian minutes-of-day** — the only reading
    self-consistent with the constant comment; GB's own ``decode`` reads LE (a bug).
  * GOAL_STEP SET is big-endian but its QUERY reply is little-endian (matches GB exactly).
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Union

from . import commands as c

QUERY_EMPTY = b""  # nearly every CMD_QUERY_* takes an empty payload


# ------------------------------------------------------------------ small helpers
def _bool_byte(enabled: bool) -> bytes:
    return bytes([1 if enabled else 0])


def _resolve(value: Union[int, str], table: Dict[str, int], what: str) -> int:
    """Map a friendly name (or pass an int through) to its wire byte."""
    if isinstance(value, str):
        key = value.strip().lower()
        if key not in table:
            raise ValueError(f"unknown {what} {value!r}; choices: {', '.join(table)}")
        return table[key]
    return int(value)


TIME_SYSTEMS = {"12": c.TIME_SYSTEM_12, "12h": c.TIME_SYSTEM_12,
                "24": c.TIME_SYSTEM_24, "24h": c.TIME_SYSTEM_24}
METRIC_SYSTEMS = {"metric": c.METRIC_SYSTEM, "imperial": c.IMPERIAL_SYSTEM}
DEVICE_VERSIONS = {"chinese": c.DEVICE_VERSION_CHINESE,
                   "international": c.DEVICE_VERSION_INTERNATIONAL}
DOMINANT_HANDS = {"left": c.DOMINANT_HAND_LEFT, "right": c.DOMINANT_HAND_RIGHT}
SEXES = {"male": c.SEX_MALE, "female": c.SEX_FEMALE, "m": c.SEX_MALE, "f": c.SEX_FEMALE}
# weekday -> repetition bit (bit0=Sun .. bit6=Sat), matching GB's createRepetitionMask
WEEKDAY_BITS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
HR_INTERVALS = {"off": c.HR_INTERVAL_OFF, "0": c.HR_INTERVAL_OFF,
                "5": c.HR_INTERVAL_5MIN, "10": c.HR_INTERVAL_10MIN,
                "20": c.HR_INTERVAL_20MIN, "30": c.HR_INTERVAL_30MIN}


# ------------------------------------------------------------------ SET builders (byte)
def time_system_payload(value: Union[int, str] = "24") -> bytes:
    return bytes([_resolve(value, TIME_SYSTEMS, "time system") & 0xFF])


def metric_system_payload(value: Union[int, str] = "metric") -> bytes:
    return bytes([_resolve(value, METRIC_SYSTEMS, "metric system") & 0xFF])


def device_version_payload(value: Union[int, str] = "international") -> bytes:
    return bytes([_resolve(value, DEVICE_VERSIONS, "device version") & 0xFF])


def dominant_hand_payload(value: Union[int, str] = "left") -> bytes:
    return bytes([_resolve(value, DOMINANT_HANDS, "dominant hand") & 0xFF])


def language_payload(value: Union[int, str] = "english") -> bytes:
    return bytes([_resolve(value, c.LANGUAGES, "language") & 0xFF])


def watch_face_payload(index: int) -> bytes:
    """CMD_SET_DISPLAY_WATCH_FACE — switch the active watch face by index."""
    return bytes([index & 0xFF])


def step_length_payload(cm: int) -> bytes:
    return bytes([cm & 0xFF])


def hr_auto_interval_payload(interval: Union[int, str]) -> bytes:
    """CMD_SET_TIMING_MEASURE_HEART_RATE — auto-HR cadence (0/5/10/20/30 min -> 0/1/2/4/6)."""
    return bytes([_resolve(interval, HR_INTERVALS, "hr interval") & 0xFF])


# ------------------------------------------------------------------ SET builders (bool)
def quick_view_payload(enabled: bool) -> bytes:
    return _bool_byte(enabled)


def sedentary_payload(enabled: bool) -> bytes:
    return _bool_byte(enabled)


def other_message_payload(enabled: bool) -> bytes:
    return _bool_byte(enabled)


def breathing_light_payload(enabled: bool) -> bytes:
    return _bool_byte(enabled)


def power_saving_payload(enabled: bool) -> bytes:
    return _bool_byte(enabled)


# ------------------------------------------------------------------ SET builders (composite)
def user_info_payload(height_cm: int, weight_kg: int, age: int,
                      sex: Union[int, str] = "male") -> bytes:
    """CMD_SET_USER_INFO — {height_cm, weight_kg, age, sex}. sex: male=0, female=1."""
    return bytes([height_cm & 0xFF, weight_kg & 0xFF, age & 0xFF,
                  _resolve(sex, SEXES, "sex") & 0xFF])


def _time_range(start_h: int, start_m: int, end_h: int, end_m: int) -> bytes:
    return bytes([start_h & 0xFF, start_m & 0xFF, end_h & 0xFF, end_m & 0xFF])


def dnd_time_payload(start_h: int, start_m: int, end_h: int, end_m: int) -> bytes:
    """CMD_SET_DO_NOT_DISTURB_TIME — {start_h, start_m, end_h, end_m}. All-zero disables."""
    return _time_range(start_h, start_m, end_h, end_m)


def quick_view_time_payload(start_h: int, start_m: int, end_h: int, end_m: int) -> bytes:
    """CMD_SET_QUICK_VIEW_TIME — schedule window for raise-to-wake. All-zero = always on."""
    return _time_range(start_h, start_m, end_h, end_m)


def reminders_to_move_payload(period: int, steps: int, start_h: int, end_h: int) -> bytes:
    """CMD_SET_REMINDERS_TO_MOVE_PERIOD — {period_min, step_threshold, start_hour, end_hour}."""
    return bytes([period & 0xFF, steps & 0xFF, start_h & 0xFF, end_h & 0xFF])


def display_functions_payload(func_ids: Iterable[int]) -> bytes:
    """CMD_SET_DISPLAY_DEVICE_FUNCTION — null-terminated list of screen ids to enable."""
    return bytes(int(f) & 0xFF for f in func_ids) + b"\x00"


def display_functions_query_payload(list_supported: bool = False) -> bytes:
    """CMD_QUERY_DISPLAY_DEVICE_FUNCTION — empty for current, {0xFF} to list supported."""
    return bytes([0xFF]) if list_supported else QUERY_EMPTY


def _days_to_bitmask(days: Optional[Iterable[Union[int, str]]]) -> int:
    """Weekdays -> repetition bitmask (bit0=Sun..bit6=Sat). Accepts ints 0-6 or names."""
    if not days:
        return 0
    mask = 0
    for d in days:
        bit = WEEKDAY_BITS[d.strip().lower()] if isinstance(d, str) else int(d)
        if not 0 <= bit <= 6:
            raise ValueError(f"weekday out of range 0(Sun)..6(Sat): {d}")
        mask |= 1 << bit
    return mask


def alarm_payload(index: int, hour: int, minute: int, *, enabled: bool = True,
                  days: Optional[Iterable[Union[int, str]]] = None,
                  year: Optional[int] = None, month: int = 1, day: int = 1) -> bytes:
    """CMD_SET_ALARM_CLOCK (legacy 8-byte form).

    ``days`` is a set of weekdays (ints 0=Sun..6=Sat or names). Empty/None => a one-shot alarm,
    which encodes the date in bytes 5-6 (``year`` 2015-based, ``month`` 1-12, ``day`` 1-31).
    ``repeat`` (byte 2) is derived: 0=single, 1=every day, 2=custom — exactly like Gadgetbridge.
    """
    bitmask = _days_to_bitmask(days)
    repeat = 0 if bitmask == 0 else (1 if bitmask == 0x7F else 2)
    if bitmask == 0 and year is not None:
        ym = (((year - 2015) & 0xFF) << 4) + (month & 0x0F)
        d = day & 0xFF
    else:
        ym, d = 0, 0
    return bytes([index & 0xFF, 1 if enabled else 0, repeat,
                  hour & 0xFF, minute & 0xFF, ym & 0xFF, d & 0xFF, bitmask & 0xFF])


def watch_face_layout_payload(time_position: int, time_top: int, time_bottom: int,
                              text_color: int, background_md5: str) -> bytes:
    """CMD_SET_WATCH_FACE_LAYOUT.

    ``text_color`` is R5G6B5 (16-bit, big-endian). ``background_md5`` is a 32-char hex string
    stored as 32 nibble bytes (values 0-15, NOT ASCII), per the constant comment.
    """
    md5 = background_md5.strip().lower()
    if len(md5) != 32 or any(ch not in "0123456789abcdef" for ch in md5):
        raise ValueError("background_md5 must be a 32-char hex string")
    nibbles = bytes(int(ch, 16) for ch in md5)
    return (bytes([time_position & 0xFF, time_top & 0xFF, time_bottom & 0xFF,
                   (text_color >> 8) & 0xFF, text_color & 0xFF]) + nibbles)


def psychological_period_payload(physiological_period: int, menstrual_period: int,
                                 start_month: int, start_date: int,
                                 reminder_h: int = 8, reminder_m: int = 0, *,
                                 menstrual_reminder: bool = True,
                                 ovulation_reminder: bool = False,
                                 ovulation_day_reminder: bool = False,
                                 ovulation_end_reminder: bool = False) -> bytes:
    """CMD_SET_PSYCHOLOGICAL_PERIOD (menstrual cycle). Marked untested (*) in Gadgetbridge.

    ``start_month`` is the raw byte GB sends (``Calendar.MONTH``, i.e. 0-based). The four
    reminder H/M pairs are repeated (GB sends the same reminder time four times).
    """
    flags = 241 if menstrual_reminder else 240
    if ovulation_reminder:
        flags += 2
    if ovulation_day_reminder:
        flags += 4
    if ovulation_end_reminder:
        flags += 8
    body = bytes([flags & 0xFF, 15, physiological_period & 0xFF, menstrual_period & 0xFF,
                  start_month & 0xFF, start_date & 0xFF])
    body += bytes([reminder_h & 0xFF, reminder_m & 0xFF]) * 4
    return body


# ------------------------------------------------------------------ response decoders
def decode_bool(payload: bytes) -> bool:
    return bool(payload and payload[0] != 0)


def decode_byte(payload: bytes) -> int:
    return payload[0] if payload else -1


def decode_goal_step(payload: bytes) -> int:
    """CMD_QUERY_GOAL_STEP reply — little-endian uint32 (SET was big-endian)."""
    return int.from_bytes(payload[:4], "little") if len(payload) >= 4 else -1


def decode_watch_face(payload: bytes) -> int:
    """CMD_QUERY_DISPLAY_WATCH_FACE reply — current face index."""
    return payload[0] if payload else -1


def decode_time_system(payload: bytes) -> Dict[str, object]:
    v = decode_byte(payload)
    return {"value": v, "name": "24h" if v == c.TIME_SYSTEM_24 else "12h"}


def decode_metric_system(payload: bytes) -> Dict[str, object]:
    v = decode_byte(payload)
    return {"value": v, "name": "imperial" if v == c.IMPERIAL_SYSTEM else "metric"}


def decode_device_version(payload: bytes) -> Dict[str, object]:
    v = decode_byte(payload)
    return {"value": v, "name": "international" if v == c.DEVICE_VERSION_INTERNATIONAL else "chinese"}


def decode_dominant_hand(payload: bytes) -> Dict[str, object]:
    v = decode_byte(payload)
    return {"value": v, "name": "right" if v == c.DOMINANT_HAND_RIGHT else "left"}


def decode_time_range(payload: bytes) -> Dict[str, int]:
    """DND / quick-view-time reply — two big-endian uint16 minutes-of-day (start, end).

    NB: GB's MoyoungSettingTimeRange.decode reads little-endian, but only the big-endian
    reading (matching the ``{start>>8, start,...}`` constant comment) yields sane clock times.
    """
    if len(payload) < 4:
        raise ValueError(f"time range needs 4 bytes, got {len(payload)}")
    start = int.from_bytes(payload[0:2], "big")
    end = int.from_bytes(payload[2:4], "big")
    return {"start_h": start // 60, "start_m": start % 60,
            "end_h": end // 60, "end_m": end % 60}


def decode_reminders_to_move(payload: bytes) -> Dict[str, int]:
    if len(payload) < 4:
        raise ValueError(f"reminders-to-move needs 4 bytes, got {len(payload)}")
    return {"period": payload[0], "steps": payload[1],
            "start_h": payload[2], "end_h": payload[3]}


def decode_watch_face_layout(payload: bytes) -> Dict[str, object]:
    if len(payload) < 5:
        raise ValueError(f"watch-face layout needs >=5 bytes, got {len(payload)}")
    md5 = "".join("%x" % (n & 0x0F) for n in payload[5:5 + 32])
    return {
        "time_position": payload[0], "time_top": payload[1], "time_bottom": payload[2],
        "text_color": int.from_bytes(payload[3:5], "big"), "background_md5": md5,
    }


def decode_support_watch_face(payload: bytes) -> int:
    """CMD_QUERY_SUPPORT_WATCH_FACE reply — {count>>8, count, ...}."""
    return int.from_bytes(payload[:2], "big") if len(payload) >= 2 else -1


def decode_display_functions(payload: bytes) -> Dict[str, object]:
    """CMD_QUERY_DISPLAY_DEVICE_FUNCTION reply — a function-id list, optionally -1 prefixed."""
    data = list(payload)
    lists_supported = bool(data) and data[0] == 0xFF
    if lists_supported:
        data = data[1:]
    return {"lists_supported": lists_supported,
            "functions": [b for b in data if b != 0]}


def decode_language(payload: bytes) -> Dict[str, object]:
    """CMD_QUERY_DEVICE_LANGUAGE reply — {current, supported_bitmask:uint32 BE}."""
    if len(payload) < 5:
        raise ValueError(f"language reply needs >=5 bytes, got {len(payload)}")
    current = payload[0]
    supported_mask = int.from_bytes(payload[1:5], "big")
    by_value = {v: k for k, v in c.LANGUAGES.items()}
    supported = [by_value[v] for v in sorted(by_value)
                 if (supported_mask >> v) & 1]
    return {"current": current, "current_name": by_value.get(current),
            "supported": supported}


def decode_alarms(payload: bytes) -> List[Dict[str, object]]:
    """CMD_QUERY_ALARM_CLOCK reply — a list of 8-byte alarm entries.

    Handles both the legacy form (payload length is a multiple of 8) and the v2 advanced form
    (a 3-byte ``{subtype, argument, count}`` prefix, i.e. ``len % 8 == 3``).
    """
    data = bytes(payload)
    if len(data) % 8 == 3:            # v2 advanced form: skip {subtype, argument, count}
        count = data[2]
        data = data[3:]
    elif len(data) % 8 == 0:
        count = len(data) // 8
    else:
        raise ValueError(f"invalid alarms payload length {len(data)}")
    inv_bits = {v: k for k, v in WEEKDAY_BITS.items()}
    alarms: List[Dict[str, object]] = []
    for i in range(min(count, len(data) // 8)):
        e = data[i * 8:i * 8 + 8]
        bitmask = e[7]
        days = [inv_bits[b] for b in range(7) if (bitmask >> b) & 1]
        alarms.append({"index": e[0], "enabled": bool(e[1]),
                       "hour": e[3], "minute": e[4], "days": days,
                       "repeat_mask": bitmask})
    return alarms
