"""Camera-shutter event entity for a MoYoung watch.

Exposes ``event.<watch>_camera_shutter`` — an HA EventEntity that fires whenever the watch's
camera-remote shutter is pressed (the coordinator broadcasts ``EVENT_CAMERA_SHUTTER`` on the bus;
this entity mirrors it so the state shows up on the dashboard with a timestamp + room attribute).
The bus event, not this entity, is what the follow-me-style light-toggle automation triggers on;
this is purely for observability.
"""
from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, EVENT_CAMERA_SHUTTER
from .coordinator import MoyoungCoordinator

EVENT_TYPE_SHUTTER = "shutter"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MoyoungCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MoyoungCameraShutterEvent(coordinator, entry)])


class MoyoungCameraShutterEvent(CoordinatorEntity[MoyoungCoordinator], EventEntity):
    _attr_has_entity_name = True
    _attr_name = "Camera shutter"
    _attr_icon = "mdi:camera-iris"
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = [EVENT_TYPE_SHUTTER]

    def __init__(self, coordinator: MoyoungCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_camera_shutter"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=entry.title,
            manufacturer="MoYoung",
            model="MOYOUNG-V2",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_CAMERA_SHUTTER, self._handle_shutter))

    @callback
    def _handle_shutter(self, event: Event) -> None:
        if event.data.get("address") != self.coordinator.address:
            return
        self._trigger_event(
            EVENT_TYPE_SHUTTER,
            {"room": event.data.get("room"), "proxy": event.data.get("proxy")},
        )
        self.async_write_ha_state()
