"""Connection + protocol coordinator for a MoYoung watch, over HA's Bluetooth stack.

HA routes ``bleak`` connections through whatever ESPHome Bluetooth-proxy (active connections)
is in radio range, so the HAOS VM needs no local adapter. This coordinator owns one connection
to the watch, runs the vendored MoYoung protocol on it, and polls battery/steps for sensors.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Optional

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (BATTERY_CHAR, BATTERY_POLL_EVERY, CAMERA_SHUTTER_DEBOUNCE_S, CTRL_CHAR,
                    DATA_CHAR, DOMAIN, EVENT_CAMERA_SHUTTER, MANUFACTURER_CHAR, MANUFACTURER_NAME,
                    NOTIFY_CHAR, PROXY_ROOM_OVERRIDES, SAFE_CHUNK, STEPS_CHAR,
                    UPDATE_INTERVAL_SECONDS)
from .proto import commands, faceupload

_LOGGER = logging.getLogger(__name__)


class MoyoungError(Exception):
    """A recoverable MoYoung connection/command error."""


def proxy_to_room(name: Optional[str]) -> Optional[str]:
    """Derive a friendly room from a proxy/scanner name.

    HA scanner names look like ``laundry-ble-proxy-2 (AA:BB:CC:DD:EE:FF)`` — strip the
    trailing ``(MAC)``, the ``-ble-proxy`` role suffix, and any trailing instance number
    so both laundry proxies collapse to the room "Laundry".
    """
    if not name:
        return None
    base = name.split(" (")[0].strip()
    if base in PROXY_ROOM_OVERRIDES:
        return PROXY_ROOM_OVERRIDES[base]
    r = base
    for suf in ("-ble-proxy", "_ble_proxy", "bluetooth-proxy", "-ble-proxy-"):
        r = r.replace(suf, " ")
    parts = [p for p in r.replace("-", " ").replace("_", " ").split() if p]
    if len(parts) > 1 and parts[-1].isdigit():
        parts = parts[:-1]
    return " ".join(parts).title() or base


class MoyoungCoordinator(DataUpdateCoordinator):
    """Owns the BLE connection and exposes MoYoung operations. Also polls sensors."""

    def __init__(self, hass: HomeAssistant, address: str, name: str) -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN} {address}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS))
        self.address = address.upper()
        self.device_name = name
        self._client: Optional[BleakClient] = None
        self._lock = asyncio.Lock()
        self._notifies: "asyncio.Queue[bytes]" = asyncio.Queue()
        self._last_notify = b""
        self._notifying = False
        self._poll_count = 0
        # A SECOND, standalone reassembler that watches the whole inbound notification stream for
        # unsolicited watch->phone events (the camera shutter). Independent of the command/ACK
        # path above: those consume ``_notifies`` only while a command is in flight, so a shutter
        # press arriving at idle would otherwise be dropped. This one sees every fragment.
        self._cam_reasm = commands.PacketReassembler()
        self._last_shutter = 0.0  # loop-clock timestamp of the last accepted shutter (de-bounce)
        self._conn_proxy: Optional[str] = None  # proxy NAME we're connected through (room signal while connected+silent)

    # ---------------------------------------------------------------- connection
    def _on_disconnect(self, _client: BleakClient) -> None:
        self._notifying = False
        self._client = None
        self._conn_proxy = None
        self._cam_reasm = commands.PacketReassembler()  # drop any partial packet across reconnects

    def _on_notify(self, _char, data: bytearray) -> None:
        b = bytes(data)
        self._last_notify = b
        self._notifies.put_nowait(b)
        # Watch for the inbound camera-shutter opcode on the same stream (ignores every other
        # packet type). Runs in the HA event loop — same context as the put_nowait above.
        for cmd_type, _payload in self._cam_reasm.feed(b):
            if cmd_type == commands.CMD_SWITCH_CAMERA_VIEW:
                self._handle_camera_shutter()

    async def _ensure_connected(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        ble_device: BLEDevice | None = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True)
        if ble_device is None:
            raise MoyoungError(
                f"{self.address} not reachable via any Bluetooth proxy/adapter "
                "(is the watch awake and in range of a proxy with active connections?)")
        client = await establish_connection(
            BleakClient, ble_device, self.address, disconnected_callback=self._on_disconnect)
        # verify identity before we start writing commands at it
        try:
            man = (await client.read_gatt_char(MANUFACTURER_CHAR)).decode("utf-8", "replace").strip()
        except Exception as err:  # noqa: BLE001
            await client.disconnect()
            raise MoyoungError(f"could not read manufacturer: {err}") from err
        if man != MANUFACTURER_NAME:
            await client.disconnect()
            raise MoyoungError(f"not a MOYOUNG-V2 device (manufacturer={man!r})")
        await client.start_notify(NOTIFY_CHAR, self._on_notify)
        self._client = client
        self._notifying = True
        # Remember which proxy we connected THROUGH. Once connected the watch goes silent and
        # async_ble_device_from_address returns nothing, so this captured NAME is our room signal
        # for the whole connection (resolved now, while the device is still in the scanner list).
        src = ble_device.details.get("source") if isinstance(ble_device.details, dict) else None
        self._conn_proxy = None
        if src:
            for sd in bluetooth.async_scanner_devices_by_address(self.hass, self.address, True):
                if sd.scanner.source == src:
                    self._conn_proxy = sd.scanner.name or sd.scanner.source
                    break

    async def async_disconnect(self) -> None:
        if self._client is not None and self._client.is_connected:
            await self._client.disconnect()
        self._client = None

    # ---------------------------------------------------------------- Uploader protocol
    async def write_ctrl(self, data: bytes) -> None:
        await self._client.write_gatt_char(CTRL_CHAR, data, response=False)

    async def write_data(self, data: bytes) -> None:
        await self._client.write_gatt_char(DATA_CHAR, data, response=False)

    async def wait_ack(self, ack: bytes, timeout: float) -> bool:
        if self._last_notify.startswith(ack):
            return True
        loop = self.hass.loop
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

    async def _send_command(self, cmd_type: int, payload: bytes = b"") -> None:
        packet = commands.build_packet(cmd_type, payload)
        for frag in faceupload.chunk(packet, SAFE_CHUNK):
            await self.write_ctrl(frag)

    async def _command_with_response(self, cmd_type: int, payload: bytes = b"",
                                     timeout: float = 12.0) -> Optional[bytes]:
        while not self._notifies.empty():
            self._notifies.get_nowait()
        reasm = commands.PacketReassembler()
        await self._send_command(cmd_type, payload)
        loop = self.hass.loop
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

    # ---------------------------------------------------------------- operations
    async def set_time(self, dt=None) -> None:
        async with self._lock:
            await self._ensure_connected()
            await self._send_command(commands.CMD_SYNC_TIME, commands.time_payload(dt))

    async def set_weather(self, temp: int, condition="sunny", city: str = "") -> None:
        cid = commands.WEATHER_CONDITIONS.get(condition, condition) if isinstance(condition, str) \
            else condition
        async with self._lock:
            await self._ensure_connected()
            await self._send_command(commands.CMD_SET_WEATHER_TODAY,
                                     commands.weather_today_payload(temp, int(cid), city))

    async def notify(self, text: str, ntype: int = commands.NOTIFY_OTHER) -> None:
        async with self._lock:
            await self._ensure_connected()
            await self._send_command(commands.CMD_SEND_MESSAGE, commands.notify_payload(text, ntype))

    async def set_music(self, track: Optional[str] = None, artist: Optional[str] = None) -> None:
        async with self._lock:
            await self._ensure_connected()
            if artist is not None:
                await self._send_command(commands.CMD_SET_MUSIC_INFO,
                                         commands.music_payload(artist, True))
            if track is not None:
                await self._send_command(commands.CMD_SET_MUSIC_INFO,
                                         commands.music_payload(track, False))

    async def find(self) -> None:
        async with self._lock:
            await self._ensure_connected()
            await self._send_command(commands.CMD_FIND_MY_WATCH, b"")

    # ---------------------------------------------------------------- camera remote
    def _handle_camera_shutter(self) -> None:
        """Inbound CMD_SWITCH_CAMERA_VIEW: a camera-screen interaction on the watch (open/shutter).

        De-bounced, then re-broadcast as an HA event carrying the watch's CURRENT room so an
        automation can act on it (e.g. toggle that room's lights). Runs in the event loop.
        """
        now = self.hass.loop.time()
        if now - self._last_shutter < CAMERA_SHUTTER_DEBOUNCE_S:
            return
        self._last_shutter = now
        data = self.data or {}
        self.hass.bus.async_fire(EVENT_CAMERA_SHUTTER, {
            "address": self.address,
            "room": data.get("room"),
            "proxy": data.get("nearest_proxy"),
        })
        _LOGGER.info("moyoung camera shutter -> room=%s", data.get("room"))

    async def enter_camera_mode(self) -> None:
        """Ask the watch to OPEN its camera-remote screen (outbound CMD_SWITCH_CAMERA_VIEW).

        Optional: the user can also just open the camera app on the watch itself. Inbound shutter
        taps arrive as the same opcode regardless of who opened the screen, so the light-toggle
        feature does not require this — it is offered as a manual convenience service only.
        """
        async with self._lock:
            await self._ensure_connected()
            await self._send_command(commands.CMD_SWITCH_CAMERA_VIEW, b"")

    async def set_goal(self, steps: int) -> None:
        async with self._lock:
            await self._ensure_connected()
            await self._send_command(commands.CMD_SET_GOAL_STEP, commands.goal_steps_payload(steps))

    # ---------------------------------------------------------------- watch-face selection
    async def _get_watch_face(self) -> Optional[int]:
        """Current displayed face index (cmd 41). Assumes the lock is held + connected."""
        resp = await self._command_with_response(commands.CMD_QUERY_DISPLAY_WATCH_FACE)
        return commands.decode_watch_face(resp) if resp is not None else None

    async def _get_watch_face_count(self) -> Optional[int]:
        """Total selectable faces (cmd 132). Assumes the lock is held + connected."""
        resp = await self._command_with_response(commands.CMD_QUERY_SUPPORT_WATCH_FACE)
        if resp is None:
            return None
        count = commands.decode_support_watch_face(resp)
        return count if count >= 0 else None

    async def _set_watch_face(self, index: int) -> None:
        """Select a face by display-list index (cmd 25). Assumes the lock is held + connected."""
        await self._send_command(commands.CMD_SET_DISPLAY_WATCH_FACE,
                                 commands.watch_face_payload(index))

    async def get_watch_faces(self) -> dict:
        """Public: return {count, current} — the live probe for the display-list mapping."""
        async with self._lock:
            await self._ensure_connected()
            return {"count": await self._get_watch_face_count(),
                    "current": await self._get_watch_face()}

    async def set_watch_face(self, index: int) -> dict:
        """Public: select a face by display-list index and read back what took."""
        async with self._lock:
            await self._ensure_connected()
            await self._set_watch_face(index)
            return {"requested": index, "current": await self._get_watch_face()}

    async def _activate_uploaded_face(self, slot: int, face_index: Optional[int],
                                      count_before: Optional[int]) -> dict:
        """After an upload, select the custom face's real display-list index and verify (cmd 41)."""
        count_after = await self._get_watch_face_count()
        current_after = await self._get_watch_face()
        target = faceupload.derive_face_index(count_before, count_after, current_after,
                                              slot, face_index)
        await self._set_watch_face(target)
        verified = await self._get_watch_face()
        result = {
            "count_before": count_before, "count_after": count_after,
            "current_after_upload": current_after, "target_index": target,
            "verified_index": verified, "activated": verified == target,
        }
        _LOGGER.info("moyoung face activate: %s", result)
        return result

    async def upload_face(self, data: bytes, slot: int = faceupload.FACE_SLOT, *,
                          activate: bool = True, face_index: Optional[int] = None) -> dict:
        """Flash a face, then make it the CURRENT face (its list index != its storage slot).

        Returns a diagnostic dict (ack + activation details). Set ``activate=False`` to skip
        selection, or pass ``face_index`` to force a specific display-list index.
        """
        async with self._lock:
            await self._ensure_connected()
            count_before = await self._get_watch_face_count() if activate else None
            acked = await faceupload.upload("face", self, data, slot=slot,
                                            chunk_size=SAFE_CHUNK, activate=False)
            result = {"acked": acked, "slot": slot, "activated": False}
            if activate:
                result.update(await self._activate_uploaded_face(slot, face_index, count_before))
            return result

    # ---------------------------------------------------------------- sensor poll
    async def _async_update_data(self) -> dict:
        """Every tick: recompute location from the advert cache (no connection). Battery/steps
        (which need a connection) are refreshed only every BATTERY_POLL_EVERY ticks."""
        data = dict(self.data or {})

        # --- location: which proxy the watch is at ---
        # This watch is a SINGLE-CONNECTION peripheral: once a proxy connects to it, it goes
        # (nearly) silent on the advertising channel. The coordinator holds a persistent connection
        # almost all the time, so the advert channel is starved — an advert-first resolver ends up
        # trusting a stale/rare advert on some far proxy (or an arbitrary reachable one), which is
        # what made the room "grab". Instead, prefer the proxy HA actually reaches the watch
        # THROUGH: the connectable "connection source" is a real, near-the-watch signal even while
        # the watch isn't broadcasting (it's the proxy relaying the GATT link the camera shutter
        # rides in on). Fall back to advert RSSI (with hysteresis) only when there's no such path.
        proxies: dict = {}
        advert_name: Optional[str] = None
        advert_rssi: Optional[int] = None
        for connectable in (True, False):
            for sd in bluetooth.async_scanner_devices_by_address(self.hass, self.address, connectable):
                adv = sd.advertisement
                rssi = adv.rssi if adv is not None else None
                name = sd.scanner.name or sd.scanner.source
                proxies.setdefault(name, rssi)
                if rssi is not None and (advert_rssi is None or rssi > advert_rssi):
                    advert_rssi, advert_name = rssi, name

        # The proxy we're connected THROUGH, captured at connect time in _ensure_connected
        # (async_ble_device_from_address returns nothing once connected, so we can't re-derive it
        # here — that bug made a connected-but-silent watch resolve to "away").
        connected = self._client is not None and self._client.is_connected
        conn_name = self._conn_proxy if connected else None

        nearest_name = conn_name or advert_name
        if nearest_name is None and not connected and proxies:
            nearest_name = next(iter(proxies))  # disconnected: any reachable proxy beats nothing
        # Hysteresis only matters on the noisy advert-fallback path; the connection source is stable.
        if conn_name is None and advert_name is not None:
            prev = (self.data or {}).get("nearest_proxy")
            if (prev and prev in proxies and nearest_name != prev
                    and proxies.get(nearest_name) is not None and proxies.get(prev) is not None
                    and proxies[nearest_name] - proxies[prev] < 6):
                nearest_name = prev
        room = proxy_to_room(nearest_name)
        if room is None and connected:
            # Connected but no proxy name resolved — the watch is demonstrably in range of SOME
            # proxy (it's connected), so keep the last known room rather than flapping to "away".
            room = (self.data or {}).get("room")
            nearest_name = nearest_name or (self.data or {}).get("nearest_proxy")
        _LOGGER.debug("moyoung location: conn=%s advert=%s(%s) chosen=%s room=%s",
                      conn_name, advert_name, advert_rssi, nearest_name, room)
        data["nearest_proxy"] = nearest_name
        data["room"] = room or "away"
        data["nearest_rssi"] = proxies.get(nearest_name)
        data["proxies"] = proxies

        # --- battery/steps: needs a connection; keep retrying until we have a reading, then
        #     back off to every BATTERY_POLL_EVERY ticks to spare the watch ---
        self._poll_count += 1
        if data.get("battery") is None or self._poll_count % BATTERY_POLL_EVERY == 0:
            async with self._lock:
                try:
                    await self._ensure_connected()
                    try:
                        raw = await self._client.read_gatt_char(BATTERY_CHAR)
                        if raw:
                            data["battery"] = raw[0]
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        raw = await self._client.read_gatt_char(STEPS_CHAR)
                        if len(raw) >= 9:
                            data["steps"] = int.from_bytes(raw[3:6], "little")
                    except Exception:  # noqa: BLE001
                        pass
                except MoyoungError as err:
                    _LOGGER.debug("battery/steps poll skipped: %s", err)
        return data
