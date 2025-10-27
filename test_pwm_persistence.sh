#!/bin/bash

# Test script to verify PWM values persist after daemon step() runs

echo "=== Testing PWM Persistence ==="
echo ""

# Find the system76 hwmon device
HWMON_DIR=""
for i in 0 1 2 3 4 5 6 7 8; do
    if [ -d /sys/class/hwmon/hwmon$i ]; then
        NAME=$(cat /sys/class/hwmon/hwmon$i/name 2>/dev/null);
        if echo "$NAME" | grep -q "system76"; then
            HWMON_DIR="/sys/class/hwmon/hwmon$i";
            break;
        fi
    fi
done

if [ -z "$HWMON_DIR" ]; then
    echo "ERROR: Could not find system76 hwmon device"
    exit 1
fi

HWMON=$(basename $HWMON_DIR)
echo "Using device: $HWMON_DIR"
echo ""

# Get initial PWM value
INITIAL_PWM=$(cat $HWMON_DIR/pwm1)
echo "Initial PWM value: $INITIAL_PWM"

# Set PWM to a known value via D-Bus
echo "Setting PWM to 128 via D-Bus..."
dbus-send --system --dest=com.system76.PowerDaemon --type=method_call /com/system76/PowerDaemon/Fan com.system76.PowerDaemon.Fan.SetDuty byte:128

sleep 1

# Check PWM value after D-Bus call
PWM_AFTER_DBUS=$(cat $HWMON_DIR/pwm1)
echo "PWM value after D-Bus call: $PWM_AFTER_DBUS"

if [ "$PWM_AFTER_DBUS" != "128" ]; then
    echo "WARNING: PWM value did not change after D-Bus call!"
    echo "Expected: 128, Got: $PWM_AFTER_DBUS"
    exit 1
fi

echo "✅ PWM value set successfully via D-Bus"

# Wait for daemon step() to run (runs every 1 second)
echo "Waiting 3 seconds for daemon step() to run..."
sleep 3

# Check PWM value after daemon step()
FINAL_PWM=$(cat $HWMON_DIR/pwm1)
echo "PWM value after daemon step() runs: $FINAL_PWM"

if [ "$FINAL_PWM" != "128" ]; then
    echo "❌ PWM value was overwritten by daemon step()!"
    echo "Expected: 128, Got: $FINAL_PWM"
    echo ""
    echo "This indicates the override logic is NOT working correctly."
    exit 1
fi

echo "✅ PWM value persisted after daemon step()!"
echo ""
echo "Test PASSED: Manual PWM override is working correctly!"

