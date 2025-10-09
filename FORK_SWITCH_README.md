# System76 Power Fork Switch Scripts

This directory contains scripts to switch between the original `system76-power` and your fork `system76-power-fork`.

## Scripts

### 1. `switch-to-fork.sh`
Switches from the original `system76-power` to your fork `system76-power-fork`.

**Prerequisites:**
- Your fork must be built: `cargo build --release`
- The fork binary must be named `system76-power-fork`
- Run as root: `sudo ./switch-to-fork.sh`

**What it does:**
- Creates a backup of the current installation
- Stops the current service
- Installs your fork binary
- Updates systemd service file
- Updates DBus and Polkit configurations
- Starts the service with your fork
- Verifies the installation

### 2. `rollback-to-original.sh`
Rolls back from your fork to the original `system76-power`.

**Usage:**
```bash
# Auto-detect most recent backup
sudo ./rollback-to-original.sh

# Use specific backup directory
sudo ./rollback-to-original.sh /tmp/system76-power-backup-20241008-143502

# Show help
./rollback-to-original.sh --help
```

**What it does:**
- Finds the backup directory (auto-detects or uses specified)
- Stops the current service
- Restores the original binary and configurations
- Starts the service with the original system76-power
- Verifies the installation
- Optionally cleans up backup files

## Important Notes

1. **Always run as root**: Both scripts require root privileges (`sudo`)

2. **Backup safety**: The switch script creates automatic backups in `/tmp/system76-power-backup-YYYYMMDD-HHMMSS/`

3. **Service management**: The scripts handle stopping/starting the `com.system76.PowerDaemon` service

4. **Configuration updates**: The scripts update:
   - Binary executable (`/usr/bin/system76-power`)
   - Systemd service file (`/lib/systemd/system/com.system76.PowerDaemon.service`)
   - DBus configuration (`/usr/share/dbus-1/system.d/com.system76.PowerDaemon.conf`)
   - Polkit policy (`/usr/share/polkit-1/actions/com.system76.PowerDaemon.policy`)
   - DBus interface (`/usr/share/dbus-1/interfaces/com.system76.PowerDaemon.xml`)

5. **Verification**: Both scripts verify the installation and show service status

## Troubleshooting

If something goes wrong:

1. **Service won't start**: Check `systemctl status com.system76.PowerDaemon` for errors
2. **Permission issues**: Ensure you're running with `sudo`
3. **Binary not found**: Make sure you've built your fork with `cargo build --release`
4. **Rollback needed**: Use `rollback-to-original.sh` to restore the original

## Example Workflow

```bash
# 1. Build your fork
cargo build --release

# 2. Switch to your fork
sudo ./switch-to-fork.sh

# 3. Test your changes...

# 4. If needed, rollback to original
sudo ./rollback-to-original.sh
```
