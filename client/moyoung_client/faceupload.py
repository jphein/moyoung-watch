"""MoYoung-v2 watch-face / background upload protocol (GATT service ``0xFEEA``).

Reimplemented from the byte sequences proven by VicGuy/DaFup on MOYOUNG-V2 hardware. The
opcode bytes are interoperability facts captured verbatim; no DaFup code is copied.

Wire model (observed on a MOY-ERJ3-2.0.7):

    control channel  0xFEE2  (write-without-response)  — start / finish / activate commands
    data channel     0xFEE6  (write-without-response)  — the image, in 512-byte chunks
    notify channel   0xFEE3  (notify)                  — device acks (e.g. transfer complete)

Command frames look like ``fe ea 20 <len> <opcode> [payload]``. We keep the proven byte
prefixes as opaque constants and only synthesise the variable tail (image length, face slot).

The transfer sequence (face; background is identical with opcode ``6e`` and slot 1):

    1. write_ctrl( face_start(len(image)) )          # announce size
    2. settle ~0.5 s
    3. write_data(chunk) for each 512-byte chunk      # ~0.3 s apart (DaFup pacing)
    4. wait for notify starting with FACE_ACK          # feea200974ff = "got it"
    5. write_ctrl(_FACE_FINISH)                        # feea20097400000000
    6. write_ctrl(_FACE_SET_XFER)                      # feea200ab41130040000
    7. write_ctrl( set_face(slot) )                    # feea200619 <slot> — activate

This module is transport-independent: :func:`upload` drives any object implementing the
:class:`Uploader` protocol, so the whole sequence is unit-testable with a fake and no BLE.
"""
from __future__ import annotations

import asyncio
from typing import Callable, List, Optional, Protocol

CHUNK_SIZE = 512          # DaFup reads the image in 512-byte pieces
START_SETTLE_S = 0.5      # pause after announcing size, before streaming
CHUNK_DELAY_S = 0.3       # pacing between data chunks (the link drops if pushed faster)

# --- control-channel command prefixes (0xFEE2), verbatim from a proven transfer -----------
_FACE_START = bytes.fromhex("feea200974")            # + u32 image length (big-endian)
_FACE_FINISH = bytes.fromhex("feea20097400000000")   # start opcode, zero length = "done"
_FACE_SET_XFER = bytes.fromhex("feea200ab41130040000")
_BG_START = bytes.fromhex("feea20096e")              # + u32 image length (big-endian)
_BG_FINISH = bytes.fromhex("feea20096e00000000")
_BG_SET_XFER = bytes.fromhex("feea200529")
_SET_FACE = bytes.fromhex("feea200619")              # + u8 slot (1..6)

# --- notify-channel acks (0xFEE3) ---------------------------------------------------------
FACE_ACK = bytes.fromhex("feea200974ff")             # transfer complete (face)
BG_ACK = bytes.fromhex("feea20096eff")               # transfer complete (background)

FACE_SLOT = 6   # DaFup activates a custom face in slot 6
BG_SLOT = 1     # ... and a background in slot 1


class Uploader(Protocol):
    """Minimal transport interface :func:`upload` needs (see MoyoungClient)."""

    async def write_ctrl(self, data: bytes) -> None: ...
    async def write_data(self, data: bytes) -> None: ...
    async def wait_ack(self, ack: bytes, timeout: float) -> bool: ...


# ------------------------------------------------------------------ pure frame builders
def face_start(length: int) -> bytes:
    """Announce a face transfer of ``length`` bytes."""
    return _FACE_START + int(length).to_bytes(4, "big")


def bg_start(length: int) -> bytes:
    """Announce a background transfer of ``length`` bytes."""
    return _BG_START + int(length).to_bytes(4, "big")


def set_face(slot: int) -> bytes:
    """Activate the face/background in ``slot`` (1..6)."""
    if not 1 <= slot <= 6:
        raise ValueError(f"face slot must be 1..6, got {slot}")
    return _SET_FACE + slot.to_bytes(1, "big")


def chunk(data: bytes, size: int = CHUNK_SIZE) -> List[bytes]:
    """Split an image into transfer chunks (last chunk may be short; empty -> [])."""
    return [data[i:i + size] for i in range(0, len(data), size)]


# ------------------------------------------------------------------ orchestration
async def upload(
    kind: str,
    uploader: Uploader,
    data: bytes,
    *,
    slot: Optional[int] = None,
    chunk_size: int = CHUNK_SIZE,
    settle: float = START_SETTLE_S,
    chunk_delay: float = CHUNK_DELAY_S,
    ack_timeout: float = 15.0,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> bool:
    """Push ``data`` as a face (``kind="face"``) or background (``kind="bg"``).

    ``chunk_size`` is the BLE write size; the watch reassembles by the announced total
    length, so this only needs to fit the negotiated ATT MTU (see MoyoungClient, which
    picks a safe value — BlueZ often leaves the MTU at 23 and rejects large writes).

    Returns True if the device acked transfer completion, False if we timed out waiting
    (the finish/activate commands are still sent either way, matching DaFup behaviour).
    """
    if kind == "face":
        start, finish, set_xfer, ack, default_slot = (
            face_start, _FACE_FINISH, _FACE_SET_XFER, FACE_ACK, FACE_SLOT)
    elif kind == "bg":
        start, finish, set_xfer, ack, default_slot = (
            bg_start, _BG_FINISH, _BG_SET_XFER, BG_ACK, BG_SLOT)
    else:
        raise ValueError(f"kind must be 'face' or 'bg', got {kind!r}")
    if slot is None:
        slot = default_slot

    chunks = chunk(data, chunk_size)
    await uploader.write_ctrl(start(len(data)))
    await asyncio.sleep(settle)

    for i, piece in enumerate(chunks, 1):
        await uploader.write_data(piece)
        if on_progress is not None:
            on_progress(i, len(chunks))
        await asyncio.sleep(chunk_delay)

    acked = await uploader.wait_ack(ack, ack_timeout)

    await uploader.write_ctrl(finish)
    await uploader.write_ctrl(set_xfer)
    await uploader.write_ctrl(set_face(slot))
    return acked
