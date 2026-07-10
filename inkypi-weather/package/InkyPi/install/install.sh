#!/bin/bash
set -Eeuo pipefail

# =============================================================================
# Script Name: install.sh
# Description: This script automates the installation of InkyPI and creation of
#              the InkyPI service.
#
# Usage: ./install.sh [-W <waveshare_device>] [--no-reboot-prompt]
#        -W <waveshare_device> (optional) Install for a Waveshare device,
#                               specifying the device model type, e.g. epd7in3e.
#
#                               If not specified then the Pimoroni Inky display
#                               is assumed.
# =============================================================================

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

SOURCE=${BASH_SOURCE[0]}
while [ -h "$SOURCE" ]; do # resolve $SOURCE until the file is no longer a symlink
  DIR=$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )
  SOURCE=$(readlink "$SOURCE")
  [[ $SOURCE != /* ]] && SOURCE=$DIR/$SOURCE
done
SCRIPT_DIR=$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )

APPNAME="inkypi"
INSTALL_ROOT="/opt/$APPNAME"
INSTALL_PATH="$INSTALL_ROOT/current"
SRC_PATH="$SCRIPT_DIR/../src"
BINPATH="/usr/local/bin"
VENV_PATH="$INSTALL_PATH/venv_$APPNAME"

SERVICE_FILE="$APPNAME.service"
SERVICE_FILE_SOURCE="$SCRIPT_DIR/$SERVICE_FILE"
SERVICE_FILE_TARGET="/etc/systemd/system/$SERVICE_FILE"
PRIVILEGED_UNIT_DIR="$SCRIPT_DIR/privileged"
PRIVILEGED_SOCKET="inkypi-privileged.socket"
PRIVILEGED_SERVICE="inkypi-privileged.service"
PRIVILEGED_BROKER_TARGET="/usr/local/libexec/inkypi-privileged"

APT_REQUIREMENTS_FILE="$SCRIPT_DIR/debian-requirements.txt"
PIP_REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"

#
# Additional requirements for Waveshare support.
#
# empty means no WS support required, otherwise we expect the type of display
# as per the WS naming convention.
WS_TYPE=""
WS_REQUIREMENTS_FILE="$SCRIPT_DIR/ws-requirements.txt"
REBOOT_PROMPT=true

usage() {
  cat <<EOF
Usage: sudo bash install/install.sh [options]

Options:
  -W, --waveshare <model>  Install for a Waveshare display, for example epd7in3e.
  --no-reboot-prompt      Finish without asking to reboot. Used by install/bootstrap.sh.
  -h, --help              Show this help.
EOF
}

# Parse optional Waveshare and automation arguments.
parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -W|--waveshare)
        if [[ $# -lt 2 ]]; then
          echo "Option $1 requires the model type of the Waveshare screen." >&2
          exit 1
        fi
        WS_TYPE="$2"
        echo "Optional parameter WS is set for Waveshare support. Screen type is: $WS_TYPE"
        shift 2
        ;;
      --no-reboot-prompt)
        REBOOT_PROMPT=false
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Invalid option: $1" >&2
        usage
        exit 1
        ;;
    esac
  done
}

check_permissions() {
  # Ensure the script is run with sudo
  if [ "$EUID" -ne 0 ]; then
    echo_error "ERROR: Installation requires root privileges. Please run it with sudo."
    exit 1
  fi
}

fetch_waveshare_driver() {
  echo "Fetching Waveshare driver for: $WS_TYPE"

  DRIVER_DEST="$SRC_PATH/display/waveshare_epd"
  DRIVER_FILE="$DRIVER_DEST/$WS_TYPE.py"
  DRIVER_URL="https://raw.githubusercontent.com/waveshareteam/e-Paper/master/RaspberryPi_JetsonNano/python/lib/waveshare_epd/$WS_TYPE.py"

  # Attempt to download the file
  if [ -f "$DRIVER_FILE" ]; then
    echo_success "\tWaveshare driver '$WS_TYPE.py' already exists at $DRIVER_FILE"
  elif curl --silent --fail -o "$DRIVER_FILE" "$DRIVER_URL"; then
    echo_success "\tWaveshare driver '$WS_TYPE.py' successfully downloaded to $DRIVER_FILE"
  else
    echo_error "ERROR: Failed to download Waveshare driver '$WS_TYPE.py'."
    echo_error "Ensure the model name is correct and exists at:"
    echo_error "https://github.com/waveshareteam/e-Paper/tree/master/RaspberryPi_JetsonNano/python/lib/waveshare_epd"
    exit 1
  fi

  EPD_CONFIG_FILE="$DRIVER_DEST/epdconfig.py"
  EPD_CONFIG_URL="https://raw.githubusercontent.com/waveshareteam/e-Paper/refs/heads/master/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epdconfig.py"
  if [ -f "$EPD_CONFIG_FILE" ]; then
    echo_success "\tWaveshare epdconfig file already exists at $EPD_CONFIG_FILE"
  elif curl --silent --fail -o "$EPD_CONFIG_FILE" "$EPD_CONFIG_URL"; then
    echo_success "\tWaveshare epdconfig file successfully downloaded to $EPD_CONFIG_FILE"
  else
    echo_error "ERROR: Failed to download Waveshare epdconfig file."
    exit 1
  fi
}

enable_interfaces(){
  echo "Enabling interfaces required for $APPNAME"
  #enable spi
  sudo sed -i 's/^dtparam=spi=.*/dtparam=spi=on/' /boot/firmware/config.txt
  sudo sed -i 's/^#dtparam=spi=.*/dtparam=spi=on/' /boot/firmware/config.txt
  sudo raspi-config nonint do_spi 0
  echo_success "\tSPI Interface has been enabled."
  #enable i2c
  sudo sed -i 's/^dtparam=i2c_arm=.*/dtparam=i2c_arm=on/' /boot/firmware/config.txt
  sudo sed -i 's/^#dtparam=i2c_arm=.*/dtparam=i2c_arm=on/' /boot/firmware/config.txt
  sudo raspi-config nonint do_i2c 0
  echo_success "\tI2C Interface has been enabled."

  # Is a Waveshare device specified as an install parameter?
  if [[ -n "$WS_TYPE" ]]; then
    # WS parameter is set for Waveshare support so ensure that both CS lines
    # are enabled in the config.txt file.  This is different to INKY which
    # only needs one line set.n
    echo "Enabling both CS lines for SPI interface in config.txt"
    if ! grep -E -q '^[[:space:]]*dtoverlay=spi0-2cs' /boot/firmware/config.txt; then
        sed -i '/^dtparam=spi=on/a dtoverlay=spi0-2cs' /boot/firmware/config.txt
    else
        echo "dtoverlay for spi0-2cs already specified"
    fi
  else
    # TODO - check if really need the dtparam set for INKY as this seems to be 
    # only for the older screens (as per INKY docs)
    echo "Enabling single CS line for SPI interface in config.txt"
    if ! grep -E -q '^[[:space:]]*dtoverlay=spi0-0cs' /boot/firmware/config.txt; then
        sed -i '/^dtparam=spi=on/a dtoverlay=spi0-0cs' /boot/firmware/config.txt
    else
        echo "dtoverlay for spi0-0cs already specified"
    fi
  fi 
}

show_loader() {
  local pid=$!
  local delay=0.1
  local spinstr='|/-\'
  printf "$1 [${spinstr:0:1}] "
  while kill -0 "$pid" 2>/dev/null; do
    local temp=${spinstr#?}
    printf "\r$1 [${temp:0:1}] "
    spinstr=${temp}${spinstr%"${temp}"}
    sleep ${delay}
  done
  if wait "$pid"; then
    printf "\r$1 [\e[32m\xE2\x9C\x94\e[0m]\n"
  else
    printf "\r$1 [\e[31m\xE2\x9C\x98\e[0m]\n"
    return 1
  fi
}

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

echo_blue() {
  echo -e "\e[38;2;65;105;225m$1\e[0m"
}


install_debian_dependencies() {
  if [ -f "$APT_REQUIREMENTS_FILE" ]; then
    sudo apt-get update > /dev/null &
    show_loader "Fetch available system dependencies updates. " 

    xargs -a "$APT_REQUIREMENTS_FILE" sudo apt-get install -y > /dev/null &
    show_loader "Installing system dependencies. "
  else
    echo "ERROR: System dependencies file $APT_REQUIREMENTS_FILE not found!"
    exit 1
  fi
}

setup_zramswap_service() {
  echo "Enabling and starting zramswap service."
  sudo apt-get install -y zram-tools > /dev/null
  echo -e "ALGO=zstd\nPERCENT=60" | sudo tee /etc/default/zramswap > /dev/null
  sudo systemctl enable --now zramswap
}

setup_earlyoom_service() {
  echo "Enabling and starting earlyoom service."
  sudo apt-get install -y earlyoom > /dev/null
  sudo systemctl enable --now earlyoom
}

create_venv(){
  echo "Creating python virtual environment. "
  python3 -m venv "$VENV_PATH"
  $VENV_PATH/bin/python -m pip install --upgrade pip setuptools wheel > /dev/null
  $VENV_PATH/bin/python -m pip install -r $PIP_REQUIREMENTS_FILE -qq > /dev/null &
  show_loader "\tInstalling python dependencies. "

  # do additional dependencies for Waveshare support.
  if [[ -n "$WS_TYPE" ]]; then
    echo "Adding additional dependencies for waveshare to the python virtual environment. "
    $VENV_PATH/bin/python -m pip install -r $WS_REQUIREMENTS_FILE > ws_pip_install.log &
    show_loader "\tInstalling additional Waveshare python dependencies. "
  fi

}

install_app_service() {
  echo "Installing $APPNAME systemd service."
  if [ -f "$SERVICE_FILE_SOURCE" ]; then
    cp "$SERVICE_FILE_SOURCE" "$SERVICE_FILE_TARGET"
    sudo systemctl daemon-reload
    sudo systemctl enable $SERVICE_FILE
  else
    echo_error "ERROR: Service file $SERVICE_FILE_SOURCE not found!"
    exit 1
  fi
}

ensure_service_user() {
  if ! id -u "$APPNAME" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/inkypi --create-home \
      --shell /usr/sbin/nologin "$APPNAME"
  fi
  for group in gpio spi video render; do
    if getent group "$group" >/dev/null 2>&1; then
      usermod -a -G "$group" "$APPNAME"
    fi
  done
  install -d -o inkypi -g inkypi -m 0750 \
    /var/lib/inkypi \
    /var/lib/inkypi/config \
    /var/lib/inkypi/data \
    /var/lib/inkypi/display \
    /var/lib/inkypi/plugins \
    /var/cache/inkypi
  chown -R -h inkypi:inkypi /var/lib/inkypi /var/cache/inkypi
  chmod -R u+rwX,go-rwx /var/lib/inkypi /var/cache/inkypi
  install -d -o root -g inkypi -m 0770 /etc/inkypi
  if [[ ! -e /etc/inkypi/inkypi.env ]]; then
    local env_source=""
    local env_candidate
    for env_candidate in "/usr/local/inkypi/.env" "$SCRIPT_DIR/../.env"; do
      if [[ -f "$env_candidate" ]]; then
        env_source="$env_candidate"
        echo "Migrating existing runtime environment from $env_candidate"
        break
      fi
    done
    if [[ -n "$env_source" ]]; then
      install -o inkypi -g inkypi -m 0600 "$env_source" /etc/inkypi/inkypi.env
    else
      install -o inkypi -g inkypi -m 0600 /dev/null /etc/inkypi/inkypi.env
    fi
  else
    chown inkypi:inkypi /etc/inkypi/inkypi.env
    chmod 0600 /etc/inkypi/inkypi.env
  fi
}

install_privileged_broker() {
  install -d -o root -g root -m 0755 /usr/local/libexec
  install -o root -g root -m 0755 \
    "$PRIVILEGED_UNIT_DIR/inkypi_privileged.py" "$PRIVILEGED_BROKER_TARGET"
  install -o root -g root -m 0644 \
    "$PRIVILEGED_UNIT_DIR/$PRIVILEGED_SOCKET" "/etc/systemd/system/$PRIVILEGED_SOCKET"
  install -o root -g root -m 0644 \
    "$PRIVILEGED_UNIT_DIR/$PRIVILEGED_SERVICE" "/etc/systemd/system/$PRIVILEGED_SERVICE"
  systemctl daemon-reload
  if systemctl is-active --quiet "$PRIVILEGED_SERVICE"; then
    systemctl stop "$PRIVILEGED_SERVICE"
  fi
  systemctl enable --now "$PRIVILEGED_SOCKET"
}

install_executable() {
  echo "Adding executable to ${BINPATH}/$APPNAME"
  cp $SCRIPT_DIR/inkypi $BINPATH/
  sudo chmod +x $BINPATH/$APPNAME
}

install_config() {
  CONFIG_BASE_DIR="$SCRIPT_DIR/config_base"
  CONFIG_DIR="/var/lib/inkypi/config"
  echo "Copying config files to $CONFIG_DIR"

  # Check and copy device.config if it doesn't exist
  if [ ! -f "$CONFIG_DIR/device.json" ]; then
    local config_source="$CONFIG_BASE_DIR/device.json"
    local candidate
    for candidate in \
      "/usr/local/inkypi/src/config/device.json" \
      "$SRC_PATH/config/device.json"; do
      if [[ ! -f "$candidate" ]]; then
        continue
      fi
      if python3 -c 'import json, sys; value = json.load(open(sys.argv[1], encoding="utf-8")); raise SystemExit(0 if isinstance(value, dict) else 1)' "$candidate"; then
        config_source="$candidate"
        echo "Migrating existing device configuration from $candidate"
        break
      fi
      echo "Ignoring invalid legacy device configuration: $candidate" >&2
    done
    install -o inkypi -g inkypi -m 0600 "$config_source" "$CONFIG_DIR/device.json"
    echo_success "\tCopied device.json to $CONFIG_DIR"
  else
    echo_success "\tdevice.json already exists in $CONFIG_DIR"
  fi
  chown inkypi:inkypi "$CONFIG_DIR/device.json"
  chmod 0600 "$CONFIG_DIR/device.json"
}

#
# Update the device.json file with the supplied Waveshare parameter (if set).
#
update_config() {
  if [[ -n "$WS_TYPE" ]]; then
      local DEVICE_JSON="$CONFIG_DIR/device.json"
      python3 - "$DEVICE_JSON" "$WS_TYPE" <<'PY'
import json
import sys

device_json, ws_type = sys.argv[1], sys.argv[2]
with open(device_json, "r", encoding="utf-8") as f:
    config = json.load(f)

config["display_type"] = ws_type

display_defaults = {
    "epd7in3e": {
        "resolution": [800, 480],
        "orientation": "horizontal",
        "image_settings": {
            "saturation": 1.0,
            "brightness": 1.0,
            "sharpness": 1.0,
            "contrast": 1.0,
        },
    },
    "epd7in5_V2": {
        "resolution": [800, 480],
        "orientation": "horizontal",
    },
}

for key, value in display_defaults.get(ws_type, {}).items():
    if isinstance(value, dict):
        existing = config.setdefault(key, {})
        existing.update(value)
    else:
        config[key] = value

with open(device_json, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=4, ensure_ascii=False)
    f.write("\n")
PY
      chown inkypi:inkypi "$DEVICE_JSON"
      chmod 0600 "$DEVICE_JSON"
      echo "Updated display configuration to: $WS_TYPE"
  else
      echo "Config not updated as WS_TYPE flag is not set"
  fi
}

stop_service() {
    echo "Checking if $SERVICE_FILE is running"
    if /usr/bin/systemctl is-active --quiet $SERVICE_FILE
    then
      /usr/bin/systemctl stop $SERVICE_FILE > /dev/null &
      show_loader "Stopping $APPNAME service"
    else  
      echo_success "\t$SERVICE_FILE not running"
    fi
}

start_service() {
  echo "Starting $APPNAME service."
  sudo systemctl start $SERVICE_FILE
}

install_src() {
  # Check if an existing installation is present
  echo "Installing $APPNAME to $INSTALL_PATH"
  if [[ -d $INSTALL_PATH ]]; then
    rm -rf "$INSTALL_PATH" > /dev/null
    echo_success "\tRemoved existing installation at $INSTALL_PATH"
  fi

  install -d -o root -g root -m 0755 "$INSTALL_ROOT" "$INSTALL_PATH"
  cp -a "$SRC_PATH" "$INSTALL_PATH/src"
  chown -R root:root "$INSTALL_ROOT"
  chmod -R go-w "$INSTALL_ROOT"
  echo_success "\tCopied immutable application source to $INSTALL_PATH/src"
}

install_cli() {
  cp -r "$SCRIPT_DIR/cli" "$INSTALL_PATH/"
  sudo chmod +x "$INSTALL_PATH/cli/"*
}

# Get Raspberry Pi hostname
get_hostname() {
  echo "$(hostname)"
}

# Get Raspberry Pi IP address
get_ip_address() {
  ip_address=$(hostname -I | awk '{print $1}')
  echo "$ip_address"
}

# Get OS release number, e.g. 11=Bullseye, 12=Bookworm, 13=Trixe
get_os_version() {
  echo "$(lsb_release -sr)"
}

ask_for_reboot() {
  # Get hostname and IP address
  hostname=$(get_hostname)
  ip_address=$(get_ip_address)
  echo_header "$(echo_success "${APPNAME^^} Installation Complete!")"
  echo_header "- A reboot of your Raspberry Pi is required for the changes to take effect."
  echo_header "- After your Pi is rebooted, access the web UI at $(echo_blue "'http://$hostname.local'") or $(echo_blue "'http://$ip_address'")."
  echo_header "- If you encounter any issues or have suggestions, please submit them here: https://github.com/fatihak/InkyPi/issues"

  if [[ "$REBOOT_PROMPT" != "true" ]]; then
    echo "Reboot prompt skipped. Run 'sudo reboot now' after optional API key setup and health checks."
    return
  fi

  read -p "Would you like to restart your Raspberry Pi now? [Y/N] " userInput
  userInput="${userInput^^}"

  if [[ "${userInput,,}" == "y" ]]; then
    echo_success "You entered 'Y', rebooting now..."
    sleep 2
    sudo reboot now
  elif [[ "${userInput,,}" == "n" ]]; then
    echo "Please restart your Raspberry Pi later to apply changes by running 'sudo reboot now'."
    exit
  else
    echo "Unknown input, please restart your Raspberry Pi later to apply changes by running 'sudo reboot now'."
    sleep 1
  fi
}

# check if we have an argument for WS display support.  Parameter is not required
# to maintain default INKY display support.
parse_arguments "$@"
check_permissions
ensure_service_user
stop_service
# fetch the WS display driver if defined.
if [[ -n "$WS_TYPE" ]]; then
  fetch_waveshare_driver
fi
enable_interfaces
install_debian_dependencies
# check OS version for Bookworm to setup zramswap
if [[ $(get_os_version) = "12" ]] ; then
  echo "OS version is Bookworm - setting up zramswap"
  setup_zramswap_service
else
  echo "OS version is not Bookworm - skipping zramswap setup."
fi
setup_earlyoom_service
echo "Update JS and CSS files"
bash "$SCRIPT_DIR/update_vendors.sh" > /dev/null
install_src
install_cli
create_venv
install_executable
install_config
# update the config file with additional WS if defined.
if [[ -n "$WS_TYPE" ]]; then
  update_config
fi
install_app_service
install_privileged_broker

ask_for_reboot
