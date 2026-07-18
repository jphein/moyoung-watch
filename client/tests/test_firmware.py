"""Tests for firmware/DFU/OTA scaffolding (offline). The upload transport is an honest stub."""
import asyncio
import hashlib

import pytest

from moyoung_client import firmware as fw


# ------------------------------------------------------------------ DFU trigger payloads
def test_dfu_trigger_payloads():
    assert fw.enable_dfu_payload() == b"\x01"        # enableHsDfu()
    assert fw.query_dfu_address_payload() == b"\x00"  # queryHsDfuAddress()


def test_decode_dfu_address_is_honest_raw():
    got = fw.decode_dfu_address(bytes([0xDE, 0xAD, 0xBE, 0xEF]))
    assert got["raw_hex"] == "deadbeef" and got["length"] == 4
    assert got["maybe_u32_le"] == 0xEFBEADDE and got["maybe_u32_be"] == 0xDEADBEEF
    assert "not reverse-engineered" in got["note"]
    six = fw.decode_dfu_address(bytes([1, 2, 3, 4, 5, 6]))
    assert six["maybe_bd_addr"] == "06:05:04:03:02:01"
    weird = fw.decode_dfu_address(b"\x01\x02\x03")
    assert weird["length"] == 3 and "maybe_bd_addr" not in weird


# ------------------------------------------------------------------ DFU status (packet 19)
def test_dfu_status_from_model():
    assert fw.dfu_status_from_model("MOY-ERJ3")["dfu_capable"] is False
    cap = fw.dfu_status_from_model("MOY-DFU1")
    assert cap["dfu_marker"] is True and cap["dfu_number"] == 1 and cap["dfu_capable"] is True
    off = fw.dfu_status_from_model("SOMEDFU0")
    assert off["dfu_number"] == 0 and off["dfu_capable"] is False
    bare = fw.dfu_status_from_model("device DFU mode")
    assert bare["dfu_marker"] is True and bare["dfu_number"] is None and bare["dfu_capable"] is True
    none = fw.dfu_status_from_model(None)
    assert none["dfu_capable"] is False and none["model"] == ""


# ------------------------------------------------------------------ md5 verify hook
def test_firmware_md5_and_verify():
    data = b"firmware-bytes"
    digest = hashlib.md5(data).hexdigest()
    assert fw.firmware_md5(data) == digest
    assert fw.verify_firmware_md5(data, digest) is True
    assert fw.verify_firmware_md5(data, digest.upper()) is True     # case-insensitive
    assert fw.verify_firmware_md5(data, "0" * 32) is False


# ------------------------------------------------------------------ the RE gap
def test_upload_firmware_image_is_not_implemented():
    with pytest.raises(NotImplementedError):
        asyncio.run(fw.upload_firmware_image(None, b"\x00\x01\x02"))
    # the reason string is kept in one place and referenced by the docs
    assert "not reverse-engineered" in fw.OTA_TRANSPORT_TODO
