// Copyright 2025
// SPDX-License-Identifier: GPL-3.0-only

use zbus::dbus_interface;

use crate::fan::FanDaemon;

pub struct FanDbus {
    // Keep a dedicated FanDaemon instance to perform immediate writes
    fan: std::sync::Mutex<FanDaemon>,
}

impl FanDbus {
    pub fn new(nvidia_exists: bool) -> Self {
        Self { fan: std::sync::Mutex::new(FanDaemon::new(nvidia_exists)) }
    }
}

#[dbus_interface(name = "com.system76.PowerDaemon.Fan")]
impl FanDbus {
    /// 0 to 255 is the standard Linux hwmon pwm unit
    fn set_duty(&self, duty: u8) -> zbus::fdo::Result<()> {
        let fan_opt = self.fan.lock();
        if let Ok(fan) = fan_opt {
            fan.set_duty(Some(duty));
        }
        Ok(())
    }

    /// Return to automatic fan control
    fn set_auto(&self) -> zbus::fdo::Result<()> {
        let fan_opt = self.fan.lock();
        if let Ok(fan) = fan_opt {
            fan.set_duty(None);
        }
        Ok(())
    }

    /// Pin CPU fan at controller max speed
    fn full_speed(&self) -> zbus::fdo::Result<()> {
        let fan_opt = self.fan.lock();
        if let Ok(fan) = fan_opt {
            fan.set_duty(Some(255));
        }
        Ok(())
    }
}
