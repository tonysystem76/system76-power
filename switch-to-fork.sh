#!/bin/bash

# Script to switch from system76-power to system76-power-fork
# This script assumes you have built system76-power-fork and it's available

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
ORIGINAL_BINARY="/usr/bin/system76-power"
FORK_BINARY="system76-power"
SERVICE_NAME="com.system76.PowerDaemon"
BACKUP_DIR="/tmp/system76-power-backup-$(date +%Y%m%d-%H%M%S)"

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

# Function to check if fork binary exists
check_fork_binary() {
    if [[ ! -f "./target/release/${FORK_BINARY}" ]]; then
        print_error "Binary not found at ./target/release/${FORK_BINARY}"
        print_error "Please build the project first with: cargo build --release"
        exit 1
    fi
}

# Function to create backup
create_backup() {
    print_status "Creating backup of current installation..."
    mkdir -p "$BACKUP_DIR"
    
    # Backup binary
    if [[ -f "$ORIGINAL_BINARY" ]]; then
        cp "$ORIGINAL_BINARY" "$BACKUP_DIR/"
        print_success "Backed up binary to $BACKUP_DIR"
    fi
    
    # Backup systemd service
    if [[ -f "/lib/systemd/system/${SERVICE_NAME}.service" ]]; then
        cp "/lib/systemd/system/${SERVICE_NAME}.service" "$BACKUP_DIR/"
        print_success "Backed up systemd service to $BACKUP_DIR"
    fi
    
    # Backup DBus configuration
    if [[ -f "/usr/share/dbus-1/system.d/${SERVICE_NAME}.conf" ]]; then
        cp "/usr/share/dbus-1/system.d/${SERVICE_NAME}.conf" "$BACKUP_DIR/"
        print_success "Backed up DBus config to $BACKUP_DIR"
    fi
    
    # Backup Polkit policy
    if [[ -f "/usr/share/polkit-1/actions/${SERVICE_NAME}.policy" ]]; then
        cp "/usr/share/polkit-1/actions/${SERVICE_NAME}.policy" "$BACKUP_DIR/"
        print_success "Backed up Polkit policy to $BACKUP_DIR"
    fi
    
    # Backup DBus interface
    if [[ -f "/usr/share/dbus-1/interfaces/${SERVICE_NAME}.xml" ]]; then
        cp "/usr/share/dbus-1/interfaces/${SERVICE_NAME}.xml" "$BACKUP_DIR/"
        print_success "Backed up DBus interface to $BACKUP_DIR"
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

# Function to install fork binary
install_fork_binary() {
    print_status "Installing fork binary..."
    
    # Install the fork binary
    install -D -m 0755 "./target/release/${FORK_BINARY}" "$ORIGINAL_BINARY"
    print_success "Fork binary installed to $ORIGINAL_BINARY"
}

# Function to update systemd service
update_systemd_service() {
    print_status "Updating systemd service file..."
    
    # Create updated service file
    cat > "/lib/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=System76 Power Daemon (Fork)

[Service]
ExecStart=/usr/bin/system76-power daemon
Restart=on-failure
Type=dbus
BusName=com.system76.PowerDaemon

[Install]
WantedBy=multi-user.target
Alias=system76-power.service
EOF
    
    # Reload systemd
    systemctl daemon-reload
    print_success "Systemd service updated and reloaded"
}

# Function to update DBus configuration
update_dbus_config() {
    print_status "Updating DBus configuration..."
    
    # Update DBus config if it exists
    if [[ -f "/usr/share/dbus-1/system.d/${SERVICE_NAME}.conf" ]]; then
        # Add a comment to indicate this is the fork version
        sed -i '1i<!-- Updated for system76-power-fork -->' "/usr/share/dbus-1/system.d/${SERVICE_NAME}.conf"
        print_success "DBus configuration updated"
    fi
}

# Function to update Polkit policy
update_polkit_policy() {
    print_status "Updating Polkit policy..."
    
    # Update Polkit policy if it exists
    if [[ -f "/usr/share/polkit-1/actions/${SERVICE_NAME}.policy" ]]; then
        # Add a comment to indicate this is the fork version
        sed -i '1i<!-- Updated for system76-power-fork -->' "/usr/share/polkit-1/actions/${SERVICE_NAME}.policy"
        print_success "Polkit policy updated"
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

# Function to show rollback instructions
show_rollback_instructions() {
    print_status "Rollback instructions:"
    echo "If you need to rollback to the original system76-power:"
    echo "1. Stop the service: sudo systemctl stop $SERVICE_NAME"
    echo "2. Restore from backup: sudo cp $BACKUP_DIR/* /usr/bin/"
    echo "3. Restore systemd service: sudo cp $BACKUP_DIR/${SERVICE_NAME}.service /lib/systemd/system/"
    echo "4. Reload systemd: sudo systemctl daemon-reload"
    echo "5. Start service: sudo systemctl start $SERVICE_NAME"
    echo ""
    echo "Backup location: $BACKUP_DIR"
}

# Main execution
main() {
    print_status "Starting switch from system76-power to system76-power-fork..."
    echo ""
    
    # Pre-flight checks
    check_root
    check_fork_binary
    
    # Create backup
    create_backup
    echo ""
    
    # Stop current service
    stop_service
    echo ""
    
    # Install fork
    install_fork_binary
    echo ""
    
    # Update configurations
    update_systemd_service
    update_dbus_config
    update_polkit_policy
    echo ""
    
    # Start service
    start_service
    echo ""
    
    # Verify installation
    verify_installation
    echo ""
    
    # Show rollback instructions
    show_rollback_instructions
    
    print_success "Switch to system76-power-fork completed successfully!"
}

# Run main function
main "$@"
