#!/usr/bin/env python3
"""Run every fan control claim test on a Thelio and log the results.

Test-branch only; not part of the upstream PR. Run from the system76-power
folder:

    sudo python3 fan-claim-test/run_all.py

Results are written to fan-claim-test/results/<date-time>/.
"""

import argparse
import csv
import datetime
import hashlib
import math
import glob
import os
import shutil
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CLIENT = os.path.join(HERE, "fan_claim_test.py")
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True  # no root-owned __pycache__ in the repo

import fan_claim_test as client  # noqa: E402
from gi.repository import Gio, GLib  # noqa: E402

SERVICE = "com.system76.PowerDaemon"
MARK = 255  # value the tests write; far from the idle curve (0 or 76)
REVERT_TIMEOUT = 3.0  # the curve runs once a second
HOLD_SECONDS = 15
CPU_HWMON_NAMES = ("k10temp", "coretemp")
FIRMWARE_LOAD_SECONDS = 90  # test 12: how long to load the CPU with system76-power stopped
FIRMWARE_TEMP_LIMIT = 90.0  # test 12: stop the load early at this CPU temperature (C)
WATCHDOG_VALUE = 76  # test 13: a low speed (30%) so a board takeover is obvious
WATCHDOG_SECONDS = 30  # test 13: how long to watch each part
WATCHDOG_IDLE_TEMP = 50.0  # test 13: wait for the CPU to cool to this first (C)
RAMP_RPM = 800  # a fan rising this far above its lowest reading counts as a takeover

# Copy of the fan curves in src/fan.rs: (temperature, duty), both in hundredths
# (4500 = 45.00 C, 3000 = 30.00%). Keep in sync with FanCurve::standard() etc.
CURVES = {
    "standard": [(4499, 0), (4500, 3000), (5500, 3500), (6500, 4000), (7500, 5000),
                 (7800, 6000), (8100, 7000), (8400, 8000), (8600, 9000), (8800, 10000)],
    "threadripper2": [(0, 3000), (4000, 4000), (4750, 5000), (5500, 6500), (6250, 8500),
                      (6625, 10000)],
    "hedt": [(0, 3000), (5000, 3500), (6000, 4500), (7000, 5500), (7400, 6000), (7600, 7000),
             (7800, 8000), (8100, 10000)],
    "xeon": [(0, 4000), (5000, 4000), (5500, 4500), (6000, 5000), (6500, 5500), (7000, 6000),
             (7200, 6500), (7400, 8000), (7600, 8500), (7700, 9000), (7800, 10000)],
}
HEDT_MODELS = ("thelio-astra-a1", "thelio-astra-a1.1", "thelio-major-r2", "thelio-major-r2.1",
               "thelio-major-b1", "thelio-major-b2", "thelio-major-b3", "thelio-mega-r1",
               "thelio-mega-r1.1")
DAEMON_TEMP_HWMON_NAMES = ("apm_xgene", "coretemp", "k10temp", "amdgpu")


def curve_for_model():
    """The curve name FanDaemon::new picks for this machine."""
    try:
        with open("/sys/class/dmi/id/product_version") as f:
            model = f.read().strip()
    except OSError:
        model = ""
    if model == "thelio-major-r1":
        return "threadripper2"
    if model in HEDT_MODELS:
        return "hedt"
    if model == "thelio-massive-b1":
        return "xeon"
    return "standard"


def curve_pwm(name, millidegrees):
    """The pwm value (0-255) FanCurve::get_duty gives at a temperature."""
    points = CURVES[name]
    temp = millidegrees // 10
    if temp < points[0][0]:
        duty = points[0][1]
    elif temp > points[-1][0]:
        duty = points[-1][1]
    else:
        duty = points[-1][1]
        for (t0, d0), (t1, d1) in zip(points, points[1:]):
            if temp == t1:
                duty = d1
                break
            if temp == t0:
                duty = d0
                break
            if t0 < temp < t1:
                duty = d0 + math.floor((d1 - d0) / (t1 - t0) * (temp - t0) + 0.5)
                break
    return duty * 255 // 10000


def daemon_temp():
    """Highest temperature FanDaemon::get_temp would see, in millidegrees C."""
    temps = []
    for name_file in glob.glob("/sys/class/hwmon/hwmon*/name"):
        with open(name_file) as f:
            if f.read().strip() in DAEMON_TEMP_HWMON_NAMES:
                value = read_int(os.path.join(os.path.dirname(name_file), "temp1_input"))
                if value is not None:
                    temps.append(value)
    if NVIDIA_SMI:
        try:
            out = run([NVIDIA_SMI, "--query-gpu=temperature.gpu", "--format=csv,noheader"],
                      timeout=5).stdout
            temps += [int(line) * 1000 for line in out.split() if line.isdigit()]
        except (OSError, subprocess.SubprocessError):
            pass
    return max(temps) if temps else None


NVIDIA_SMI = shutil.which("nvidia-smi")


# --- small helpers -----------------------------------------------------------

def read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def pwm(hwmon, i=1):
    return read_int(os.path.join(hwmon, f"pwm{i}"))


def rpm(hwmon, i):
    return read_int(os.path.join(hwmon, f"fan{i}_input"))


def write_all(hwmon, value):
    """Set pwm1-4. Raises if the main fan file can't be written."""
    enable = os.path.join(hwmon, "pwm1_enable")
    if os.path.exists(enable):
        try:
            with open(enable, "w") as f:
                f.write("1")
        except OSError:
            pass
    for i in range(1, 5):
        path = os.path.join(hwmon, f"pwm{i}")
        if os.path.exists(path):
            with open(path, "w") as f:
                f.write(str(value))


def cpu_temp_path():
    for name_file in sorted(glob.glob("/sys/class/hwmon/hwmon*/name")):
        with open(name_file) as f:
            if f.read().strip() in CPU_HWMON_NAMES:
                return os.path.join(os.path.dirname(name_file), "temp1_input")
    return None


def wait_for(predicate, timeout, step=0.1):
    """Seconds until predicate() is true, or None if it never is."""
    start = time.monotonic()
    while True:
        if predicate():
            return time.monotonic() - start
        if time.monotonic() - start >= timeout:
            return None
        time.sleep(step)


def average_rpm(hwmon, seconds):
    readings = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        values = [v for v in (rpm(hwmon, i) for i in range(1, 5)) if v is not None]
        if values:
            readings.append(sum(values) / len(values))
        time.sleep(0.25)
    return round(sum(readings) / len(readings)) if readings else None


def run(cmd, timeout=30):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def service_active():
    return run(["systemctl", "is-active", SERVICE]).stdout.strip() == "active"


def daemon_has_methods():
    try:
        conn = client.connect()
        reply = conn.call_sync(
            client.BUS_NAME, client.OBJECT_PATH, "org.freedesktop.DBus.Introspectable",
            "Introspect", None, GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 3000, None,
        )
        conn.close_sync(None)
        return "ClaimFanControl" in reply.unpack()[0]
    except GLib.Error:
        return False


def claim_then_release():
    """Claim and release from a new connection. Returns None or the error."""
    conn = client.connect()
    try:
        error = client.call(conn, "ClaimFanControl")
        if error:
            return error
        return client.call(conn, "ReleaseFanControl")
    finally:
        conn.close_sync(None)


def journal_since(epoch):
    time.sleep(0.5)
    result = run(["journalctl", "-u", SERVICE, "--since", f"@{int(epoch) - 1}",
                  "--no-pager", "-o", "short-iso"])
    return result.stdout


def sha256(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def git(*args):
    result = run(["git", "-c", "safe.directory=*", "-C", REPO, *args])
    return result.stdout.strip() or "?"


class Holder:
    """fan_claim_test.py hold --pwm MARK, running as its own process."""

    def __init__(self, *extra):
        self.lines = []
        self.proc = subprocess.Popen(
            [sys.executable, "-u", CLIENT, "hold", "--pwm", str(MARK), *extra],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.append(line.rstrip())

    def claimed(self, timeout=10):
        wait_for(lambda: any("claim " in line for line in self.lines), timeout)
        return any("claim accepted" in line for line in self.lines)

    def output(self):
        return " | ".join(line.strip() for line in self.lines[:4])

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(5)


class Sampler(threading.Thread):
    """Writes fan and temperature readings to samples.csv every half second."""

    def __init__(self, path, hwmon, temp_path):
        super().__init__(daemon=True)
        self.path = path
        self.hwmon = hwmon
        self.temp_path = temp_path
        self.test = "setup"
        self.stopped = threading.Event()
        self.started = time.monotonic()

    def run(self):
        with open(self.path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "time", "elapsed_s", "test", "cpu_temp_c",
                *(f"pwm{i}" for i in range(1, 5)), *(f"fan{i}_rpm" for i in range(1, 5)),
            ])
            while True:
                temp = read_int(self.temp_path) if self.temp_path else None
                writer.writerow([
                    datetime.datetime.now().isoformat(timespec="milliseconds"),
                    f"{time.monotonic() - self.started:.1f}",
                    self.test,
                    "" if temp is None else f"{temp / 1000:.1f}",
                    *(pwm(self.hwmon, i) for i in range(1, 5)),
                    *(rpm(self.hwmon, i) for i in range(1, 5)),
                ])
                f.flush()
                if self.stopped.wait(0.5):
                    return


# --- the tests ---------------------------------------------------------------

class Runner:
    def __init__(self, hwmon, sampler, temp_path):
        self.hwmon = hwmon
        self.sampler = sampler
        self.temp_path = temp_path
        self.holder_conn = None
        self.second_conn = None
        self.procs = []
        self.curve_rpm = None
        self.curve = curve_for_model()

    def reverted(self):
        return pwm(self.hwmon) != MARK

    def curve_tick(self):
        """Catch one system76-power curve tick and check what it wrote.

        Only valid while nobody holds a claim. Writes MARK, watches for the
        moment the daemon overwrites it, and reads the temperature right then,
        so a jumpy CPU temperature can't hide a wrong value.
        Returns (ok, text)."""
        write_all(self.hwmon, MARK)
        recent = []  # (time, temperature) just before the tick
        deadline = time.monotonic() + REVERT_TIMEOUT
        while time.monotonic() < deadline:
            temp = daemon_temp()
            if temp is not None:
                recent = [(t, v) for t, v in recent if time.monotonic() - t < 0.15]
                recent.append((time.monotonic(), temp))
            if pwm(self.hwmon) != MARK:
                break
            time.sleep(0.005)
        else:
            hot = recent and curve_pwm(self.curve, recent[-1][1]) == MARK
            return hot, ("curve is at full speed, so a tick can't be seen" if hot
                         else f"system76-power did not overwrite {MARK} within {REVERT_TIMEOUT:.0f} s")
        after = daemon_temp()
        time.sleep(0.2)  # the daemon writes pwm1 to pwm4 one after another
        values = [v for v in (pwm(self.hwmon, i) for i in range(1, 5)) if v is not None]
        temps = [v for _, v in recent] + ([after] if after is not None else [])
        if not temps:
            return False, "could not read the CPU temperature"
        low = curve_pwm(self.curve, min(temps) - 500)
        high = curve_pwm(self.curve, max(temps) + 500)
        ok = bool(values) and all(low <= v <= high for v in values)
        expected = f"{low}" if low == high else f"{low}-{high}"
        fans = values[0] if len(set(values)) == 1 else "/".join(str(v) for v in values)
        return ok, f"{max(temps) / 1000:.1f} C -> {expected}, fans {fans}{'' if ok else ' WRONG'}"

    def on_curve(self, ticks=3):
        """Check that the fans follow system76-power's curve on several ticks.
        Returns (ok, text)."""
        checks = [self.curve_tick() for _ in range(ticks)]
        ok = all(check_ok for check_ok, _ in checks)
        return ok, (f"'{self.curve}' curve {'matched' if ok else 'did NOT match'} on {ticks} ticks ("
                    + "; ".join(text for _, text in checks) + ")")

    def write_mark(self):
        write_all(self.hwmon, MARK)
        if pwm(self.hwmon) != MARK:
            raise RuntimeError(f"wrote {MARK} to pwm1 but it reads {pwm(self.hwmon)}")

    def holder(self, *extra):
        holder = Holder(*extra)
        self.procs.append(holder.proc)
        return holder

    def test_control(self):
        before = pwm(self.hwmon)
        self.curve_rpm = average_rpm(self.hwmon, 2)
        self.write_mark()
        elapsed = wait_for(self.reverted, REVERT_TIMEOUT)
        if elapsed is None:
            return False, (f"pwm1 stayed at {MARK} for {REVERT_TIMEOUT:.0f} s with no claim: "
                           "system76-power is not writing the fans, so the other tests prove nothing")
        curve_ok, curve_text = self.on_curve()
        if not curve_ok:
            return False, (f"system76-power overrode {MARK} after {elapsed:.1f} s, but {curve_text}. "
                           "This script's copy of the curve doesn't match the daemon, so the "
                           "curve checks in tests 5-7 and 11 can't be trusted")
        return True, (f"no claim: pwm1 {MARK} -> {pwm(self.hwmon)} after {elapsed:.1f} s "
                      f"(was {before}); {curve_text}; fans ~{self.curve_rpm} RPM")

    def test_claim_holds(self):
        self.holder_conn = client.connect()
        error = client.call(self.holder_conn, "ClaimFanControl")
        if error:
            return False, f"claim rejected: {error}"
        self.write_mark()
        load = [subprocess.Popen([sys.executable, "-c", "while True: pass"])
                for _ in range(os.cpu_count() or 4)]
        self.procs.extend(load)
        changed = None
        max_temp = None
        try:
            end = time.monotonic() + HOLD_SECONDS
            while time.monotonic() < end and changed is None:
                values = [pwm(self.hwmon, i) for i in range(1, 5)]
                if any(v is not None and v != MARK for v in values):
                    changed = values
                temp = read_int(self.temp_path) if self.temp_path else None
                if temp is not None:
                    max_temp = max(max_temp or 0, temp)
                time.sleep(0.25)
        finally:
            for proc in load:
                proc.kill()
        held_rpm = average_rpm(self.hwmon, 2)
        temp_text = f" (CPU up to {max_temp / 1000:.0f} C)" if max_temp else ""
        if changed:
            return False, f"fans changed to {changed} while claimed: system76-power is still writing"
        return True, (f"fans held at {MARK} for {HOLD_SECONDS} s under CPU load{temp_text}; "
                      f"~{held_rpm} RPM vs ~{self.curve_rpm} RPM on the curve")

    def test_second_claim(self):
        if not self.holder_conn:
            return False, "needs the claim from test 2"
        self.second_conn = client.connect()
        error = client.call(self.second_conn, "ClaimFanControl")
        if error is None:
            client.call(self.second_conn, "ReleaseFanControl")
            return False, "second connection's claim was ACCEPTED while the first held it"
        if "already claimed" in error:
            return True, "second connection rejected: already claimed"
        return False, f"unexpected error: {error}"

    def test_foreign_release(self):
        if not self.second_conn:
            return False, "needs the second connection from test 3"
        error = client.call(self.second_conn, "ReleaseFanControl")
        self.second_conn.close_sync(None)
        self.second_conn = None
        if error and "another process" in error:
            still = pwm(self.hwmon) == MARK
            return still, ("non-holder's release rejected; fans still held"
                           if still else "rejected, but the fans changed anyway")
        return False, f"non-holder's release was not rejected ({error or 'accepted'})"

    def test_release(self):
        if not self.holder_conn:
            return False, "needs the claim from test 2"
        error = client.call(self.holder_conn, "ReleaseFanControl")
        self.holder_conn.close_sync(None)
        self.holder_conn = None
        if error:
            return False, f"release rejected: {error}"
        elapsed = wait_for(self.reverted, REVERT_TIMEOUT)
        if elapsed is None:
            return False, f"release accepted but fans stayed at {MARK}"
        curve_ok, curve_text = self.on_curve()
        return curve_ok, f"released: fans left {MARK} after {elapsed:.1f} s; {curve_text}"

    def holder_vanishes(self, how):
        started = time.time()
        extra = ("--no-release",) if how == "exit" else ()
        holder = self.holder(*extra)
        if not holder.claimed():
            holder.kill()
            return False, f"helper could not claim: {holder.output()}"
        time.sleep(2)
        if pwm(self.hwmon) != MARK:
            holder.kill()
            return False, f"helper claimed but pwm1 reads {pwm(self.hwmon)}, not {MARK}"
        if how == "kill":
            holder.kill()
        else:
            holder.proc.stdin.write("\n")
            holder.proc.stdin.flush()
            holder.proc.wait(10)
        elapsed = wait_for(self.reverted, REVERT_TIMEOUT)
        if elapsed is None:
            return False, f"fans stayed at {MARK} after the holder went away (stuck claim)"
        curve_ok, curve_text = self.on_curve()
        error = claim_then_release()
        logged = "disconnected; releasing" in journal_since(started)
        if error:
            return False, f"fans reverted but a new claim failed: {error}"
        return curve_ok, (f"fans left {MARK} after {elapsed:.1f} s; {curve_text}; new claim accepted; "
                          f"daemon log {'shows' if logged else 'does NOT show'} 'disconnected; releasing'")

    def test_killed_holder(self):
        return self.holder_vanishes("kill")

    def test_exit_without_release(self):
        return self.holder_vanishes("exit")

    def test_instant_disconnect(self):
        result = run([sys.executable, CLIENT, "fire-and-forget"])
        if result.returncode != 0:
            return False, f"fire-and-forget failed: {result.stdout.strip()} {result.stderr.strip()}"
        attempts = []

        def claim_free():
            attempts.append(claim_then_release())
            return attempts[-1] is None

        elapsed = wait_for(claim_free, 3.0, step=0.25)
        if elapsed is None:
            return False, f"claim still held 3 s after the client vanished (stuck): {attempts[-1]}"
        return True, f"no stuck claim: new claim accepted after {elapsed:.1f} s ({len(attempts)} attempt(s))"

    def test_flood(self):
        churn = subprocess.Popen([sys.executable, CLIENT, "churn", "--count", "100000000"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(churn)
        times = []
        try:
            time.sleep(1)
            for _ in range(3):
                if churn.poll() is not None:
                    return False, "the connection flood stopped early"
                self.write_mark()
                times.append(wait_for(self.reverted, REVERT_TIMEOUT))
                time.sleep(0.5)
        finally:
            churn.kill()
        if any(t is None for t in times):
            return False, f"curve did not override {MARK} within {REVERT_TIMEOUT:.0f} s during the flood"
        return True, "during the flood the curve overrode pwm1 after " + ", ".join(f"{t:.1f}" for t in times) + " s"

    def test_non_root(self):
        user = os.environ.get("SUDO_USER") or "nobody"
        if user == "root":
            user = "nobody"
        result = run(["runuser", "-u", user, "--", sys.executable, CLIENT, "contend"])
        output = (result.stdout + result.stderr).strip()
        if "claim accepted" in output:
            return False, f"user '{user}' was ALLOWED to claim"
        if "REJECTED" in output and "not permitted" in output.lower():
            return True, f"user '{user}' rejected by polkit"
        return False, f"could not check as user '{user}': {output.splitlines()[-1] if output else result.returncode}"

    def test_restart(self):
        started = time.time()
        holder = self.holder()
        if not holder.claimed():
            holder.kill()
            return False, f"helper could not claim: {holder.output()}"
        time.sleep(1)
        t = time.monotonic()
        stop = run(["systemctl", "stop", SERVICE], timeout=60)
        stop_seconds = time.monotonic() - t
        stopped = not service_active()
        pwm_after_stop = pwm(self.hwmon)
        enable_path = os.path.join(self.hwmon, "pwm1_enable")
        enable_after_stop = read_int(enable_path) if os.path.exists(enable_path) else None
        holder.kill()

        start = run(["systemctl", "start", SERVICE], timeout=60)
        ready = wait_for(daemon_has_methods, 20, step=0.5)
        resumed = wait_for(self.reverted, 10)
        curve_ok, curve_text = self.on_curve() if resumed is not None else (False, "")
        error = claim_then_release() if ready is not None else "daemon did not come back"
        exited_cleanly = "daemon exited from loop" in journal_since(started)

        problems = []
        if stop.returncode != 0 or not stopped:
            problems.append(f"stop failed: {stop.stderr.strip()}")
        elif stop_seconds > 20:
            problems.append(f"stop took {stop_seconds:.0f} s")
        if enable_after_stop is not None and enable_after_stop != 2:
            problems.append(f"pwm1_enable={enable_after_stop} after stop, expected 2")
        if start.returncode != 0 or ready is None:
            problems.append("daemon did not restart")
        elif resumed is None:
            problems.append("curve did not resume after restart")
        elif not curve_ok:
            problems.append(f"after restart {curve_text}")
        if error:
            problems.append(f"new claim after restart failed: {error}")
        if problems:
            return False, "; ".join(problems)
        after_stop = (f"pwm1_enable=2" if enable_after_stop == 2 else
                      f"fans stayed at pwm1={pwm_after_stop} (no automatic mode on this board)")
        return True, (f"stopped in {stop_seconds:.1f} s while claimed "
                      f"({'clean exit logged' if exited_cleanly else 'no exit line in log'}); "
                      f"{after_stop}; restarted, fans left {MARK} after {resumed:.1f} s; "
                      f"{curve_text}; new claim accepted")

    def test_firmware_fallback(self):
        """What the fan board does on its own while system76-power is stopped.

        Informational: returns None for the result unless system76-power fails
        to come back and resume its curve, which is a failure."""
        stop = run(["systemctl", "stop", SERVICE], timeout=60)
        if stop.returncode != 0 or service_active():
            return False, f"could not stop system76-power: {stop.stderr.strip()}"
        load = []
        start_temp = peak_temp = daemon_temp()
        start_rpm = peak_rpm = None
        reacted_after = reacted_pwm = reacted_temp = None
        hit_limit = False
        duration = 0.0
        try:
            write_all(self.hwmon, 0)
            time.sleep(2)  # let the fans settle at 0
            start_rpm = max((rpm(self.hwmon, i) or 0) for i in range(1, 5))
            peak_rpm = start_rpm
            load = [subprocess.Popen([sys.executable, "-c", "while True: pass"])
                    for _ in range(os.cpu_count() or 4)]
            self.procs.extend(load)
            began = time.monotonic()
            while True:
                duration = time.monotonic() - began
                temp = daemon_temp()
                if temp is not None:
                    peak_temp = max(peak_temp or 0, temp)
                fan_pwm = pwm(self.hwmon)
                fan_rpm = max((rpm(self.hwmon, i) or 0) for i in range(1, 5))
                peak_rpm = max(peak_rpm, fan_rpm)
                # "Reacted": the board changed the pwm, or spun a fan up clearly.
                if reacted_after is None and (fan_pwm not in (0, None) or fan_rpm > start_rpm + 500):
                    reacted_after, reacted_pwm, reacted_temp = duration, fan_pwm, temp
                if reacted_after is not None and duration - reacted_after > 10:
                    break
                if temp is not None and temp / 1000 >= FIRMWARE_TEMP_LIMIT:
                    hit_limit = True
                    break
                if duration >= FIRMWARE_LOAD_SECONDS:
                    break
                time.sleep(0.5)
        finally:
            for proc in load:
                proc.kill()
            run(["systemctl", "start", SERVICE], timeout=60)

        ready = wait_for(daemon_has_methods, 20, step=0.5)
        if ready is None:
            return False, "system76-power did not come back after the test"
        time.sleep(2)  # a couple of curve ticks
        curve_ok, curve_text = self.on_curve()
        if not curve_ok:
            return False, f"system76-power restarted but {curve_text}"

        def celsius(value):
            return f"{value / 1000:.0f} C" if value is not None else "? C"

        observed = (f"with system76-power stopped and the fans set to 0, CPU load took the CPU "
                    f"from {celsius(start_temp)} to {celsius(peak_temp)} over {duration:.0f} s"
                    + (f" (load stopped at the {FIRMWARE_TEMP_LIMIT:.0f} C limit)" if hit_limit else ""))
        if reacted_after is None:
            board = (f"pwm1 stayed 0 and fan RPM peaked at {peak_rpm} (was {start_rpm}): the board "
                     "has NO fallback of its own; the fans only follow a curve while system76-power runs")
        else:
            board = (f"the board raised the fans by itself after {reacted_after:.0f} s at "
                     f"{celsius(reacted_temp)} (pwm1 {reacted_pwm}, peak {peak_rpm} RPM)")
        return None, f"{observed}; {board}. After restart: {curve_text}"

    def label(self, text):
        if self.sampler:
            self.sampler.test = text

    def watch_fans(self, rewrite_every=None):
        """Set the fans to WATCHDOG_VALUE, then watch the RPM for WATCHDOG_SECONDS.
        With rewrite_every, write the value again that often. Reports whether
        the fans ramped up although nothing asked them to."""
        write_all(self.hwmon, WATCHDOG_VALUE)
        start = last_write = time.monotonic()
        lowest = ramp_at = ramp_since_write = max_temp = None
        peak = 0
        while time.monotonic() - start < WATCHDOG_SECONDS:
            now = time.monotonic()
            if rewrite_every and now - last_write >= rewrite_every:
                write_all(self.hwmon, WATCHDOG_VALUE)
                last_write = now
            fan = max((rpm(self.hwmon, i) or 0) for i in range(1, 5))
            if lowest is None or fan < lowest:
                # Still spinning down from before: only count the peak after this.
                lowest = peak = fan
            peak = max(peak, fan)
            temp = daemon_temp()
            if temp is not None:
                max_temp = max(max_temp or 0, temp)
            if ramp_at is None and fan > lowest + RAMP_RPM:
                ramp_at, ramp_since_write = now - start, now - last_write
            time.sleep(0.25)
        return {"ramp_at": ramp_at, "since_write": ramp_since_write, "lowest": lowest,
                "peak": peak, "max_temp": max_temp}

    @staticmethod
    def describe_watch(name, result):
        temp = f"CPU <= {result['max_temp'] / 1000:.0f} C" if result["max_temp"] else "CPU ? C"
        if result["ramp_at"] is None:
            return (f"{name}: fans stayed low for {WATCHDOG_SECONDS} s "
                    f"(settled at {result['lowest']}-{result['peak']} RPM, {temp})")
        return (f"{name}: board ramped the fans to {result['peak']} RPM "
                f"{result['since_write']:.1f} s after the last write ({temp})")

    def test_watchdog(self):
        """Is test 12's takeover a watchdog (host stopped writing) or temperature?

        Informational, like test 12. Three parts, all with the CPU idle:
          a. system76-power stopped, fans set to WATCHDOG_VALUE once
          b. system76-power running but claimed, fans set once
          c. claimed, fans rewritten every second, like system76-power does
        FAIL only if system76-power doesn't come back and follow its curve."""
        self.label("13 cooling down")
        cooled = wait_for(lambda: (daemon_temp() or 0) < WATCHDOG_IDLE_TEMP * 1000, 120, step=1)
        start_temp = daemon_temp()

        self.label("13a system76-power stopped, fans set once")
        stop = run(["systemctl", "stop", SERVICE], timeout=60)
        if stop.returncode != 0 or service_active():
            return False, f"could not stop system76-power: {stop.stderr.strip()}"
        try:
            part_a = self.watch_fans()
        finally:
            run(["systemctl", "start", SERVICE], timeout=60)
        if wait_for(daemon_has_methods, 20, step=0.5) is None:
            return False, "system76-power did not come back after part a"
        time.sleep(3)  # let the curve settle the fans again

        self.holder_conn = client.connect()
        error = client.call(self.holder_conn, "ClaimFanControl")
        if error:
            return False, f"claim for parts b and c rejected: {error}"
        try:
            self.label("13b claimed, fans set once")
            part_b = self.watch_fans()
            self.label("13c claimed, fans rewritten every 1 s")
            part_c = self.watch_fans(rewrite_every=1.0)
        finally:
            client.call(self.holder_conn, "ReleaseFanControl")
            self.holder_conn.close_sync(None)
            self.holder_conn = None
        self.label("13 back on the curve")
        curve_ok, curve_text = self.on_curve()
        if not curve_ok:
            return False, f"after the test {curve_text}"

        once = [part_a, part_b]
        hottest = max((r["max_temp"] or 0) for r in once + [part_c]) / 1000
        idle = hottest < WATCHDOG_IDLE_TEMP + 10
        idle_note = ("" if cooled is not None else
                     f" Note: the CPU only cooled to {start_temp / 1000:.0f} C, not {WATCHDOG_IDLE_TEMP:.0f} C.")
        cpu = (f"with the CPU idle (<= {hottest:.0f} C)" if idle else
               f"but the CPU reached {hottest:.0f} C, so temperature can't be ruled out")
        if all(r["ramp_at"] is not None for r in once) and part_c["ramp_at"] is None:
            delay = max(r["since_write"] for r in once)
            verdict = (f"VERDICT: the board has a watchdog. If nothing writes the fans for about "
                       f"{delay:.0f} s it takes over and ramps them up, {cpu}. "
                       "Rewriting every 1 s kept it from taking over, so a claimant must keep "
                       "writing the fans regularly (system76-power writes every 1 s).")
        elif all(r["ramp_at"] is None for r in once + [part_c]):
            verdict = (f"VERDICT: no watchdog: the board left the fans alone {cpu}"
                       + (", so test 12's takeover was triggered by temperature." if idle else "."))
        else:
            verdict = "VERDICT: mixed results; see the three parts and samples.csv."
        parts = "; ".join([
            self.describe_watch("a (system76-power stopped, set once)", part_a),
            self.describe_watch("b (claimed, set once)", part_b),
            self.describe_watch("c (claimed, rewritten every 1 s)", part_c),
        ])
        return None, f"{parts}. {verdict}{idle_note} After release: {curve_text}"

    def cleanup(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
        for conn in (self.second_conn, self.holder_conn):
            if conn:
                client.call(conn, "ReleaseFanControl")
                conn.close_sync(None)
        self.second_conn = self.holder_conn = None
        if not service_active():
            print("cleanup: starting system76-power again")
            run(["systemctl", "start", SERVICE], timeout=60)
            wait_for(daemon_has_methods, 20, step=0.5)


# --- main --------------------------------------------------------------------

def write_system_info(path, hwmon):
    def dmi(name):
        try:
            with open(f"/sys/class/dmi/id/{name}") as f:
                return f.read().strip()
        except OSError:
            return "?"

    installed = sha256("/usr/bin/system76-power")
    built = sha256(os.path.join(REPO, "target", "release", "system76-power"))
    lines = [
        f"date:            {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"host:            {socket.gethostname()}",
        f"vendor/model:    {dmi('sys_vendor')} {dmi('product_name')} ({dmi('product_version')})",
        f"kernel:          {os.uname().release}",
        f"fan board:       {hwmon or 'not found'}"
        + (f" ({open(os.path.join(hwmon, 'name')).read().strip()})" if hwmon else ""),
        f"pwm1_enable:     {'present' if hwmon and os.path.exists(os.path.join(hwmon, 'pwm1_enable')) else 'absent'}",
        f"git branch:      {git('rev-parse', '--abbrev-ref', 'HEAD')}",
        f"git commit:      {git('rev-parse', 'HEAD')}",
        f"git changes:     {'; '.join(git('status', '--short').splitlines()) if git('status', '--short') != '?' else 'none'}",
        f"installed binary matches this build: "
        + ("yes" if installed and installed == built else "NO" if installed and built else "unknown"),
    ]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return lines


def hand_back(path):
    """Give the results to the user who ran sudo, so they aren't root-owned."""
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if not uid or not gid:
        return
    for root, dirs, files in os.walk(path):
        for name in [root] + [os.path.join(root, n) for n in dirs + files]:
            os.chown(name, int(uid), int(gid))


def main():
    parser = argparse.ArgumentParser(description="Run every fan control claim test and log the results.")
    parser.add_argument("--skip-restart", action="store_true",
                        help="skip test 11, which stops and restarts system76-power")
    parser.add_argument("--skip-firmware-check", action="store_true",
                        help="skip test 12, which stops system76-power and loads the CPU with the fans at 0")
    parser.add_argument("--skip-watchdog-check", action="store_true",
                        help="skip test 13, which checks whether the fan board takes over when not written to")
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("Run with sudo, from the system76-power folder:\n"
              "  sudo python3 fan-claim-test/run_all.py")
        return 2

    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    results_root = os.path.join(HERE, "results")
    results = os.path.join(results_root, stamp)
    os.makedirs(results)
    run_started = time.time()

    hwmons = client.fan_hwmons()
    hwmon = hwmons[0] if hwmons else None
    info = write_system_info(os.path.join(results, "system.txt"), hwmon)
    print("\n".join(info))
    print(f"\nResults folder: {results}\n")

    prechecks = [
        ("system76-power is running", service_active()),
        ("system76-power has ClaimFanControl (patched build installed)", daemon_has_methods()),
        ("fan board found", hwmon is not None),
    ]
    if all(ok for _, ok in prechecks):
        error = claim_then_release()
        prechecks.append(("fan control is not already claimed" + (f" ({error})" if error else ""),
                          error is None))
    summary = [f"Fan control claim test: {stamp}", *info, ""]
    for name, ok in prechecks:
        line = f"{'ok  ' if ok else 'FAIL'}  pre-check: {name}"
        print(line)
        summary.append(line)
    if not all(ok for _, ok in prechecks):
        summary.append("\nPre-checks failed; no tests were run.")
        with open(os.path.join(results, "summary.txt"), "w") as f:
            f.write("\n".join(summary) + "\n")
        hand_back(results_root)
        print("\nPre-checks failed; no tests were run. See README.md, Part 1.")
        return 1
    print()
    summary.append("")

    sampler = Sampler(os.path.join(results, "samples.csv"), hwmon, cpu_temp_path())
    sampler.start()
    runner = Runner(hwmon, sampler, cpu_temp_path())
    tests = [
        ("Control: no claim, system76-power overrides writes", runner.test_control),
        ("Claim holds the fans under load", runner.test_claim_holds),
        ("Second program's claim is rejected", runner.test_second_claim),
        ("Non-holder can't release", runner.test_foreign_release),
        ("Release hands back to the curve", runner.test_release),
        ("Killed holder releases the claim", runner.test_killed_holder),
        ("Holder exiting without release releases", runner.test_exit_without_release),
        ("Instant disconnect leaves no stuck claim", runner.test_instant_disconnect),
        ("Curve keeps running during a D-Bus flood", runner.test_flood),
        ("Non-root user is refused", runner.test_non_root),
        ("Daemon stop/restart while claimed", runner.test_restart),
        ("Fan board on its own with system76-power stopped (info)", runner.test_firmware_fallback),
        ("Fan board watchdog: what triggers the takeover (info)", runner.test_watchdog),
    ]
    skips = {11: "--skip-restart" if args.skip_restart else None,
             12: "--skip-firmware-check" if args.skip_firmware_check else None,
             13: "--skip-watchdog-check" if args.skip_watchdog_check else None}

    passed = failed = 0
    try:
        for number, (name, test) in enumerate(tests, start=1):
            if skips.get(number):
                line = f"SKIP  {number:2}  {name}: {skips[number]}"
                print(line)
                summary.append(line)
                continue
            sampler.test = f"{number} {name}"
            print(f"[{number:2}/{len(tests)}] {name} ...", flush=True)
            try:
                ok, detail = test()
            except Exception as err:  # keep going; record what broke
                ok, detail = False, f"error: {err}"
            if ok is not None:
                passed += ok
                failed += not ok
            status = "INFO" if ok is None else "PASS" if ok else "FAIL"
            line = f"{status}  {number:2}  {name}: {detail}"
            print(f"        {line}")
            summary.append(line)
    except KeyboardInterrupt:
        print("\nStopped with Ctrl+C.")
        summary.append("Stopped with Ctrl+C before all tests ran.")
    finally:
        sampler.test = "cleanup"
        runner.cleanup()
        sampler.stopped.set()
        sampler.join(5)
        with open(os.path.join(results, "daemon.log"), "w") as f:
            f.write(journal_since(run_started))
        total = f"Result: {passed} passed, {failed} failed"
        summary += ["", total]
        with open(os.path.join(results, "summary.txt"), "w") as f:
            f.write("\n".join(summary) + "\n")
        hand_back(results_root)
        print(f"\n{total}\nSaved to {results}:")
        print("  summary.txt  pass/fail for each test")
        print("  samples.csv  fan speed (pwm), fan RPM and CPU temperature every 0.5 s")
        print("  daemon.log   system76-power's log during the run")
        print("  system.txt   machine, kernel and the exact build tested")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
