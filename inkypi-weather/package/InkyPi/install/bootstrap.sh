#!/bin/bash
set -Eeuo pipefail

SOURCE=${BASH_SOURCE[0]}
while [ -h "$SOURCE" ]; do
  DIR=$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )
  SOURCE=$(readlink "$SOURCE")
  [[ $SOURCE != /* ]] && SOURCE=$DIR/$SOURCE
done
SCRIPT_DIR=$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )
PROJECT_DIR=$( cd -P "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd )
RUNTIME_ENV_FILE="/etc/inkypi/inkypi.env"
ORIGINAL_ARGS=("$@")

DEFAULT_WS_TYPE="epd7in3e"
DISPLAY_MODE="waveshare"
WS_TYPE="$DEFAULT_WS_TYPE"
SKIP_INSTALL=false
SKIP_KEYS=false
CONFIGURE_ALL_KEYS=false
NON_INTERACTIVE=false
LANG_MODE="${INKYPI_LANG:-}"
DISPLAY_EXPLICIT=false
CHECK_ONLY=false
LIST_DISPLAYS=false
KEYS_CHANGED=false
STAGE="arguments"

on_error() {
  local status=$?
  say "Installation stopped during: $STAGE (exit $status)." "安装在「$STAGE」阶段停止（退出码 $status）。" >&2
  say "See the error above. Fix it, then rerun the same command." "请按上方错误提示处理后，重新运行相同命令。" >&2
  echo "  sudo journalctl -u inkypi -n 80 --no-pager" >&2
  exit "$status"
}

detect_language() {
  if [[ -n "$LANG_MODE" ]]; then
    case "${LANG_MODE,,}" in
      zh*|cn|zh-cn|zh_cn) LANG_MODE="zh-CN" ;;
      *) LANG_MODE="en" ;;
    esac
    return
  fi
  local env_lang="${LC_ALL:-${LANG:-}}"
  case "${env_lang,,}" in
    zh*|*zh_cn*|*zh-cn*) LANG_MODE="zh-CN" ;;
    *) LANG_MODE="en" ;;
  esac
}

is_zh() {
  [[ "${LANG_MODE,,}" == zh* ]]
}

say() {
  if is_zh; then
    echo "$2"
  else
    echo "$1"
  fi
}

usage() {
  cat <<EOF
Usage: sudo bash install/bootstrap.sh [options]
用法: sudo bash install/bootstrap.sh [选项]

This beginner installer runs the normal InkyPi install, prepares the runtime environment,
helps add optional API keys, starts the service, and runs a health check.
这个新手安装器会运行基础安装、准备运行配置、引导填写可选 API Key、
启动服务，并执行健康检查。

Options:
  -W, --waveshare <model>  Use a Waveshare display model. Default: epd7in3e.
  --pimoroni              Install for Pimoroni Inky displays instead of Waveshare.
  --skip-install          Do not run install/install.sh; only configure keys and check.
  --skip-keys             Do not prompt for API keys.
  --all-keys              Prompt for every optional API key, not just common keys.
  --non-interactive       Use defaults and do not prompt.
  --check                 Check prerequisites without installing anything.
  --list-displays         List Waveshare drivers included in this checkout.
  --lang <en|zh-CN>       Set installer language. Also supports INKYPI_LANG.
  --zh-cn                 Shortcut for --lang zh-CN.
  -h, --help              Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -W|--waveshare)
      if [[ $# -lt 2 ]]; then
        echo "Option $1 requires a Waveshare model such as epd7in3e." >&2
        exit 1
      fi
      DISPLAY_MODE="waveshare"
      DISPLAY_EXPLICIT=true
      WS_TYPE="$2"
      shift 2
      ;;
    --pimoroni)
      DISPLAY_MODE="pimoroni"
      DISPLAY_EXPLICIT=true
      WS_TYPE=""
      shift
      ;;
    --skip-install)
      SKIP_INSTALL=true
      shift
      ;;
    --skip-keys)
      SKIP_KEYS=true
      shift
      ;;
    --all-keys)
      CONFIGURE_ALL_KEYS=true
      shift
      ;;
    --non-interactive)
      NON_INTERACTIVE=true
      shift
      ;;
    --check)
      CHECK_ONLY=true
      shift
      ;;
    --list-displays)
      LIST_DISPLAYS=true
      shift
      ;;
    --lang)
      if [[ $# -lt 2 ]]; then
        echo "Option $1 requires en or zh-CN." >&2
        exit 1
      fi
      LANG_MODE="$2"
      shift 2
      ;;
    --zh-cn)
      LANG_MODE="zh-CN"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

prepare_input() {
  if [[ "$NON_INTERACTIVE" == true || "$CHECK_ONLY" == true ]]; then
    return
  fi
  if [[ ! -t 0 ]]; then
    # curl | sudo bash owns stdin. Reattach prompts to the SSH terminal.
    if { exec 3</dev/tty; } 2>/dev/null; then
      exec 0<&3 3<&-
    else
      say "No terminal. Add --non-interactive, or connect using ssh -t." "没有交互终端。请添加 --non-interactive，或使用 ssh -t 登录。" >&2
      return 1
    fi
  fi
}

list_displays() {
  local driver
  for driver in "$PROJECT_DIR"/src/display/waveshare_epd/epd*in*.py; do
    [[ -f "$driver" ]] || continue
    driver="${driver##*/}"
    echo "  ${driver%.py}"
  done
}

check_prerequisites() {
  local args=(--lang "$LANG_MODE")
  if [[ "$DISPLAY_MODE" == waveshare ]]; then
    args+=(--waveshare "$WS_TYPE")
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    say "Python 3 is missing. Run this on Raspberry Pi OS with Python 3.11 or newer." "缺少 Python 3。请在带有 Python 3.11 或更新版本的 Raspberry Pi OS 上运行。" >&2
    return 1
  fi
  python3 "$SCRIPT_DIR/check_install.py" "${args[@]}"
}

require_root() {
  if [[ "$EUID" -ne 0 ]]; then
    say "This installer needs sudo. Re-running with sudo..." "安装器需要 sudo 权限，正在用 sudo 重新运行..."
    exec sudo -E bash "$0" "${ORIGINAL_ARGS[@]}"
  fi
}

choose_display() {
  if [[ -f /var/lib/inkypi/config/device.json ]]; then
    say "Existing device settings will be preserved, including the display model." "检测到已有设备配置，将保留屏幕型号和个人设置。"
    # Base install preserves this configuration too. Validate the model that
    # will actually be used instead of advertising an ignored new selection.
    WS_TYPE=$(python3 -c 'import json; print(json.load(open("/var/lib/inkypi/config/device.json"))["display_type"])')
    if [[ "$WS_TYPE" == inky ]]; then
      DISPLAY_MODE=pimoroni
      WS_TYPE=""
    else
      DISPLAY_MODE=waveshare
    fi
    return
  fi
  if [[ "$NON_INTERACTIVE" == "true" || "$DISPLAY_EXPLICIT" == true ]]; then
    return
  fi

  echo
  say "Display selection" "选择显示屏"
  say "  1) Waveshare 7.3 inch color e-Paper HAT E / epd7in3e (recommended default)" "  1) Waveshare 7.3 寸彩色墨水屏 HAT E / epd7in3e（推荐默认）"
  say "  2) Another Waveshare display model" "  2) 其他 Waveshare 显示屏型号"
  say "  3) Pimoroni Inky display" "  3) Pimoroni Inky 显示屏"
  if is_zh; then
    read -r -p "选择 1/2/3 [1]: " choice
  else
    read -r -p "Choose 1/2/3 [1]: " choice
  fi
  choice=${choice:-1}
  case "$choice" in
    1)
      DISPLAY_MODE="waveshare"
      WS_TYPE="$DEFAULT_WS_TYPE"
      ;;
    2)
      list_displays
      if is_zh; then
        read -r -p "输入 Waveshare 驱动型号，例如 epd7in3e: " custom_ws
      else
        read -r -p "Enter Waveshare driver model, for example epd7in3e: " custom_ws
      fi
      custom_ws=${custom_ws:-$DEFAULT_WS_TYPE}
      DISPLAY_MODE="waveshare"
      WS_TYPE="$custom_ws"
      ;;
    3)
      DISPLAY_MODE="pimoroni"
      WS_TYPE=""
      ;;
    *)
      say "Unknown choice. Run again and select 1, 2 or 3." "无法识别该选项。请重新运行并选择 1、2 或 3。" >&2
      return 1
      ;;
  esac
}

run_install() {
  if [[ "$SKIP_INSTALL" == "true" ]]; then
    say "Skipping base install." "跳过基础安装。"
    return
  fi

  install_args=("--no-reboot-prompt")
  if [[ "$DISPLAY_MODE" == "waveshare" ]]; then
    install_args+=("-W" "$WS_TYPE")
  fi

  echo
  say "Running base installer from $PROJECT_DIR" "正在从 $PROJECT_DIR 运行基础安装器"
  bash "$SCRIPT_DIR/install.sh" "${install_args[@]}"
}

ensure_env_file() {
  local env_file="$RUNTIME_ENV_FILE"
  if [[ -f "$env_file" ]]; then
    say ".env already exists: $env_file" ".env 已存在: $env_file"
    return
  fi
  say "Creating starter .env at $env_file" "正在创建初始 .env: $env_file"
  python3 "$SCRIPT_DIR/configure_api_keys.py" --write-example "$env_file" --lang "$LANG_MODE"
  chown inkypi:inkypi "$env_file"
  chmod 0600 "$env_file"
}

configure_keys() {
  if [[ "$SKIP_KEYS" == "true" ]]; then
    say "Skipping API key prompts. Add keys later with:" "跳过 API Key 填写。之后可用以下命令添加："
    echo "  sudo python3 /opt/inkypi/current/install/configure_api_keys.py --env-file $RUNTIME_ENV_FILE --lang $LANG_MODE"
    say "or open the web UI at /api-keys." "也可以打开 Web UI 的 /api-keys 页面。"
    return
  fi

  if [[ "$NON_INTERACTIVE" == "true" ]]; then
    say "Non-interactive mode: not prompting for API keys." "非交互模式：不会询问 API Key。"
    return
  fi

  echo
  say "API keys are optional. You can press Enter to skip every key and add them later." "API Key 都是可选的。可以直接按回车跳过，之后再添加。"
  say "Registration URLs are shown next to each key." "每个 Key 旁边都会显示注册网址。"
  if is_zh; then
    read -r -p "现在配置 API Key 吗？[y=常用/N=跳过/a=全部]: " answer
  else
    read -r -p "Configure API keys now? [y/N/a for all keys]: " answer
  fi
  answer=${answer:-n}
  if [[ "$CONFIGURE_ALL_KEYS" == true && "${answer,,}" =~ ^(y|yes)$ ]]; then
    answer=all
  fi
  case "${answer,,}" in
    y|yes)
      python3 "$SCRIPT_DIR/configure_api_keys.py" --env-file "$RUNTIME_ENV_FILE" --lang "$LANG_MODE"
      chown inkypi:inkypi "$RUNTIME_ENV_FILE"
      chmod 0600 "$RUNTIME_ENV_FILE"
      KEYS_CHANGED=true
      ;;
    a|all)
      python3 "$SCRIPT_DIR/configure_api_keys.py" --env-file "$RUNTIME_ENV_FILE" --all --lang "$LANG_MODE"
      chown inkypi:inkypi "$RUNTIME_ENV_FILE"
      chmod 0600 "$RUNTIME_ENV_FILE"
      KEYS_CHANGED=true
      ;;
    *)
      say "Skipping API key entry for now." "暂时跳过 API Key 填写。"
      ;;
  esac
}

restart_service() {
  echo
  say "Starting InkyPi service..." "正在启动 InkyPi 服务..."
  systemctl daemon-reload
  systemctl restart inkypi
}

show_access_info() {
  local host_name
  local ip_address
  host_name=$(hostname)
  ip_address=$(hostname -I | awk '{for(i=1;i<=NF;i++) if ($i ~ /^[0-9]+\./) {print $i; exit}}')
  echo
  say "Open the web UI after the service starts:" "服务启动后，打开 Web UI："
  echo "  http://$host_name.local"
  if [[ -n "$ip_address" ]]; then
    echo "  http://$ip_address"
  fi
  echo
  say "First visit: open /auth/setup and set your administrator password." "首次访问：打开 /auth/setup，设置自己的管理员密码。"
  say "Read the one-time setup token in this SSH terminal:" "在当前 SSH 终端读取一次性配对码："
  echo "  sudo cat /var/lib/inkypi/data/security/bootstrap_admin.token"
  say "Already configured? Use /auth/login. If the setup token expired: sudo inkypi admin bootstrap" "已设置密码请使用 /auth/login。配对码过期可运行：sudo inkypi admin bootstrap"
  say "Then choose plugins, add your location/content and build a playlist. API keys are optional at /api-keys." "登录后选择插件、设置自己的位置与内容、创建轮播。需要的 API Key 可在 /api-keys 添加。"
  echo
  say "Useful commands:" "常用命令："
  echo "  sudo bash /opt/inkypi/current/install/healthcheck.sh --lang $LANG_MODE --wait 120"
  echo "  sudo python3 /opt/inkypi/current/install/configure_api_keys.py --check --env-file $RUNTIME_ENV_FILE --lang $LANG_MODE"
  echo "  sudo journalctl -u inkypi -n 120 --no-pager"
  echo
  say "If this is a fresh Raspberry Pi install, reboot once now so SPI/I2C changes are fully active:" "如果这是全新的 Raspberry Pi 安装，请现在重启一次，让 SPI/I2C 设置完全生效："
  echo "  sudo reboot now"
}

main() {
  detect_language
  trap on_error ERR
  if [[ "$LIST_DISPLAYS" == true ]]; then
    list_displays
    return
  fi
  if [[ "$CHECK_ONLY" == true ]]; then
    STAGE="preflight / 安装前检查"
    check_prerequisites
    return
  fi
  require_root
  STAGE="input / 交互终端"
  prepare_input
  cd "$PROJECT_DIR"
  STAGE="display / 选择屏幕"
  if [[ "$SKIP_INSTALL" != true ]]; then
    choose_display
    STAGE="preflight / 安装前检查"
    check_prerequisites
  elif [[ ! -f /opt/inkypi/current/.release-id ]]; then
    say "No installed release found. Run again without --skip-install." "尚未安装，请去掉 --skip-install 后重试。" >&2
    return 1
  fi
  STAGE="install / 安装依赖与应用"
  run_install
  STAGE="API keys / 可选服务配置"
  ensure_env_file
  configure_keys
  STAGE="startup / 启动服务"
  if [[ "$SKIP_INSTALL" == true || "$KEYS_CHANGED" == true ]]; then
    restart_service
  fi
  STAGE="health check / 运行检查"
  local health_status=0
  bash "$SCRIPT_DIR/healthcheck.sh" --lang "$LANG_MODE" --wait 120 || health_status=$?
  show_access_info
  if [[ "$health_status" -ne 0 ]]; then
    say "The health check failed. Installation is not confirmed ready; follow the diagnostics above." "健康检查未通过，当前不能确认安装就绪，请按上方诊断提示处理。" >&2
  fi
  return "$health_status"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main
fi
