"""The MoYoung Watch integration.

Runs the MoYoung BLE protocol inside HA via bleak, routed through an ESPHome Bluetooth
proxy (active connections). Exposes battery/steps sensors and a set of control/injection
services. See the repo's moyoung-client for the standalone CLI this shares protocol code with.
"""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import (HomeAssistant, ServiceCall, ServiceResponse,
                                SupportsResponse)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (DOMAIN, EVENT_FACE_ACTIVATED, SERVICE_CAMERA, SERVICE_FIND,
                    SERVICE_GET_WATCH_FACES, SERVICE_MUSIC, SERVICE_NOTIFY, SERVICE_SET_GOAL,
                    SERVICE_SET_TIME, SERVICE_SET_WATCH_FACE, SERVICE_UPLOAD_FACE, SERVICE_WEATHER)
from .coordinator import MoyoungCoordinator, MoyoungError
from .proto import commands

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.EVENT]

# Optional device target on every service; if omitted and only one watch is configured, it's used.
_TARGET = {vol.Optional("device_id"): cv.string}


def _resolve(hass: HomeAssistant, call: ServiceCall) -> MoyoungCoordinator:
    entries = hass.data.get(DOMAIN, {})
    if not entries:
        raise HomeAssistantError("No MoYoung watch is configured")
    device_id = call.data.get("device_id")
    if device_id:
        device = dr.async_get(hass).async_get(device_id)
        if device:
            for identifier in device.identifiers:
                if identifier[0] == DOMAIN:
                    for coord in entries.values():
                        if coord.address == identifier[1]:
                            return coord
        raise HomeAssistantError(f"device_id {device_id} is not a MoYoung watch")
    if len(entries) == 1:
        return next(iter(entries.values()))
    raise HomeAssistantError("Multiple MoYoung watches configured — pass device_id")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = MoyoungCoordinator(hass, entry.data[CONF_ADDRESS], entry.title)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: MoyoungCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_disconnect()
        if not hass.data[DOMAIN]:
            for svc in (SERVICE_SET_TIME, SERVICE_WEATHER, SERVICE_NOTIFY, SERVICE_MUSIC,
                        SERVICE_FIND, SERVICE_SET_GOAL, SERVICE_UPLOAD_FACE,
                        SERVICE_SET_WATCH_FACE, SERVICE_GET_WATCH_FACES, SERVICE_CAMERA):
                hass.services.async_remove(DOMAIN, svc)
    return unloaded


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SET_TIME):
        return

    async def _guard(coro):
        try:
            await coro
        except MoyoungError as err:
            raise HomeAssistantError(str(err)) from err

    async def set_time(call: ServiceCall) -> None:
        await _guard(_resolve(hass, call).set_time())

    async def weather(call: ServiceCall) -> None:
        await _guard(_resolve(hass, call).set_weather(
            call.data["temp"], call.data.get("condition", "sunny"), call.data.get("city", "")))

    async def notify(call: ServiceCall) -> None:
        title = call.data["title"]
        text = f"{title}:{call.data['body']}" if call.data.get("body") else title
        await _guard(_resolve(hass, call).notify(text, call.data.get("type", commands.NOTIFY_OTHER)))

    async def music(call: ServiceCall) -> None:
        await _guard(_resolve(hass, call).set_music(
            track=call.data.get("track"), artist=call.data.get("artist")))

    async def find(call: ServiceCall) -> None:
        await _guard(_resolve(hass, call).find())

    async def camera(call: ServiceCall) -> None:
        # Manual convenience: open the watch's camera-remote screen. Not required for the
        # shutter->lights automation (that only LISTENS for the inbound shutter opcode).
        await _guard(_resolve(hass, call).enter_camera_mode())

    async def set_goal(call: ServiceCall) -> None:
        await _guard(_resolve(hass, call).set_goal(call.data["steps"]))

    async def upload_face(call: ServiceCall) -> None:
        path = call.data["path"]
        if not hass.config.is_allowed_path(path):
            raise HomeAssistantError(f"path not allowed: {path}")
        try:
            data = await hass.async_add_executor_job(lambda: open(path, "rb").read())
        except OSError as err:
            raise HomeAssistantError(f"cannot read {path}: {err}") from err
        coord = _resolve(hass, call)
        try:
            result = await coord.upload_face(
                data, call.data.get("slot", 6),
                activate=call.data.get("activate", True),
                face_index=call.data.get("face_index"))
        except MoyoungError as err:
            raise HomeAssistantError(str(err)) from err
        # Broadcast the activation diagnostics so automations (and the live test) can observe
        # exactly which display-list index the custom face landed at.
        hass.bus.async_fire(EVENT_FACE_ACTIVATED, {"address": coord.address, **result})

    async def set_watch_face(call: ServiceCall) -> ServiceResponse:
        coord = _resolve(hass, call)
        try:
            return await coord.set_watch_face(call.data["index"])
        except MoyoungError as err:
            raise HomeAssistantError(str(err)) from err

    async def get_watch_faces(call: ServiceCall) -> ServiceResponse:
        coord = _resolve(hass, call)
        try:
            return await coord.get_watch_faces()
        except MoyoungError as err:
            raise HomeAssistantError(str(err)) from err

    reg = hass.services.async_register
    reg(DOMAIN, SERVICE_SET_TIME, set_time, schema=vol.Schema(_TARGET))
    reg(DOMAIN, SERVICE_WEATHER, weather, schema=vol.Schema({
        **_TARGET,
        vol.Required("temp"): vol.All(vol.Coerce(int), vol.Range(min=-128, max=127)),
        vol.Optional("condition"): vol.In(list(commands.WEATHER_CONDITIONS)),
        vol.Optional("city"): cv.string,
    }))
    reg(DOMAIN, SERVICE_NOTIFY, notify, schema=vol.Schema({
        **_TARGET,
        vol.Required("title"): cv.string,
        vol.Optional("body"): cv.string,
        vol.Optional("type"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
    }))
    reg(DOMAIN, SERVICE_MUSIC, music, schema=vol.Schema({
        **_TARGET, vol.Optional("track"): cv.string, vol.Optional("artist"): cv.string,
    }))
    reg(DOMAIN, SERVICE_FIND, find, schema=vol.Schema(_TARGET))
    reg(DOMAIN, SERVICE_CAMERA, camera, schema=vol.Schema(_TARGET))
    reg(DOMAIN, SERVICE_SET_GOAL, set_goal, schema=vol.Schema({
        **_TARGET, vol.Required("steps"): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }))
    reg(DOMAIN, SERVICE_UPLOAD_FACE, upload_face, schema=vol.Schema({
        **_TARGET,
        vol.Required("path"): cv.string,
        vol.Optional("slot"): vol.All(vol.Coerce(int), vol.Range(min=1, max=6)),
        vol.Optional("face_index"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
        vol.Optional("activate"): cv.boolean,
    }))
    reg(DOMAIN, SERVICE_SET_WATCH_FACE, set_watch_face, schema=vol.Schema({
        **_TARGET, vol.Required("index"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
    }), supports_response=SupportsResponse.OPTIONAL)
    reg(DOMAIN, SERVICE_GET_WATCH_FACES, get_watch_faces, schema=vol.Schema(_TARGET),
        supports_response=SupportsResponse.ONLY)
