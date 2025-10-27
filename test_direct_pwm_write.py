#!/usr/bin/env python3
"""Test direct PWM write to verify hardware works"""

import subprocess
import sys

def read_pwm():
    try:
        with open('/sys/class/hwmon/hwmon3/pwm1', 'r') as f:
            return f.read().strip()
    except Exception as e:
        print(f"Failed to read PWM: {e}")
        sys.exit(1)

def write_pwm(value):
    try:
        # Use sudo to write
        result = subprocess.run(
            ['sudo', 'tee', '/sys/class/hwmon/hwmon3/pwm1'],
            input=value,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(f"Failed to write PWM: {result.stderr}")
            sys.exit(1)
        return True
    except Exception as e:
        print(f"Error writing PWM: {e}")
        sys.exit(1)

print("Current PWM value:", read_pwm())
print("Writing 128...")
write_pwm("128")
import time
time.sleep(0.5)
print("PWM after write:", read_pwm())
print("Waiting 2 seconds...")
time.sleep(2)
print("PWM after 2 seconds:", read_pwm())

