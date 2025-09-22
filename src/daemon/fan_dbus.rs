// Copyright 2025
// SPDX-License-Identifier: GPL-3.0-only

use std::sync::{Arc, Mutex};
use zbus::dbus_interface;

pub struct FanDbus {
    // Shared override PWM (0-255). None means auto.
    override_pwm: Arc<Mutex<Option<u8>>>,
}

impl FanDbus {
    pub fn new(override_pwm: Arc<Mutex<Option<u8>>>) -> Self { Self { override_pwm } }
}

#[dbus_interface(name = "com.system76.PowerDaemon.Fan")]
impl FanDbus {
    /// Set the current duty cycle, from 0 to 255
    fn set_duty(&self, duty: u8) -> zbus::fdo::Result<()> {
        if let Ok(mut guard) = self.override_pwm.lock() { *guard = Some(duty); }
        Ok(())
    }

    /// Return to automatic fan control
    fn set_auto(&self) -> zbus::fdo::Result<()> {
        if let Ok(mut guard) = self.override_pwm.lock() { *guard = None; }
        Ok(())
    }

    /// Set fan to full speed (override)
    fn full_speed(&self) -> zbus::fdo::Result<()> {
        if let Ok(mut guard) = self.override_pwm.lock() { *guard = Some(255); }
        Ok(())
    }
}

// (duplicate block removed)
