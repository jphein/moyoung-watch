"""Constants for the MoYoung Watch integration."""
from __future__ import annotations

DOMAIN = "moyoung"

# Identity
MANUFACTURER_NAME = "MOYOUNG-V2"
DEFAULT_NAME = "MoYoung Watch"

# MoYoung command/data service (0xFEEA) + the characteristics we use.
FEEA_SERVICE = "0000feea-0000-1000-8000-00805f9b34fb"
# Advertised local_name(s) for units that DO NOT advertise FEEA_SERVICE (e.g. TG38 / MOY-ERJ3).
# Used for BLE auto-discovery (manifest) + manual-pick filtering so they don't need a manual MAC.
MOYOUNG_LOCAL_NAMES = ("TG38",)
STEPS_CHAR = "0000fee1-0000-1000-8000-00805f9b34fb"     # read/notify: pedometer
CTRL_CHAR = "0000fee2-0000-1000-8000-00805f9b34fb"      # write-no-resp: commands / face control
DATA_CHAR = "0000fee6-0000-1000-8000-00805f9b34fb"      # write-no-resp: image chunks
NOTIFY_CHAR = "0000fee3-0000-1000-8000-00805f9b34fb"    # notify: acks / responses
MANUFACTURER_CHAR = "00002a29-0000-1000-8000-00805f9b34fb"
BATTERY_CHAR = "00002a19-0000-1000-8000-00805f9b34fb"

# BlueZ/proxy MTU is often unnegotiated (23); 244 is safe for the MTU-247 these watches use.
SAFE_CHUNK = 244

# Coordinator tick: location (which proxy hears the watch) is recomputed every tick from the
# Bluetooth advert cache (cheap, no connection). Battery/steps need a BLE connection, so they
# are polled only every Nth tick to spare the watch battery / proxy connection slot.
UPDATE_INTERVAL_SECONDS = 60
BATTERY_POLL_EVERY = 10  # -> battery/steps ~every 10 min at a 60s tick

# Map a proxy/scanner name to a friendly room. Falls back to a heuristic on the name.
PROXY_ROOM_OVERRIDES = {
    "lunas-room-ble-proxy": "Luna's Room",
}

# Services
SERVICE_SET_TIME = "set_time"
SERVICE_WEATHER = "weather"
SERVICE_NOTIFY = "notify"
SERVICE_MUSIC = "music"
SERVICE_FIND = "find"
SERVICE_SET_GOAL = "set_goal"
SERVICE_UPLOAD_FACE = "upload_face"
SERVICE_SET_WATCH_FACE = "set_watch_face"
SERVICE_GET_WATCH_FACES = "get_watch_faces"
SERVICE_CAMERA = "camera"

# Fired after upload_face with the activation diagnostics (count/current/target/verified).
EVENT_FACE_ACTIVATED = "moyoung_face_activated"

# Camera-remote / shutter button. The watch's camera-remote screen speaks the SAME opcode in
# both directions — Gadgetbridge's ``MoyoungConstants.CMD_SWITCH_CAMERA_VIEW = 102`` (0x66):
# OUTBOUND (phone->watch) opens the watch's camera screen; INBOUND (watch->phone) is emitted on
# every interaction with that screen (the open + each shutter tap). We surface the INBOUND opcode
# as an HA event so automations can react — e.g. toggle the lights in the watch's current room.
# This is the confirmed wire opcode (same clean-room source the rest of proto/commands.py is
# built from), NOT the starmax-client's --force-gated 0x1d camera placeholder (a DIFFERENT watch;
# see docs/command-audit.md). Fired with {address, room, proxy}.
EVENT_CAMERA_SHUTTER = "moyoung_camera_shutter"

# De-bounce inbound shutter notifications: ignore repeats within this many seconds. Collapses a
# single physical press that may emit a short burst, and the open+first-tap pair. Tunable; the
# real burst cadence still wants an on-watch capture (see the coordinator + findings).
CAMERA_SHUTTER_DEBOUNCE_S = 1.2
