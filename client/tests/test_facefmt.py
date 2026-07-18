"""Tests for the MoYoung face format field table + header parser (offline)."""
import struct

import pytest

from moyoung_client import facefmt


# ------------------------------------------------------------------ field table
def test_field_table_has_no_duplicate_codes():
    codes = [c for (c, *_rest) in facefmt._FIELDS]
    assert len(codes) == len(set(codes))
    assert len(facefmt.FIELDS) == len(facefmt._FIELDS)


def test_every_field_group_is_in_group_order():
    for ft in facefmt.FIELDS.values():
        assert ft.group in facefmt.GROUP_ORDER


def test_well_known_fields_present():
    assert facefmt.FIELDS[0x01].name == "BACKGROUND"
    assert facefmt.FIELDS[0x40].name == "TIME_H1"
    assert facefmt.FIELDS[0x65].name == "HR"
    assert facefmt.FIELDS[0xD7].name == "WEATHER_TEMP"
    assert facefmt.FIELDS[0xF1].name == "HAND_HOUR"


def test_fields_by_group_sorted_and_complete():
    grouped = facefmt.fields_by_group()
    total = sum(len(v) for v in grouped.values())
    assert total == len(facefmt.FIELDS)
    steps = [f.code for f in grouped["Steps"]]
    assert steps == sorted(steps)


# ------------------------------------------------------------------ header parser
def _build_type_c_face():
    """A minimal Type C (fileID 0x81) header: BACKGROUND + TIME_H1, 240x280."""
    buf = bytearray()
    buf += bytes([0x81, 2, 5])                 # fileID, dataCount, blobCount
    buf += struct.pack("<H", 50002)            # faceNumber
    # entry 0: BACKGROUND, blob 0, full screen
    buf += bytes([0x01, 0x00]) + struct.pack("<HHHH", 0, 0, 240, 280)
    # entry 1: TIME_H1, blob 1
    buf += bytes([0x40, 0x01]) + struct.pack("<HHHH", 31, 105, 40, 70)
    buf += bytes(64)                           # trailing padding/offset table (ignored)
    return bytes(buf)


def test_parse_type_c_header():
    face = facefmt.parse_header(_build_type_c_face())
    assert face.file_id == 0x81
    assert face.file_type == "B/C"
    assert face.data_count == 2
    assert face.blob_count == 5
    assert face.face_number == 50002
    assert face.field_names() == ["BACKGROUND", "TIME_H1"]

    bg, t = face.entries
    assert (bg.code, bg.oidx, bg.x, bg.y, bg.w, bg.h) == (0x01, 0, 0, 0, 240, 280)
    assert (t.code, t.oidx, t.x, t.y, t.w, t.h) == (0x40, 1, 31, 105, 40, 70)


def test_parse_type_a_header():
    buf = bytearray()
    buf += bytes([0x04, 1, 1])                 # fileID Type A, 1 field, 1 blob
    buf += struct.pack("<H", 12345)
    # Type A entry: [type, x, y, w, h, oidx] as u8s
    buf += bytes([0x00, 0, 0, 240, 24, 0])     # BACKGROUNDS
    buf += bytes(32)
    face = facefmt.parse_header(bytes(buf))
    assert face.file_type == "A"
    assert face.entries[0].code == 0x00
    assert face.entries[0].field.name == "BACKGROUNDS"


def test_parse_rejects_unknown_file_id():
    with pytest.raises(ValueError):
        facefmt.parse_header(bytes([0x99, 1, 1, 0, 0]) + bytes(40))


def test_parse_rejects_tiny_buffer():
    with pytest.raises(ValueError):
        facefmt.parse_header(b"\x81\x02")
