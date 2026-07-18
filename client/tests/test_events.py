"""Tests for incoming watch->phone event decoding + interaction builders (offline)."""
from moyoung_client import commands as c
from moyoung_client import events as e


# ------------------------------------------------------------------ outgoing interaction
def test_interaction_builders():
    assert e.camera_open_payload() == b""
    assert e.return_home_payload() == b""
    assert e.find_phone_stop_payload() == b"\xff"
    assert e.send_volume_payload(8) == bytes([c.ARG_OPERATION_SEND_CURRENT_VOLUME, 8])
    assert e.send_volume_payload(99) == bytes([c.ARG_OPERATION_SEND_CURRENT_VOLUME, 16])  # clamp
    assert e.send_volume_payload(-5) == bytes([c.ARG_OPERATION_SEND_CURRENT_VOLUME, 0])
    assert e.phone_operation_payload(2) == bytes([2])
    assert e.phone_operation_payload(12, 8) == bytes([12, 8])


# ------------------------------------------------------------------ stateless decode
def test_decode_camera_and_return():
    assert e.decode_event(c.CMD_SWITCH_CAMERA_VIEW, b"").kind == "camera_shutter"
    assert e.decode_event(c.CMD_RETURN_PRINCIPAL_SCREEN, b"").kind == "return_home"


def test_decode_find_phone():
    assert e.decode_event(c.CMD_FIND_MY_PHONE, b"\x00").data["action"] == "start"
    assert e.decode_event(c.CMD_FIND_MY_PHONE, b"\x01").data["action"] == "stop"


def test_decode_phone_operation_names():
    ev = e.decode_event(c.CMD_NOTIFY_PHONE_OPERATION, bytes([c.ARG_OPERATION_NEXT_SONG]))
    assert ev.kind == "phone_operation" and ev.data == {"operation": 2, "name": "next"}
    ev2 = e.decode_event(c.CMD_NOTIFY_PHONE_OPERATION, bytes([c.ARG_OPERATION_DROP_INCOMING_CALL]))
    assert ev2.data["name"] == "reject_call"


def test_decode_weather_and_measurements():
    assert e.decode_event(c.CMD_NOTIFY_WEATHER_CHANGE, b"").kind == "weather_request"
    assert e.decode_event(c.CMD_TRIGGER_MEASURE_HEARTRATE, bytes([75])).data == {"bpm": 75}
    assert e.decode_event(c.CMD_TRIGGER_MEASURE_BLOOD_OXYGEN, bytes([98])).data == {"percent": 98}
    bp = e.decode_event(c.CMD_TRIGGER_MEASURE_BLOOD_PRESSURE, bytes([0, 120, 80]))
    assert bp.kind == "blood_pressure" and bp.data["systolic"] == 120
    tr = e.decode_event(c.CMD_START_STOP_MEASURE_DYNAMIC_RATE, bytes([1]))
    assert tr.kind == "training" and tr.data["workout_name"] == "run"
    ldr = e.decode_event(c.CMD_QUERY_LAST_DYNAMIC_RATE, bytes([0, 1, 2]))
    assert ldr.kind == "last_dynamic_rate" and ldr.data["sequence"] == 0


def test_decode_unknown_is_none():
    assert e.decode_event(0x99, b"\x01") is None


# ------------------------------------------------------------------ stateful decoder
def test_camera_open_shutter_close_cycle():
    d = e.EventDecoder()
    assert d.feed(c.CMD_SWITCH_CAMERA_VIEW, b"").kind == "camera_open"
    assert d.feed(c.CMD_SWITCH_CAMERA_VIEW, b"").kind == "camera_shutter"
    assert d.feed(c.CMD_SWITCH_CAMERA_VIEW, b"").kind == "camera_shutter"
    assert d.feed(c.CMD_RETURN_PRINCIPAL_SCREEN, b"").kind == "camera_close"
    # after close, 83 is just a plain return-home
    assert d.feed(c.CMD_RETURN_PRINCIPAL_SCREEN, b"").kind == "return_home"


def test_find_phone_toggle():
    d = e.EventDecoder()
    assert d.feed(c.CMD_FIND_MY_PHONE, b"").data["action"] == "start"
    assert d.feed(c.CMD_FIND_MY_PHONE, b"").data["action"] == "stop"
    assert d.feed(c.CMD_FIND_MY_PHONE, b"").data["action"] == "start"


def test_stateful_delegates_to_stateless():
    d = e.EventDecoder()
    assert d.feed(c.CMD_TRIGGER_MEASURE_HEARTRATE, bytes([80])).data == {"bpm": 80}


def test_event_dataclass_shape():
    ev = e.decode_event(c.CMD_NOTIFY_WEATHER_CHANGE, b"")
    assert ev.cmd == c.CMD_NOTIFY_WEATHER_CHANGE and ev.payload == b""
