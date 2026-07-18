"""Incoming watch->phone events + phone/interaction builders.

The watch pushes unsolicited packets on the notify characteristic: a camera shutter press, a
find-phone toggle, media/call control, a weather-retransmit request, and measurement
completions. :func:`decode_event` turns one ``(cmd_type, payload)`` into an :class:`Event`;
:class:`EventDecoder` adds the small amount of state Gadgetbridge tracks (camera open/shutter
and find-phone start/stop are the *same* opcode toggling), so downstream automation can treat
the watch as a wireless button.

Dispatch mirrors ``MoyoungDeviceSupport.handlePacket`` (Gadgetbridge, AGPLv3, krzys_h).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from . import commands as c
from . import health as h

# CMD_NOTIFY_PHONE_OPERATION operation byte -> name
PHONE_OPERATIONS = {
    c.ARG_OPERATION_PLAY_PAUSE: "play_pause",
    c.ARG_OPERATION_PREV_SONG: "prev",
    c.ARG_OPERATION_NEXT_SONG: "next",
    c.ARG_OPERATION_DROP_INCOMING_CALL: "reject_call",
    c.ARG_OPERATION_VOLUME_UP: "volume_up",
    c.ARG_OPERATION_VOLUME_DOWN: "volume_down",
    c.ARG_OPERATION_PLAY: "play",
    c.ARG_OPERATION_PAUSE: "pause",
}


@dataclass
class Event:
    """A decoded incoming event. ``kind`` is a stable string; ``data`` is kind-specific."""
    kind: str
    cmd: int
    data: Dict[str, object] = field(default_factory=dict)
    payload: bytes = b""


# ------------------------------------------------------------------ outgoing interaction
def camera_open_payload() -> bytes:
    """CMD_SWITCH_CAMERA_VIEW — open the camera-remote screen on the watch (empty payload)."""
    return b""


def find_phone_stop_payload() -> bytes:
    """CMD_FIND_MY_PHONE — {0xFF} tells the watch to stop the find-phone alert."""
    return bytes([0xFF])


def return_home_payload() -> bytes:
    """CMD_RETURN_PRINCIPAL_SCREEN — send the watch back to its home screen (empty payload)."""
    return b""


def send_volume_payload(level: int) -> bytes:
    """CMD_NOTIFY_PHONE_OPERATION — report current phone volume (0..16) to the watch."""
    return bytes([c.ARG_OPERATION_SEND_CURRENT_VOLUME, max(0, min(16, level)) & 0xFF])


def phone_operation_payload(operation: int, *extra: int) -> bytes:
    """CMD_NOTIFY_PHONE_OPERATION — generic {operation[, extra...]} builder."""
    return bytes([operation & 0xFF, *(e & 0xFF for e in extra)])


# ------------------------------------------------------------------ incoming decode
def decode_event(cmd_type: int, payload: bytes) -> Optional[Event]:
    """Decode a single incoming packet into an :class:`Event`, or ``None`` if unrecognised.

    Stateless: cmd 102 is reported as ``camera_shutter`` and cmd 98 as ``find_phone`` without
    the open/close or start/stop distinction — use :class:`EventDecoder` for that.
    """
    p = bytes(payload)
    if cmd_type == c.CMD_SWITCH_CAMERA_VIEW:
        return Event("camera_shutter", cmd_type, {}, p)
    if cmd_type == c.CMD_RETURN_PRINCIPAL_SCREEN:
        return Event("return_home", cmd_type, {}, p)
    if cmd_type == c.CMD_FIND_MY_PHONE:
        # constant comment: incoming {0} start, {!=0} stop
        action = None
        if p:
            action = "start" if p[0] == 0 else "stop"
        return Event("find_phone", cmd_type, {"action": action}, p)
    if cmd_type == c.CMD_NOTIFY_PHONE_OPERATION:
        op = p[0] if p else -1
        return Event("phone_operation", cmd_type,
                     {"operation": op, "name": PHONE_OPERATIONS.get(op, "unknown")}, p)
    if cmd_type == c.CMD_NOTIFY_WEATHER_CHANGE:
        return Event("weather_request", cmd_type, {}, p)
    if cmd_type == c.CMD_TRIGGER_MEASURE_HEARTRATE:
        return Event("hr", cmd_type, {"bpm": h.decode_hr(p)}, p)
    if cmd_type == c.CMD_TRIGGER_MEASURE_BLOOD_OXYGEN:
        return Event("spo2", cmd_type, {"percent": h.decode_spo2(p)}, p)
    if cmd_type == c.CMD_TRIGGER_MEASURE_BLOOD_PRESSURE:
        return Event("blood_pressure", cmd_type, h.decode_blood_pressure(p), p)
    if cmd_type == c.CMD_START_STOP_MEASURE_DYNAMIC_RATE:
        return Event("training", cmd_type, h.decode_dynamic_hr_event(p), p)
    if cmd_type == c.CMD_QUERY_LAST_DYNAMIC_RATE:
        return Event("last_dynamic_rate", cmd_type, h.decode_last_dynamic_rate_packet(p), p)
    return None


class EventDecoder:
    """Stateful incoming-event decoder mirroring Gadgetbridge's toggles.

    The watch reuses one opcode for a two-state action: cmd 102 first *opens* the camera then
    reports each *shutter* press (cmd 83 closes it), and cmd 98 alternately *starts* and *stops*
    find-phone. This class tracks that state so callers get ``camera_open`` / ``camera_shutter``
    / ``camera_close`` and find-phone ``start`` / ``stop`` distinctly.
    """

    def __init__(self) -> None:
        self._camera_open = False
        self._find_phone_active = False

    def feed(self, cmd_type: int, payload: bytes) -> Optional[Event]:
        p = bytes(payload)
        if cmd_type == c.CMD_SWITCH_CAMERA_VIEW:
            if not self._camera_open:
                self._camera_open = True
                return Event("camera_open", cmd_type, {}, p)
            return Event("camera_shutter", cmd_type, {}, p)
        if cmd_type == c.CMD_RETURN_PRINCIPAL_SCREEN:
            if self._camera_open:
                self._camera_open = False
                return Event("camera_close", cmd_type, {}, p)
            return Event("return_home", cmd_type, {}, p)
        if cmd_type == c.CMD_FIND_MY_PHONE:
            action = "stop" if self._find_phone_active else "start"
            self._find_phone_active = not self._find_phone_active
            return Event("find_phone", cmd_type, {"action": action}, p)
        return decode_event(cmd_type, p)
