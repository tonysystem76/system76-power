# Testing fan control claim on a Thelio

This branch (`fan-control-claim-test`) is the patch from `fan-control-claim` plus this test folder. The test folder is not part of the upstream PR.

## What the patch does

Normally system76-power rewrites the fan speed every second from its built-in fan curve. That overwrites any other program that tries to set the fans.

The patch adds two D-Bus methods:

- **`ClaimFanControl`** tells system76-power to stop writing fan speeds. The program that called it is now "holding the claim" and can set the fans itself.
- **`ReleaseFanControl`** gives control back. system76-power puts the fans back to automatic and resumes its curve.

The claim is also released automatically if the holding program exits or crashes, or if system76-power itself is stopped. Only one program can hold the claim at a time, and only root (or a user who passes a polkit check) can claim.

## How to read the fan values

Every command below that shows fan values prints a line like this:

```
  /sys/class/hwmon/hwmon5: pwm1_enable=1 pwm1=102 pwm2=102 pwm3=102 pwm4=102
```

- `pwm1_enable=1` means **manual**: software is setting the speed. When system76-power is running its curve, you'll see `1`.
- `pwm1_enable=2` means **automatic**: the fan controller's own firmware is in charge. A release sets this, but system76-power resumes its curve within a second and sets it back to `1`. So while system76-power is running, `2` usually flashes by too fast to see. You'll see it stay at `2` only in Test 8, where system76-power is stopped.
- `pwm1` to `pwm4` are fan speeds from `0` (off) to `255` (full). `200` is about 78%.

`hwmon5` is just an example. The number differs between machines.

## Part 1: Set up (once)

All commands are run from the `system76-power` folder.

**1. Install build tools** (skip any you already have):

```bash
sudo apt install git cargo pkg-config libdbus-1-dev libusb-1.0-0-dev python3-gi
```

**2. Get the code and build it** (takes a few minutes):

```bash
git clone -b fan-control-claim-test https://github.com/tonysystem76/system76-power.git
cd system76-power
cargo build --release
```

**3. Replace the installed system76-power with the patched one:**

```bash
sudo install -m 0755 target/release/system76-power /usr/bin/system76-power
sudo install -m 0644 data/com.system76.PowerDaemon.policy /usr/share/polkit-1/actions/
sudo systemctl restart com.system76.PowerDaemon
```

**4. Confirm the patched version is running.** This should print two lines, `.ClaimFanControl` and `.ReleaseFanControl`:

```bash
busctl --system introspect com.system76.PowerDaemon /com/system76/PowerDaemon | grep FanControl
```

If it prints nothing, the old version is still running. Check that step 3 had no errors.

**5. Confirm the test script can see the fans:**

```bash
python3 fan-claim-test/fan_claim_test.py status
```

You should see one or more fan lines as described above. If it says `no system76_io / system76_thelio_io hwmon found`, this machine's fan board isn't detected and the fan tests can't run.

**6. Open three terminals**, all in the `system76-power` folder:

- **Terminal A** runs the main test commands.
- **Terminal B** is for second commands while A is busy.
- **Terminal C** shows the daemon log. Start it now and leave it running:

  ```bash
  journalctl -u com.system76.PowerDaemon -f
  ```

## Part 2: The tests

Run them in order. Each one says what to do and what you should see. Write down any result that doesn't match.

### Test 1: Normal behaviour, before any claim

**Terminal A:**

```bash
python3 fan-claim-test/fan_claim_test.py status
```

**Expect:** `pwm1_enable=1` and fan speeds that system76-power picked from its curve. This is the baseline.

### Test 2: A claim stops system76-power from changing the fans

**Terminal A:**

```bash
sudo python3 fan-claim-test/fan_claim_test.py hold --pwm 200
```

This claims fan control, sets all fans to 200, then prints the fan values every 2 seconds. Leave it running for about 30 seconds. To make sure system76-power would want to change the speed, put the CPU under load in Terminal B, for example `stress-ng --cpu 0 --timeout 60s` or `yes > /dev/null` (stop it with Ctrl+C).

**Expect:**

- `claim accepted` at the top.
- Every status line keeps showing `pwm1=200`, even under load.

If the value drifts away from 200, system76-power is still writing the fans and the test **fails**.

**Leave Terminal A running** for Test 3.

### Test 3: A second program can't take the claim

**Terminal B**, while Terminal A from Test 2 is still holding:

```bash
sudo python3 fan-claim-test/fan_claim_test.py contend
```

**Expect:** `claim REJECTED: ... fan control already claimed`.

### Test 4: Releasing gives control back

Go back to **Terminal A** and press **Enter**.

**Expect:**

- `release accepted`.
- The final status line shows the fans **no longer at 200**: they're back to system76-power's curve speed. That means system76-power took control again.

### Test 5: If the holding program is killed, the claim is released

**Terminal A:**

```bash
sudo python3 fan-claim-test/fan_claim_test.py hold --pwm 200
```

Note the `pid` number on the first line, for example `pid 4321`.

**Terminal B** (replace `4321` with that pid):

```bash
sudo kill -9 4321
python3 fan-claim-test/fan_claim_test.py status
```

**Expect:**

- Terminal C (the log) shows `fan control claimant :1.xxx disconnected; releasing`.
- `status` shows the fans **no longer at 200**: system76-power's curve is running again.

### Test 6: If the holding program exits without releasing, the claim is released

**Terminal A:**

```bash
sudo python3 fan-claim-test/fan_claim_test.py hold --pwm 200 --no-release
```

Press **Enter**. The script exits without calling `ReleaseFanControl`.

**Expect:** the same as Test 5. The log shows `disconnected; releasing`, and `python3 fan-claim-test/fan_claim_test.py status` shows the fans no longer at 200.

### Test 7: A program that disconnects instantly doesn't leave a stuck claim

This covers a program that sends a claim and exits before the daemon has finished processing it.

**Terminal A:**

```bash
sudo python3 fan-claim-test/fan_claim_test.py fire-and-forget
sudo python3 fan-claim-test/fan_claim_test.py contend
```

**Expect:** the `contend` command prints `claim accepted; releasing`. If it says `claim REJECTED`, a stuck claim was left behind and the test **fails**. To recover from that, run `sudo systemctl restart com.system76.PowerDaemon`.

### Test 8: Stopping system76-power while claimed puts the fans back to automatic

**Terminal A:**

```bash
sudo python3 fan-claim-test/fan_claim_test.py hold --pwm 200
```

**Terminal B:**

```bash
sudo systemctl stop com.system76.PowerDaemon
python3 fan-claim-test/fan_claim_test.py status
```

**Expect:** `pwm1_enable=2`. Without the patch's fix, it would stay at `1` with the fans stuck at 200.

Then clean up: press **Ctrl+C** in Terminal A (its release call will fail because the daemon is stopped, which is expected), and start the daemon again:

```bash
sudo systemctl start com.system76.PowerDaemon
```

### Test 9: Lots of D-Bus traffic doesn't slow the fan curve

The patch watches for programs disconnecting from D-Bus. This test checks that a flood of connections doesn't stop system76-power from updating the fans every second.

**Terminal A** starts the flood (it takes several seconds):

```bash
python3 fan-claim-test/fan_claim_test.py churn --count 5000
```

**Terminal B**, while the flood is running, set a fan speed by hand and check it a few seconds later:

```bash
HW=$(dirname $(grep -lE '^system76(_thelio)?_io$' /sys/class/hwmon/hwmon*/name | head -1))
echo 255 | sudo tee $HW/pwm1
sleep 3
python3 fan-claim-test/fan_claim_test.py status
```

**Expect:** `pwm1` is back to system76-power's curve value, not `255`. That shows the curve is still running every second during the flood. If `churn` finishes before you get to the `status` command, run it again with a bigger `--count`.

### Test 10: A normal user can't claim the fans

**Terminal A**, without `sudo`:

```bash
python3 fan-claim-test/fan_claim_test.py contend
```

**Expect:** `claim REJECTED: ... Operation not permitted by Polkit`.

## Part 3: Put the original system76-power back

When you're done testing:

```bash
sudo apt install --reinstall system76-power
sudo systemctl restart com.system76.PowerDaemon
```

To confirm, the `busctl ... | grep FanControl` command from setup step 4 should now print nothing.

## Results checklist

| Test | What it checks | Pass? |
|---|---|---|
| 1 | Normal fan curve before any claim | |
| 2 | Claim stops system76-power writing the fans | |
| 3 | Second program's claim is rejected | |
| 4 | Release hands the fans back to system76-power's curve | |
| 5 | Killed holder releases the claim | |
| 6 | Holder exiting without release releases the claim | |
| 7 | Instant disconnect leaves no stuck claim | |
| 8 | Stopping the daemon returns fans to automatic | |
| 9 | Fan curve keeps running during heavy D-Bus traffic | |
| 10 | Non-root user is refused | |
