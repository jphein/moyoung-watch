# MoYoung Watch — Home Assistant integration

Drives a MoYoung-v2 / Da Fit watch (e.g. MOY-ERJ3) from Home Assistant. Runs the MoYoung BLE
protocol **inside HA via `bleak`**, routed through an **ESPHome Bluetooth proxy** (active
connections) — so the HAOS VM needs no local adapter, and any room with a proxy can reach the
watch. Shares protocol code with the standalone `moyoung-client` CLI (in `client/`, mirrored
here under `proto/` so the integration installs cleanly via HACS with no extra dependency).

The entire MoYoung protocol runs inside HA; the ESPHome proxies are used purely as generic BLE
transport, so the HAOS host needs no Bluetooth adapter of its own.

## Entities

- `sensor.*_battery` — watch battery %
- `sensor.*_steps` — pedometer steps

## Services

| Service | What it does |
|---|---|
| `moyoung.set_time` | set the clock to HA local time |
| `moyoung.weather` | push weather; **`temp` is painted onto any WEATHER_TEMP face field** (injection) |
| `moyoung.notify` | push a screen notification (arbitrary text) |
| `moyoung.music` | push now-playing artist/track text |
| `moyoung.find` | buzz the watch |
| `moyoung.set_goal` | set the daily step goal |
| `moyoung.upload_face` | flash a face `.bin` from an allowlisted HA path |

All take an optional `device_id` (needed only if more than one watch is configured).

## Injection (the GTX2 trick, here)

Most face fields show the watch's own state, but `WEATHER_TEMP` accepts an arbitrary signed
number over `moyoung.weather`. Flash a face containing a `WEATHER_TEMP` field and you have a
generic numeric readout driven from HA — see `ha-packages/moyoung_inject.yaml` for a grid-kW
example that mirrors the GTX2 live-kW driver.

## Deploy

```
./deploy-moyoung-cc.sh            # validate + ship to the HA VM + config-check (no restart)
./deploy-moyoung-cc.sh --restart  # activate (restart HA, verify come-back, rollback on fail)
```

After activation the watch **auto-discovers** when a proxy sees it advertising `0xFEEA`
(Settings → Devices → discovered), or add it manually via *Add Integration → MoYoung Watch*.

## Requirements

- An ESPHome Bluetooth proxy with **active connections** enabled, in radio range of the watch.
- `bleak-retry-connector` (bundled with HA).
