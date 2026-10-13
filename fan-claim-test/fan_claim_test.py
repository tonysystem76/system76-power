#!/usr/bin/env python3
"""Manual test client for ClaimFanControl / ReleaseFanControl.

Test-branch only; not part of the upstream PR. Run as root on a Thelio
running the patched daemon. See README.md next to this file.
"""

import argparse
import glob
import os
import select
import sys
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

BUS_NAME = "com.system76.PowerDaemon"
OBJECT_PATH = "/com/system76/PowerDaemon"
INTERFACE = "com.system76.PowerDaemon"
FAN_HWMON_NAMES = ("system76_io", "system76_thelio_io")


def fan_hwmons():
    found = []
    for name_file in sorted(glob.glob("/sys/class/hwmon/hwmon*/name")):
        with open(name_file) as f:
            if f.read().strip() in FAN_HWMON_NAMES:
                found.append(os.path.dirname(name_file))
    return found


def read_attr(hwmon, attr):
    try:
        with open(os.path.join(hwmon, attr)) as f:
            return f.read().strip()
    except OSError:
        return "-"


def print_status():
    hwmons = fan_hwmons()
    if not hwmons:
        print("  no system76_io / system76_thelio_io hwmon found")
    for hwmon in hwmons:
        pwms = " ".join(f"pwm{i}={read_attr(hwmon, f'pwm{i}')}" for i in range(1, 5))
        print(f"  {hwmon}: pwm1_enable={read_attr(hwmon, 'pwm1_enable')} {pwms}", flush=True)


def write_pwm(duty):
    for hwmon in fan_hwmons():
        # Thelio Io boards (system76_thelio_io) have no pwm1_enable file.
        enable = os.path.join(hwmon, "pwm1_enable")
        if os.path.exists(enable):
            try:
                with open(enable, "w") as f:
                    f.write("1")
            except OSError as err:
                print(f"  could not write {enable}: {err}")
        for i in range(1, 5):
            path = os.path.join(hwmon, f"pwm{i}")
            if not os.path.exists(path):
                continue
            try:
                with open(path, "w") as f:
                    f.write(str(duty))
            except OSError as err:
                print(f"  could not write {path}: {err}")


def connect():
    """A new private system-bus connection with its own unique name."""
    address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SYSTEM, None)
    flags = (
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
    )
    return Gio.DBusConnection.new_for_address_sync(address, flags, None, None)


def call(conn, method):
    """Call a no-argument method. Returns None on success or the error message."""
    try:
        conn.call_sync(
            BUS_NAME, OBJECT_PATH, INTERFACE, method,
            None, None, Gio.DBusCallFlags.NONE, -1, None,
        )
        return None
    except GLib.Error as err:
        return err.message


def cmd_status(_args):
    print_status()


def cmd_hold(args):
    conn = connect()
    print(f"pid {os.getpid()}, bus name {conn.get_unique_name()}")
    error = call(conn, "ClaimFanControl")
    if error:
        print(f"claim REJECTED: {error}")
        return 1
    print("claim accepted")

    if args.pwm is not None:
        write_pwm(args.pwm)
        print(f"set pwm1-4={args.pwm}")

    print("holding; press Enter to finish, or kill this pid from another terminal")
    try:
        while True:
            print_status()
            ready, _, _ = select.select([sys.stdin], [], [], 2.0)
            if ready:
                sys.stdin.readline()
                break
    except KeyboardInterrupt:
        print()

    if args.no_release:
        print("exiting WITHOUT ReleaseFanControl (daemon should auto-release)")
        return 0

    error = call(conn, "ReleaseFanControl")
    print(f"release REJECTED: {error}" if error else "release accepted")
    time.sleep(1.5)
    print_status()
    return 0


def cmd_contend(_args):
    conn = connect()
    print(f"bus name {conn.get_unique_name()}")
    error = call(conn, "ClaimFanControl")
    if error:
        print(f"claim REJECTED: {error}")
        return 0
    print("claim accepted; releasing")
    error = call(conn, "ReleaseFanControl")
    print(f"release REJECTED: {error}" if error else "release accepted")
    return 0


def cmd_fire_and_forget(_args):
    conn = connect()
    message = Gio.DBusMessage.new_method_call(BUS_NAME, OBJECT_PATH, INTERFACE, "ClaimFanControl")
    message.set_flags(Gio.DBusMessageFlags.NO_REPLY_EXPECTED)
    conn.send_message(message, Gio.DBusSendMessageFlags.NONE)
    conn.flush_sync(None)
    print(f"sent ClaimFanControl from {conn.get_unique_name()} and disconnected without waiting")
    print("now run 'contend': its claim should be accepted")
    conn.close_sync(None)
    return 0


def cmd_churn(args):
    start = time.monotonic()
    for _ in range(args.count):
        connect().close_sync(None)
    print(f"opened and closed {args.count} connections in {time.monotonic() - start:.1f}s")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show fan pwm values").set_defaults(func=cmd_status)

    hold = sub.add_parser("hold", help="claim on one connection and keep it open")
    hold.add_argument("--pwm", type=int, help="after claiming, set pwm1-4 to this (0-255)")
    hold.add_argument("--no-release", action="store_true", help="exit without releasing")
    hold.set_defaults(func=cmd_hold)

    contend = sub.add_parser("contend", help="claim from a new connection, then release")
    contend.set_defaults(func=cmd_contend)

    fire = sub.add_parser("fire-and-forget", help="send a claim and disconnect at once")
    fire.set_defaults(func=cmd_fire_and_forget)

    churn = sub.add_parser("churn", help="open and close many bus connections")
    churn.add_argument("--count", type=int, default=500)
    churn.set_defaults(func=cmd_churn)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
