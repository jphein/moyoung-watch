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
CMD_SWITCH_CAMERA_VIEW = 102      # 0x66 {} — OUT: open watch camera screen; IN: shutter/take-photo
CMD_SHUTDOWN = 81                 # {0xFF}
CMD_TRIGGER_MEASURE_HEARTRATE = 109   # {0} start / {0xFF} stop -> responds {bpm}

# Watch-face selection (used to auto-activate a just-uploaded custom face).
CMD_SET_DISPLAY_WATCH_FACE = 25       # {index} — select by DISPLAY-LIST index (NOT storage slot!)
CMD_QUERY_DISPLAY_WATCH_FACE = 41     # {} -> {current_index}
CMD_QUERY_SUPPORT_WATCH_FACE = 132    # 0x84 (-124): {} -> {count>>8, count, ...}

# notification types (CMD_SEND_MESSAGE)
NOTIFY_CALL = 0
NOTIFY_SMS = 1
NOTIFY_OTHER = 11

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


def watch_face_payload(index: int) -> bytes:
    """CMD_SET_DISPLAY_WATCH_FACE — select the active face by its DISPLAY-LIST index."""
    return bytes([index & 0xFF])


def decode_watch_face(payload: bytes) -> int:
    """CMD_QUERY_DISPLAY_WATCH_FACE reply — the currently displayed face index."""
    return payload[0] if payload else -1


def decode_support_watch_face(payload: bytes) -> int:
    """CMD_QUERY_SUPPORT_WATCH_FACE reply — total selectable faces {count>>8, count, ...}."""
    return int.from_bytes(payload[:2], "big") if len(payload) >= 2 else -1


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
