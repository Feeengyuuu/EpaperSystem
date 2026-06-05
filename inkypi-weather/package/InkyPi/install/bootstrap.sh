#!/bin/bash
set -euo pipefail

SOURCE=${BASH_SOURCE[0]}
while [ -h "$SOURCE" ]; do
  DIR=$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )
  SOURCE=$(readlink "$SOURCE")
  [[ $SOURCE != /* ]] && SOURCE=$DIR/$SOURCE
done
SCRIPT_DIR=$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )
PROJECT_DIR=$( cd -P "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd )
ORIGINAL_ARGS=("$@")

DEFAULT_WS_TYPE="epd7in3e"
DISPLAY_MODE="waveshare"
WS_TYPE="$DEFAULT_WS_TYPE"
SKIP_INSTALL=false
SKIP_KEYS=false
CONFIGURE_ALL_KEYS=false
NON_INTERACTIVE=false
LANG_MODE="${INKYPI_LANG:-}"

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

This beginner installer runs the normal InkyPi install, creates .env if needed,
helps add optional API keys, starts the service, and runs a health check.
这个新手安装器会运行基础安装、按需创建 .env、引导填写可选 API Key、
启动服务，并执行健康检查。

Options:
  -W, --waveshare <model>  Use a Waveshare display model. Default: epd7in3e.
  --pimoroni              Install for Pimoroni Inky displays instead of Waveshare.
  --skip-install          Do not run install/install.sh; only configure keys and check.
  --skip-keys             Do not prompt for API keys.
  --all-keys              Prompt for every optional API key, not just common keys.
  --non-interactive       Use defaults and do not prompt.
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
      WS_TYPE="$2"
      shift 2
      ;;
    --pimoroni)
      DISPLAY_MODE="pimoroni"
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

detect_language

if [[ "$EUID" -ne 0 ]]; then
  say "This installer needs sudo. Re-running with sudo..." "安装器需要 sudo 权限，正在用 sudo 重新运行..."
  exec sudo -E bash "$0" "${ORIGINAL_ARGS[@]}"
fi

choose_display() {
  if [[ "$NON_INTERACTIVE" == "true" || "$DISPLAY_MODE" != "waveshare" || "$WS_TYPE" != "$DEFAULT_WS_TYPE" ]]; then
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
      say "Unknown choice. Using default $DEFAULT_WS_TYPE." "无法识别该选项，使用默认型号 $DEFAULT_WS_TYPE。"
      DISPLAY_MODE="waveshare"
      WS_TYPE="$DEFAULT_WS_TYPE"
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
  local env_file="$PROJECT_DIR/.env"
  if [[ -f "$env_file" ]]; then
    say ".env already exists: $env_file" ".env 已存在: $env_file"
    return
  fi
  say "Creating starter .env at $env_file" "正在创建初始 .env: $env_file"
  python3 "$SCRIPT_DIR/configure_api_keys.py" --write-example "$env_file" --lang "$LANG_MODE"
}

configure_keys() {
  if [[ "$SKIP_KEYS" == "true" ]]; then
    say "Skipping API key prompts. Add keys later with:" "跳过 API Key 填写。之后可用以下命令添加："
    echo "  python3 install/configure_api_keys.py --env-file .env"
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
  case "${answer,,}" in
    y|yes)
      python3 "$SCRIPT_DIR/configure_api_keys.py" --env-file "$PROJECT_DIR/.env" --lang "$LANG_MODE"
      ;;
    a|all)
      python3 "$SCRIPT_DIR/configure_api_keys.py" --env-file "$PROJECT_DIR/.env" --all --lang "$LANG_MODE"
      ;;
    *)
      say "Skipping API key entry for now." "暂时跳过 API Key 填写。"
      ;;
  esac
}

restart_service() {
  echo
  say "Starting InkyPi service..." "正在启动 InkyPi 服务..."
  systemctl daemon-reload || true
  systemctl restart inkypi || true
  sleep 3
}

show_access_info() {
  local host_name
  local ip_address
  host_name=$(hostname)
  ip_address=$(hostname -I | awk '{print $1}')
  echo
  say "Open the web UI after the service starts:" "服务启动后，打开 Web UI："
  echo "  http://$host_name.local"
  if [[ -n "$ip_address" ]]; then
    echo "  http://$ip_address"
  fi
  echo
  say "Useful commands:" "常用命令："
  echo "  bash install/healthcheck.sh"
  echo "  python3 install/configure_api_keys.py --check"
  echo "  sudo journalctl -u inkypi -n 120 --no-pager"
  echo
  say "If this is a fresh Raspberry Pi install, reboot once now so SPI/I2C changes are fully active:" "如果这是全新的 Raspberry Pi 安装，请现在重启一次，让 SPI/I2C 设置完全生效："
  echo "  sudo reboot now"
}

cd "$PROJECT_DIR"
choose_display
run_install
ensure_env_file
configure_keys
restart_service
bash "$SCRIPT_DIR/healthcheck.sh" --lang "$LANG_MODE" || true
show_access_info
