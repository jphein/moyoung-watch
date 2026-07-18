# moyoung-client

Standalone BLE client for **MoYoung-v2 / Da Fit** smartwatches (e.g. the **MOY-ERJ3**) — the
Realtek RTL8762-class rectangle watches whose manufacturer characteristic reads `MOYOUNG-V2`.

It does the one "DaFlasher-like" thing that's actually possible on this chip: **flash a custom
watch-face or background over Bluetooth**, no phone and no Da Fit app. (Firmware stays stock —
the MoYoung OTA path is Realtek's closed, signed channel; see the repo's watch notes.)

(The Actions/Zephyr "GTX2"-class watches are a completely different platform — different SoC,
protobuf-over-BLE — and are out of scope here.) Verified on-glass against a MOY-ERJ3-2.0.7 on
2026-07-16.

## Install

```
python3 -m venv venv && ./venv/bin/pip install -e ".[test]"
```

## Commands

```
# discovery / faces
moyoung scan                              # find MoYoung watches (advertise service 0xFEEA)
moyoung info                              # connect, print device info + GATT services
moyoung list-fields                       # every embeddable face field (offline)
moyoung inspect face.bin                  # parse a face .bin and list its fields (offline)
moyoung --address <MAC> upload-face f.bin # flash a face   (activates slot 6 by default)
moyoung --address <MAC> upload-bg   b.bin # flash a background (slot 1)

# control / inject (ported from Gadgetbridge's MoYoung coordinator)
moyoung --address <MAC> set-time          # set the clock to local now
moyoung --address <MAC> weather --temp 23 --condition sunny --city HOME
moyoung --address <MAC> notify "Title" --body "text"   # inject a screen notification
moyoung --address <MAC> music --track "Song" --artist "Artist"
moyoung --address <MAC> find              # buzz the watch
moyoung --address <MAC> set-goal 12000    # daily step goal
# sync / read
moyoung --address <MAC> battery
moyoung --address <MAC> steps
moyoung --address <MAC> measure-hr        # trigger a live HR reading
```

**Injecting values (cf. the GTX2 day-field trick).** These watches paint the value into
whatever face field is placed, so the control commands double as an injection surface:
`weather --temp N` drives any `WEATHER_TEMP` field with an arbitrary signed number, and
`notify` / `music` inject arbitrary text onto the screen. Flash a face that includes a
`WEATHER_TEMP` field and you have a generic numeric display driven from the CLI.

Global options (`--address`, `--scan-timeout`, `-v`) go **before** the subcommand.
Without `--address`, on-watch commands scan and pick the strongest MoYoung device.

## Full MoYoung command surface

The complete Gadgetbridge MoYoung control/settings/health/interaction surface is ported here
(`moyoung --help` lists all 70+ subcommands). Layouts live in `moyoung_client/commands.py`
(framing + shared builders), `settings.py`, `health.py`, and `events.py`; the opcode→CLI
coverage matrix is in `scratch/gb-port/coverage.md`.

```
# device settings (each SET has a matching get-*)
moyoung --address <MAC> set-watch-face 3          # switch active face by index
moyoung --address <MAC> set-time-system 24        # 12 / 24
moyoung --address <MAC> set-units metric          # metric / imperial
moyoung --address <MAC> set-language english
moyoung --address <MAC> set-quick-view on         # raise-to-wake  (+ set-quick-view-time)
moyoung --address <MAC> set-dnd 01:00 06:30       # do-not-disturb window
moyoung --address <MAC> set-user-info --height 178 --weight 74 --age 33 --sex male
moyoung --address <MAC> set-alarm 0 07:30 --days mon,tue,wed,thu,fri
moyoung --address <MAC> get-watch-face            # query current face index
moyoung --address <MAC> get-alarms                # decode the alarm list

# health & measurement (trigger + parse the reply)
moyoung --address <MAC> blood-pressure            # -> {systolic, diastolic}
moyoung --address <MAC> spo2                       # -> blood-oxygen %
moyoung --address <MAC> dynamic-hr start           # continuous HR stream
moyoung --address <MAC> sync-sleep                 # decode today's sleep stages
moyoung --address <MAC> movement-hr                # last 3 workout summaries

# phone / interaction
moyoung --address <MAC> camera-open                # open the camera-remote screen
moyoung --address <MAC> music-state play
moyoung --address <MAC> send-volume 12

# events — turn the watch into a wireless button (decodes camera shutter, find-phone,
# media keys, and measurement completions the watch pushes unsolicited)
moyoung --address <MAC> listen
```

### Firmware / DFU / OTA (BRICK RISK)

We intend to flash firmware from the CLI too, but ported honestly — the DFU trigger/status are
real; the image-upload transport is **not** reverse-engineered and is a clearly-marked stub.

```
moyoung --address <MAC> dfu-status                 # DFU-capable? (model-number-string heuristic)
moyoung --address <MAC> enable-dfu --i-understand-brick-risk         # CMD_HS_DFU {1}
moyoung --address <MAC> query-dfu-address --i-understand-brick-risk  # CMD_HS_DFU {0} + decode
moyoung --address <MAC> ota-flash fw.bin --md5 <hex> --i-understand-brick-risk
```

`ota-flash` runs the real md5 hook + `enable-dfu` + `query-dfu-address` handshake, then hits a
`NotImplementedError`: the MoYoung/Da Fit OTA upload transport (suspected Realtek RTL8762 OTA —
custom service, MPPackTool header + banks + md5, two-bank, maybe AES/secure-boot) has not been
publicly RE'd. The handshake still runs, which is useful for capturing a real DFU session. See
`scratch/gb-port/coverage.md` → "Firmware OTA — RE status" for the capture/RE plan. All on-watch
DFU operations are gated behind `--i-understand-brick-risk`.

**Home Assistant candidates.** The incoming `listen` events (camera-shutter, find-phone,
media keys) map cleanly to HA triggers; `set-watch-face` to an HA service; and the
measurement reads (`measure-hr`, `spo2`, `blood-pressure`) to on-demand sensors.

## How face upload works (service `0xFEEA`)

| Characteristic | Role |
|---|---|
| `0xFEE2` | control — announce size, finish, activate slot (write-without-response) |
| `0xFEE6` | data — image bytes in MTU-sized chunks (write-without-response) |
| `0xFEE3` | notify — device acks (e.g. `feea200974ff` = transfer complete) |
| `0x2A29` | manufacturer — must read `MOYOUNG-V2` |

Announce `feea200974` + u32 big-endian length → stream the image → wait for the completion
ack → send finish/set-transfer → `feea200619`+slot to switch faces. The watch reassembles by
the announced total length, so BLE chunk size is free — important on Linux/BlueZ, which often
leaves the ATT MTU at 23 and rejects large writes (we chunk to ~244 bytes).

Face `.bin` files are the MoYoung/Da Fit format — build/dump them with
[`david47k/dawft`](https://github.com/david47k/dawft); `moyoung inspect` reads the header back.

## Tests

```
./venv/bin/python -m pytest
```

The suite is fully offline (no BLE): it asserts the exact protocol byte sequences and the face
header parser.

## Credits

Face-upload protocol reimplemented from the byte sequences proven by
[VicGuy/DaFup](https://github.com/VicGuy/DaFup) (GPLv3); face binary format from
[david47k/dawft](https://github.com/david47k/dawft). Opcodes/offsets are interoperability
facts; no upstream code is copied. Control/sync for these watches also exists upstream in
[Gadgetbridge](https://gadgetbridge.org/gadgets/wearables/moyoung/).
