#!/usr/bin/env python3
"""Read-only prerequisites for the Raspberry Pi beginner installer."""

from __future__ import annotations

import argparse
import platform
from pathlib import Path
import re
import shutil
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GIB = 1024**3


def check_install(
    *,
    project=PROJECT_ROOT,
    root=Path("/"),
    system=None,
    version=None,
    which=shutil.which,
    disk_usage=shutil.disk_usage,
    waveshare=None,
):
    """Return (success, English, Chinese) checks without changing the host."""
    checks = []
    system = platform.system() if system is None else system
    version = sys.version_info[:2] if version is None else version
    checks.append(
        (
            system == "Linux",
            "Run on Raspberry Pi OS (Linux), via SSH.",
            "请先 SSH 登录 Raspberry Pi OS（Linux）再安装。",
        )
    )
    checks.append((version >= (3, 11), "Python 3.11 or newer is required.", "需要 Python 3.11 或更新版本。"))
    models = (root / "proc/device-tree/model", root / "sys/firmware/devicetree/base/model")
    is_pi = any(path.is_file() and "Raspberry Pi" in path.read_text(errors="replace") for path in models)
    checks.append(
        (is_pi, "Raspberry Pi hardware must be detected.", "需要检测到 Raspberry Pi 硬件；普通电脑请使用开发预览。")
    )
    checks.append(
        ((root / "run/systemd/system").is_dir(), "systemd must be running.", "需要正在运行的 systemd 服务管理器。")
    )
    for command in ("apt-get", "systemctl", "python3"):
        checks.append((which(command) is not None, f"Required command: {command}", f"必需命令：{command}"))
    for directory, minimum in (("opt", 2 * GIB), ("var", GIB // 2)):
        path = root / directory
        while not path.exists() and path != path.parent:
            path = path.parent
        free = disk_usage(path).free
        checks.append(
            (
                free >= minimum,
                f"/{directory}: {free / GIB:.1f} GiB free; need at least {minimum / GIB:g} GiB.",
                f"/{directory} 可用 {free / GIB:.1f} GiB；至少需要 {minimum / GIB:g} GiB。",
            )
        )
    if waveshare is not None:
        safe_name = re.fullmatch(r"epd[A-Za-z0-9_]+", waveshare) is not None
        driver = project / "src/display/waveshare_epd" / f"{waveshare}.py"
        exists = safe_name and driver.is_file()
        checks.append(
            (
                exists,
                f"Packaged Waveshare driver: {waveshare}. Use --list-displays to see available models.",
                f"内置 Waveshare 驱动：{waveshare}。可用 --list-displays 查看型号。",
            )
        )
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", default="en")
    parser.add_argument("--waveshare")
    args = parser.parse_args(argv)
    zh = args.lang.lower().startswith("zh")
    checks = check_install(waveshare=args.waveshare)
    for passed, en, cn in checks:
        print(f"{'OK' if passed else 'FAIL'}  {cn if zh else en}")
    if not all(check[0] for check in checks):
        print(
            "请先处理 FAIL 项目，再重试安装。" if zh else "Resolve the FAIL items before installing.", file=sys.stderr
        )
        return 1
    print(
        "安装前检查通过。依赖下载需要正常联网；尚未测试屏幕实际显示。"
        if zh
        else "Prerequisites passed. Dependency downloads need internet access; physical display output has not been tested."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
