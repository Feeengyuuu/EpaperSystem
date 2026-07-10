#!/bin/bash
set -Eeuo pipefail

# Formatting stuff
if [[ -t 1 ]] && command -v tput >/dev/null 2>&1 && tput colors >/dev/null 2>&1; then
  bold=$(tput bold)
  normal=$(tput sgr0)
  red=$(tput setaf 1)
  green=$(tput setaf 2)
else
  bold=""
  normal=""
  red=""
  green=""
fi

APPNAME="inkypi"
INSTALL_PATH="/opt/$APPNAME"
BINPATH="/usr/local/bin"
VENV_PATH="$INSTALL_PATH/venv_$APPNAME"
SERVICE_FILE="/etc/systemd/system/$APPNAME.service"
PRIVILEGED_SOCKET_FILE="/etc/systemd/system/inkypi-privileged.socket"
PRIVILEGED_SERVICE_FILE="/etc/systemd/system/inkypi-privileged.service"
PRIVILEGED_BROKER="/usr/local/libexec/inkypi-privileged"
CONFIG_DIR="/var/lib/inkypi/config"

echo_success() {
  echo -e "$1 [\e[32m\xE2\x9C\x94\e[0m]"
}

echo_override() {
  echo -e "\r$1"
}

echo_header() {
  echo -e "${bold}$1${normal}"
}

echo_error() {
  echo -e "${red}$1${normal} [\e[31m\xE2\x9C\x98\e[0m]\n"
}

check_permissions() {
  # Ensure the script is run with sudo
  if [ "$EUID" -ne 0 ]; then
    echo_error "ERROR: Uninstallation requires root privileges. Please run it with sudo."
    exit 1
  fi
}

stop_service() {
  echo "Stopping $APPNAME service"
  if /usr/bin/systemctl is-active --quiet "$APPNAME.service"
  then
    /usr/bin/systemctl stop "$APPNAME.service"
    echo_success "\tService stopped successfully."
  else
    echo_success "\tService is not running."
  fi
}

disable_service() {
  echo "Disabling $APPNAME service"
  if [ -f "$SERVICE_FILE" ]; then
    /usr/bin/systemctl disable "$APPNAME.service"
    rm -f "$SERVICE_FILE"
    /usr/bin/systemctl daemon-reload
    echo_success "\tService disabled and removed."
  else
    echo_success "\tService file does not exist. Nothing to remove."
  fi
  if /usr/bin/systemctl is-active --quiet inkypi-privileged.service; then
    /usr/bin/systemctl stop inkypi-privileged.service
  fi
  if /usr/bin/systemctl is-active --quiet inkypi-privileged.socket; then
    /usr/bin/systemctl stop inkypi-privileged.socket
  fi
  if /usr/bin/systemctl is-enabled --quiet inkypi-privileged.socket; then
    /usr/bin/systemctl disable inkypi-privileged.socket >/dev/null 2>&1
  fi
  rm -f "$PRIVILEGED_SOCKET_FILE" "$PRIVILEGED_SERVICE_FILE" "$PRIVILEGED_BROKER"
  /usr/bin/systemctl daemon-reload
}

remove_files() {
  echo "Removing application files"
  echo_success "\tPreserving /etc/inkypi, /var/lib/inkypi, and /var/cache/inkypi."

  # Remove the installation directory
  if [ -d "$INSTALL_PATH" ]; then
    rm -rf "$INSTALL_PATH"
    echo_success "\tInstallation directory $INSTALL_PATH removed."
  else
    echo_success "\tInstallation directory $INSTALL_PATH does not exist."
  fi

  # Remove the executable
  if [ -f "$BINPATH/$APPNAME" ]; then
    rm -f "$BINPATH/$APPNAME"
    echo_success "\tExecutable $BINPATH/$APPNAME removed."
  else
    echo_success "\tExecutable $BINPATH/$APPNAME does not exist."
  fi
}

confirm_uninstall() {
  echo -e "${bold}Are you sure you want to uninstall $APPNAME? (y/N): ${normal}"
  read -r confirmation
  if [[ "$confirmation" != "y" && "$confirmation" != "Y" ]]; then
    echo_error "Uninstallation cancelled."
    exit 1
  fi
}

check_permissions
confirm_uninstall
stop_service
disable_service
remove_files

echo_success "Uninstallation complete."
echo_header "All components of $APPNAME have been removed."
