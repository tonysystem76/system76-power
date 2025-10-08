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
        log::info!("Creating FanDbus with nvidia_exists={}", nvidia_exists);
        let fan_daemon = FanDaemon::new(nvidia_exists);
        log::info!("FanDaemon created successfully");
        Self { fan: std::sync::Mutex::new(fan_daemon) }
    }
}

#[dbus_interface(name = "com.system76.PowerDaemon.Fan")]
impl FanDbus {
    /// 0 to 255 is the standard Linux hwmon pwm unit
    fn set_duty(&self, duty: u8) -> zbus::fdo::Result<()> {
        log::info!("Fan DBus: set_duty called with duty={}", duty);
        
        let fan_opt = self.fan.lock();
        match fan_opt {
            Ok(fan) => {
                log::debug!("Fan DBus: successfully acquired fan lock, setting duty to {}", duty);
                fan.set_duty(Some(duty));
                log::info!("Fan DBus: set_duty completed successfully");
                Ok(())
            }
            Err(e) => {
                log::error!("Fan DBus: failed to acquire fan lock: {}", e);
                Err(zbus::fdo::Error::Failed(format!("Failed to acquire fan lock: {}", e)))
            }
        }
    }

    /// Return to automatic fan control
    fn set_auto(&self) -> zbus::fdo::Result<()> {
        log::info!("Fan DBus: set_auto called");
        
        let fan_opt = self.fan.lock();
        match fan_opt {
            Ok(fan) => {
                log::debug!("Fan DBus: successfully acquired fan lock, setting to auto mode");
                fan.set_duty(None);
                log::info!("Fan DBus: set_auto completed successfully");
                Ok(())
            }
            Err(e) => {
                log::error!("Fan DBus: failed to acquire fan lock: {}", e);
                Err(zbus::fdo::Error::Failed(format!("Failed to acquire fan lock: {}", e)))
            }
        }
    }

    /// Pin CPU fan at controller max speed
    fn full_speed(&self) -> zbus::fdo::Result<()> {
        log::info!("Fan DBus: full_speed called");
        
        let fan_opt = self.fan.lock();
        match fan_opt {
            Ok(fan) => {
                log::debug!("Fan DBus: successfully acquired fan lock, setting to full speed (255)");
                fan.set_duty(Some(255));
                log::info!("Fan DBus: full_speed completed successfully");
                Ok(())
            }
            Err(e) => {
                log::error!("Fan DBus: failed to acquire fan lock: {}", e);
                Err(zbus::fdo::Error::Failed(format!("Failed to acquire fan lock: {}", e)))
            }
        }
    }
}
