"""MoYoung health & measurement payloads: on-demand triggers, history sync, and decoders.

Trigger builders start/stop a live measurement (the result arrives later as an incoming packet
— see :mod:`moyoung_client.events`). Sync builders pull stored history; the ``decode_*``
helpers parse those replies. Layouts come from ``MoyoungDeviceSupport`` +
``FetchDataOperation`` + ``TrainingFinishedDataOperation`` (Gadgetbridge, AGPLv3, krzys_h).

Multi-byte history values are little-endian (matching GB's fetch operations). Step triples use
the project's established ``{distance, steps, calories}`` order (see ``transport.read_steps`` and
the constant comment); GB's own ``handleStepsHistory`` reads steps-first — a documented
in-GB inconsistency, noted in scratch/gb-port/coverage.md.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Union

from . import commands as c

_WATCH_TZ = timezone(timedelta(hours=8))  # the watch clock is hardwired to GMT+8


def _u24le(b: bytes) -> int:
    return int.from_bytes(b[:3], "little")


def watch_epoch_to_wallclock(epoch: int) -> datetime:
    """Watch GMT+8 epoch -> the naive local wall-clock the watch displayed (inverse of set-time)."""
    return datetime.fromtimestamp(epoch, tz=_WATCH_TZ).replace(tzinfo=None)


# ------------------------------------------------------------------ measurement triggers
def blood_pressure_payload(start: bool = True) -> bytes:
    """CMD_TRIGGER_MEASURE_BLOOD_PRESSURE — {0,0,0} start / {0xFF,0xFF,0xFF} stop."""
    return bytes([0, 0, 0]) if start else bytes([0xFF, 0xFF, 0xFF])


def blood_oxygen_payload(start: bool = True) -> bytes:
    """CMD_TRIGGER_MEASURE_BLOOD_OXYGEN — {0} start / {0xFF} stop."""
    return bytes([0 if start else 0xFF])


def dynamic_hr_payload(start: bool = True) -> bytes:
    """CMD_START_STOP_MEASURE_DYNAMIC_RATE — {0} start / {0xFF} stop the continuous HR stream."""
    return bytes([0 if start else 0xFF])


def ecg_payload(mode: Union[int, str] = "start") -> bytes:
    """CMD_ECG — {1} start / {0} stop / {2} query, or pass an int heart-rate/raw byte.

    ECG waveform data returns on a dedicated ECG characteristic that this hardware (MOY-ERJ3)
    does not expose, so only the trigger is meaningful here.
    """
    table = {"start": 1, "stop": 0, "query": 2}
    if isinstance(mode, str):
        if mode.strip().lower() not in table:
            raise ValueError(f"ecg mode must be start/stop/query or an int, got {mode!r}")
        return bytes([table[mode.strip().lower()]])
    return bytes([int(mode) & 0xFF])


# ------------------------------------------------------------------ history sync (queries)
def sync_sleep_payload() -> bytes:
    """CMD_SYNC_SLEEP — today's sleep stages (empty query)."""
    return b""


SYNC_PAST = {
    "yesterday_steps": c.ARG_SYNC_YESTERDAY_STEPS,
    "day_before_steps": c.ARG_SYNC_DAY_BEFORE_YESTERDAY_STEPS,
    "yesterday_sleep": c.ARG_SYNC_YESTERDAY_SLEEP,
    "day_before_sleep": c.ARG_SYNC_DAY_BEFORE_YESTERDAY_SLEEP,
}


def sync_past_payload(which: Union[int, str]) -> bytes:
    """CMD_SYNC_PAST_SLEEP_AND_STEP — {arg}. ``which`` names a day/kind (see ``SYNC_PAST``)."""
    if isinstance(which, str):
        key = which.strip().lower()
        if key not in SYNC_PAST:
            raise ValueError(f"unknown sync target {which!r}; choices: {', '.join(SYNC_PAST)}")
        return bytes([SYNC_PAST[key]])
    return bytes([int(which) & 0xFF])


def steps_category_payload(index: int = 0) -> bytes:
    """CMD_QUERY_STEPS_CATEGORY — {index}. 0 = today, 2 = yesterday (hourly buckets)."""
    return bytes([index & 0xFF])


def movement_hr_payload() -> bytes:
    """CMD_QUERY_MOVEMENT_HEART_RATE — last 3 workout summaries (empty query)."""
    return b""


def last_dynamic_rate_payload() -> bytes:
    """CMD_QUERY_LAST_DYNAMIC_RATE — empty payload requests the NEXT continuation packet.

    This transfer is watch-initiated: the watch sends the first packet after a workout ends,
    then the phone repeats this empty query until it receives the ``ARG_TRANSMISSION_LAST`` packet.
    """
    return b""


def past_heart_rate_payload(index: int = 0) -> bytes:
    """CMD_QUERY_PAST_HEART_RATE_1 — {index}. 5-min HR history; 8 packets (0..7) = 2 days."""
    return bytes([index & 0xFF])


def past_heart_rate2_payload(index: int) -> bytes:
    """CMD_QUERY_PAST_HEART_RATE_2 — {index}. 1-min HR history (marked * in GB)."""
    return bytes([index & 0xFF])


def sleep_action_payload(index: int) -> bytes:
    """CMD_QUERY_SLEEP_ACTION — {i} -> {hour, x[60]} (marked * in GB)."""
    return bytes([index & 0xFF])


# ------------------------------------------------------------------ measurement decoders
def decode_hr(payload: bytes) -> int:
    """CMD_TRIGGER_MEASURE_HEARTRATE completion — {bpm}."""
    return payload[0] if payload else -1


def decode_spo2(payload: bytes) -> int:
    """CMD_TRIGGER_MEASURE_BLOOD_OXYGEN completion — {percent}."""
    return payload[0] if payload else -1


def decode_blood_pressure(payload: bytes) -> Dict[str, int]:
    """CMD_TRIGGER_MEASURE_BLOOD_PRESSURE completion — {unknown, systolic, diastolic}."""
    if len(payload) < 3:
        raise ValueError(f"blood pressure needs 3 bytes, got {len(payload)}")
    return {"systolic": payload[1], "diastolic": payload[2], "unknown": payload[0]}


def decode_dynamic_hr_event(payload: bytes) -> Dict[str, object]:
    """CMD_START_STOP_MEASURE_DYNAMIC_RATE incoming — training started/stopped on the watch."""
    kind = payload[0] if payload else 0xFF
    if kind == 0xFF:
        return {"running": False, "workout_type": None, "workout_name": None}
    return {"running": True, "workout_type": kind,
            "workout_name": c.WORKOUT_TYPES.get(kind, "unknown")}


# ------------------------------------------------------------------ history decoders
def decode_sleep(payload: bytes) -> List[Dict[str, object]]:
    """CMD_SYNC_SLEEP reply — repeating {type, start_h, start_m} triples."""
    if len(payload) % 3 != 0:
        raise ValueError(f"sleep payload must be a multiple of 3, got {len(payload)}")
    names = {c.SLEEP_SOBER: "sober", c.SLEEP_LIGHT: "light",
             c.SLEEP_RESTFUL: "restful", c.SLEEP_REM: "rem"}
    out: List[Dict[str, object]] = []
    for i in range(0, len(payload), 3):
        stage = payload[i]
        out.append({"stage": stage, "stage_name": names.get(stage, "unknown"),
                    "start_h": payload[i + 1], "start_m": payload[i + 2]})
    return out


def decode_past(payload: bytes) -> Dict[str, object]:
    """CMD_SYNC_PAST_SLEEP_AND_STEP reply — {arg} then step triple (arg<=2) or sleep triples."""
    if not payload:
        raise ValueError("empty past-data payload")
    arg = payload[0]
    data = payload[1:]
    if arg in (c.ARG_SYNC_YESTERDAY_STEPS, c.ARG_SYNC_DAY_BEFORE_YESTERDAY_STEPS):
        if len(data) < 9:
            raise ValueError(f"step triple needs 9 bytes, got {len(data)}")
        return {"kind": "steps", "arg": arg,
                "distance": _u24le(data[0:3]), "steps": _u24le(data[3:6]),
                "calories": _u24le(data[6:9])}
    return {"kind": "sleep", "arg": arg, "stages": decode_sleep(data)}


def decode_steps_category(payload: bytes) -> Dict[str, object]:
    """CMD_QUERY_STEPS_CATEGORY reply — {index, uint16[] hourly buckets} (little-endian)."""
    if not payload:
        raise ValueError("empty steps-category payload")
    index = payload[0]
    body = payload[1:]
    buckets = [int.from_bytes(body[i:i + 2], "little") for i in range(0, len(body) - 1, 2)]
    return {"index": index, "buckets": buckets}


def decode_movement_hr(payload: bytes) -> List[Dict[str, object]]:
    """CMD_QUERY_MOVEMENT_HEART_RATE reply — 24-byte workout summaries (v1 layout, little-endian).

    Layout: startTime u32, endTime u32, validTime u16, entry_number u8, type u8, steps u32,
    distance u32, calories u16 (2 trailing bytes unused). All-zero entries are skipped.
    """
    out: List[Dict[str, object]] = []
    for i in range(0, len(payload) - 23, 24):
        e = payload[i:i + 24]
        if not any(e):
            continue
        start = int.from_bytes(e[0:4], "little")
        end = int.from_bytes(e[4:8], "little")
        out.append({
            "start": start, "end": end,
            "start_time": watch_epoch_to_wallclock(start),
            "end_time": watch_epoch_to_wallclock(end),
            "valid_time_s": int.from_bytes(e[8:10], "little"),
            "entry_number": e[10],
            "workout_type": e[11], "workout_name": c.WORKOUT_TYPES.get(e[11], "unknown"),
            "steps": int.from_bytes(e[12:16], "little"),
            "distance": int.from_bytes(e[16:20], "little"),
            "calories": int.from_bytes(e[20:22], "little"),
        })
    return out


def decode_last_dynamic_rate_packet(payload: bytes) -> Dict[str, object]:
    """One CMD_QUERY_LAST_DYNAMIC_RATE packet — {sequence, data} (strip the sequence byte).

    ``sequence``: 0 first, 1 continuation, 2 last (empty). Concatenate the ``data`` fields in
    order and pass to :func:`decode_last_dynamic_rate` once the last packet arrives.
    """
    if not payload:
        raise ValueError("empty dynamic-rate packet")
    return {"sequence": payload[0], "data": payload[1:]}


def decode_last_dynamic_rate(assembled: bytes) -> Dict[str, object]:
    """The concatenated CMD_QUERY_LAST_DYNAMIC_RATE data — u32 start time (LE) + per-minute HR.

    Heart-rate samples are one byte each at 1-minute spacing; 0 means "no valid measurement".
    """
    if len(assembled) < 4:
        raise ValueError(f"assembled dynamic-rate needs >=4 bytes, got {len(assembled)}")
    epoch = int.from_bytes(assembled[0:4], "little")
    return {"start": epoch, "start_time": watch_epoch_to_wallclock(epoch),
            "heart_rates": list(assembled[4:])}


def decode_hr_history(payload: bytes) -> Dict[str, object]:
    """CMD_QUERY_PAST_HEART_RATE_1 reply — {index} + 72 five-minute HR bytes (6h/packet).

    ``days_ago = index // 4``; ``start_hour = (index % 4) * 6``; there are 6 hours × 12
    (five-minute) samples = 72 values; a value of 0 is an invalid/absent reading.
    """
    if not payload:
        raise ValueError("empty HR-history payload")
    index = payload[0]
    return {"index": index, "days_ago": index // 4, "start_hour": (index % 4) * 6,
            "heart_rates": list(payload[1:])}


def decode_sleep_action(payload: bytes) -> Dict[str, object]:
    """CMD_QUERY_SLEEP_ACTION reply — {hour, x[60]} (marked * in GB)."""
    if not payload:
        raise ValueError("empty sleep-action payload")
    return {"hour": payload[0], "values": list(payload[1:])}
