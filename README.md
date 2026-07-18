# MoYoung Watch

Drive a **MoYoung-v2 / Da Fit** smartwatch (the cheap RTL8762-class rectangles — e.g. the
**MOY-ERJ3 / TG38**, the ones whose manufacturer string reads `MOYOUNG-V2` and that pair with the
*Da Fit* app) **entirely from Home Assistant over Bluetooth** — no phone, no vendor app, stock
firmware. Custom **solar watch faces**, live value injection, follow-me lighting, a
camera-shutter room-light remote, and a standalone Python/CLI client all live here.

Everything runs through **ESPHome Bluetooth proxies** as generic BLE transport, so any room with a
proxy can reach the watch and the HAOS host needs no adapter of its own.

> The watch speaks the **0xFEEA "Da Fit" byte protocol** (ported clean-room from Gadgetbridge's
> MoYoung coordinator) and loads the MoYoung **`.bin`** watch-face format — *not* the protobuf
> protocol of the unrelated Actions/Zephyr "GTX2"-class watches. Verified on-glass against a
> MOY-ERJ3 running firmware 2.0.7 (2026).

---

## Features

- **⌚ Home Assistant integration** (`custom_components/moyoung/`, HACS-installable) — battery &
  steps sensors, nearest-proxy **room tracking** + RSSI, and control services: set time, push
  weather/notifications/now-playing, find-my-watch, set step goal, and **flash a watch face**.
- **☀️ Custom solar watch faces** (`watchfaces/`) — a big solar **State-of-Charge hero**, a 12-hour
  clock with **leading-zero drop + AM/PM**, watch-native date & battery, and **TOU-period
  colour themes** (green off-peak / amber part-peak / red peak) that auto-switch with your
  electricity rate.
- **💉 Live value injection** — the `moyoung.weather` service paints any signed number onto a face's
  `WEATHER_TEMP` field, turning it into a generic HA-driven readout (grid kW, solar SoC, …).
- **💡 Follow-me lighting** — lights in whatever room the watch is in follow you, per-room gated.
- **📸 Camera-shutter room remote** — the watch's camera-remote shutter toggles the lights in its
  current room (it emits a BLE opcode HA listens for).
- **🐍 Standalone client + CLI** (`client/`) — scan, inspect, control and flash faces from a
  terminal, no Home Assistant required.

## Repository layout

```
custom_components/moyoung/   HACS-installable Home Assistant integration (the core)
client/                      standalone `moyoung` Python client + CLI (pip-installable)
watchfaces/                  face builders + shipped .bin faces + glyphs; dawft fetch script
packages/                    HA packages: follow-me, camera-lights, solar SoC, TOU rate/faces
dashboards/                  the MoYoung Lovelace dashboard (+ FIXES.md)
tests/                       offline integration tests (proto + camera + package structure)
docs/                        GitHub Pages site (landing + face builder) + protocol notes
```

## Install

### 1. The Home Assistant integration (HACS)

Add this repository as a **custom repository** (category: *Integration*) in HACS, install
**MoYoung Watch**, and restart HA. Then **Settings → Devices & Services → Add Integration →
MoYoung Watch** and pick your watch (discovered over Bluetooth via your proxies).

*Manual:* copy `custom_components/moyoung/` into your HA `config/custom_components/` and restart.

**Requires** at least one [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy/)
with active connections, within BLE range of the watch.

### 2. The automations & lighting (packages)

Copy the `packages/moyoung_*.yaml` you want into your HA `config/packages/` directory (enable
packages with `homeassistant: packages: !include_dir_named packages` in `configuration.yaml`).
Each file's header documents the entities it expects and how to point it at your own sensors.

| Package | What it adds |
|---|---|
| `moyoung_solar_soc.yaml` | pushes a solar-battery SoC % onto the face's `WEATHER_TEMP` hero |
| `moyoung_tou.yaml` | `sensor.moyoung_rate_cents` — live ¢/kWh for the dashboard |
| `moyoung_tou_faces.yaml` | auto-switch the watch face by time-of-use period |
| `moyoung_follow.yaml` | follow-me lighting (master + per-room toggles) |
| `moyoung_camera_lights.yaml` | camera-shutter → toggle current-room lights |
| `moyoung_inject.yaml` | example: push any HA number onto the face (grid kW) |

### 3. The dashboard

Import `dashboards/moyoung-dashboard.yaml` (see `dashboards/FIXES.md` for notes). Swap the
external `sensor.pge_*` / `sensor.epever_battery_soc` references for your own rate / SoC sensors.

### 4. The standalone client (optional)

```bash
cd client
python3 -m venv venv && ./venv/bin/pip install -e ".[test]"
./venv/bin/moyoung scan                 # find MoYoung watches
./venv/bin/moyoung --address <MAC> upload-face ../watchfaces/solar-soc/solar-hero5.bin
```

## Watch faces

Pre-built faces ship in `watchfaces/solar-soc/*.bin` and are the canonical artifacts. To rebuild
from source you need Python + Pillow, the **DejaVu** fonts (`fonts-dejavu`), and the **dawft**
packer:

```bash
cd watchfaces/dawft && ./get-dawft.sh          # fetch + build the GPL packer (once)
cd ../solar-soc && python3 build_tou.py        # → solar-offpeak/partial/peak.bin
```

`build_hero.py` is the shared render library; `build_hero5.py` builds the SoC-hero seed;
`build_tou.py` stamps the three TOU-period colour themes. dawft is resolved via `$DAWFT`, then
`$PATH`, then `watchfaces/dawft/dawft`. See `watchfaces/README.md` for the full recipe.

## The protocol, briefly

The watch exposes a single **0xFEEA** GATT service; commands are framed `FE EA | flags | len |
cmd | payload…` and written to the control characteristic, with acks/notifications coming back on
another. Face `.bin` files are a MoYoung/Da-Fit container of RLE/raw RGB565 blobs plus a layout
table of positioned fields (clock digits, hero number, weekday, battery, …); the firmware
repositions value-driven fields at render time. Face flashing streams the blob over the data
characteristic and activates a slot. See `docs/PROTOCOL.md` and the interactive field reference in
`docs/face-builder.html`.

## Testing

```bash
pytest                     # offline integration tests (tests/)
cd client && pytest        # standalone client suite
```

Both suites are fully offline — no BLE, no Home Assistant, no watch required.

## Credits

- **[Gadgetbridge](https://gadgetbridge.org/gadgets/wearables/moyoung/)** — the MoYoung coordinator
  the control/sync protocol was ported (clean-room) from.
- **[david47k/dawft](https://github.com/david47k/dawft)** — the Da Fit watch-face `.bin` format &
  packer (GPL-2.0; fetched, not vendored).
- **[VicGuy/DaFup](https://github.com/VicGuy/DaFup)** — the watch-face upload protocol reference.

## License

MIT — see [LICENSE](LICENSE). (dawft, invoked as an external tool, is GPL-2.0 and not included.)
