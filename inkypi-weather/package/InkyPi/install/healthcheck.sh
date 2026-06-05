#!/bin/bash
set -u

SOURCE=${BASH_SOURCE[0]}
while [ -h "$SOURCE" ]; do
  DIR=$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )
  SOURCE=$(readlink "$SOURCE")
  [[ $SOURCE != /* ]] && SOURCE=$DIR/$SOURCE
done
SCRIPT_DIR=$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )
PROJECT_DIR=$( cd -P "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd )
INSTALL_DIR="/usr/local/inkypi"
SERVICE_NAME="inkypi"
LANG_MODE="${INKYPI_LANG:-}"

FAILURES=0
WARNINGS=0

usage() {
  cat <<EOF
Usage: bash install/healthcheck.sh [--lang en|zh-CN]
用法: bash install/healthcheck.sh [--lang en|zh-CN]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --lang)
      if [[ $# -lt 2 ]]; then
        echo "Option --lang requires en or zh-CN." >&2
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

ok() {
  echo "OK    $1"
}

warn() {
  WARNINGS=$((WARNINGS + 1))
  echo "WARN  $1"
}

fail() {
  FAILURES=$((FAILURES + 1))
  echo "FAIL  $1"
}

check_command() {
  if command -v "$1" >/dev/null 2>&1; then
    if is_zh; then
      ok "命令可用: $1"
    else
      ok "Command available: $1"
    fi
  else
    if is_zh; then
      fail "缺少命令: $1"
    else
      fail "Missing command: $1"
    fi
  fi
}

check_path() {
  if [[ -e "$1" ]]; then
    ok "$2: $1"
  else
    if is_zh; then
      fail "$2 不存在: $1"
    else
      fail "$2 missing: $1"
    fi
  fi
}

detect_language

say "InkyPi health check" "InkyPi 健康检查"
say "Project: $PROJECT_DIR" "项目路径: $PROJECT_DIR"
echo

check_command python3
check_command curl
if is_zh; then
  check_path "$PROJECT_DIR/src/inkypi.py" "源码入口"
  check_path "$PROJECT_DIR/src/config/device.json" "设备配置"
  check_path "$INSTALL_DIR/src" "安装软链接"
  check_path "$INSTALL_DIR/venv_inkypi/bin/python" "虚拟环境 Python"
  check_path "/usr/local/bin/inkypi" "命令行入口"
else
  check_path "$PROJECT_DIR/src/inkypi.py" "Source entry point"
  check_path "$PROJECT_DIR/src/config/device.json" "Device config"
  check_path "$INSTALL_DIR/src" "Install symlink"
  check_path "$INSTALL_DIR/venv_inkypi/bin/python" "Virtualenv python"
  check_path "/usr/local/bin/inkypi" "CLI command"
fi

if [[ -f "$PROJECT_DIR/.env" ]]; then
  ok ".env exists"
else
  if is_zh; then
    warn ".env 不存在。可选 API Key 可以之后再添加。"
  else
    warn ".env does not exist. Optional API keys can be added later."
  fi
fi

if command -v python3 >/dev/null 2>&1 && [[ -f "$SCRIPT_DIR/configure_api_keys.py" ]]; then
  echo
  python3 "$SCRIPT_DIR/configure_api_keys.py" --env-file "$PROJECT_DIR/.env" --check --lang "$LANG_MODE" || warn "$(if is_zh; then echo "API Key 检查无法运行"; else echo "API key check could not run"; fi)"
fi

echo
if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-enabled "$SERVICE_NAME" >/dev/null 2>&1; then
    ok "$(if is_zh; then echo "systemd 服务已启用"; else echo "systemd service is enabled"; fi)"
  else
    warn "$(if is_zh; then echo "systemd 服务未启用。尝试: sudo systemctl enable $SERVICE_NAME"; else echo "systemd service is not enabled. Try: sudo systemctl enable $SERVICE_NAME"; fi)"
  fi

  if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "$(if is_zh; then echo "systemd 服务正在运行"; else echo "systemd service is active"; fi)"
  else
    fail "$(if is_zh; then echo "systemd 服务未运行。尝试: sudo systemctl restart $SERVICE_NAME"; else echo "systemd service is not active. Try: sudo systemctl restart $SERVICE_NAME"; fi)"
  fi
else
  warn "$(if is_zh; then echo "未找到 systemctl，跳过服务检查"; else echo "systemctl not found; skipping service checks"; fi)"
fi

echo
if command -v curl >/dev/null 2>&1; then
  if curl --max-time 5 --fail --silent http://127.0.0.1/playlist >/dev/null; then
    ok "$(if is_zh; then echo "Web 端点 /playlist 有响应"; else echo "Web endpoint /playlist responded"; fi)"
  else
    fail "$(if is_zh; then echo "Web 端点 /playlist 无响应。查看日志: sudo journalctl -u $SERVICE_NAME -n 120 --no-pager"; else echo "Web endpoint /playlist did not respond. Check logs with: sudo journalctl -u $SERVICE_NAME -n 120 --no-pager"; fi)"
  fi

  current_image=$(curl --max-time 5 --fail --silent http://127.0.0.1/api/current_image 2>/dev/null | head -c 120)
  if [[ -n "$current_image" ]]; then
    ok "$(if is_zh; then echo "Web 端点 /api/current_image 有响应"; else echo "Web endpoint /api/current_image responded"; fi)"
  else
    warn "$(if is_zh; then echo "Web 端点 /api/current_image 暂无数据。首次刷新前这可能是正常的。"; else echo "Web endpoint /api/current_image returned no data yet. This can be normal before the first display refresh."; fi)"
  fi
else
  warn "$(if is_zh; then echo "curl 不可用，跳过 HTTP 检查"; else echo "curl not available; skipping HTTP checks"; fi)"
fi

echo
if [[ "$FAILURES" -eq 0 ]]; then
  ok "$(if is_zh; then echo "健康检查完成，警告数: $WARNINGS。"; else echo "Health check finished with $WARNINGS warning(s)."; fi)"
  exit 0
fi

fail "$(if is_zh; then echo "健康检查完成，失败数: $FAILURES，警告数: $WARNINGS。"; else echo "Health check finished with $FAILURES failure(s) and $WARNINGS warning(s)."; fi)"
echo
say "Next debugging commands:" "下一步调试命令："
echo "  sudo systemctl status $SERVICE_NAME --no-pager"
echo "  sudo journalctl -u $SERVICE_NAME -n 120 --no-pager"
echo "  python3 install/configure_api_keys.py --check --lang $LANG_MODE"
exit 1
