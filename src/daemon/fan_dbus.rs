// Copyright 2025
// SPDX-License-Identifier: GPL-3.0-only

use std::fs;
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

    /// Get current temperature reading in thousandths of Celsius
    fn get_current_temperature(&self) -> zbus::fdo::Result<u32> {
        log::debug!("Fan DBus: get_current_temperature called");
        
        let fan_opt = self.fan.lock();
        match fan_opt {
            Ok(fan) => {
                let temp = fan.get_temp().unwrap_or(0);
                log::debug!("Fan DBus: current temperature: {} thousandths of Celsius", temp);
                Ok(temp)
            }
            Err(e) => {
                log::error!("Fan DBus: failed to acquire fan lock: {}", e);
                Err(zbus::fdo::Error::Failed(format!("Failed to acquire fan lock: {}", e)))
            }
        }
    }

    /// Get current fan duty/PWM value (0-255)
    fn get_current_duty(&self) -> zbus::fdo::Result<u8> {
        log::debug!("Fan DBus: get_current_duty called");

        let fan_opt = self.fan.lock();
        match fan_opt {
            Ok(fan) => {
                // Get current temperature, then calculate what duty would be applied
                let duty = fan.get_temp()
                    .and_then(|temp| fan.get_duty(temp))
                    .unwrap_or(0);
                log::debug!("Fan DBus: current duty: {}", duty);
                Ok(duty)
            }
            Err(e) => {
                log::error!("Fan DBus: failed to acquire fan lock: {}", e);
                Err(zbus::fdo::Error::Failed(format!("Failed to acquire fan lock: {}", e)))
            }
        }
    }
    
    /// Get current fan speeds in RPM
    fn get_fan_speeds(&self) -> zbus::fdo::Result<Vec<u32>> {
        log::debug!("Fan DBus: get_fan_speeds called");
        
        let fan_opt = self.fan.lock();
        match fan_opt {
            Ok(_fan) => {
                let mut speeds = Vec::new();
                
                // Read fan speeds from system76_thelio_io device
                if let Ok(entries) = fs::read_dir("/sys/class/hwmon") {
                    for entry in entries.flatten() {
                        let path = entry.path();
                        let name_path = path.join("name");
                        if let Ok(name) = fs::read_to_string(&name_path) {
                            let name = name.trim();
                            if name == "system76_thelio_io" {
                                // Read fan1_input, fan2_input, fan3_input, fan4_input
                                for fan_num in 1..=4 {
                                    let fan_path = path.join(format!("fan{}_input", fan_num));
                                    if let Ok(speed_str) = fs::read_to_string(&fan_path) {
                                        if let Ok(speed) = speed_str.trim().parse::<u32>() {
                                            speeds.push(speed);
                                            log::debug!("Fan {} speed: {} RPM", fan_num, speed);
                                        }
                                    }
                                }
                                break;
                            }
                        }
                    }
                }
                
                log::debug!("Fan DBus: fan speeds: {:?}", speeds);
                Ok(speeds)
            }
            Err(e) => {
                log::error!("Fan DBus: failed to acquire fan lock: {}", e);
                Err(zbus::fdo::Error::Failed(format!("Failed to acquire fan lock: {}", e)))
            }
        }
    }

    /// Get current fan curve points
    fn get_fan_curve(&self) -> zbus::fdo::Result<Vec<(i16, u16)>> {
        log::debug!("Fan DBus: get_fan_curve called");
        
        let fan_opt = self.fan.lock();
        match fan_opt {
            Ok(fan) => {
                let curve = fan.get_fan_curve();
                log::debug!("Fan DBus: fan curve: {:?}", curve);
                Ok(curve)
            }
            Err(e) => {
                log::error!("Fan DBus: failed to acquire fan lock: {}", e);
                Err(zbus::fdo::Error::Failed(format!("Failed to acquire fan lock: {}", e)))
            }
        }
    }
        /// Set custom fan curve points (temperature, duty pairs)
    fn set_fan_curve(&self, points: Vec<(i16, u16)>) -> zbus::fdo::Result<()> {
        log::debug!("Fan DBus: set_fan_curve called with {} points", points.len());
        
        let fan_opt = self.fan.lock();
        match fan_opt {
            Ok(mut fan) => {
                match fan.set_fan_curve(points) {
                    Ok(()) => {
                        log::info!("Fan DBus: set_fan_curve completed successfully");
                        Ok(())
                    }
                    Err(e) => {
                        log::error!("Fan DBus: failed to set fan curve: {}", e);
                        Err(zbus::fdo::Error::Failed(format!("Failed to set fan curve: {}", e)))
                    }
                }
            }
            Err(e) => {
                log::error!("Fan DBus: failed to acquire fan lock: {}", e);
                Err(zbus::fdo::Error::Failed(format!("Failed to acquire fan lock: {}", e)))
            }
        }
    }
        /// Apply the current fan curve based on current temperature
    fn apply_fan_curve(&self) -> zbus::fdo::Result<()> {
        log::debug!("Fan DBus: apply_fan_curve called");
        
        let fan_opt = self.fan.lock();
        match fan_opt {
            Ok(fan) => {
                // Get current temperature and apply fan curve
                if let Some(temp) = fan.get_temp() {
                    if let Some(duty) = fan.get_duty(temp) {
                        fan.set_duty(Some(duty));
                        log::info!("Fan DBus: apply_fan_curve completed successfully - temp: {}, duty: {}", temp, duty);
                        Ok(())
                    } else {
                        log::warn!("Fan DBus: could not calculate duty for temperature {}", temp);
                        Err(zbus::fdo::Error::Failed("Could not calculate duty for current temperature".to_string()))
                    }
                } else {
                    log::warn!("Fan DBus: could not get current temperature");
                    Err(zbus::fdo::Error::Failed("Could not get current temperature".to_string()))
                }
            }
            Err(e) => {
                log::error!("Fan DBus: failed to acquire fan lock: {}", e);
                Err(zbus::fdo::Error::Failed(format!("Failed to acquire fan lock: {}", e)))
            }
        }
    }
}
