"""MoYoung control/sync command protocol (service 0xFEEA).

Reimplemented from Gadgetbridge's clean-room MoYoung coordinator (`MoyoungConstants`,
`MoyoungPacketOut`/`In`, `MoyoungDeviceSupport`) — AGPLv3, originally by krzys_h. Command
IDs, the packet framing, and payload layouts are interoperability facts; no code is copied.

Framing (the same envelope face upload uses — face-start is just command 0x74):

    FE EA | b2 | b3 | cmdType | payload...
      b2/b3 encode the TOTAL length (payload + 5):
        MTU == 20 (v1):  b2 = 0x10,                 b3 = len & 0xFF
        MTU  > 20 (v2):  b2 = (0x20 + (len>>8))&FF, b3 = len & 0xFF

Commands write to DATA_OUT (0xFEE2); responses arrive as notifications on DATA_IN (0xFEE3)
in the same envelope. Live pedometer data is a plain read of the STEPS characteristic (0xFEE1).

"Injection" angle (why this matters, cf. the GTX2 day-field trick): the watch paints these
values into whatever face fields are placed. `set_weather` drives the WEATHER_TEMP field with
an arbitrary signed number; `notify` / `set_music` inject arbitrary text onto the screen.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

DEFAULT_MTU = 247  # these watches negotiate MTU 247; any value > 20 selects the v2 framing

# --- command IDs (MoyoungConstants) -------------------------------------------------------
CMD_SET_GOAL_STEP = 22
CMD_SYNC_TIME = 49
CMD_SEND_MESSAGE = 65             # notification: [type] + utf8 text ("sender:body")
CMD_SET_WEATHER_FUTURE = 66       # {conditionId, low, high} * 7
CMD_SET_WEATHER_TODAY = 67        # [pm25?][conditionId][temp][lunar 8B][city 8B]
CMD_SET_MUSIC_INFO = 68           # [is_artist] + utf8 text
CMD_SET_MUSIC_STATE = 123
CMD_FIND_MY_WATCH = 97            # empty payload -> buzz
CMD_SHUTDOWN = 81                 # {0xFF}
CMD_TRIGGER_MEASURE_HEARTRATE = 109   # {0} start / {0xFF} stop -> responds {bpm}

# --- full command surface (rest of MoyoungConstants) --------------------------------------
# These are used by the settings/health/events sub-modules, but they all live here so there
# is one authoritative opcode table and `from moyoung_client import commands as c` sees them.
#
# Special / power
CMD_FIND_MY_PHONE = 98                # out {0xFF} to stop; incoming {0} start / {!=0} stop
CMD_HS_DFU = 99                       # vendor OTA trigger — BRICK RISK, gated (see dfu_payload)
CMD_GSENSOR_CALIBRATION = 82          # {} calibrate the accelerometer
CMD_RETURN_PRINCIPAL_SCREEN = 83      # out {} go to home screen; incoming = camera closed
CMD_QUERY_POWER_SAVING = 0xA4
CMD_SET_POWER_SAVING = 0x94           # {enabled ? 1 : 0}

# Activity / training
CMD_QUERY_LAST_DYNAMIC_RATE = 52      # watch-initiated; phone sends empty to pull next part
CMD_QUERY_PAST_HEART_RATE_1 = 53      # {index} -> 5-min HR history, 8 packets = 2 days
CMD_QUERY_PAST_HEART_RATE_2 = 54      # {index} -> 1-min HR history (marked * in GB)
CMD_QUERY_MOVEMENT_HEART_RATE = 55    # {} -> last 3 workouts, 24 bytes each
ARG_TRANSMISSION_FIRST = 0
ARG_TRANSMISSION_NEXT = 1
ARG_TRANSMISSION_LAST = 2

# Timing / auto HR
CMD_QUERY_TIMING_MEASURE_HEART_RATE = 47
CMD_SET_TIMING_MEASURE_HEART_RATE = 31   # {interval byte}
CMD_START_STOP_MEASURE_DYNAMIC_RATE = 104  # {0} start / {0xFF} stop; incoming = training start/stop
HR_INTERVAL_OFF = 0
HR_INTERVAL_5MIN = 1
HR_INTERVAL_10MIN = 2
HR_INTERVAL_20MIN = 4
HR_INTERVAL_30MIN = 6

# Health measurements
CMD_TRIGGER_MEASURE_BLOOD_PRESSURE = 105  # {0,0,0} start / {0xFF,0xFF,0xFF} stop -> {?, sys, dia}
CMD_TRIGGER_MEASURE_BLOOD_OXYGEN = 107    # {0} start / {0xFF} stop -> {percent}
CMD_ECG = 111                             # {1} start / {0} stop / {2} query / {hr}

# Functionality / sync
CMD_SYNC_SLEEP = 50                   # {} -> repeating {type, start_h, start_m}
CMD_SYNC_PAST_SLEEP_AND_STEP = 51     # {arg} -> steps triple or sleep triples
ARG_SYNC_YESTERDAY_STEPS = 1
ARG_SYNC_DAY_BEFORE_YESTERDAY_STEPS = 2
ARG_SYNC_YESTERDAY_SLEEP = 3
ARG_SYNC_DAY_BEFORE_YESTERDAY_SLEEP = 4
SLEEP_SOBER = 0
SLEEP_LIGHT = 1
SLEEP_RESTFUL = 2
SLEEP_REM = 3
CMD_QUERY_SLEEP_ACTION = 58           # {i} -> {hour, x[60]}
CMD_QUERY_STEPS_CATEGORY = 89         # {index} -> {index, uint16[]} hourly buckets

# Weather (extends the today/future set above)
CMD_SET_WEATHER_LOCATION = 69         # {utf8 string}
CMD_SET_SUNRISE_SUNSET = 181          # 0xB5 (-75): {0,cond,temp,0,0,srH,srM,ssH,ssM, loc utf8}

# Phone / interaction
CMD_SWITCH_CAMERA_VIEW = 102          # out {} open camera; incoming = shutter / open toggle
CMD_NOTIFY_PHONE_OPERATION = 103      # incoming media/call ops; out {12, vol} sends volume
CMD_NOTIFY_WEATHER_CHANGE = 100       # incoming only: watch asks for a weather retransmit
ARG_OPERATION_PLAY_PAUSE = 0
ARG_OPERATION_PREV_SONG = 1
ARG_OPERATION_NEXT_SONG = 2
ARG_OPERATION_DROP_INCOMING_CALL = 3
ARG_OPERATION_VOLUME_UP = 4
ARG_OPERATION_VOLUME_DOWN = 5
ARG_OPERATION_PLAY = 6
ARG_OPERATION_PAUSE = 7
ARG_OPERATION_SEND_CURRENT_VOLUME = 12  # {0x00..0x10}

# Alarms
CMD_QUERY_ALARM_CLOCK = 33
CMD_SET_ALARM_CLOCK = 17

# Settings — SET / QUERY pairs
CMD_SET_USER_INFO = 18               # {height_cm, weight_kg, age, sex}  (sex: 0 male, 1 female)
CMD_QUERY_DOMINANT_HAND = 36
CMD_SET_DOMINANT_HAND = 20
CMD_QUERY_DISPLAY_DEVICE_FUNCTION = 37
CMD_SET_DISPLAY_DEVICE_FUNCTION = 21  # {func ids..., 0}  null-terminated enable list
CMD_QUERY_GOAL_STEP = 38             # response is little-endian (SET is big-endian!)
CMD_QUERY_TIME_SYSTEM = 39
CMD_SET_TIME_SYSTEM = 23
CMD_QUERY_QUICK_VIEW = 40
CMD_SET_QUICK_VIEW = 24
CMD_QUERY_DISPLAY_WATCH_FACE = 41
CMD_SET_DISPLAY_WATCH_FACE = 25      # {index}  switch active watch face
CMD_QUERY_METRIC_SYSTEM = 42
CMD_SET_METRIC_SYSTEM = 26
CMD_QUERY_DEVICE_LANGUAGE = 43
CMD_SET_DEVICE_LANGUAGE = 27
CMD_QUERY_OTHER_MESSAGE_STATE = 44
CMD_SET_OTHER_MESSAGE_STATE = 28
CMD_QUERY_SEDENTARY_REMINDER = 45
CMD_SET_SEDENTARY_REMINDER = 29
CMD_QUERY_DEVICE_VERSION = 46
CMD_SET_DEVICE_VERSION = 30
CMD_QUERY_WATCH_FACE_LAYOUT = 57
CMD_SET_WATCH_FACE_LAYOUT = 56
CMD_SET_STEP_LENGTH = 84             # {cm}
CMD_QUERY_DO_NOT_DISTURB_TIME = 129   # 0x81 (-127)
CMD_SET_DO_NOT_DISTURB_TIME = 113     # {start_h, start_m, end_h, end_m}
CMD_QUERY_QUICK_VIEW_TIME = 130       # 0x82 (-126)
CMD_SET_QUICK_VIEW_TIME = 114         # {start_h, start_m, end_h, end_m}
CMD_QUERY_REMINDERS_TO_MOVE_PERIOD = 131  # 0x83 (-125)
CMD_SET_REMINDERS_TO_MOVE_PERIOD = 115    # {period, steps, start_h, end_h}
CMD_QUERY_SUPPORT_WATCH_FACE = 132    # 0x84 (-124) -> {count>>8, count, ...}
CMD_QUERY_PSYCHOLOGICAL_PERIOD = 133  # 0x85 (-123)
CMD_SET_PSYCHOLOGICAL_PERIOD = 117
CMD_QUERY_BREATHING_LIGHT = 136       # 0x88 (-120)
CMD_SET_BREATHING_LIGHT = 120

# Settings enum values (MoyoungEnum*)
TIME_SYSTEM_12 = 0
TIME_SYSTEM_24 = 1
METRIC_SYSTEM = 0
IMPERIAL_SYSTEM = 1
DEVICE_VERSION_CHINESE = 0
DEVICE_VERSION_INTERNATIONAL = 1
DOMINANT_HAND_LEFT = 0
DOMINANT_HAND_RIGHT = 1
SEX_MALE = 0
SEX_FEMALE = 1

# Device language name -> byte value (MoyoungEnumLanguage)
LANGUAGES = {
    "english": 0, "chinese": 1, "japanese": 2, "korean": 3, "german": 4, "french": 5,
    "spanish": 6, "arabic": 7, "russian": 8, "traditional": 9, "ukrainian": 10,
    "italian": 11, "portuguese": 12, "dutch": 13, "polish": 14, "swedish": 15,
    "finnish": 16, "danish": 17, "norwegian": 18, "hungarian": 19, "czech": 20,
    "bulgarian": 21, "romanian": 22, "slovak": 23, "latvian": 24,
}

# Workout type code -> name (for movement-HR / dynamic-HR decode)
WORKOUT_TYPES = {
    0: "walk", 1: "run", 2: "biking", 3: "rope", 4: "badminton", 5: "basketball",
    6: "football", 7: "swim", 8: "mountaineering", 9: "tennis", 10: "rugby", 11: "golf",
    12: "yoga", 13: "fitness", 14: "dancing", 15: "baseball", 16: "elliptical",
    17: "indoor_cycling", 18: "free_exercise", 19: "rowing_machine",
}

# notification types (CMD_SEND_MESSAGE)
NOTIFY_CALL = 0
NOTIFY_SMS = 1
NOTIFY_OTHER = 11
NOTIFY_CALL_OFF_HOOK = 0xFF           # type byte -1: "call ended", sent with empty text

# weather condition ids (values > 7 render garbage)
WEATHER_CONDITIONS = {
    "cloudy": 0, "foggy": 1, "overcast": 2, "rainy": 3,
    "snowy": 4, "sunny": 5, "wind": 6, "haze": 7,
}

# The watch clock is hardwired to GMT+8 internally.
_WATCH_TZ = timezone(timedelta(hours=8))


# ------------------------------------------------------------------ time helpers
def local_to_watch_time(dt: Optional[datetime] = None) -> int:
    """Epoch the watch needs so its GMT+8 clock displays local wall-clock ``dt``.

    Mirrors Gadgetbridge's LocalTimeToWatchTime: take the local wall-clock components and
    reinterpret them as GMT+8, then take the epoch.
    """
    dt = dt or datetime.now()
    naive = dt.replace(tzinfo=None)
    return int(naive.replace(tzinfo=_WATCH_TZ).timestamp())


# ------------------------------------------------------------------ payload builders
def time_payload(dt: Optional[datetime] = None) -> bytes:
    return local_to_watch_time(dt).to_bytes(4, "big") + bytes([8])


def _field4(s: str) -> bytes:
    """Exactly 4 chars, space-padded/truncated, UTF-16 big-endian (8 bytes)."""
    return (s or "")[:4].ljust(4).encode("utf-16-be")


def weather_today_payload(temp: int, condition: int = WEATHER_CONDITIONS["sunny"],
                          city: str = "", lunar: str = "") -> bytes:
    """Today's weather. ``temp`` is a signed °C byte painted into the WEATHER_TEMP field."""
    if not -128 <= temp <= 127:
        raise ValueError(f"temp must fit a signed byte (-128..127), got {temp}")
    return (bytes([0, condition & 0xFF, temp & 0xFF]) + _field4(lunar) + _field4(city))


def notify_payload(text: str, ntype: int = NOTIFY_OTHER) -> bytes:
    """A screen notification. Title/body are split on the first ':' by the watch."""
    return bytes([ntype & 0xFF]) + text.encode("utf-8")


def music_payload(text: str, is_artist: bool) -> bytes:
    return bytes([1 if is_artist else 0]) + text.encode("utf-8")


def goal_steps_payload(steps: int) -> bytes:
    return int(steps).to_bytes(4, "big")   # SET_GOAL_STEP is big-endian (query is not!)


def hr_trigger_payload(start: bool = True) -> bytes:
    return bytes([0 if start else 0xFF])


def _signed_byte(value: int, name: str = "value") -> int:
    """Validate ``value`` fits a signed byte and return its 0..255 wire form."""
    if not -128 <= value <= 127:
        raise ValueError(f"{name} must fit a signed byte (-128..127), got {value}")
    return value & 0xFF


def shutdown_payload() -> bytes:
    """CMD_SHUTDOWN — powers the watch off (there is no reboot)."""
    return bytes([0xFF])


def call_off_hook_payload() -> bytes:
    """CMD_SEND_MESSAGE payload that clears an incoming-call screen (type -1, no text)."""
    return bytes([NOTIFY_CALL_OFF_HOOK])


def music_state_payload(playing: bool) -> bytes:
    """CMD_SET_MUSIC_STATE — {1} playing / {0} paused."""
    return bytes([1 if playing else 0])


def weather_location_payload(location: str) -> bytes:
    """CMD_SET_WEATHER_LOCATION — a free UTF-8 location string (GB prefixes 'HH:MM ')."""
    return location.encode("utf-8")


def weather_forecast_payload(today_condition: int, today_temp: int,
                             forecasts: Optional[List[Tuple[int, int, int]]] = None) -> bytes:
    """CMD_SET_WEATHER_FUTURE — 24 bytes: today then 7 days.

    Layout (from ``onSendWeather``): ``[today_cond, today_temp, today_temp]`` followed by
    seven ``(conditionId, high_temp, low_temp)`` triples. ``forecasts`` is padded/truncated to
    7; missing days use ``(haze, -100, -100)`` exactly like Gadgetbridge. Temps are signed °C.
    """
    out = bytearray([today_condition & 0xFF,
                     _signed_byte(today_temp, "today_temp"),
                     _signed_byte(today_temp, "today_temp")])
    forecasts = list(forecasts or [])[:7]
    for i in range(7):
        if i < len(forecasts):
            cond, high, low = forecasts[i]
            out += bytes([cond & 0xFF, _signed_byte(high, "high"), _signed_byte(low, "low")])
        else:
            out += bytes([WEATHER_CONDITIONS["haze"], _signed_byte(-100), _signed_byte(-100)])
    return bytes(out)


def sunrise_sunset_payload(sunrise_h: int, sunrise_m: int, sunset_h: int, sunset_m: int,
                           *, condition: int = 0, temp: int = 0, location: str = "") -> bytes:
    """CMD_SET_SUNRISE_SUNSET (0xB5) — {0, cond, temp, 0, 0, srH, srM, ssH, ssM} + loc UTF-8."""
    return (bytes([0x00, condition & 0xFF, _signed_byte(temp, "temp"), 0x00, 0x00,
                   sunrise_h & 0xFF, sunrise_m & 0xFF, sunset_h & 0xFF, sunset_m & 0xFF])
            + location.encode("utf-8"))


def dfu_payload(enable: bool = False) -> bytes:
    """CMD_HS_DFU (99) — the vendor firmware-update trigger. **BRICK RISK.**

    This puts the watch into the vendor over-the-air DFU bootloader. It is intentionally NOT
    wired into any normal flow; the CLI only exposes it behind ``--i-understand-brick-risk``.
    ``{1}`` enables HS-DFU, ``{0}`` queries the DFU address. Do not call this unless you have a
    known-good firmware image and a recovery path.
    """
    return bytes([1 if enable else 0])


# ------------------------------------------------------------------ framing
def build_packet(cmd_type: int, payload: bytes = b"", mtu: int = DEFAULT_MTU) -> bytes:
    """Wrap a command in the FE EA envelope. ``total`` length = payload + 5-byte header."""
    total = len(payload) + 5
    if mtu == 20:
        b2 = 0x10
    else:
        b2 = (0x20 + (total >> 8)) & 0xFF
    return bytes([0xFE, 0xEA, b2, total & 0xFF, cmd_type & 0xFF]) + payload


def parse_packet_length(buf: bytes) -> int:
    """Total packet length from the header, or -1 if the header is invalid."""
    if len(buf) < 4 or buf[0] != 0xFE or buf[1] != 0xEA:
        return -1
    if buf[2] == 0x10:
        len_h = 0
    else:
        if buf[2] < 0x20:
            return -1
        len_h = buf[2] - 0x20
    return (len_h << 8) | buf[3]


def parse_packet(buf: bytes) -> Optional[Tuple[int, bytes]]:
    """Return (cmd_type, payload) for a complete packet, or None if malformed."""
    total = parse_packet_length(buf)
    if total < 0 or total != len(buf) or total < 5:
        return None
    return buf[4], bytes(buf[5:])


class PacketReassembler:
    """Reassemble notification fragments (0xFEE3) into complete (cmd_type, payload) packets."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, fragment: bytes) -> List[Tuple[int, bytes]]:
        self._buf += fragment
        out: List[Tuple[int, bytes]] = []
        while len(self._buf) >= 4:
            total = parse_packet_length(self._buf)
            if total < 5:                      # bad header — resync by dropping a byte
                del self._buf[0]
                continue
            if len(self._buf) < total:
                break                          # need more fragments
            packet = bytes(self._buf[:total])
            del self._buf[:total]
            parsed = parse_packet(packet)
            if parsed is not None:
                out.append(parsed)
        return out
