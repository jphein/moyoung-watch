"""Offline tests for the MoYoung camera-shutter -> room-light-toggle feature.

Two halves, both dependency-light:
  * proto opcode round-trip (pure stdlib — imports the vendored ``proto`` package directly), and
  * structural checks on the ``moyoung_camera_lights.yaml`` HA package (skipped without PyYAML).

The wire opcode is Gadgetbridge's ``MoyoungConstants.CMD_SWITCH_CAMERA_VIEW = 102`` (0x66) — the
same clean-room source the rest of proto/commands.py is built from. The watch emits it INBOUND on
every camera-remote interaction (open + each shutter tap); the coordinator turns that into the
``moyoung_camera_shutter`` HA event this package's automation triggers on.
"""
import os
import pathlib
import sys

import pytest

_MOYOUNG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "moyoung"
sys.path.insert(0, str(_MOYOUNG))

from proto import commands as c  # noqa: E402


# ------------------------------------------------------------------ proto opcode
def test_camera_opcode_value():
    # Confirmed wire opcode (NOT the starmax-client 0x1d placeholder for a different watch).
    assert c.CMD_SWITCH_CAMERA_VIEW == 102 == 0x66


def test_camera_view_frame_bytes():
    # Empty-payload command -> v2 framing: FE EA | b2 | len | cmd. total = 0 + 5 = 5.
    assert c.build_packet(c.CMD_SWITCH_CAMERA_VIEW, b"") == bytes.fromhex("feea200566")


def test_camera_notification_reassembles():
    """The coordinator's standalone reassembler must recover (102, b'') from the inbound frame,
    whole or fragmented, and must NOT confuse it with a neighbouring opcode."""
    frame = c.build_packet(c.CMD_SWITCH_CAMERA_VIEW, b"")

    whole = c.PacketReassembler()
    assert whole.feed(frame) == [(c.CMD_SWITCH_CAMERA_VIEW, b"")]

    frag = c.PacketReassembler()
    assert frag.feed(frame[:2]) == []          # header split across notifications
    assert frag.feed(frame[2:]) == [(102, b"")]

    # A find-my-watch frame (0x61) interleaved before the camera frame is not mis-decoded.
    mixed = c.PacketReassembler()
    out = mixed.feed(c.build_packet(c.CMD_FIND_MY_WATCH, b"") + frame)
    assert (c.CMD_SWITCH_CAMERA_VIEW, b"") in out
    assert (c.CMD_FIND_MY_WATCH, b"") in out


# ------------------------------------------------------------------ HA package structure
yaml = pytest.importorskip("yaml")

_PKG = os.path.join(os.path.dirname(__file__), "..", "packages", "moyoung_camera_lights.yaml")
_FOLLOW_ROOMS = ("laundry", "kitchen", "bedroom", "luna_s_room", "shed")


@pytest.fixture(scope="module")
def pkg():
    with open(_PKG) as fh:
        return yaml.safe_load(fh)


def test_master_and_per_room_toggles(pkg):
    booleans = pkg["input_boolean"]
    assert "watch_camera_lights" in booleans, "missing master toggle"
    for room in _FOLLOW_ROOMS:
        assert f"watch_camera_{room}" in booleans, f"missing per-room toggle for {room}"


def test_automation_triggers_on_shutter_event(pkg):
    auto = next(a for a in pkg["automation"] if a["id"] == "watch_camera_shutter_toggle")
    trigs = auto["triggers"]
    assert any(t.get("trigger") == "event" and t.get("event_type") == "moyoung_camera_shutter"
               for t in trigs), "automation must trigger on the moyoung_camera_shutter event"


def test_automation_is_master_gated(pkg):
    auto = next(a for a in pkg["automation"] if a["id"] == "watch_camera_shutter_toggle")
    conds = auto["conditions"]
    assert any(cnd.get("entity_id") == "input_boolean.watch_camera_lights"
               and cnd.get("state") == "on" for cnd in conds), "must gate on the master toggle"


def test_automation_toggles_via_ring_script(pkg):
    """Reuses script.ring_set_room_lights (the shared button/follow-me light set) and flips it:
    any target light on -> off, else on."""
    blob = str(pkg["automation"])
    assert "script.ring_set_room_lights" in blob
    assert "label_entities('button_lights')" in blob  # same target set as the ring script
    assert "'off' if room_on else 'on'" in blob        # the actual toggle expression
