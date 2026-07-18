"""moyoung_client — talk to MoYoung-v2 / Da Fit watches over BLE.

The MoYoung-v2 family (Realtek RTL8762-class SoC; manufacturer characteristic reads
``MOYOUNG-V2``) exposes a command/data service ``0xFEEA``. This package speaks that
service well enough to push a custom watch-face or background image — the one
"DaFlasher-like" thing that is actually possible on this chip (firmware stays stock;
its OTA is Realtek's closed path). Verified on-glass against a MOY-ERJ3-2.0.7 unit.

Protocol reimplemented from the byte sequences proven by VicGuy/DaFup (GPLv3) and the
face binary format documented by david47k/dawft — opcodes/offsets are interoperability
facts, no upstream code copied.
"""

__version__ = "0.1.0"
