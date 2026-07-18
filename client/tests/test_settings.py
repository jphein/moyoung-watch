"""Byte-exact tests for MoYoung device-settings builders + decoders (offline)."""
import pytest

from moyoung_client import commands as c
from moyoung_client import settings as s


# ------------------------------------------------------------------ enum / byte setters
def test_time_system():
    assert s.time_system_payload("24") == b"\x01"
    assert s.time_system_payload("12") == b"\x00"
    assert s.time_system_payload("12h") == b"\x00"
    assert s.time_system_payload(1) == b"\x01"


def test_metric_and_device_version_and_hand():
    assert s.metric_system_payload("metric") == b"\x00"
    assert s.metric_system_payload("imperial") == b"\x01"
    assert s.device_version_payload("international") == b"\x01"
    assert s.device_version_payload("chinese") == b"\x00"
    assert s.dominant_hand_payload("left") == b"\x00"
    assert s.dominant_hand_payload("right") == b"\x01"


def test_language_values():
    assert s.language_payload("english") == b"\x00"
    assert s.language_payload("chinese") == b"\x01"
    assert s.language_payload("russian") == bytes([8])
    assert s.language_payload("latvian") == bytes([24])
    with pytest.raises(ValueError):
        s.language_payload("klingon")


def test_watch_face_and_step_length():
    assert s.watch_face_payload(3) == b"\x03"
    assert s.step_length_payload(70) == bytes([70])


def test_hr_auto_interval_encoding():
    assert s.hr_auto_interval_payload("off") == bytes([0])
    assert s.hr_auto_interval_payload("5") == bytes([1])
    assert s.hr_auto_interval_payload("10") == bytes([2])
    assert s.hr_auto_interval_payload("20") == bytes([4])
    assert s.hr_auto_interval_payload("30") == bytes([6])


def test_bool_setters():
    for fn in (s.quick_view_payload, s.sedentary_payload, s.other_message_payload,
               s.breathing_light_payload, s.power_saving_payload):
        assert fn(True) == b"\x01"
        assert fn(False) == b"\x00"


# ------------------------------------------------------------------ composite setters
def test_user_info_layout():
    assert s.user_info_payload(170, 65, 25, "male") == bytes([170, 65, 25, 0])
    assert s.user_info_payload(160, 55, 30, "female") == bytes([160, 55, 30, 1])


def test_time_range_setters():
    assert s.dnd_time_payload(1, 0, 6, 30) == bytes([1, 0, 6, 30])
    assert s.quick_view_time_payload(8, 30, 22, 0) == bytes([8, 30, 22, 0])


def test_reminders_to_move_setter():
    assert s.reminders_to_move_payload(30, 100, 10, 22) == bytes([30, 100, 10, 22])


def test_display_functions():
    assert s.display_functions_payload([1, 2, 5]) == bytes([1, 2, 5, 0])
    assert s.display_functions_query_payload() == b""
    assert s.display_functions_query_payload(True) == b"\xff"


def test_watch_face_layout_build():
    # text_color 0xF800 (red R5G6B5); md5 all 'a' -> 32 nibble bytes of 0x0A
    pkt = s.watch_face_layout_payload(1, 2, 3, 0xF800, "a" * 32)
    assert pkt[:5] == bytes([1, 2, 3, 0xF8, 0x00])
    assert pkt[5:] == bytes([0x0A] * 32)
    assert len(pkt) == 37
    with pytest.raises(ValueError):
        s.watch_face_layout_payload(0, 0, 0, 0, "nothex" * 5 + "gg")


def test_alarm_oneshot_with_date():
    # 2026-07-17: ym = ((2026-2015)<<4)+7 = (11<<4)+7 = 183; day = 17
    pkt = s.alarm_payload(1, 7, 30, year=2026, month=7, day=17)
    assert pkt == bytes([1, 1, 0, 7, 30, 183, 17, 0])


def test_alarm_everyday_and_custom_days():
    everyday = s.alarm_payload(2, 8, 0, days=["sun", "mon", "tue", "wed", "thu", "fri", "sat"])
    assert everyday == bytes([2, 1, 1, 8, 0, 0, 0, 0x7F])   # repeat=1, mask=0x7F
    custom = s.alarm_payload(0, 9, 15, days=["mon", "wed", "fri"])
    # bits 1,3,5 = 0b101010 = 0x2A, repeat=2
    assert custom == bytes([0, 1, 2, 9, 15, 0, 0, 0x2A])


def test_alarm_disabled_flag():
    assert s.alarm_payload(3, 6, 0, enabled=False, days=["mon"])[1] == 0


def test_psychological_period_flags():
    p = s.psychological_period_payload(28, 5, 6, 15, 9, 0)
    assert p[0] == 241 and p[1] == 15 and p[2] == 28 and p[3] == 5
    assert p[4] == 6 and p[5] == 15
    assert p[6:14] == bytes([9, 0]) * 4
    p2 = s.psychological_period_payload(28, 5, 6, 15, menstrual_reminder=False,
                                        ovulation_reminder=True, ovulation_day_reminder=True)
    assert p2[0] == 240 + 2 + 4


# ------------------------------------------------------------------ decoders
def test_decode_scalars():
    assert s.decode_bool(b"\x01") is True
    assert s.decode_bool(b"\x00") is False
    assert s.decode_bool(b"") is False
    assert s.decode_byte(b"\x05") == 5
    assert s.decode_goal_step((10000).to_bytes(4, "little")) == 10000
    assert s.decode_watch_face(b"\x04") == 4


def test_decode_named_enums():
    assert s.decode_time_system(b"\x01") == {"value": 1, "name": "24h"}
    assert s.decode_time_system(b"\x00") == {"value": 0, "name": "12h"}
    assert s.decode_metric_system(b"\x01")["name"] == "imperial"
    assert s.decode_device_version(b"\x01")["name"] == "international"
    assert s.decode_dominant_hand(b"\x01")["name"] == "right"


def test_decode_time_range_big_endian():
    # start = 60 min (01:00), end = 390 min (06:30), 16-bit big-endian
    payload = (60).to_bytes(2, "big") + (390).to_bytes(2, "big")
    assert s.decode_time_range(payload) == {"start_h": 1, "start_m": 0, "end_h": 6, "end_m": 30}
    with pytest.raises(ValueError):
        s.decode_time_range(b"\x00")


def test_decode_reminders_to_move():
    assert s.decode_reminders_to_move(bytes([30, 100, 10, 22])) == {
        "period": 30, "steps": 100, "start_h": 10, "end_h": 22}


def test_decode_watch_face_layout():
    payload = bytes([1, 2, 3, 0xF8, 0x00]) + bytes([0x0A] * 32)
    got = s.decode_watch_face_layout(payload)
    assert got["time_position"] == 1 and got["time_top"] == 2 and got["time_bottom"] == 3
    assert got["text_color"] == 0xF800
    assert got["background_md5"] == "a" * 32


def test_decode_support_watch_face_and_display_functions():
    assert s.decode_support_watch_face(bytes([0, 5])) == 5
    assert s.decode_support_watch_face(bytes([1, 0])) == 256
    assert s.decode_display_functions(bytes([1, 2, 3, 0])) == {
        "lists_supported": False, "functions": [1, 2, 3]}
    assert s.decode_display_functions(bytes([0xFF, 4, 5, 0]))["lists_supported"] is True


def test_decode_language():
    # current english(0); supported bits 0,1,8 -> mask 0b100000011 = 259, big-endian uint32
    mask = (1 << 0) | (1 << 1) | (1 << 8)
    payload = bytes([0]) + mask.to_bytes(4, "big")
    got = s.decode_language(payload)
    assert got["current"] == 0 and got["current_name"] == "english"
    assert set(got["supported"]) == {"english", "chinese", "russian"}


def test_decode_alarms_v1():
    e0 = bytes([0, 1, 1, 7, 30, 0, 0, 0x02])   # mon (bit1)
    e1 = bytes([1, 0, 0, 9, 0, 0, 0, 0x7F])    # all days, disabled
    alarms = s.decode_alarms(e0 + e1)
    assert len(alarms) == 2
    assert alarms[0] == {"index": 0, "enabled": True, "hour": 7, "minute": 30,
                         "days": ["mon"], "repeat_mask": 2}
    assert alarms[1]["enabled"] is False and set(alarms[1]["days"]) == set(s.WEEKDAY_BITS)


def test_decode_alarms_v2_prefixed():
    prefix = bytes([0x15, 0x04, 1])            # {subtype, arg, count}
    entry = bytes([2, 1, 1, 6, 45, 0, 0, 0x01])  # sun (bit0)
    alarms = s.decode_alarms(prefix + entry)
    assert len(alarms) == 1
    assert alarms[0]["index"] == 2 and alarms[0]["days"] == ["sun"]


def test_decode_alarms_bad_length():
    with pytest.raises(ValueError):
        s.decode_alarms(bytes(5))
