# Fan control claim: Thelio test

Test-branch only (`fan-control-claim-test`); not part of the upstream PR.

## Install the patched daemon

```bash
cargo build --release
sudo install -m 0755 target/release/system76-power /usr/bin/system76-power
sudo install -m 0644 data/com.system76.PowerDaemon.policy /usr/share/polkit-1/actions/
sudo systemctl restart com.system76.PowerDaemon
busctl --system introspect com.system76.PowerDaemon /com/system76/PowerDaemon | grep FanControl
```

Undo: `sudo apt install --reinstall system76-power`

Keep a log open in another terminal:

```bash
journalctl -u com.system76.PowerDaemon -f
```

Below, `T` is `sudo python3 fan-claim-test/fan_claim_test.py`.

## Checks

| # | Do | Expect |
|---|----|--------|
| 1 | `T status` | `pwm1_enable=1`, duty follows the stock curve |
| 2 | `T hold --pwm 200`, watch ~30 s (add CPU load) | pwm stays 200 every tick |
| 3 | While 2 is holding, in a 2nd terminal: `T contend` | `claim REJECTED: ... already claimed` |
| 4 | Press Enter in the `hold` terminal | `release accepted`, then `pwm1_enable=2`; curve resumes within ~1 s |
| 5 | `T hold --pwm 200`, then `kill -9 <pid>` from a 2nd terminal | log shows `claimant ... disconnected; releasing`; `T status` shows `pwm1_enable=2` |
| 6 | `T hold --pwm 200 --no-release`, press Enter | same as 5 |
| 7 | `T fire-and-forget`, then `T contend` | `contend` claim is accepted (no stale claim) |
| 8 | `T hold --pwm 200`, then `sudo systemctl stop com.system76.PowerDaemon` | `T status`: `pwm1_enable=2`. Start the daemon again afterwards |
| 9 | `T churn --count 2000` while the daemon is unclaimed | stock curve keeps updating; no stalls |
| 10 | As a normal user (no sudo): `python3 fan-claim-test/fan_claim_test.py contend` | `claim REJECTED: Operation not permitted by Polkit` |
