"""Offline tests for the MoYoung HA integration's watch-face auto-activate logic.

Pure protocol/derivation only (no BLE, no Home Assistant) — imports the vendored proto package
directly so it runs without HA installed.
"""
import pathlib
import sys

_MOYOUNG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "moyoung"
sys.path.insert(0, str(_MOYOUNG))

from proto import commands as c   # noqa: E402
from proto import faceupload as fu  # noqa: E402


# ------------------------------------------------------------------ new builders / decoders
def test_watch_face_builder_matches_legacy_set_face():
    # cmd 25 with an index must frame identically to the legacy set_face(slot) bytes
    assert c.watch_face_payload(3) == b"\x03"
    assert (c.build_packet(c.CMD_SET_DISPLAY_WATCH_FACE, c.watch_face_payload(6))
            == fu.set_face(6) == bytes.fromhex("feea200619") + b"\x06")


def test_decode_watch_face_and_count():
    assert c.decode_watch_face(bytes([2])) == 2
    assert c.decode_watch_face(b"") == -1
    assert c.decode_support_watch_face(bytes([0, 7])) == 7
    assert c.decode_support_watch_face(bytes([1, 0])) == 256
    assert c.decode_support_watch_face(b"\x00") == -1


# ------------------------------------------------------------------ index derivation
def test_derive_appended_is_last_index():
    # upload grew the list by one -> custom face appended -> last (0-based)
    assert fu.derive_face_index(7, 8, 3, 6) == 7


def test_derive_explicit_override_wins():
    assert fu.derive_face_index(7, 8, 3, 6, face_index=2) == 2


def test_derive_replaced_falls_back_to_current_after_upload():
    # count unchanged (replaced a slot) -> the watch may have auto-activated -> current_after
    assert fu.derive_face_index(8, 8, 5, 6) == 5


def test_derive_count_only_uses_last():
    assert fu.derive_face_index(None, 9, None, 6) == 8


def test_derive_no_signal_falls_back_to_slot():
    assert fu.derive_face_index(None, None, None, 6) == 6


# ------------------------------------------------------------------ activate gating
async def _run(coro):
    return await coro


def test_upload_activate_flag_gates_set_face():
    """upload(activate=False) must NOT send the legacy set_face; activate=True must."""
    import asyncio

    class FakeUploader:
        def __init__(self):
            self.ctrl = []
        async def write_ctrl(self, data):
            self.ctrl.append(data)
        async def write_data(self, data):
            pass
        async def wait_ack(self, ack, timeout):
            return True

    set_face6 = fu.set_face(6)
    off = FakeUploader()
    asyncio.run(fu.upload("face", off, b"x" * 10, slot=6, chunk_size=8,
                          settle=0, chunk_delay=0, ack_timeout=0, activate=False))
    assert set_face6 not in off.ctrl

    on = FakeUploader()
    asyncio.run(fu.upload("face", on, b"x" * 10, slot=6, chunk_size=8,
                          settle=0, chunk_delay=0, ack_timeout=0, activate=True))
    assert on.ctrl[-1] == set_face6
