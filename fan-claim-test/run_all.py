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
import glob
import os
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

    def reverted(self):
        return pwm(self.hwmon) != MARK

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
        return True, (f"no claim: pwm1 {MARK} -> {pwm(self.hwmon)} after {elapsed:.1f} s "
                      f"(was {before}); fans on the curve ~{self.curve_rpm} RPM")

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
        return True, f"released: pwm1 {MARK} -> {pwm(self.hwmon)} after {elapsed:.1f} s"

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
        error = claim_then_release()
        logged = "disconnected; releasing" in journal_since(started)
        if elapsed is None:
            return False, f"fans stayed at {MARK} after the holder went away (stuck claim)"
        if error:
            return False, f"fans reverted but a new claim failed: {error}"
        return True, (f"curve resumed after {elapsed:.1f} s; new claim accepted; "
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
        if error:
            problems.append(f"new claim after restart failed: {error}")
        if problems:
            return False, "; ".join(problems)
        after_stop = (f"pwm1_enable=2" if enable_after_stop == 2 else
                      f"fans stayed at pwm1={pwm_after_stop} (no automatic mode on this board)")
        return True, (f"stopped in {stop_seconds:.1f} s while claimed "
                      f"({'clean exit logged' if exited_cleanly else 'no exit line in log'}); "
                      f"{after_stop}; restarted, curve resumed after {resumed:.1f} s, new claim accepted")

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
    ]

    passed = failed = 0
    try:
        for number, (name, test) in enumerate(tests, start=1):
            if number == 11 and args.skip_restart:
                line = f"SKIP  {number:2}  {name}: --skip-restart"
                print(line)
                summary.append(line)
                continue
            sampler.test = f"{number} {name}"
            print(f"[{number:2}/{len(tests)}] {name} ...", flush=True)
            try:
                ok, detail = test()
            except Exception as err:  # keep going; record what broke
                ok, detail = False, f"error: {err}"
            passed += ok
            failed += not ok
            line = f"{'PASS' if ok else 'FAIL'}  {number:2}  {name}: {detail}"
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
