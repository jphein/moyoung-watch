"""BLE transport for MoYoung-v2 / Da Fit watches, built on bleak.

Service ``0xFEEA`` carries the MoYoung command/data protocol. This module handles scanning,
connecting, identity verification, and the three characteristics the face-upload protocol
needs; :mod:`moyoung_client.faceupload` drives the actual transfer sequence through the
:class:`MoyoungClient` (which satisfies the ``faceupload.Uploader`` protocol).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

from . import commands, events, faceupload, firmware, health, settings

log = logging.getLogger("moyoung_client.transport")

# MoYoung command/data service and its characteristics (verified on MOY-ERJ3-2.0.7).
FEEA_SERVICE = "0000feea-0000-1000-8000-00805f9b34fb"
STEPS_CHAR = "0000fee1-0000-1000-8000-00805f9b34fb"   # read/notify: live pedometer
CTRL_CHAR = "0000fee2-0000-1000-8000-00805f9b34fb"    # write-no-resp: commands / face control
DATA_CHAR = "0000fee6-0000-1000-8000-00805f9b34fb"    # write-no-resp: image chunks
NOTIFY_CHAR = "0000fee3-0000-1000-8000-00805f9b34fb"  # notify: device acks / responses
BATTERY_CHAR = "00002a19-0000-1000-8000-00805f9b34fb"  # standard Battery Level

# Standard Device Information Service strings we surface in `info`.
DIS = {
    "00002a29-0000-1000-8000-00805f9b34fb": "manufacturer",
    "00002a24-0000-1000-8000-00805f9b34fb": "model",
    "00002a25-0000-1000-8000-00805f9b34fb": "serial",
    "00002a26-0000-1000-8000-00805f9b34fb": "firmware_rev",
    "00002a27-0000-1000-8000-00805f9b34fb": "hardware_rev",
    "00002a28-0000-1000-8000-00805f9b34fb": "software_rev",
}
MANUFACTURER_CHAR = "00002a29-0000-1000-8000-00805f9b34fb"
MOYOUNG_MANUFACTURER = "MOYOUNG-V2"

# BLE write size. These watches negotiate ATT MTU 247 (payload 244), but BlueZ frequently
# reports the default MTU of 23 and rejects larger writes with "Failed to initiate write".
# 244 is safe for the near-universal MTU-247 case; we go higher only if we can confirm the MTU.
SAFE_CHUNK = 244


async def scan(timeout: float = 8.0, feea_only: bool = True
               ) -> List[Tuple[str, str, Optional[int]]]:
    """Scan for advertising watches. Returns [(name, address, rssi), ...].

    MoYoung watches advertise the ``0xFEEA`` service UUID, so ``feea_only`` (default) filters
    to those without needing to connect. Pass ``feea_only=False`` to list every device.
    """
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    results: List[Tuple[str, str, Optional[int]]] = []
    for address, (device, adv) in found.items():
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if feea_only and not any("feea" in u for u in uuids):
            continue
        name = adv.local_name or device.name or ""
        results.append((name, address, getattr(adv, "rssi", None)))
    results.sort(key=lambda r: (r[2] is None, -(r[2] or 0)))
    return results


class MoyoungClient:
    """Async BLE session with a MoYoung-v2 watch.

    Usage::

        async with MoyoungClient(address) as w:
            await w.verify_moyoung()
            await w.upload_face(open("face.bin", "rb").read())
    """

    def __init__(self, address: str, *, connect_timeout: float = 20.0) -> None:
        self.address = address
        self.connect_timeout = connect_timeout
        self._client = None
        self._notifies: "asyncio.Queue[bytes]" = asyncio.Queue()
        self._last_notify = b""
        self._notifying = False

    async def __aenter__(self) -> "MoyoungClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()

    # ---------------------------------------------------------------- lifecycle
    async def connect(self) -> None:
        from bleak import BleakClient

        self._client = BleakClient(self.address, timeout=self.connect_timeout)
        await self._client.connect()
        log.debug("connected to %s", self.address)

    async def disconnect(self) -> None:
        if self._client is not None and self._client.is_connected:
            try:
                if self._notifying:
                    await self._client.stop_notify(NOTIFY_CHAR)
            except Exception:  # noqa: BLE001 — best-effort teardown
                pass
            await self._client.disconnect()

    # ---------------------------------------------------------------- identity / info
    async def read_manufacturer(self) -> str:
        raw = await self._client.read_gatt_char(MANUFACTURER_CHAR)
        return raw.decode("utf-8", "replace").strip("\x00").strip()

    async def verify_moyoung(self) -> str:
        """Raise unless the watch identifies as MOYOUNG-V2. Returns the manufacturer string."""
        man = await self.read_manufacturer()
        if man != MOYOUNG_MANUFACTURER:
            raise RuntimeError(
                f"not a MOYOUNG-V2 device (manufacturer characteristic = {man!r}); "
                "this tool only speaks the MoYoung-v2 protocol")
        return man

    async def read_info(self) -> Dict[str, object]:
        """Read Device Information strings and list GATT service UUIDs."""
        info: Dict[str, object] = {}
        for uuid, key in DIS.items():
            try:
                raw = await self._client.read_gatt_char(uuid)
                info[key] = raw.decode("utf-8", "replace").strip("\x00").strip()
            except Exception:  # noqa: BLE001 — characteristic may be absent
                info[key] = None
        info["services"] = [s.uuid for s in self._client.services]
        return info

    # ---------------------------------------------------------------- notify plumbing
    def _on_notify(self, _char, data: bytearray) -> None:
        b = bytes(data)
        self._last_notify = b
        self._notifies.put_nowait(b)

    async def _ensure_notify(self) -> None:
        if not self._notifying:
            await self._client.start_notify(NOTIFY_CHAR, self._on_notify)
            self._notifying = True

    async def safe_chunk_size(self) -> int:
        """Largest BLE write we can trust. Tries to learn the real MTU; falls back to 244."""
        size = SAFE_CHUNK
        try:
            acquire = getattr(self._client, "_acquire_mtu", None)
            if acquire is not None:
                await acquire()
            mtu = int(getattr(self._client, "mtu_size", 0) or 0)
            if mtu > 23:
                size = max(20, min(mtu - 3, 512))
        except Exception:  # noqa: BLE001 — MTU discovery is best-effort
            pass
        return size

    # ---------------------------------------------------------------- Uploader protocol
    async def write_ctrl(self, data: bytes) -> None:
        await self._client.write_gatt_char(CTRL_CHAR, data, response=False)

    async def write_data(self, data: bytes) -> None:
        await self._client.write_gatt_char(DATA_CHAR, data, response=False)

    async def wait_ack(self, ack: bytes, timeout: float) -> bool:
        """Wait until a notification starting with ``ack`` arrives (or timeout)."""
        if self._last_notify.startswith(ack):
            return True
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            try:
                data = await asyncio.wait_for(self._notifies.get(), remaining)
            except asyncio.TimeoutError:
                return False
            if data.startswith(ack):
                return True

    # ---------------------------------------------------------------- high-level ops
    async def upload_face(self, data: bytes, *, slot: int = faceupload.FACE_SLOT, **kw) -> bool:
        await self._ensure_notify()
        kw.setdefault("chunk_size", await self.safe_chunk_size())
        return await faceupload.upload("face", self, data, slot=slot, **kw)

    async def upload_bg(self, data: bytes, *, slot: int = faceupload.BG_SLOT, **kw) -> bool:
        await self._ensure_notify()
        kw.setdefault("chunk_size", await self.safe_chunk_size())
        return await faceupload.upload("bg", self, data, slot=slot, **kw)

    # ---------------------------------------------------------------- control / sync
    async def send_command(self, cmd_type: int, payload: bytes = b"") -> None:
        """Frame a MoYoung command and write it to the control channel (fragmenting if needed)."""
        await self._ensure_notify()
        packet = commands.build_packet(cmd_type, payload)
        size = await self.safe_chunk_size()
        for frag in faceupload.chunk(packet, size):
            await self.write_ctrl(frag)

    async def command_with_response(self, cmd_type: int, payload: bytes = b"",
                                    timeout: float = 12.0) -> Optional[bytes]:
        """Send a command and return the payload of the first matching response, or None."""
        await self._ensure_notify()
        while not self._notifies.empty():          # drop stale notifications
            self._notifies.get_nowait()
        reasm = commands.PacketReassembler()
        await self.send_command(cmd_type, payload)
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                frag = await asyncio.wait_for(self._notifies.get(), remaining)
            except asyncio.TimeoutError:
                return None
            for ct, pl in reasm.feed(frag):
                if ct == cmd_type:
                    return pl

    # -- injection / control (write) --
    async def set_time(self, dt=None) -> None:
        await self.send_command(commands.CMD_SYNC_TIME, commands.time_payload(dt))

    async def notify(self, text: str, ntype: int = commands.NOTIFY_OTHER) -> None:
        await self.send_command(commands.CMD_SEND_MESSAGE, commands.notify_payload(text, ntype))

    async def set_weather(self, temp: int, condition="sunny", city: str = "") -> None:
        cid = commands.WEATHER_CONDITIONS.get(condition, condition) if isinstance(condition, str) \
            else condition
        await self.send_command(commands.CMD_SET_WEATHER_TODAY,
                                commands.weather_today_payload(temp, int(cid), city))

    async def set_music(self, track: Optional[str] = None, artist: Optional[str] = None) -> None:
        if artist is not None:
            await self.send_command(commands.CMD_SET_MUSIC_INFO, commands.music_payload(artist, True))
        if track is not None:
            await self.send_command(commands.CMD_SET_MUSIC_INFO, commands.music_payload(track, False))

    async def find_watch(self) -> None:
        await self.send_command(commands.CMD_FIND_MY_WATCH, b"")

    async def set_goal_steps(self, steps: int) -> None:
        await self.send_command(commands.CMD_SET_GOAL_STEP, commands.goal_steps_payload(steps))

    # -- sync (read) --
    async def measure_hr(self, timeout: float = 30.0) -> Optional[int]:
        resp = await self.command_with_response(
            commands.CMD_TRIGGER_MEASURE_HEARTRATE, commands.hr_trigger_payload(True), timeout)
        try:
            await self.send_command(commands.CMD_TRIGGER_MEASURE_HEARTRATE,
                                    commands.hr_trigger_payload(False))
        except Exception:  # noqa: BLE001
            pass
        return resp[0] if resp else None

    async def read_steps(self) -> dict:
        """Read the live pedometer characteristic: distance / steps / calories (uint24 LE)."""
        raw = await self._client.read_gatt_char(STEPS_CHAR)
        if len(raw) >= 9:
            return {
                "distance": int.from_bytes(raw[0:3], "little"),
                "steps": int.from_bytes(raw[3:6], "little"),
                "calories": int.from_bytes(raw[6:9], "little"),
            }
        return {"raw": raw.hex()}

    async def read_battery(self) -> Optional[int]:
        raw = await self._client.read_gatt_char(BATTERY_CHAR)
        return raw[0] if raw else None

    # ================================================================ full GB command surface
    # Thin wrappers: each builds a payload from commands/settings/health/events and sends it
    # (write-only) or queries + decodes (read). See those modules for the byte layouts.

    async def query(self, query_cmd: int, payload: bytes = b"",
                    timeout: float = 12.0) -> Optional[bytes]:
        """Send a CMD_QUERY_* and return the matching response payload (or None on timeout)."""
        return await self.command_with_response(query_cmd, payload, timeout)

    # -- device settings: SET (write-only) --
    async def set_time_system(self, value="24") -> None:
        await self.send_command(commands.CMD_SET_TIME_SYSTEM, settings.time_system_payload(value))

    async def set_units(self, value="metric") -> None:
        await self.send_command(commands.CMD_SET_METRIC_SYSTEM, settings.metric_system_payload(value))

    async def set_language(self, value="english") -> None:
        await self.send_command(commands.CMD_SET_DEVICE_LANGUAGE, settings.language_payload(value))

    async def set_device_version(self, value="international") -> None:
        await self.send_command(commands.CMD_SET_DEVICE_VERSION, settings.device_version_payload(value))

    async def set_dominant_hand(self, value="left") -> None:
        await self.send_command(commands.CMD_SET_DOMINANT_HAND, settings.dominant_hand_payload(value))

    async def set_quick_view(self, enabled: bool) -> None:
        await self.send_command(commands.CMD_SET_QUICK_VIEW, settings.quick_view_payload(enabled))

    async def set_quick_view_time(self, sh, sm, eh, em) -> None:
        await self.send_command(commands.CMD_SET_QUICK_VIEW_TIME,
                                settings.quick_view_time_payload(sh, sm, eh, em))

    async def set_sedentary(self, enabled: bool) -> None:
        await self.send_command(commands.CMD_SET_SEDENTARY_REMINDER, settings.sedentary_payload(enabled))

    async def set_reminders_to_move(self, period, steps, start_h, end_h) -> None:
        await self.send_command(commands.CMD_SET_REMINDERS_TO_MOVE_PERIOD,
                                settings.reminders_to_move_payload(period, steps, start_h, end_h))

    async def set_dnd(self, sh, sm, eh, em) -> None:
        await self.send_command(commands.CMD_SET_DO_NOT_DISTURB_TIME,
                                settings.dnd_time_payload(sh, sm, eh, em))

    async def set_other_message(self, enabled: bool) -> None:
        await self.send_command(commands.CMD_SET_OTHER_MESSAGE_STATE,
                                settings.other_message_payload(enabled))

    async def set_breathing_light(self, enabled: bool) -> None:
        await self.send_command(commands.CMD_SET_BREATHING_LIGHT, settings.breathing_light_payload(enabled))

    async def set_power_saving(self, enabled: bool) -> None:
        await self.send_command(commands.CMD_SET_POWER_SAVING, settings.power_saving_payload(enabled))

    async def set_watch_face(self, index: int) -> None:
        await self.send_command(commands.CMD_SET_DISPLAY_WATCH_FACE, settings.watch_face_payload(index))

    async def set_watch_face_layout(self, time_position, time_top, time_bottom,
                                    text_color, background_md5) -> None:
        await self.send_command(commands.CMD_SET_WATCH_FACE_LAYOUT,
                                settings.watch_face_layout_payload(time_position, time_top,
                                                                   time_bottom, text_color,
                                                                   background_md5))

    async def set_display_functions(self, func_ids) -> None:
        await self.send_command(commands.CMD_SET_DISPLAY_DEVICE_FUNCTION,
                                settings.display_functions_payload(func_ids))

    async def set_user_info(self, height_cm, weight_kg, age, sex="male") -> None:
        await self.send_command(commands.CMD_SET_USER_INFO,
                                settings.user_info_payload(height_cm, weight_kg, age, sex))

    async def set_step_length(self, cm: int) -> None:
        await self.send_command(commands.CMD_SET_STEP_LENGTH, settings.step_length_payload(cm))

    async def set_hr_interval(self, interval) -> None:
        await self.send_command(commands.CMD_SET_TIMING_MEASURE_HEART_RATE,
                                settings.hr_auto_interval_payload(interval))

    async def set_alarm(self, index, hour, minute, *, enabled=True, days=None,
                        year=None, month=1, day=1) -> None:
        await self.send_command(commands.CMD_SET_ALARM_CLOCK,
                                settings.alarm_payload(index, hour, minute, enabled=enabled,
                                                       days=days, year=year, month=month, day=day))

    async def set_psychological_period(self, *args, **kw) -> None:
        await self.send_command(commands.CMD_SET_PSYCHOLOGICAL_PERIOD,
                                settings.psychological_period_payload(*args, **kw))

    async def gsensor_calibrate(self) -> None:
        await self.send_command(commands.CMD_GSENSOR_CALIBRATION, b"")

    async def return_home(self) -> None:
        await self.send_command(commands.CMD_RETURN_PRINCIPAL_SCREEN, events.return_home_payload())

    # -- device settings: QUERY (read + decode) --
    async def get_time_system(self, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_TIME_SYSTEM, timeout=timeout)
        return settings.decode_time_system(r) if r is not None else None

    async def get_units(self, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_METRIC_SYSTEM, timeout=timeout)
        return settings.decode_metric_system(r) if r is not None else None

    async def get_language(self, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_DEVICE_LANGUAGE, timeout=timeout)
        return settings.decode_language(r) if r is not None else None

    async def get_device_version(self, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_DEVICE_VERSION, timeout=timeout)
        return settings.decode_device_version(r) if r is not None else None

    async def get_dominant_hand(self, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_DOMINANT_HAND, timeout=timeout)
        return settings.decode_dominant_hand(r) if r is not None else None

    async def get_quick_view(self, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_QUICK_VIEW, timeout=timeout)
        return settings.decode_bool(r) if r is not None else None

    async def get_quick_view_time(self, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_QUICK_VIEW_TIME, timeout=timeout)
        return settings.decode_time_range(r) if r is not None else None

    async def get_dnd_time(self, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_DO_NOT_DISTURB_TIME, timeout=timeout)
        return settings.decode_time_range(r) if r is not None else None

    async def get_sedentary(self, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_SEDENTARY_REMINDER, timeout=timeout)
        return settings.decode_bool(r) if r is not None else None

    async def get_reminders_to_move(self, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_REMINDERS_TO_MOVE_PERIOD, timeout=timeout)
        return settings.decode_reminders_to_move(r) if r is not None else None

    async def get_other_message(self, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_OTHER_MESSAGE_STATE, timeout=timeout)
        return settings.decode_bool(r) if r is not None else None

    async def get_breathing_light(self, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_BREATHING_LIGHT, timeout=timeout)
        return settings.decode_bool(r) if r is not None else None

    async def get_goal_steps(self, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_GOAL_STEP, timeout=timeout)
        return settings.decode_goal_step(r) if r is not None else None

    async def get_watch_face(self, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_DISPLAY_WATCH_FACE, timeout=timeout)
        return settings.decode_watch_face(r) if r is not None else None

    async def get_watch_face_layout(self, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_WATCH_FACE_LAYOUT, timeout=timeout)
        return settings.decode_watch_face_layout(r) if r is not None else None

    async def get_support_watch_face(self, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_SUPPORT_WATCH_FACE, timeout=timeout)
        return settings.decode_support_watch_face(r) if r is not None else None

    async def get_display_functions(self, list_supported=False, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_DISPLAY_DEVICE_FUNCTION,
                             settings.display_functions_query_payload(list_supported), timeout)
        return settings.decode_display_functions(r) if r is not None else None

    async def get_alarms(self, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_ALARM_CLOCK, timeout=timeout)
        return settings.decode_alarms(r) if r is not None else None

    async def get_device_version_string(self, timeout=12.0):
        # CMD_QUERY_DEVICE_VERSION returns a single-byte enum; alias kept for clarity
        return await self.get_device_version(timeout)

    # -- health: on-demand measurements (trigger + await completion) --
    async def _measure(self, cmd, start_payload, stop_payload, decoder, timeout):
        resp = await self.command_with_response(cmd, start_payload, timeout)
        try:
            await self.send_command(cmd, stop_payload)
        except Exception:  # noqa: BLE001 — best-effort stop
            pass
        return decoder(resp) if resp is not None else None

    async def measure_blood_pressure(self, timeout=30.0):
        return await self._measure(commands.CMD_TRIGGER_MEASURE_BLOOD_PRESSURE,
                                   health.blood_pressure_payload(True),
                                   health.blood_pressure_payload(False),
                                   health.decode_blood_pressure, timeout)

    async def measure_spo2(self, timeout=30.0):
        return await self._measure(commands.CMD_TRIGGER_MEASURE_BLOOD_OXYGEN,
                                   health.blood_oxygen_payload(True),
                                   health.blood_oxygen_payload(False),
                                   health.decode_spo2, timeout)

    async def ecg(self, mode="start") -> None:
        await self.send_command(commands.CMD_ECG, health.ecg_payload(mode))

    async def dynamic_hr(self, start: bool) -> None:
        await self.send_command(commands.CMD_START_STOP_MEASURE_DYNAMIC_RATE,
                                health.dynamic_hr_payload(start))

    # -- health: history sync (query + decode) --
    async def sync_sleep(self, timeout=12.0):
        r = await self.query(commands.CMD_SYNC_SLEEP, health.sync_sleep_payload(), timeout)
        return health.decode_sleep(r) if r is not None else None

    async def sync_past(self, which, timeout=12.0):
        r = await self.query(commands.CMD_SYNC_PAST_SLEEP_AND_STEP,
                             health.sync_past_payload(which), timeout)
        return health.decode_past(r) if r is not None else None

    async def steps_category(self, index=0, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_STEPS_CATEGORY,
                             health.steps_category_payload(index), timeout)
        return health.decode_steps_category(r) if r is not None else None

    async def movement_hr(self, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_MOVEMENT_HEART_RATE,
                             health.movement_hr_payload(), timeout)
        return health.decode_movement_hr(r) if r is not None else None

    async def past_heart_rate(self, index=0, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_PAST_HEART_RATE_1,
                             health.past_heart_rate_payload(index), timeout)
        return health.decode_hr_history(r) if r is not None else None

    async def sleep_action(self, index, timeout=12.0):
        r = await self.query(commands.CMD_QUERY_SLEEP_ACTION,
                             health.sleep_action_payload(index), timeout)
        return health.decode_sleep_action(r) if r is not None else None

    # -- phone / interaction (write) --
    async def set_music_state(self, playing: bool) -> None:
        await self.send_command(commands.CMD_SET_MUSIC_STATE, commands.music_state_payload(playing))

    async def call_off_hook(self) -> None:
        await self.send_command(commands.CMD_SEND_MESSAGE, commands.call_off_hook_payload())

    async def set_weather_location(self, location: str) -> None:
        await self.send_command(commands.CMD_SET_WEATHER_LOCATION,
                                commands.weather_location_payload(location))

    async def set_weather_forecast(self, today_condition, today_temp, forecasts=None) -> None:
        await self.send_command(commands.CMD_SET_WEATHER_FUTURE,
                                commands.weather_forecast_payload(today_condition, today_temp, forecasts))

    async def set_sunrise_sunset(self, sr_h, sr_m, ss_h, ss_m, *, condition=0, temp=0, location="") -> None:
        await self.send_command(commands.CMD_SET_SUNRISE_SUNSET,
                                commands.sunrise_sunset_payload(sr_h, sr_m, ss_h, ss_m,
                                                                condition=condition, temp=temp,
                                                                location=location))

    async def camera_open(self) -> None:
        await self.send_command(commands.CMD_SWITCH_CAMERA_VIEW, events.camera_open_payload())

    async def find_phone_stop(self) -> None:
        await self.send_command(commands.CMD_FIND_MY_PHONE, events.find_phone_stop_payload())

    async def send_volume(self, level: int) -> None:
        await self.send_command(commands.CMD_NOTIFY_PHONE_OPERATION, events.send_volume_payload(level))

    async def shutdown(self) -> None:
        await self.send_command(commands.CMD_SHUTDOWN, commands.shutdown_payload())

    # -- firmware / DFU / OTA (BRICK RISK — see moyoung_client.firmware) --
    @staticmethod
    def _require_brick_ack(ack: bool) -> None:
        if not ack:
            raise RuntimeError(
                "This is a firmware/DFU operation with BRICK RISK; pass "
                "i_understand_brick_risk=True to proceed (and have a recovery path).")

    async def enable_dfu(self, *, i_understand_brick_risk: bool = False) -> None:
        """CMD_HS_DFU {1} — put the watch into its OTA/DFU bootloader (RE'd from the vendor app)."""
        self._require_brick_ack(i_understand_brick_risk)
        await self.send_command(commands.CMD_HS_DFU, firmware.enable_dfu_payload())

    async def query_dfu_address(self, *, i_understand_brick_risk: bool = False, timeout: float = 8.0):
        """CMD_HS_DFU {0} — ask for the DFU address; returns a best-effort decode (or None).

        Gadgetbridge never reads this reply, so the format is unknown — the decode is raw bytes
        plus labelled guesses. Useful precisely for capturing what the watch actually returns.
        """
        self._require_brick_ack(i_understand_brick_risk)
        resp = await self.command_with_response(commands.CMD_HS_DFU,
                                                firmware.query_dfu_address_payload(), timeout)
        return firmware.decode_dfu_address(resp) if resp is not None else None

    async def dfu_status(self):
        """Report DFU capability by inspecting the Model Number String (GB's "packet 19" heuristic)."""
        model = None
        try:
            raw = await self._client.read_gatt_char("00002a24-0000-1000-8000-00805f9b34fb")
            model = raw.decode("utf-8", "replace").strip("\x00").strip()
        except Exception:  # noqa: BLE001 — characteristic may be absent
            pass
        return firmware.dfu_status_from_model(model)

    async def ota_flash(self, data: bytes, *, i_understand_brick_risk: bool = False,
                        expected_md5: Optional[str] = None, on_progress=None) -> bool:
        """Scaffolded firmware flash: real enable/query-address handshake, then the RE gap.

        Runs the honest, testable-on-hardware part (md5 hook + enable-dfu + query-dfu-address) and
        then calls :func:`firmware.upload_firmware_image`, which raises NotImplementedError until
        the Realtek OTA transport is reverse-engineered. Returns the upload ack once implemented.
        """
        self._require_brick_ack(i_understand_brick_risk)
        computed = firmware.firmware_md5(data)
        log.warning("OTA image md5=%s (%d bytes)", computed, len(data))
        if expected_md5 and not firmware.verify_firmware_md5(data, expected_md5):
            raise ValueError(f"firmware md5 mismatch: got {computed}, expected {expected_md5}")
        await self.enable_dfu(i_understand_brick_risk=True)
        addr = await self.query_dfu_address(i_understand_brick_risk=True)
        log.warning("DFU address reply: %s", addr)
        # >>> RE GAP: the image-upload transport is unknown — this raises NotImplementedError <<<
        return await firmware.upload_firmware_image(self, data, on_progress=on_progress)

    # -- incoming events --
    async def listen_events(self, timeout: Optional[float] = None):
        """Yield decoded incoming :class:`events.Event`s (camera, find-phone, media, etc.).

        Runs until ``timeout`` seconds elapse (``None`` = forever). Uses a stateful
        :class:`events.EventDecoder` so camera open/shutter/close and find-phone start/stop are
        distinguished. Unrecognised packets are yielded as ``Event(kind="raw")``.
        """
        await self._ensure_notify()
        reasm = commands.PacketReassembler()
        decoder = events.EventDecoder()
        loop = asyncio.get_event_loop()
        deadline = None if timeout is None else loop.time() + timeout
        while True:
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                return
            try:
                frag = await asyncio.wait_for(self._notifies.get(), remaining)
            except asyncio.TimeoutError:
                return
            for cmd_type, payload in reasm.feed(frag):
                ev = decoder.feed(cmd_type, payload)
                if ev is None:
                    ev = events.Event("raw", cmd_type, {}, payload)
                yield ev
