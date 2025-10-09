// Copyright 2025
// SPDX-License-Identifier: GPL-3.0-only

use zbus::dbus_interface;
use anyhow::Context;

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

    async fn check_authorization(&self, action_id: &str) -> zbus::fdo::Result<()> {
        let connection = zbus::Connection::system().await?;
        let polkit = zbus_polkit::policykit1::AuthorityProxy::new(&connection)
            .await
            .context("could not connect to polkit authority daemon")
            .map_err(zbus_error_from_display)?;

        let pid = std::process::id();

        let permitted = if pid == 0 {
            true
        } else {
            let subject = zbus_polkit::policykit1::Subject::new_for_owner(pid, None, None)
                .context("could not create policykit1 subject")
                .map_err(zbus_error_from_display)?;

            polkit
                .check_authorization(
                    &subject,
                    action_id,
                    &std::collections::HashMap::new(),
                    Default::default(),
                    "",
                )
                .await
                .context("could not check policykit authorization")
                .map_err(zbus_error_from_display)?
                .is_authorized
        };

        if permitted {
            Ok(())
        } else {
            Err(zbus_error_from_display("Operation not permitted by Polkit"))
        }
    }
}

fn zbus_error_from_display<E: std::fmt::Display>(why: E) -> zbus::fdo::Error {
    zbus::fdo::Error::Failed(format!("{}", why))
}

#[dbus_interface(name = "com.system76.PowerDaemon.Fan")]
impl FanDbus {
    /// 0 to 255 is the standard Linux hwmon pwm unit
    async fn set_duty(&self, duty: u8) -> zbus::fdo::Result<()> {
        log::info!("Fan DBus: set_duty called with duty={}", duty);
        
        // Check authorization
        self.check_authorization("com.system76.powerdaemon.fan.set-duty").await?;
        
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
                Err(zbus_error_from_display(format!("Failed to acquire fan lock: {}", e)))
            }
        }
    }

    /// Return to automatic fan control
    async fn set_auto(&self) -> zbus::fdo::Result<()> {
        log::info!("Fan DBus: set_auto called");
        
        // Check authorization
        self.check_authorization("com.system76.powerdaemon.fan.set-auto").await?;
        
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
                Err(zbus_error_from_display(format!("Failed to acquire fan lock: {}", e)))
            }
        }
    }

    /// Pin CPU fan at controller max speed
    async fn full_speed(&self) -> zbus::fdo::Result<()> {
        log::info!("Fan DBus: full_speed called");
        
        // Check authorization
        self.check_authorization("com.system76.powerdaemon.fan.full-speed").await?;
        
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
                Err(zbus_error_from_display(format!("Failed to acquire fan lock: {}", e)))
            }
        }
    }
}