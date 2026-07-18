# Dashboard fixes — `moyoung-dashboard.yaml`

The dashboard copied out of the original repo had stale entity references and inaccurate
"on-glass" claims that no longer matched the integration + HA packages. This documents exactly
what was broken and what was changed, so the corrected dashboard can be redeployed to live HA.

> Deploy note (for the orchestrator, **not** done here): this is the reference copy of the
> storage-mode Lovelace dashboard at `url_path: moyoung-watch`. Push it with the Lovelace
> WebSocket API (the original was created/updated that way), or paste it into the dashboard's
> raw-config editor. It was **not** deployed to live HA from this project.

## Fixed

### 1. Wrong TOU source: `select.pge_eva2` → `sensor.pge_tou_tier` / `sensor.moyoung_rate_cents`
**Was:** the "Electricity rate" card derived its period label from
`states('select.pge_eva2')`.
**Why it's wrong:** `select.pge_eva2` is the **PG&E Opower integration's** own selector, not the
authoritative time-of-use tier. The `moyoung_tou.yaml` package is explicit: *"Do NOT use
`select.pge_eva2`… SOURCE OF TRUTH = `sensor.pge_tou_tier`."*
**Fix:** the rate card now reads the dedicated **`sensor.moyoung_rate_cents`** sensor (defined in
`packages/moyoung_tou.yaml` — whole ¢/kWh from `sensor.pge_current_price`) and its `period`
attribute (which resolves to `sensor.pge_tou_tier`'s label), falling back to formatting
`sensor.pge_tou_tier` directly. No more `select.pge_eva2`.

### 2. Inaccurate on-glass mapping: rate is **not** on the watch
**Was:** the Solar Faceplate intro and the "On-glass mapping" card claimed *"HA keeps the two
numbers live on the watch (SoC → `WEATHER_TEMP`, rate → `STEPS_GOAL`)"* and that both were
*"injected live from HA (`moyoung.weather` and `moyoung.set_goal`)."*
**Why it's wrong:** the on-watch rate push was **disabled** — `STEPS_GOAL` (0x76), the second
injectable slot, renders as corrupted pixels on the MOY-ERJ3 firmware (verified on-glass
2026-07-16), so the `moyoung.set_goal` automation in `moyoung_tou.yaml` is commented out. Only
the **SoC** reaches the watch (via `moyoung.weather` → `WEATHER_TEMP`).
**Fix:** both markdown cards now state that SoC is on the watch face and the rate is
**dashboard-only** on this firmware, and the trailing note no longer claims "the rate pushes
automatically."

### 3. Flash-face button pointed at a stale face binary
**Was:** the "Flash solar face" button uploaded `/media/moyoung/solar-soc.bin` (the older 55 KB
single-face build).
**Fix:** points at `/media/moyoung/solar-hero5.bin` (the current clean SoC-hero face with the
watch-native date + battery line). The note now explains the path is where you copy a built
`.bin` on the HA host (see the repo's `watchfaces/`), and that TOU-period **auto-switching**
faces are handled by the `moyoung_tou_faces` package rather than this manual button.

## Verified correct (left unchanged)

- `sensor.moyoung_watch_battery` / `_steps` / `_location` / `_rssi` — match the integration's
  sensor entities (`sensor.py`: keys `battery`, `steps`, `room`→"Location", `nearest_rssi`→"RSSI").
- `event.moyoung_watch_camera_shutter` — provided by the integration's event platform.
- All `input_boolean.watch_follow_*` / `watch_camera_*` toggles — defined in
  `packages/moyoung_follow.yaml` + `packages/moyoung_camera_lights.yaml`.
- `sensor.epever_battery_soc` (SoC source) and `sensor.pge_current_price` (rate source) — the
  sources the `moyoung_solar_soc.yaml` / `moyoung_tou.yaml` packages actually use.

## External dependencies (not part of this project)

The Solar Faceplate view references sensors that live in JP's wider HA config, not this repo:
`sensor.pge_current_price` + `sensor.pge_tou_tier` (a `pge_tou_tariff.yaml` package) and
`sensor.epever_battery_soc` (the solar charge controller). Swap these for your own SoC / rate
sensors — see the `◀ SOURCE SENSOR` markers in `packages/moyoung_solar_soc.yaml`.
