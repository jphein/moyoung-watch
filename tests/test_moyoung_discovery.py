"""Regression guard for MoYoung BLE auto-discovery of units that DON'T advertise the feea
service UUID (e.g. TG38 / MOY-ERJ3).

Root cause of the "add device flow" bug (2026-07-18): the manifest matched ONLY
``service_uuid: 0000feea``, but a fresh TG38 advertises a ``local_name`` ("TG38") and NO feea
UUID — so HA never fired a discovery flow and the watch had to be added by MAC. Fix = a
``local_name`` matcher in the manifest + a name fallback in ``config_flow._is_moyoung``.

HA-free: checks the const list, the manifest matchers, and that the name fallback is wired
(config_flow imports HA at module top, so we assert on its source rather than importing it)."""
import json
import pathlib
import sys

_MOYOUNG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "moyoung"
sys.path.insert(0, str(_MOYOUNG))

import const  # noqa: E402  (pure constants, no HA import)


def test_tg38_is_a_known_local_name():
    assert "TG38" in const.MOYOUNG_LOCAL_NAMES


def test_manifest_matches_both_feea_and_local_name():
    m = json.loads((_MOYOUNG / "manifest.json").read_text())
    matchers = m["bluetooth"]
    # the original service-uuid matcher must remain (watches that DO advertise feea)
    assert any(x.get("service_uuid") == const.FEEA_SERVICE for x in matchers), \
        "feea service_uuid matcher must remain"
    # and every known local_name must have a matcher (so name-only units auto-discover)
    for name in const.MOYOUNG_LOCAL_NAMES:
        assert any(x.get("local_name") == name for x in matchers), \
            f"manifest missing local_name matcher for {name!r}"


def test_is_moyoung_has_the_name_fallback_wired():
    src = (_MOYOUNG / "config_flow.py").read_text()
    # the name fallback must reference the shared const (not just the feea service check)
    assert "MOYOUNG_LOCAL_NAMES" in src, "config_flow._is_moyoung must use MOYOUNG_LOCAL_NAMES"
