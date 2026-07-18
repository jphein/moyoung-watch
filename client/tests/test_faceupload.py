"""Byte-exact tests for the MoYoung face-upload protocol (offline, no BLE)."""
import asyncio

import pytest

from moyoung_client import faceupload as fu


# ------------------------------------------------------------------ frame builders
def test_face_start_appends_u32_big_endian_length():
    assert fu.face_start(9) == bytes.fromhex("feea200974") + b"\x00\x00\x00\x09"
    assert fu.face_start(0x1234) == bytes.fromhex("feea200974") + b"\x00\x00\x12\x34"


def test_bg_start_appends_u32_big_endian_length():
    assert fu.bg_start(600) == bytes.fromhex("feea20096e") + (600).to_bytes(4, "big")


def test_set_face_appends_slot_byte():
    assert fu.set_face(6) == bytes.fromhex("feea20061906")
    assert fu.set_face(1) == bytes.fromhex("feea20061901")


@pytest.mark.parametrize("bad", [0, 7, -1, 256])
def test_set_face_rejects_out_of_range_slot(bad):
    with pytest.raises(ValueError):
        fu.set_face(bad)


def test_known_ack_constants():
    assert fu.FACE_ACK == bytes.fromhex("feea200974ff")
    assert fu.BG_ACK == bytes.fromhex("feea20096eff")


def test_chunk_splits_into_512_byte_pieces_with_short_tail():
    data = bytes(range(256)) * 5  # 1280 bytes -> 512 + 512 + 256
    chunks = fu.chunk(data)
    assert [len(c) for c in chunks] == [512, 512, 256]
    assert b"".join(chunks) == data


def test_chunk_empty_input():
    assert fu.chunk(b"") == []


# ------------------------------------------------------------------ full sequence
class FakeUploader:
    """Records the exact ordered control/data/ack traffic."""

    def __init__(self, ack_result=True):
        self.log = []
        self.ack_result = ack_result

    async def write_ctrl(self, data):
        self.log.append(("ctrl", data))

    async def write_data(self, data):
        self.log.append(("data", data))

    async def wait_ack(self, ack, timeout):
        self.log.append(("ack", ack))
        return self.ack_result


def test_face_upload_emits_exact_sequence():
    data = bytes(600)  # -> 2 chunks: 512 + 88
    up = FakeUploader(ack_result=True)
    acked = asyncio.run(
        fu.upload("face", up, data, settle=0, chunk_delay=0, on_progress=None))

    assert acked is True
    expected = [
        ("ctrl", fu.face_start(600)),
        ("data", data[:512]),
        ("data", data[512:]),
        ("ack", fu.FACE_ACK),
        ("ctrl", fu._FACE_FINISH),
        ("ctrl", fu._FACE_SET_XFER),
        ("ctrl", fu.set_face(fu.FACE_SLOT)),
    ]
    assert up.log == expected


def test_bg_upload_uses_bg_opcodes_and_slot():
    data = bytes(100)  # 1 chunk
    up = FakeUploader(ack_result=True)
    asyncio.run(fu.upload("bg", up, data, settle=0, chunk_delay=0))

    assert up.log == [
        ("ctrl", fu.bg_start(100)),
        ("data", data),
        ("ack", fu.BG_ACK),
        ("ctrl", fu._BG_FINISH),
        ("ctrl", fu._BG_SET_XFER),
        ("ctrl", fu.set_face(fu.BG_SLOT)),
    ]


def test_upload_still_finishes_when_ack_times_out():
    up = FakeUploader(ack_result=False)
    acked = asyncio.run(fu.upload("face", up, bytes(10), settle=0, chunk_delay=0))
    assert acked is False
    # finish + activate are still sent so a slow/silent watch still switches faces
    assert up.log[-1] == ("ctrl", fu.set_face(fu.FACE_SLOT))


def test_custom_slot_override():
    up = FakeUploader()
    asyncio.run(fu.upload("face", up, bytes(10), slot=3, settle=0, chunk_delay=0))
    assert up.log[-1] == ("ctrl", fu.set_face(3))


def test_progress_callback_reports_each_chunk():
    seen = []
    up = FakeUploader()
    asyncio.run(fu.upload("face", up, bytes(1100), settle=0, chunk_delay=0,
                          on_progress=lambda d, t: seen.append((d, t))))
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_upload_rejects_unknown_kind():
    up = FakeUploader()
    with pytest.raises(ValueError):
        asyncio.run(fu.upload("firmware", up, bytes(10), settle=0, chunk_delay=0))
