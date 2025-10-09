#!/bin/bash

# Script to rollback from system76-power-fork back to original system76-power
# This script restores from a backup created by switch-to-fork.sh

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
ORIGINAL_BINARY="/usr/bin/system76-power"
SERVICE_NAME="com.system76.PowerDaemon"

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        print_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

# Function to find backup directory
find_backup_directory() {
    # Look for backup directories
    BACKUP_DIRS=($(ls -td /tmp/system76-power-backup-* 2>/dev/null || true))
    
    if [[ ${#BACKUP_DIRS[@]} -eq 0 ]]; then
        print_error "No backup directories found in /tmp/"
        print_error "Please specify the backup directory manually:"
        print_error "Usage: $0 <backup_directory>"
        exit 1
    elif [[ ${#BACKUP_DIRS[@]} -eq 1 ]]; then
        BACKUP_DIR="${BACKUP_DIRS[0]}"
        print_status "Using backup directory: $BACKUP_DIR"
    else
        print_warning "Multiple backup directories found:"
        for i in "${!BACKUP_DIRS[@]}"; do
            echo "  $((i+1)). ${BACKUP_DIRS[i]}"
        done
        echo ""
        read -p "Select backup directory (1-${#BACKUP_DIRS[@]}): " selection
        
        if [[ "$selection" =~ ^[0-9]+$ ]] && [[ "$selection" -ge 1 ]] && [[ "$selection" -le ${#BACKUP_DIRS[@]} ]]; then
            BACKUP_DIR="${BACKUP_DIRS[$((selection-1))]}"
            print_status "Using backup directory: $BACKUP_DIR"
        else
            print_error "Invalid selection"
            exit 1
        fi
    fi
    
    # Verify backup directory exists and has files
    if [[ ! -d "$BACKUP_DIR" ]]; then
        print_error "Backup directory does not exist: $BACKUP_DIR"
        exit 1
    fi
    
    if [[ ! -f "$BACKUP_DIR/system76-power" ]]; then
        print_error "Backup binary not found in $BACKUP_DIR"
        exit 1
    fi
}

# Function to stop service
stop_service() {
    print_status "Stopping ${SERVICE_NAME} service..."
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        systemctl stop "$SERVICE_NAME"
        print_success "Service stopped"
    else
        print_warning "Service was not running"
    fi
}

# Function to restore binary
restore_binary() {
    print_status "Restoring original binary..."
    
    if [[ -f "$BACKUP_DIR/system76-power" ]]; then
        cp "$BACKUP_DIR/system76-power" "$ORIGINAL_BINARY"
        chmod +x "$ORIGINAL_BINARY"
        print_success "Original binary restored"
    else
        print_error "Backup binary not found"
        exit 1
    fi
}

# Function to restore systemd service
restore_systemd_service() {
    print_status "Restoring systemd service file..."
    
    if [[ -f "$BACKUP_DIR/${SERVICE_NAME}.service" ]]; then
        cp "$BACKUP_DIR/${SERVICE_NAME}.service" "/lib/systemd/system/"
        systemctl daemon-reload
        print_success "Systemd service restored and reloaded"
    else
        print_warning "Systemd service backup not found, skipping"
    fi
}

# Function to restore DBus configuration
restore_dbus_config() {
    print_status "Restoring DBus configuration..."
    
    if [[ -f "$BACKUP_DIR/${SERVICE_NAME}.conf" ]]; then
        cp "$BACKUP_DIR/${SERVICE_NAME}.conf" "/usr/share/dbus-1/system.d/"
        print_success "DBus configuration restored"
    else
        print_warning "DBus configuration backup not found, skipping"
    fi
}

# Function to restore Polkit policy
restore_polkit_policy() {
    print_status "Restoring Polkit policy..."
    
    if [[ -f "$BACKUP_DIR/${SERVICE_NAME}.policy" ]]; then
        cp "$BACKUP_DIR/${SERVICE_NAME}.policy" "/usr/share/polkit-1/actions/"
        print_success "Polkit policy restored"
    else
        print_warning "Polkit policy backup not found, skipping"
    fi
}

# Function to restore DBus interface
restore_dbus_interface() {
    print_status "Restoring DBus interface..."
    
    if [[ -f "$BACKUP_DIR/${SERVICE_NAME}.xml" ]]; then
        cp "$BACKUP_DIR/${SERVICE_NAME}.xml" "/usr/share/dbus-1/interfaces/"
        print_success "DBus interface restored"
    else
        print_warning "DBus interface backup not found, skipping"
    fi
}

# Function to start service
start_service() {
    print_status "Starting ${SERVICE_NAME} service..."
    systemctl start "$SERVICE_NAME"
    
    # Wait a moment and check status
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        print_success "Service started successfully"
    else
        print_error "Failed to start service"
        systemctl status "$SERVICE_NAME" --no-pager
        exit 1
    fi
}

# Function to verify installation
verify_installation() {
    print_status "Verifying installation..."
    
    # Check if binary exists and is executable
    if [[ -x "$ORIGINAL_BINARY" ]]; then
        print_success "Binary is installed and executable"
    else
        print_error "Binary is not executable"
        exit 1
    fi
    
    # Check service status
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        print_success "Service is running"
    else
        print_error "Service is not running"
        exit 1
    fi
    
    # Show service status
    print_status "Service status:"
    systemctl status "$SERVICE_NAME" --no-pager -l
}

# Function to clean up backup
cleanup_backup() {
    print_status "Cleaning up backup directory..."
    read -p "Do you want to remove the backup directory $BACKUP_DIR? (y/N): " cleanup
    
    if [[ "$cleanup" =~ ^[Yy]$ ]]; then
        rm -rf "$BACKUP_DIR"
        print_success "Backup directory removed"
    else
        print_status "Backup directory preserved at: $BACKUP_DIR"
    fi
}

# Main execution
main() {
    print_status "Starting rollback from system76-power-fork to original system76-power..."
    echo ""
    
    # Pre-flight checks
    check_root
    
    # Handle backup directory argument or auto-detect
    if [[ $# -eq 1 ]]; then
        BACKUP_DIR="$1"
        if [[ ! -d "$BACKUP_DIR" ]]; then
            print_error "Specified backup directory does not exist: $BACKUP_DIR"
            exit 1
        fi
        print_status "Using specified backup directory: $BACKUP_DIR"
    else
        find_backup_directory
    fi
    
    echo ""
    
    # Stop current service
    stop_service
    echo ""
    
    # Restore files
    restore_binary
    restore_systemd_service
    restore_dbus_config
    restore_polkit_policy
    restore_dbus_interface
    echo ""
    
    # Start service
    start_service
    echo ""
    
    # Verify installation
    verify_installation
    echo ""
    
    # Clean up backup
    cleanup_backup
    
    print_success "Rollback to original system76-power completed successfully!"
}

# Show usage if help requested
if [[ "$1" == "-h" ]] || [[ "$1" == "--help" ]]; then
    echo "Usage: $0 [backup_directory]"
    echo ""
    echo "Rollback from system76-power-fork to original system76-power"
    echo ""
    echo "Arguments:"
    echo "  backup_directory    Optional. Specify backup directory to restore from."
    echo "                      If not provided, will auto-detect the most recent backup."
    echo ""
    echo "Examples:"
    echo "  $0                                    # Auto-detect backup"
    echo "  $0 /tmp/system76-power-backup-20241008-143502  # Use specific backup"
    exit 0
fi

# Run main function
main "$@"
