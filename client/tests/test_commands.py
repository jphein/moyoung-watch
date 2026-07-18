"""Byte-exact tests for the MoYoung control/sync command protocol (offline)."""
from datetime import datetime, timedelta, timezone

import pytest

from moyoung_client import commands as c
from moyoung_client import faceupload as fu


# ------------------------------------------------------------------ framing
def test_build_packet_header_small():
    pkt = c.build_packet(0x74, b"\x00\x00\x00\x09")
    # FE EA | 0x20 (v2, len<256) | 0x09 (total=9) | 0x74 | payload
    assert pkt == bytes.fromhex("feea200974") + b"\x00\x00\x00\x09"


def test_build_packet_matches_faceupload_envelope():
    # face upload's start command is just command 0x74 through the same framing.
    length = 63108
    assert c.build_packet(0x74, length.to_bytes(4, "big")) == fu.face_start(length)


def test_build_packet_length_high_byte_rolls_over_256():
    payload = bytes(252)                 # total = 257 -> high byte 1
    pkt = c.build_packet(0x41, payload)
    assert pkt[2] == 0x21                 # 0x20 + 1
    assert pkt[3] == 0x01                 # 257 & 0xFF
    assert c.parse_packet_length(pkt) == 257


def test_v1_framing_when_mtu_20():
    pkt = c.build_packet(0x31, b"\x01", mtu=20)
    assert pkt[2] == 0x10
    assert pkt[3] == 6


def test_parse_packet_roundtrip():
    pkt = c.build_packet(0x43, b"hello")
    assert c.parse_packet(pkt) == (0x43, b"hello")


def test_parse_packet_rejects_bad_header():
    assert c.parse_packet(b"\x00\x01\x02\x03\x04") is None
    assert c.parse_packet(bytes.fromhex("feea200974") + b"\x00") is None  # length mismatch


# ------------------------------------------------------------------ reassembler
def test_reassembler_across_fragments():
    pkt = c.build_packet(0x6d, bytes(range(30)))   # 35 bytes total
    r = c.PacketReassembler()
    assert r.feed(pkt[:20]) == []                  # first MTU fragment, incomplete
    out = r.feed(pkt[20:])
    assert out == [(0x6d, bytes(range(30)))]


def test_reassembler_two_packets_one_buffer():
    a = c.build_packet(0x01, b"aa")
    b = c.build_packet(0x02, b"bbbb")
    r = c.PacketReassembler()
    assert r.feed(a + b) == [(0x01, b"aa"), (0x02, b"bbbb")]


# ------------------------------------------------------------------ payload builders
def test_time_payload_semantics():
    dt = datetime(2026, 7, 16, 15, 48, 0)
    p = c.time_payload(dt)
    assert len(p) == 5 and p[4] == 8
    epoch = int.from_bytes(p[:4], "big")
    # the epoch, read back in the watch's GMT+8, must equal the wall-clock we asked for
    back = datetime.fromtimestamp(epoch, tz=timezone(timedelta(hours=8))).replace(tzinfo=None)
    assert back == dt


def test_weather_payload_layout_and_negative_temp():
    p = c.weather_today_payload(-5, condition=c.WEATHER_CONDITIONS["snowy"], city="HOME")
    assert len(p) == 19                       # 3 + 8 (lunar) + 8 (city), no pm25
    assert p[0] == 0                          # no pm25
    assert p[1] == 4                           # snowy
    assert p[2] == 0xFB                        # -5 as signed byte
    assert p[3:11] == "    ".encode("utf-16-be")   # default lunar = 4 spaces
    assert p[11:19] == "HOME".encode("utf-16-be")


def test_weather_city_truncated_and_padded_to_four_chars():
    assert c.weather_today_payload(20, city="A")[11:19] == "A   ".encode("utf-16-be")
    assert c.weather_today_payload(20, city="LONGCITY")[11:19] == "LONG".encode("utf-16-be")


def test_weather_rejects_out_of_range_temp():
    with pytest.raises(ValueError):
        c.weather_today_payload(200)


def test_notify_payload():
    assert c.notify_payload("Title:body", ntype=11) == bytes([11]) + b"Title:body"


def test_music_payload_artist_flag():
    assert c.music_payload("Radiohead", True) == b"\x01Radiohead"
    assert c.music_payload("Creep", False) == b"\x00Creep"


def test_goal_steps_big_endian():
    assert c.goal_steps_payload(10000) == (10000).to_bytes(4, "big")


def test_hr_trigger_payload():
    assert c.hr_trigger_payload(True) == b"\x00"
    assert c.hr_trigger_payload(False) == b"\xff"


# ------------------------------------------------------------------ new shared builders
def test_shutdown_and_call_off_hook():
    assert c.shutdown_payload() == b"\xff"
    assert c.call_off_hook_payload() == bytes([c.NOTIFY_CALL_OFF_HOOK]) == b"\xff"


def test_music_state_payload():
    assert c.music_state_payload(True) == b"\x01"
    assert c.music_state_payload(False) == b"\x00"


def test_weather_location_payload():
    assert c.weather_location_payload("Paris") == b"Paris"


def test_weather_forecast_layout_and_padding():
    p = c.weather_forecast_payload(5, 20, [(3, 25, 15)])
    # today = [cond, temp, temp] then 7 (cond, max, min) triples => 3 + 21 = 24 bytes
    assert len(p) == 24
    assert p[:6] == bytes([5, 20, 20, 3, 25, 15])
    # remaining 6 days padded with (haze=7, -100, -100)
    assert p[6:9] == bytes([c.WEATHER_CONDITIONS["haze"], (-100) & 0xFF, (-100) & 0xFF])


def test_weather_forecast_truncates_to_seven():
    days = [(0, i, i) for i in range(10)]
    assert len(c.weather_forecast_payload(5, 10, days)) == 24


def test_weather_forecast_rejects_bad_temp():
    with pytest.raises(ValueError):
        c.weather_forecast_payload(5, 200)


def test_sunrise_sunset_payload():
    p = c.sunrise_sunset_payload(6, 30, 20, 15, condition=5, temp=18, location="NYC")
    assert p == bytes([0x00, 5, 18, 0x00, 0x00, 6, 30, 20, 15]) + b"NYC"


def test_dfu_payload_values():
    assert c.dfu_payload(True) == b"\x01"
    assert c.dfu_payload(False) == b"\x00"


def test_new_builders_roundtrip_through_framing():
    # a settings/health payload must survive the FE EA envelope + reassembly unchanged
    pkt = c.build_packet(c.CMD_SET_WEATHER_FUTURE, c.weather_forecast_payload(5, 20))
    assert c.parse_packet(pkt) == (c.CMD_SET_WEATHER_FUTURE, c.weather_forecast_payload(5, 20))
