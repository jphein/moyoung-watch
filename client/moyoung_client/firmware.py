"""Firmware / DFU / OTA — the honest state of the art.

**Danger:** everything here can brick the watch. All on-watch entry points are gated behind an
explicit ``i_understand_brick_risk`` acknowledgement.

What Gadgetbridge actually has (ported here as real commands):
  * ``CMD_HS_DFU = 99`` — from *static* RE of the vendor app only, marked ``(?)`` in GB and
    never sent or handled by GB itself: ``{1}`` = ``enableHsDfu()``, ``{0}`` = ``queryHsDfuAddress()``.
  * "DFU status" (GB's internal *packet 19*) is a comment, not code: it reads the standard
    Model Number String (``0x2A24``) and looks for the substring ``DFU`` followed by a number
    (``0`` = not in DFU / not capable, ``!= 0`` = DFU-capable). Implemented as
    :func:`dfu_status_from_model`.

What NOBODY has (the real gap — deliberately NOT faked):
  * There is **no firmware-image upload protocol** in Gadgetbridge (no FirmwareInstaller, no OTA
    service). The MOY-ERJ3 is a Realtek RTL8762-class watch, so the transport is almost certainly
    Realtek's OTA (a custom GATT service, an MPPackTool-style OTA header + image bank(s) + a MD5/
    CRC trailer, two-bank A/B, and possibly AES-encrypted / secure-boot-signed). This has not been
    publicly reverse-engineered for Da Fit firmware.
  * :func:`upload_firmware_image` is therefore a **stub that raises NotImplementedError**. The
    ``ota_flash`` orchestration (in :class:`transport.MoyoungClient`) runs the real
    enable/query-address handshake and then hits this stub — structure it so the transport drops
    in once we capture and RE a DFU session (see scratch/gb-port/coverage.md "Firmware OTA — RE
    status").
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, Optional

from . import commands as c

# The upload transport is unknown; keep the reason in one place so the stub + docs agree.
OTA_TRANSPORT_TODO = (
    "MoYoung/Da Fit firmware OTA upload transport is not reverse-engineered. Suspected Realtek "
    "RTL8762 OTA (custom service e.g. 0xFEE7/0xD0FF, MPPackTool header + image bank + MD5/CRC, "
    "two-bank, possibly AES/secure-boot). Capture a real DFU session and RE it before enabling. "
    "See scratch/gb-port/coverage.md 'Firmware OTA — RE status'."
)


# ------------------------------------------------------------------ DFU trigger payloads
def enable_dfu_payload() -> bytes:
    """CMD_HS_DFU {1} — enableHsDfu(): ask the watch to enter its OTA/DFU bootloader."""
    return c.dfu_payload(True)


def query_dfu_address_payload() -> bytes:
    """CMD_HS_DFU {0} — queryHsDfuAddress(): ask the watch for its DFU target address."""
    return c.dfu_payload(False)


def decode_dfu_address(payload: bytes) -> Dict[str, object]:
    """Best-effort view of a query-dfu-address reply.

    The reply format is **not reverse-engineered** (GB never even reads it), so this returns the
    raw bytes plus clearly-labelled speculative interpretations rather than pretending to parse it.
    """
    p = bytes(payload)
    out: Dict[str, object] = {
        "raw_hex": p.hex(),
        "length": len(p),
        "note": "OTA/DFU address format not reverse-engineered — raw bytes only",
    }
    if len(p) == 6:  # could be a BLE BD_ADDR (DFU often advertises at +1 of the app address)
        out["maybe_bd_addr"] = ":".join(f"{b:02X}" for b in reversed(p))
    if len(p) == 4:
        out["maybe_u32_le"] = int.from_bytes(p, "little")
        out["maybe_u32_be"] = int.from_bytes(p, "big")
    return out


# ------------------------------------------------------------------ DFU status (packet 19)
def dfu_status_from_model(model: Optional[str]) -> Dict[str, object]:
    """Reproduce GB's "packet 19" DFU check against a Model Number String (0x2A24).

    Looks for the substring ``DFU`` optionally followed by a number: a trailing ``0`` means not
    DFU-capable / not in DFU mode, any non-zero means capable. Absent marker => not capable.
    """
    text = model or ""
    upper = text.upper()
    has_marker = "DFU" in upper
    number: Optional[int] = None
    capable: bool = False
    if has_marker:
        m = re.search(r"DFU\s*([0-9]+)", upper)
        if m:
            number = int(m.group(1))
            capable = number != 0
        else:
            capable = True  # marker present, no number -> treat as capable
    return {"model": text, "dfu_marker": has_marker,
            "dfu_number": number, "dfu_capable": capable}


# ------------------------------------------------------------------ md5 verify hook
def firmware_md5(data: bytes) -> str:
    """MD5 of a firmware image (Realtek OTA images ship a trailing MD5; verify before/after)."""
    return hashlib.md5(data).hexdigest()


def verify_firmware_md5(data: bytes, expected: str) -> bool:
    return firmware_md5(data) == (expected or "").strip().lower()


# ------------------------------------------------------------------ the RE gap
async def upload_firmware_image(client, data: bytes, *, on_progress=None) -> bool:
    """STUB — the OTA image-upload transport is not reverse-engineered. Raises NotImplementedError.

    Once a DFU capture is reversed, implement the Realtek OTA sequence here (open the OTA service,
    send the packed header, stream image banks, send the MD5/CRC trailer, await the apply/verify
    ack) and return whether the watch acknowledged. Keep the signature stable so
    ``transport.MoyoungClient.ota_flash`` can call it unchanged.
    """
    raise NotImplementedError(OTA_TRANSPORT_TODO)
