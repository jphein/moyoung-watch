"""Command-line interface for moyoung_client.

Offline commands (no watch needed): ``list-fields``, ``inspect``.
On-watch commands: ``scan``, ``info``, ``upload-face``, ``upload-bg``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Optional

from . import commands, facefmt, health, settings


# ----------------------------------------------------------------- address helper
async def _resolve_address(args) -> Optional[str]:
    from .transport import scan
    if args.address:
        return args.address
    print("No --address given; scanning for MoYoung (0xFEEA) watches ...", file=sys.stderr)
    found = await scan(timeout=args.scan_timeout)
    if not found:
        print("No MoYoung watch found. Is it awake and out of the Da Fit app?", file=sys.stderr)
        return None
    name, address, rssi = found[0]
    print(f"Using {name or '(no name)'} [{address}] rssi={rssi}", file=sys.stderr)
    return address


# ----------------------------------------------------------------- on-watch commands
async def cmd_scan(args) -> int:
    from .transport import scan
    found = await scan(timeout=args.scan_timeout, feea_only=not args.all)
    if not found:
        print("No matching devices.")
        return 1
    for name, address, rssi in found:
        print(f"{(name or '(no name)'):<24} {address:<20} {rssi if rssi is not None else '?'}")
    return 0


async def cmd_info(args) -> int:
    from .transport import MoyoungClient
    address = await _resolve_address(args)
    if not address:
        return 1
    async with MoyoungClient(address) as w:
        info = await w.read_info()
        for key in ("manufacturer", "model", "serial",
                    "firmware_rev", "hardware_rev", "software_rev"):
            print(f"{key:<14}: {info.get(key)}")
        print("services      :")
        for s in info.get("services", []):
            print(f"  {s}")
    return 0


def _print_progress(done: int, total: int) -> None:
    bar = int(28 * done / total) if total else 0
    sys.stderr.write(f"\r  [{'#' * bar}{'.' * (28 - bar)}] {done}/{total} chunks")
    sys.stderr.flush()
    if done == total:
        sys.stderr.write("\n")


async def _do_upload(args, kind: str) -> int:
    from .transport import MoyoungClient
    data = open(args.file, "rb").read()
    print(f"{args.file}: {len(data)} bytes")
    if kind == "face":
        try:
            face = facefmt.parse_header(data)
            print(f"  fileType {face.file_type}, faceNumber {face.face_number}, "
                  f"{face.data_count} fields: {', '.join(face.field_names())}")
        except ValueError as e:
            print(f"  (not a recognised face header: {e}) — uploading raw anyway")
    address = await _resolve_address(args)
    if not address:
        return 1
    async with MoyoungClient(address) as w:
        man = await w.verify_moyoung()
        print(f"Connected to {man} watch; uploading {kind} ...")
        upload = w.upload_face if kind == "face" else w.upload_bg
        kwargs = {"on_progress": _print_progress}
        if args.slot is not None:
            kwargs["slot"] = args.slot
        if getattr(args, "chunk_size", None):
            kwargs["chunk_size"] = args.chunk_size
        acked = await upload(data, **kwargs)
    print("Transfer complete." if acked else
          "Chunks sent, but no completion ack (activation still attempted).")
    return 0 if acked else 2


async def cmd_upload_face(args) -> int:
    return await _do_upload(args, "face")


async def cmd_upload_bg(args) -> int:
    return await _do_upload(args, "bg")


# ----------------------------------------------------------------- control / sync
async def _run_on_watch(args, fn) -> int:
    from .transport import MoyoungClient
    address = await _resolve_address(args)
    if not address:
        return 1
    async with MoyoungClient(address) as w:
        await w.verify_moyoung()
        return await fn(w)


async def cmd_set_time(args) -> int:
    async def go(w):
        await w.set_time()
        print("Watch clock set to local now.")
        return 0
    return await _run_on_watch(args, go)


async def cmd_notify(args) -> int:
    text = f"{args.text}:{args.body}" if args.body else args.text
    async def go(w):
        await w.notify(text, ntype=args.type)
        print(f"Sent notification: {text!r}")
        return 0
    return await _run_on_watch(args, go)


async def cmd_weather(args) -> int:
    async def go(w):
        await w.set_weather(args.temp, condition=args.condition, city=args.city)
        print(f"Pushed weather {args.temp}° ({args.condition}) — "
              f"shows on any WEATHER_TEMP field on the active face.")
        return 0
    return await _run_on_watch(args, go)


async def cmd_music(args) -> int:
    if args.track is None and args.artist is None:
        print("Give --track and/or --artist.")
        return 1
    async def go(w):
        await w.set_music(track=args.track, artist=args.artist)
        print("Pushed now-playing info.")
        return 0
    return await _run_on_watch(args, go)


async def cmd_find(args) -> int:
    async def go(w):
        await w.find_watch()
        print("Buzzing the watch.")
        return 0
    return await _run_on_watch(args, go)


async def cmd_set_goal(args) -> int:
    async def go(w):
        await w.set_goal_steps(args.steps)
        print(f"Daily step goal set to {args.steps}.")
        return 0
    return await _run_on_watch(args, go)


async def cmd_measure_hr(args) -> int:
    async def go(w):
        print("Measuring heart rate (hold still, up to 30s) ...")
        bpm = await w.measure_hr(timeout=args.timeout)
        print(f"Heart rate: {bpm} bpm" if bpm else "No reading (timed out).")
        return 0 if bpm else 2
    return await _run_on_watch(args, go)


async def cmd_steps(args) -> int:
    async def go(w):
        s = await w.read_steps()
        print(f"steps {s.get('steps')}  distance {s.get('distance')}  "
              f"calories {s.get('calories')}" if "steps" in s else f"raw: {s.get('raw')}")
        return 0
    return await _run_on_watch(args, go)


async def cmd_battery(args) -> int:
    async def go(w):
        print(f"Battery: {await w.read_battery()}%")
        return 0
    return await _run_on_watch(args, go)


# ----------------------------------------------------------------- full GB command surface
def _emit(obj) -> int:
    """Print a query/measurement result as JSON (datetimes stringified)."""
    print(json.dumps(obj, indent=2, default=str))
    return 0


def _hhmm(s: str):
    h, m = s.split(":")
    return int(h), int(m)


_ON_OFF = {"on": True, "off": False}


def _getter(method: str):
    async def cmd(args) -> int:
        async def go(w):
            res = await getattr(w, method)()
            return _emit(res)
        return await _run_on_watch(args, go)
    return cmd


def _bool_setter(method: str):
    async def cmd(args) -> int:
        async def go(w):
            await getattr(w, method)(_ON_OFF[args.state])
            print(f"{method} -> {args.state}")
            return 0
        return await _run_on_watch(args, go)
    return cmd


def _value_setter(method: str, cast=str):
    async def cmd(args) -> int:
        async def go(w):
            await getattr(w, method)(cast(args.value))
            print(f"{method} -> {args.value}")
            return 0
        return await _run_on_watch(args, go)
    return cmd


# -- composite setters --
async def cmd_set_quick_view_time(args) -> int:
    sh, sm = _hhmm(args.start); eh, em = _hhmm(args.end)
    async def go(w):
        await w.set_quick_view_time(sh, sm, eh, em)
        print(f"Quick-view window {args.start}-{args.end}")
        return 0
    return await _run_on_watch(args, go)


async def cmd_set_dnd(args) -> int:
    sh, sm = _hhmm(args.start); eh, em = _hhmm(args.end)
    async def go(w):
        await w.set_dnd(sh, sm, eh, em)
        print(f"Do-not-disturb {args.start}-{args.end}")
        return 0
    return await _run_on_watch(args, go)


async def cmd_set_reminders_to_move(args) -> int:
    async def go(w):
        await w.set_reminders_to_move(args.period, args.steps, args.start, args.end)
        print("Reminders-to-move set.")
        return 0
    return await _run_on_watch(args, go)


async def cmd_set_user_info(args) -> int:
    async def go(w):
        await w.set_user_info(args.height, args.weight, args.age, args.sex)
        print("User info set.")
        return 0
    return await _run_on_watch(args, go)


async def cmd_set_step_length(args) -> int:
    async def go(w):
        await w.set_step_length(args.cm)
        print(f"Step length {args.cm} cm.")
        return 0
    return await _run_on_watch(args, go)


async def cmd_set_display_functions(args) -> int:
    async def go(w):
        await w.set_display_functions(args.ids)
        print(f"Enabled screens: {args.ids}")
        return 0
    return await _run_on_watch(args, go)


async def cmd_set_watch_face_layout(args) -> int:
    async def go(w):
        await w.set_watch_face_layout(args.time_position, args.time_top, args.time_bottom,
                                      args.color, args.bg_md5)
        print("Watch-face layout set.")
        return 0
    return await _run_on_watch(args, go)


async def cmd_set_alarm(args) -> int:
    h, m = _hhmm(args.time)
    days = args.days.split(",") if args.days else None
    year = month = day = None
    if args.date:
        y, mo, d = args.date.split("-"); year, month, day = int(y), int(mo), int(d)
    async def go(w):
        await w.set_alarm(args.index, h, m, enabled=not args.disabled, days=days,
                          year=year, month=(month or 1), day=(day or 1))
        print(f"Alarm {args.index} set for {args.time}.")
        return 0
    return await _run_on_watch(args, go)


async def cmd_set_menstrual(args) -> int:
    async def go(w):
        await w.set_psychological_period(args.physiological, args.menstrual,
                                         args.start_month, args.start_date,
                                         args.reminder_h, args.reminder_m)
        print("Menstrual/psychological period set.")
        return 0
    return await _run_on_watch(args, go)


# -- health measurements --
async def cmd_blood_pressure(args) -> int:
    async def go(w):
        print("Measuring blood pressure (hold still) ...", file=sys.stderr)
        res = await w.measure_blood_pressure(timeout=args.timeout)
        return _emit(res) if res else 2
    return await _run_on_watch(args, go)


async def cmd_spo2(args) -> int:
    async def go(w):
        print("Measuring SpO2 (hold still) ...", file=sys.stderr)
        res = await w.measure_spo2(timeout=args.timeout)
        return _emit({"spo2_percent": res}) if res is not None else 2
    return await _run_on_watch(args, go)


async def cmd_ecg(args) -> int:
    async def go(w):
        await w.ecg(args.mode)
        print(f"ECG {args.mode} (waveform arrives on a channel this device lacks).")
        return 0
    return await _run_on_watch(args, go)


async def cmd_dynamic_hr(args) -> int:
    async def go(w):
        await w.dynamic_hr(args.state == "start")
        print(f"Dynamic HR {args.state}.")
        return 0
    return await _run_on_watch(args, go)


async def cmd_sync_sleep(args) -> int:
    return await _run_on_watch(args, lambda w: _sync(w.sync_sleep()))


async def cmd_sync_past(args) -> int:
    async def go(w):
        return _emit(await w.sync_past(args.which))
    return await _run_on_watch(args, go)


async def cmd_steps_category(args) -> int:
    async def go(w):
        return _emit(await w.steps_category(index=args.index))
    return await _run_on_watch(args, go)


async def cmd_movement_hr(args) -> int:
    async def go(w):
        return _emit(await w.movement_hr())
    return await _run_on_watch(args, go)


async def cmd_past_hr(args) -> int:
    async def go(w):
        return _emit(await w.past_heart_rate(index=args.index))
    return await _run_on_watch(args, go)


async def cmd_sleep_action(args) -> int:
    async def go(w):
        return _emit(await w.sleep_action(args.index))
    return await _run_on_watch(args, go)


async def _sync(coro):
    return _emit(await coro)


# -- interaction / weather --
async def cmd_music_state(args) -> int:
    async def go(w):
        await w.set_music_state(args.state == "play")
        print(f"Music state -> {args.state}")
        return 0
    return await _run_on_watch(args, go)


async def cmd_weather_location(args) -> int:
    async def go(w):
        await w.set_weather_location(args.location)
        print(f"Weather location -> {args.location!r}")
        return 0
    return await _run_on_watch(args, go)


async def cmd_weather_forecast(args) -> int:
    forecasts = None
    if args.day:
        forecasts = [tuple(int(x) for x in d.split(",")) for d in args.day]
    async def go(w):
        await w.set_weather_forecast(args.today_condition, args.today_temp, forecasts)
        print("Pushed 7-day forecast.")
        return 0
    return await _run_on_watch(args, go)


async def cmd_sunrise_sunset(args) -> int:
    srh, srm = _hhmm(args.sunrise); ssh, ssm = _hhmm(args.sunset)
    async def go(w):
        await w.set_sunrise_sunset(srh, srm, ssh, ssm, location=args.location)
        print(f"Sunrise {args.sunrise} / sunset {args.sunset}.")
        return 0
    return await _run_on_watch(args, go)


async def cmd_send_volume(args) -> int:
    async def go(w):
        await w.send_volume(args.level)
        print(f"Reported volume {args.level}/16.")
        return 0
    return await _run_on_watch(args, go)


async def cmd_shutdown(args) -> int:
    async def go(w):
        await w.shutdown()
        print("Sent shutdown.")
        return 0
    return await _run_on_watch(args, go)


def _brick_refuse() -> int:
    print("Refusing: firmware/DFU operation with BRICK RISK.\n"
          "Re-run with --i-understand-brick-risk only if you have a recovery path.",
          file=sys.stderr)
    return 2


async def cmd_enable_dfu(args) -> int:
    if not args.i_understand_brick_risk:
        return _brick_refuse()
    async def go(w):
        await w.enable_dfu(i_understand_brick_risk=True)
        print("Sent enable-DFU ({1}). The watch should enter its OTA bootloader.")
        return 0
    return await _run_on_watch(args, go)


async def cmd_query_dfu_address(args) -> int:
    if not args.i_understand_brick_risk:
        return _brick_refuse()
    async def go(w):
        res = await w.query_dfu_address(i_understand_brick_risk=True)
        if res is None:
            print("No DFU-address reply (watch may not answer this opcode).", file=sys.stderr)
            return 2
        return _emit(res)
    return await _run_on_watch(args, go)


async def cmd_dfu_status(args) -> int:
    async def go(w):
        return _emit(await w.dfu_status())
    return await _run_on_watch(args, go)


async def cmd_ota_flash(args) -> int:
    if not args.i_understand_brick_risk:
        return _brick_refuse()
    data = open(args.file, "rb").read()
    print(f"{args.file}: {len(data)} bytes", file=sys.stderr)
    async def go(w):
        try:
            acked = await w.ota_flash(data, i_understand_brick_risk=True,
                                      expected_md5=args.md5, on_progress=_print_progress)
            print("OTA flash complete." if acked else "OTA finished without an ack.")
            return 0 if acked else 2
        except NotImplementedError as e:
            print("\nOTA image-upload transport is NOT reverse-engineered yet — this is the "
                  "honest gap.\nThe enable-DFU + query-DFU-address handshake above DID run "
                  "(useful for an RE capture).\n" + str(e), file=sys.stderr)
            return 3
    return await _run_on_watch(args, go)


async def cmd_listen(args) -> int:
    from .transport import MoyoungClient
    address = await _resolve_address(args)
    if not address:
        return 1
    async with MoyoungClient(address) as w:
        await w.verify_moyoung()
        print("Listening for watch events (camera / find-phone / media / measurements) ...",
              file=sys.stderr)
        async for ev in w.listen_events(timeout=args.timeout):
            print(json.dumps({"kind": ev.kind, "cmd": ev.cmd, "data": ev.data,
                              "hex": ev.payload.hex()}, default=str))
    return 0


# tables consumed by build_parser (subcmd, client-method[, cast])
_GET_COMMANDS = [
    ("get-time-system", "get_time_system"), ("get-units", "get_units"),
    ("get-language", "get_language"), ("get-device-version", "get_device_version"),
    ("get-dominant-hand", "get_dominant_hand"), ("get-quick-view", "get_quick_view"),
    ("get-quick-view-time", "get_quick_view_time"), ("get-dnd", "get_dnd_time"),
    ("get-sedentary", "get_sedentary"), ("get-reminders-to-move", "get_reminders_to_move"),
    ("get-other-message", "get_other_message"), ("get-breathing-light", "get_breathing_light"),
    ("get-goal", "get_goal_steps"), ("get-watch-face", "get_watch_face"),
    ("get-watch-face-layout", "get_watch_face_layout"),
    ("get-support-watch-face", "get_support_watch_face"),
    ("get-display-functions", "get_display_functions"), ("get-alarms", "get_alarms"),
]
_BOOL_COMMANDS = [
    ("set-quick-view", "set_quick_view"), ("set-sedentary", "set_sedentary"),
    ("set-other-message", "set_other_message"), ("set-breathing-light", "set_breathing_light"),
    ("set-power-saving", "set_power_saving"),
]
# (subcmd, method, choices-or-None, cast)
_VALUE_COMMANDS = [
    ("set-time-system", "set_time_system", ["12", "24"], str),
    ("set-units", "set_units", ["metric", "imperial"], str),
    ("set-language", "set_language", sorted(commands.LANGUAGES), str),
    ("set-device-version", "set_device_version", ["chinese", "international"], str),
    ("set-dominant-hand", "set_dominant_hand", ["left", "right"], str),
    ("set-watch-face", "set_watch_face", None, int),
    ("set-hr-interval", "set_hr_interval", sorted(settings.HR_INTERVALS), str),
]


# ----------------------------------------------------------------- offline commands
def cmd_list_fields(args) -> int:
    print("Embeddable watch-face fields (MoYoung / Da Fit format)\n")
    grouped = facefmt.fields_by_group()
    for group in facefmt.GROUP_ORDER:
        fts = grouped.get(group) or []
        if not fts:
            continue
        print(f"== {group} ==")
        for ft in fts:
            print(f"  0x{ft.code:02x}  {ft.name:<16} x{ft.count:<2}  {ft.desc}")
        print()
    print(f"{len(facefmt.FIELDS)} field types total.")
    return 0


def cmd_inspect(args) -> int:
    data = open(args.file, "rb").read()
    face = facefmt.parse_header(data)
    print(f"file        : {args.file} ({len(data)} bytes)")
    print(f"fileID      : 0x{face.file_id:02x}  (Type {face.file_type})")
    print(f"faceNumber  : {face.face_number}")
    print(f"dataCount   : {face.data_count}   blobCount: {face.blob_count}")
    print("fields      :")
    for e in face.entries:
        f = e.field
        label = f.name if f else f"UNKNOWN(0x{e.code:02x})"
        print(f"  0x{e.code:02x}  {label:<16} @({e.x},{e.y}) {e.w}x{e.h}  blob#{e.oidx}")
    return 0


# ----------------------------------------------------------------- arg parsing
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="moyoung",
                                description="BLE client for MoYoung-v2 / Da Fit watches.")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    p.add_argument("--address", help="watch BLE address (skip scan)")
    p.add_argument("--scan-timeout", type=float, default=8.0, help="scan seconds (default 8)")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("scan", help="scan for advertising MoYoung watches")
    sp.add_argument("--all", action="store_true", help="list every BLE device, not just 0xFEEA")
    sp.set_defaults(func=cmd_scan, is_async=True)

    sp = sub.add_parser("info", help="connect and print device info + GATT services")
    sp.set_defaults(func=cmd_info, is_async=True)

    sp = sub.add_parser("list-fields", help="print every embeddable face field (offline)")
    sp.set_defaults(func=cmd_list_fields, is_async=False)

    sp = sub.add_parser("inspect", help="parse a face .bin and list its embedded fields (offline)")
    sp.add_argument("file", help="path to a MoYoung face .bin")
    sp.set_defaults(func=cmd_inspect, is_async=False)

    sp = sub.add_parser("upload-face", help="flash a watch-face .bin to the watch")
    sp.add_argument("file", help="path to a MoYoung face .bin")
    sp.add_argument("--slot", type=int, default=None, help="face slot 1..6 (default 6)")
    sp.add_argument("--chunk-size", type=int, default=None,
                    help="BLE write size override (default: auto from MTU, ~244)")
    sp.set_defaults(func=cmd_upload_face, is_async=True)

    sp = sub.add_parser("upload-bg", help="flash a background image .bin to the watch")
    sp.add_argument("file", help="path to a MoYoung background .bin")
    sp.add_argument("--slot", type=int, default=None, help="background slot 1..6 (default 1)")
    sp.add_argument("--chunk-size", type=int, default=None,
                    help="BLE write size override (default: auto from MTU, ~244)")
    sp.set_defaults(func=cmd_upload_bg, is_async=True)

    # -- control / injection --
    sp = sub.add_parser("set-time", help="set the watch clock to local now")
    sp.set_defaults(func=cmd_set_time, is_async=True)

    sp = sub.add_parser("notify", help="push a screen notification (injects arbitrary text)")
    sp.add_argument("text", help="notification title (or use --body for a title/body split)")
    sp.add_argument("--body", help="notification body")
    sp.add_argument("--type", type=int, default=commands.NOTIFY_OTHER,
                    help="notification type id (default 11=other, 0=call)")
    sp.set_defaults(func=cmd_notify, is_async=True)

    sp = sub.add_parser("weather", help="push weather; temp shows on any WEATHER_TEMP face field")
    sp.add_argument("--temp", type=int, required=True, help="temperature, signed (-128..127)")
    sp.add_argument("--condition", default="sunny", choices=list(commands.WEATHER_CONDITIONS),
                    help="condition icon (default sunny)")
    sp.add_argument("--city", default="", help="city label, <=4 chars")
    sp.set_defaults(func=cmd_weather, is_async=True)

    sp = sub.add_parser("music", help="push now-playing text (injects arbitrary text)")
    sp.add_argument("--track", help="track title")
    sp.add_argument("--artist", help="artist name")
    sp.set_defaults(func=cmd_music, is_async=True)

    sp = sub.add_parser("find", help="buzz/ring the watch")
    sp.set_defaults(func=cmd_find, is_async=True)

    sp = sub.add_parser("set-goal", help="set the daily step goal")
    sp.add_argument("steps", type=int, help="step goal")
    sp.set_defaults(func=cmd_set_goal, is_async=True)

    sp = sub.add_parser("measure-hr", help="trigger a live heart-rate measurement")
    sp.add_argument("--timeout", type=float, default=30.0, help="wait seconds (default 30)")
    sp.set_defaults(func=cmd_measure_hr, is_async=True)

    sp = sub.add_parser("steps", help="read the live pedometer (distance/steps/calories)")
    sp.set_defaults(func=cmd_steps, is_async=True)

    sp = sub.add_parser("battery", help="read the battery level")
    sp.set_defaults(func=cmd_battery, is_async=True)

    # -- settings: table-driven bool / value / query commands --
    for name, method in _BOOL_COMMANDS:
        sp = sub.add_parser(name, help=f"{name.replace('-', ' ')} (on/off)")
        sp.add_argument("state", choices=["on", "off"])
        sp.set_defaults(func=_bool_setter(method), is_async=True)
    for name, method, choices, cast in _VALUE_COMMANDS:
        sp = sub.add_parser(name, help=name.replace("-", " "))
        if choices:
            sp.add_argument("value", choices=choices)
        else:
            sp.add_argument("value")
        sp.set_defaults(func=_value_setter(method, cast), is_async=True)
    for name, method in _GET_COMMANDS:
        sp = sub.add_parser(name, help=f"query: {name.replace('get-', '')}")
        sp.set_defaults(func=_getter(method), is_async=True)

    # -- settings: composite --
    sp = sub.add_parser("set-quick-view-time", help="raise-to-wake schedule window")
    sp.add_argument("start", help="HH:MM"); sp.add_argument("end", help="HH:MM")
    sp.set_defaults(func=cmd_set_quick_view_time, is_async=True)

    sp = sub.add_parser("set-dnd", help="do-not-disturb window (00:00 00:00 disables)")
    sp.add_argument("start", help="HH:MM"); sp.add_argument("end", help="HH:MM")
    sp.set_defaults(func=cmd_set_dnd, is_async=True)

    sp = sub.add_parser("set-reminders-to-move", help="sedentary reminder period")
    sp.add_argument("--period", type=int, default=30); sp.add_argument("--steps", type=int, default=100)
    sp.add_argument("--start", type=int, default=10); sp.add_argument("--end", type=int, default=22)
    sp.set_defaults(func=cmd_set_reminders_to_move, is_async=True)

    sp = sub.add_parser("set-user-info", help="height/weight/age/sex")
    sp.add_argument("--height", type=int, required=True, help="cm")
    sp.add_argument("--weight", type=int, required=True, help="kg")
    sp.add_argument("--age", type=int, required=True)
    sp.add_argument("--sex", choices=["male", "female"], default="male")
    sp.set_defaults(func=cmd_set_user_info, is_async=True)

    sp = sub.add_parser("set-step-length", help="stride length in cm")
    sp.add_argument("cm", type=int)
    sp.set_defaults(func=cmd_set_step_length, is_async=True)

    sp = sub.add_parser("set-display-functions", help="null-terminated enabled-screen ids")
    sp.add_argument("ids", type=int, nargs="+")
    sp.set_defaults(func=cmd_set_display_functions, is_async=True)

    sp = sub.add_parser("set-watch-face-layout", help="custom watch-face layout")
    sp.add_argument("--time-position", type=int, default=0)
    sp.add_argument("--time-top", type=int, default=0)
    sp.add_argument("--time-bottom", type=int, default=0)
    sp.add_argument("--color", type=int, default=0xFFFF, help="R5G6B5 (0..65535)")
    sp.add_argument("--bg-md5", required=True, help="32-char hex md5 of the background")
    sp.set_defaults(func=cmd_set_watch_face_layout, is_async=True)

    sp = sub.add_parser("set-alarm", help="set an alarm slot (legacy 8-byte form)")
    sp.add_argument("index", type=int); sp.add_argument("time", help="HH:MM")
    sp.add_argument("--days", help="comma weekdays sun,mon,...,sat (omit = one-shot)")
    sp.add_argument("--date", help="YYYY-MM-DD for a one-shot alarm")
    sp.add_argument("--disabled", action="store_true")
    sp.set_defaults(func=cmd_set_alarm, is_async=True)

    sp = sub.add_parser("set-menstrual", help="menstrual/psychological period (untested*)")
    sp.add_argument("--physiological", type=int, required=True)
    sp.add_argument("--menstrual", type=int, required=True)
    sp.add_argument("--start-month", type=int, required=True, help="0-based, like GB")
    sp.add_argument("--start-date", type=int, required=True)
    sp.add_argument("--reminder-h", type=int, default=8); sp.add_argument("--reminder-m", type=int, default=0)
    sp.set_defaults(func=cmd_set_menstrual, is_async=True)

    sp = sub.add_parser("gsensor-calibrate", help="calibrate the accelerometer")
    sp.set_defaults(func=_getter("gsensor_calibrate"), is_async=True)
    sp = sub.add_parser("return-home", help="send the watch to its home screen")
    sp.set_defaults(func=_getter("return_home"), is_async=True)

    # -- health --
    sp = sub.add_parser("blood-pressure", help="trigger a blood-pressure measurement")
    sp.add_argument("--timeout", type=float, default=30.0)
    sp.set_defaults(func=cmd_blood_pressure, is_async=True)
    sp = sub.add_parser("spo2", help="trigger a blood-oxygen measurement")
    sp.add_argument("--timeout", type=float, default=30.0)
    sp.set_defaults(func=cmd_spo2, is_async=True)
    sp = sub.add_parser("ecg", help="ECG trigger (no waveform channel on this device)")
    sp.add_argument("mode", choices=["start", "stop", "query"], nargs="?", default="start")
    sp.set_defaults(func=cmd_ecg, is_async=True)
    sp = sub.add_parser("dynamic-hr", help="start/stop the continuous HR stream")
    sp.add_argument("state", choices=["start", "stop"])
    sp.set_defaults(func=cmd_dynamic_hr, is_async=True)
    sp = sub.add_parser("sync-sleep", help="read today's sleep stages")
    sp.set_defaults(func=cmd_sync_sleep, is_async=True)
    sp = sub.add_parser("sync-past", help="read stored steps/sleep for a past day")
    sp.add_argument("which", choices=sorted(health.SYNC_PAST))
    sp.set_defaults(func=cmd_sync_past, is_async=True)
    sp = sub.add_parser("steps-category", help="hourly step buckets (0=today, 2=yesterday)")
    sp.add_argument("--index", type=int, default=0)
    sp.set_defaults(func=cmd_steps_category, is_async=True)
    sp = sub.add_parser("movement-hr", help="last 3 workout summaries")
    sp.set_defaults(func=cmd_movement_hr, is_async=True)
    sp = sub.add_parser("past-hr", help="5-minute HR history (index 0..7)")
    sp.add_argument("--index", type=int, default=0)
    sp.set_defaults(func=cmd_past_hr, is_async=True)
    sp = sub.add_parser("sleep-action", help="sleep-action detail for an index")
    sp.add_argument("index", type=int)
    sp.set_defaults(func=cmd_sleep_action, is_async=True)

    # -- interaction / weather --
    sp = sub.add_parser("music-state", help="tell the watch play/pause state")
    sp.add_argument("state", choices=["play", "pause"])
    sp.set_defaults(func=cmd_music_state, is_async=True)
    sp = sub.add_parser("weather-location", help="set the weather location string")
    sp.add_argument("location")
    sp.set_defaults(func=cmd_weather_location, is_async=True)
    sp = sub.add_parser("weather-forecast", help="push a 7-day forecast")
    sp.add_argument("today_temp", type=int)
    sp.add_argument("--today-condition", type=int, default=5, help="condition id 0..7 (5=sunny)")
    sp.add_argument("--day", action="append", help="'cond,high,low' (repeat up to 7x)")
    sp.set_defaults(func=cmd_weather_forecast, is_async=True)
    sp = sub.add_parser("sunrise-sunset", help="push sunrise/sunset times")
    sp.add_argument("sunrise", help="HH:MM"); sp.add_argument("sunset", help="HH:MM")
    sp.add_argument("--location", default="")
    sp.set_defaults(func=cmd_sunrise_sunset, is_async=True)
    sp = sub.add_parser("send-volume", help="report phone volume (0..16) to the watch")
    sp.add_argument("level", type=int)
    sp.set_defaults(func=cmd_send_volume, is_async=True)
    sp = sub.add_parser("camera-open", help="open the camera-remote screen on the watch")
    sp.set_defaults(func=_getter("camera_open"), is_async=True)
    sp = sub.add_parser("find-phone-stop", help="stop the watch's find-phone alert")
    sp.set_defaults(func=_getter("find_phone_stop"), is_async=True)
    sp = sub.add_parser("shutdown", help="power the watch off")
    sp.set_defaults(func=cmd_shutdown, is_async=True)

    # -- events --
    sp = sub.add_parser("listen", help="decode incoming watch events (camera/find-phone/media)")
    sp.add_argument("--timeout", type=float, default=None, help="stop after N seconds")
    sp.set_defaults(func=cmd_listen, is_async=True)

    # -- firmware / DFU / OTA (BRICK RISK — gated) --
    sp = sub.add_parser("dfu-status", help="report DFU capability (model-number-string heuristic)")
    sp.set_defaults(func=cmd_dfu_status, is_async=True)
    sp = sub.add_parser("enable-dfu", help="enter the OTA/DFU bootloader — BRICK RISK")
    sp.add_argument("--i-understand-brick-risk", action="store_true")
    sp.set_defaults(func=cmd_enable_dfu, is_async=True)
    sp = sub.add_parser("query-dfu-address", help="ask the watch for its DFU address — BRICK RISK")
    sp.add_argument("--i-understand-brick-risk", action="store_true")
    sp.set_defaults(func=cmd_query_dfu_address, is_async=True)
    sp = sub.add_parser("ota-flash",
                        help="flash a firmware image — BRICK RISK; upload transport not yet RE'd")
    sp.add_argument("file", help="path to a firmware .bin")
    sp.add_argument("--md5", help="expected md5 of the image (verified before flashing)")
    sp.add_argument("--i-understand-brick-risk", action="store_true")
    sp.set_defaults(func=cmd_ota_flash, is_async=True)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(name)s %(levelname)s %(message)s")
    if getattr(args, "is_async", False):
        return asyncio.run(args.func(args))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
