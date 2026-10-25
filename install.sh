#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="${EPAPERSYSTEM_REPO_URL:-https://github.com/Feeengyuuu/EpaperSystem.git}"
INSTALL_PARENT="${EPAPERSYSTEM_INSTALL_PARENT:-/opt}"
CHECKOUT_DIR="${EPAPERSYSTEM_CHECKOUT_DIR:-$INSTALL_PARENT/EpaperSystem}"
BRANCH="${EPAPERSYSTEM_BRANCH:-main}"
RELATIVE_BOOTSTRAP="inkypi-weather/package/InkyPi/install/bootstrap.sh"

usage() {
  cat <<EOF
Usage / 用法:
  bash install.sh --lang zh-CN
  curl -fsSL https://raw.githubusercontent.com/Feeengyuuu/EpaperSystem/main/install.sh | sudo bash -s -- --lang zh-CN

Options / 选项:
  --lang en|zh-CN          Installer language / 安装语言
  -W, --waveshare MODEL    Packaged Waveshare driver (default: epd7in3e)
  --pimoroni              Pimoroni Inky automatic detection
  --list-displays         List packaged Waveshare drivers / 列出驱动
  --check                 Check prerequisites without installing / 仅检查环境
  --skip-keys             Configure optional API keys later in the web UI
  --all-keys              Offer all optional API keys
  --non-interactive       Use defaults without prompts (required without a terminal)
  --skip-install          Configure keys and check an existing installation
  --no-starter            Start a fresh install with an empty playlist / 不预装入门轮播
  -h, --help              Show this help / 显示帮助

The online installer downloads only the latest version of $BRANCH (no history) to
$CHECKOUT_DIR, and running the same command again updates it to the latest version.
Use EPAPERSYSTEM_CHECKOUT_DIR for a different path, EPAPERSYSTEM_BRANCH for another
branch and EPAPERSYSTEM_REPO_URL for your own fork. Existing local changes are preserved.
在线安装只下载 $BRANCH 的最新版本（不含历史），再次运行同一条命令即可更新到最新版；
已有本地修改时会停止自动更新，请先处理自己的修改。
EOF
}

check_permissions() {
  [[ "$EUID" -eq 0 ]]
}

# Download the latest version, or bring an existing download up to it. A fresh download
# fetches only the newest commit (about half the size of the full history, which matters
# on a Pi Zero over Wi-Fi); an older full checkout keeps updating fast-forward.
sync_checkout() {
  if [[ -e "$CHECKOUT_DIR/.git" ]]; then
    if [[ "$(git -C "$CHECKOUT_DIR" remote get-url origin)" != "$REPO_URL" ]]; then
      echo "Checkout origin differs from $REPO_URL; choose another EPAPERSYSTEM_CHECKOUT_DIR. / 目录属于其他仓库，请换一个安装目录。" >&2
      return 1
    fi
    if [[ -n "$(git -C "$CHECKOUT_DIR" status --porcelain)" ]]; then
      echo "Local changes found at $CHECKOUT_DIR. / 检测到本地修改，自动更新已停止；请先提交或保存自己的修改。" >&2
      return 1
    fi
    echo "Updating to the latest version / 正在更新到最新版本 $CHECKOUT_DIR..."
    if [[ "$(git -C "$CHECKOUT_DIR" rev-parse --is-shallow-repository)" == true ]]; then
      git -C "$CHECKOUT_DIR" fetch --depth 1 origin "$BRANCH"
      git -C "$CHECKOUT_DIR" reset --keep FETCH_HEAD
    else
      git -C "$CHECKOUT_DIR" pull --ff-only origin "$BRANCH"
    fi
  elif [[ -e "$CHECKOUT_DIR" && ( ! -d "$CHECKOUT_DIR" || -n "$(find "$CHECKOUT_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ) ]]; then
    echo "Refusing to overwrite $CHECKOUT_DIR. Set EPAPERSYSTEM_CHECKOUT_DIR to an empty directory. / 请指定空目录。" >&2
    return 1
  else
    mkdir -p "$(dirname "$CHECKOUT_DIR")"
    echo "Downloading the latest version / 正在下载最新版本 EpaperSystem ($BRANCH)..."
    git clone --depth 1 --single-branch --branch "$BRANCH" "$REPO_URL" "$CHECKOUT_DIR"
  fi
  echo "Version / 版本: $(git -C "$CHECKOUT_DIR" log -1 --format='%h · %cd' --date=short)"
}

# Parse the whole function before any child can consume the script on stdin.
main() {
  local script_path="${BASH_SOURCE[0]:-}"
  local script_dir=""
  if [[ -n "$script_path" && -f "$script_path" ]]; then
    script_dir="$(cd -P "$(dirname "$script_path")" >/dev/null 2>&1 && pwd)"
  fi
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    return
  fi

  if [[ -n "$script_dir" && -f "$script_dir/$RELATIVE_BOOTSTRAP" ]]; then
    exec bash "$script_dir/$RELATIVE_BOOTSTRAP" "$@"
  fi

  if [[ "$(uname -s)" != Linux ]] || ! command -v apt-get >/dev/null 2>&1; then
    echo "Run this command on Raspberry Pi OS over SSH. / 请先 SSH 登录树莓派，再运行安装命令。" >&2
    return 1
  fi
  if ! check_permissions; then
    if [[ -n "$script_path" && -f "$script_path" ]]; then
      exec sudo -E bash "$script_path" "$@"
    fi
    echo "Use: curl -fsSL https://raw.githubusercontent.com/Feeengyuuu/EpaperSystem/main/install.sh | sudo bash -s -- --lang zh-CN" >&2
    return 1
  fi

  if ! command -v git >/dev/null 2>&1; then
    echo "Installing git and ca-certificates / 正在安装下载工具..."
    apt-get update
    apt-get install -y git ca-certificates
  fi

  sync_checkout

  local bootstrap="$CHECKOUT_DIR/$RELATIVE_BOOTSTRAP"
  if [[ ! -f "$bootstrap" ]]; then
    echo "Bootstrap installer missing: $bootstrap" >&2
    return 1
  fi
  exec bash "$bootstrap" "$@"
}

if [[ "${BASH_SOURCE[0]:-}" == "$0" || -z "${BASH_SOURCE[0]:-}" ]]; then
  main "$@"
fi
