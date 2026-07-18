"""Byte-exact tests for MoYoung health/measurement builders + decoders (offline)."""
import struct
from datetime import datetime

import pytest

from moyoung_client import commands as c
from moyoung_client import health as h


# ------------------------------------------------------------------ triggers
def test_measurement_triggers():
    assert h.blood_pressure_payload(True) == b"\x00\x00\x00"
    assert h.blood_pressure_payload(False) == b"\xff\xff\xff"
    assert h.blood_oxygen_payload(True) == b"\x00"
    assert h.blood_oxygen_payload(False) == b"\xff"
    assert h.dynamic_hr_payload(True) == b"\x00"
    assert h.dynamic_hr_payload(False) == b"\xff"


def test_ecg_modes():
    assert h.ecg_payload("start") == b"\x01"
    assert h.ecg_payload("stop") == b"\x00"
    assert h.ecg_payload("query") == b"\x02"
    assert h.ecg_payload(72) == bytes([72])
    with pytest.raises(ValueError):
        h.ecg_payload("bogus")


def test_sync_and_query_builders():
    assert h.sync_sleep_payload() == b""
    assert h.movement_hr_payload() == b""
    assert h.last_dynamic_rate_payload() == b""
    assert h.sync_past_payload("yesterday_steps") == bytes([1])
    assert h.sync_past_payload("day_before_steps") == bytes([2])
    assert h.sync_past_payload("yesterday_sleep") == bytes([3])
    assert h.sync_past_payload("day_before_sleep") == bytes([4])
    assert h.sync_past_payload(3) == bytes([3])
    assert h.steps_category_payload(2) == b"\x02"
    assert h.past_heart_rate_payload(0) == b"\x00"
    assert h.past_heart_rate2_payload(5) == b"\x05"
    assert h.sleep_action_payload(3) == b"\x03"


def test_sync_past_unknown():
    with pytest.raises(ValueError):
        h.sync_past_payload("last_week")


# ------------------------------------------------------------------ measurement decoders
def test_decode_hr_spo2_bp():
    assert h.decode_hr(bytes([75])) == 75
    assert h.decode_spo2(bytes([98])) == 98
    assert h.decode_blood_pressure(bytes([0, 120, 80])) == {
        "systolic": 120, "diastolic": 80, "unknown": 0}
    with pytest.raises(ValueError):
        h.decode_blood_pressure(bytes([0, 0]))


def test_decode_dynamic_hr_event():
    assert h.decode_dynamic_hr_event(bytes([1])) == {
        "running": True, "workout_type": 1, "workout_name": "run"}
    assert h.decode_dynamic_hr_event(bytes([0xFF])) == {
        "running": False, "workout_type": None, "workout_name": None}


# ------------------------------------------------------------------ history decoders
def test_decode_sleep():
    payload = bytes([1, 23, 0]) + bytes([2, 23, 30]) + bytes([0, 7, 0])
    stages = h.decode_sleep(payload)
    assert stages[0] == {"stage": 1, "stage_name": "light", "start_h": 23, "start_m": 0}
    assert stages[1]["stage_name"] == "restful"
    assert stages[2]["stage_name"] == "sober"
    with pytest.raises(ValueError):
        h.decode_sleep(bytes(4))


def test_decode_past_steps_distance_first():
    body = (5000).to_bytes(3, "little") + (6000).to_bytes(3, "little") + (200).to_bytes(3, "little")
    got = h.decode_past(bytes([c.ARG_SYNC_YESTERDAY_STEPS]) + body)
    assert got == {"kind": "steps", "arg": 1,
                   "distance": 5000, "steps": 6000, "calories": 200}


def test_decode_past_sleep():
    got = h.decode_past(bytes([c.ARG_SYNC_YESTERDAY_SLEEP]) + bytes([2, 1, 0]))
    assert got["kind"] == "sleep"
    assert got["stages"][0]["stage_name"] == "restful"


def test_decode_steps_category():
    body = (100).to_bytes(2, "little") + (200).to_bytes(2, "little") + (300).to_bytes(2, "little")
    assert h.decode_steps_category(bytes([0]) + body) == {"index": 0, "buckets": [100, 200, 300]}


def test_decode_movement_hr():
    entry = struct.pack("<IIHBBIIH", 1000000, 1003600, 3500, 0, 1, 5000, 4200, 250) + b"\x00\x00"
    assert len(entry) == 24
    out = h.decode_movement_hr(entry + bytes(24))   # second (empty) entry is skipped
    assert len(out) == 1
    e = out[0]
    assert e["start"] == 1000000 and e["end"] == 1003600 and e["valid_time_s"] == 3500
    assert e["workout_type"] == 1 and e["workout_name"] == "run"
    assert e["steps"] == 5000 and e["distance"] == 4200 and e["calories"] == 250


def test_decode_last_dynamic_rate():
    assert h.decode_last_dynamic_rate_packet(bytes([0, 1, 2, 3])) == {
        "sequence": 0, "data": b"\x01\x02\x03"}
    dt = datetime(2026, 7, 17, 15, 48, 0)
    epoch = c.local_to_watch_time(dt)
    assembled = epoch.to_bytes(4, "little") + bytes([60, 0, 62])
    got = h.decode_last_dynamic_rate(assembled)
    assert got["heart_rates"] == [60, 0, 62]
    assert got["start_time"] == dt          # inverse of commands.local_to_watch_time


def test_decode_hr_history():
    payload = bytes([5]) + bytes(range(72))
    got = h.decode_hr_history(payload)
    assert got["index"] == 5 and got["days_ago"] == 1 and got["start_hour"] == 6
    assert len(got["heart_rates"]) == 72


def test_decode_sleep_action():
    got = h.decode_sleep_action(bytes([22]) + bytes(60))
    assert got["hour"] == 22 and len(got["values"]) == 60


def test_watch_epoch_roundtrip():
    dt = datetime(2025, 1, 2, 3, 4, 5)
    assert h.watch_epoch_to_wallclock(c.local_to_watch_time(dt)) == dt
