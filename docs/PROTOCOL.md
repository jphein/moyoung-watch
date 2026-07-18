# MoYoung-v2 / Da Fit — protocol & face-format notes

Reference notes for the **MoYoung-v2 / Da Fit** watches (e.g. the MOY-ERJ3). The control/sync
side was ported clean-room from **Gadgetbridge's MoYoung coordinator**; the watch-face `.bin`
format mirrors **[david47k/dawft](https://github.com/david47k/dawft)**. This is a completely
different platform from the Actions/Zephyr protobuf watches — different SoC, different protocol,
different face format.

Canonical code: `custom_components/moyoung/proto/` (mirrored in `client/moyoung_client/`).

## GATT

A single vendor service carries everything:

| UUID | Role |
|---|---|
| `0000feea-…` | the MoYoung command/data service |
| `0000fee1-…` | **steps** — read / notify (pedometer) |
| `0000fee2-…` | **control** — write-without-response: commands & face control |
| `0000fee6-…` | **data** — write-without-response: image / face chunks |
| `0000fee3-…` | **notify** — acks & command responses come back here |
| `00002a19-…` | standard Battery Level |
| `00002a29-…` | standard Manufacturer Name (reads `MOYOUNG-V2`) |

Everything is routed through an **ESPHome Bluetooth proxy** with active connections, so the watch
is reachable from any room with a proxy and the HA host needs no adapter.

## Command framing

Commands are written to the **control** characteristic as:

```
FE EA │ b2 │ len │ cmd │ payload…
```

- `FE EA` — fixed magic.
- `b2` / `len` encode the total packet length (header included):
  - **MTU == 20 (v1):** `b2 = 0x10`, `len = total & 0xFF`
  - **MTU  > 20 (v2):** `b2 = (0x20 + (total >> 8)) & 0xFF`, `len = total & 0xFF`
- `cmd` — the opcode; `payload` is opcode-specific. `total = 5 + len(payload)`.

Example — open the camera-remote screen (`CMD_SWITCH_CAMERA_VIEW = 0x66`, empty payload, v2):
`FE EA 20 05 66`.

Responses/acks arrive on the **notify** characteristic and are reassembled (a frame may span
several notifications) — see `PacketReassembler` in `proto/commands.py`.

A few opcodes used here (full set in `commands.py`):

| Opcode | Meaning |
|---|---|
| `25` (0x19) | select active face by **display-list index** (not storage slot) |
| `102` (0x66) | camera-remote view — OUT opens it; IN is emitted on every shutter interaction |
| `109` (0x6d) | trigger heart-rate measurement |
| weather | writes a signed number into the face's `WEATHER_TEMP` field (the injection trick) |

## Face `.bin` format

A MoYoung face is a container of bitmap **blobs** plus a table of positioned **fields**:

```
byte 0      fileID      0x04 = Type A ; 0x81 / 0x84 = Type B / C
byte 1      dataCount   number of faceData (field) entries
byte 2      blobCount   number of bitmap blobs
bytes 3-4   faceNumber  design id (u16, little-endian) — must be UNIQUE per face
byte 5+     faceData    Type A:  32 × 6-byte  [type, x, y, w, h, blobIndex]  (u8)
                        Type B/C: 39 × 10-byte [type, blobIndex, x, y, w, h] (x/y/w/h u16 LE)
            …then the blob table + RLE / raw RGB565 bitmap data
```

Key ideas:

- **Fields are typed** (clock digits, hero number, weekday, battery, heart rate, weather, analog
  hands, …). Each field points at a blob and a position; the firmware paints the *current value*
  using that blob as the glyph set. Every embeddable field type is listed — with a live preview —
  in [`face-builder.html`](face-builder.html).
- **Value-driven fields are repositioned at render time.** A centre-aligned number shifts with its
  digit count, so the background *behind* such a field must be X-invariant (uniform per row) for a
  pre-baked glyph tile to composite seamlessly. This is the single biggest constraint on face art
  and is why the solar faces keep the sky flat behind the hero and clock.
- **Compression is per-blob** (`NONE` / `RLE_LINE` / `TRY_RLE`). RLE crushes flat art but garbles
  fine glyphs, so value glyphs are usually `NONE` while the background is RLE.
- **The carousel preview must be the last blob** and 140×163.

The `.bin` is assembled by **dawft** from a folder of `.bmp` blobs + a `watchface.txt` layout;
the builders in `watchfaces/solar-soc/` generate that folder and shell out to dawft.

## Flashing a face

Stream the `.bin` over the **data** characteristic in ≤244-byte chunks, then activate the target
slot. The integration's `moyoung.upload_face` service does this and derives the display-list index
the newly-uploaded face landed at (see `proto/faceupload.py`), firing a `moyoung_face_activated`
event with the activation diagnostics.

## Injection (a generic HA readout on the watch)

`WEATHER_TEMP` accepts an arbitrary signed number over the weather command. Flash a face
containing a `WEATHER_TEMP` field and it becomes a generic numeric readout you drive from any HA
sensor — the solar faces use it for the battery State-of-Charge; `packages/moyoung_inject.yaml`
shows a grid-kW example. Note: the watch's *second* injectable slot (`STEPS_GOAL`) renders as
corrupted pixels on the MOY-ERJ3 firmware, so only one live number is practical on-glass.
