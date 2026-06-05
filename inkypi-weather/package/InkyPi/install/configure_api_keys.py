#!/usr/bin/env python3
"""Interactive API key helper for InkyPi.

This script intentionally uses only the Python standard library so it can run
before the InkyPi virtual environment exists.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
REGISTRY_PATH = SCRIPT_DIR / "api_key_registry.json"
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"

COMMON_KEYS = {
    "OPEN_WEATHER_MAP_SECRET",
    "OPEN_AI_SECRET",
    "GROQ_API_KEY",
    "NASA_SECRET",
    "UNSPLASH_ACCESS_KEY",
    "GITHUB_SECRET",
    "STEAM_API_KEY",
    "COMIC_VINE_API_KEY",
    "RIOT_API_KEY",
}

EXAMPLE_RUNTIME_DEFAULTS = [
    ("OPENWEATHER_ONECALL_DAILY_LIMIT", "900", "Weather safety throttle."),
    ("OPENWEATHER_ONECALL_MIN_SECONDS", "1800", "Weather safety throttle."),
    ("OPENWEATHER_AUX_MIN_SECONDS", "1800", "Weather safety throttle."),
    ("OPENWEATHER_LOCATION_MIN_SECONDS", "86400", "Weather safety throttle."),
]

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def detect_language(explicit: str | None = None) -> str:
    raw = explicit or os.environ.get("INKYPI_LANG") or os.environ.get("LC_ALL") or os.environ.get("LANG") or ""
    normalized = raw.strip().lower().replace("_", "-")
    return "zh-CN" if normalized.startswith("zh") else "en"


def is_zh(lang: str) -> bool:
    return lang.lower().startswith("zh")


def load_registry() -> list[dict]:
    with REGISTRY_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    keys = payload.get("keys", [])
    if not isinstance(keys, list):
        raise ValueError(f"Invalid registry format in {REGISTRY_PATH}")
    return keys


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not KEY_RE.match(key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def quote_env_value(value: str) -> str:
    if value == "":
        return ""
    if re.search(r"\s|#|\"|'", value):
        return json.dumps(value)
    return value


def mask(value: str) -> str:
    if not value:
        return "-"
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}...{value[-3:]}"


def entry_configured(entry: dict, values: dict[str, str]) -> tuple[str, str]:
    names = [entry["key"], *entry.get("aliases", [])]
    for name in names:
        value = str(values.get(name, "")).strip()
        if value:
            return name, value
    return "", ""


def write_env(path: Path, values: dict[str, str], registry: list[dict]) -> None:
    primary_keys = [entry["key"] for entry in registry]
    known = set(primary_keys)
    for entry in registry:
        known.update(entry.get("aliases", []))

    lines: list[str] = [
        "# InkyPi API Keys and Secrets",
        "# This file is local-only. Do not commit real API keys to GitHub.",
        "# You can edit it by hand, run install/configure_api_keys.py,",
        "# or use the web UI at http://<your-pi>/api-keys.",
        "",
    ]

    for entry in registry:
        key = entry["key"]
        service = entry.get("service", key)
        features = ", ".join(entry.get("features", []))
        signup_url = entry.get("signup_url", "")
        notes = entry.get("notes", "")
        lines.append(f"# {service} - {features}")
        if signup_url:
            lines.append(f"# Get key: {signup_url}")
        if notes:
            lines.append(f"# Note: {notes}")
        value = values.get(key, "")
        if not value:
            for alias in entry.get("aliases", []):
                value = values.get(alias, "")
                if value:
                    break
        if value:
            lines.append(f"{key}={quote_env_value(value)}")
        else:
            lines.append(f"# {key}=")
        lines.append("")

    lines.append("# Non-secret runtime defaults")
    for key, default, note in EXAMPLE_RUNTIME_DEFAULTS:
        value = values.get(key, default)
        lines.append(f"# {note}")
        lines.append(f"{key}={quote_env_value(value)}")
    lines.append("")

    extras = sorted(key for key in values if key not in known and key not in {item[0] for item in EXAMPLE_RUNTIME_DEFAULTS})
    if extras:
        lines.append("# Existing custom variables preserved by configure_api_keys.py")
        for key in extras:
            lines.append(f"{key}={quote_env_value(values[key])}")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def write_example(path: Path, registry: list[dict]) -> None:
    placeholder_values: dict[str, str] = {}
    write_env(path, placeholder_values, registry)


def print_registry(registry: list[dict], common_only: bool = False, lang: str = "en") -> None:
    selected = registry
    if common_only:
        selected = [entry for entry in registry if entry["key"] in COMMON_KEYS]
    labels = {
        "service": "服务" if is_zh(lang) else "Service",
        "features": "启用功能" if is_zh(lang) else "Features",
        "get_key": "获取 Key" if is_zh(lang) else "Get key",
        "aliases": "兼容别名" if is_zh(lang) else "Aliases",
        "note": "说明" if is_zh(lang) else "Note",
    }
    for entry in selected:
        aliases = ", ".join(entry.get("aliases", [])) or "-"
        features = ", ".join(entry.get("features", [])) or "-"
        print(f"{entry['key']}")
        print(f"  {labels['service']}: {entry.get('service', '-')}")
        print(f"  {labels['features']}: {features}")
        print(f"  {labels['get_key']}: {entry.get('signup_url', '-')}")
        print(f"  {labels['aliases']}: {aliases}")
        note = entry.get("notes", "")
        if note:
            print(f"  {labels['note']}: {note}")
        print()


def print_check(registry: list[dict], env_path: Path, lang: str = "en") -> int:
    values = parse_env(env_path)
    print(f"{'正在检查' if is_zh(lang) else 'Checking'} {env_path}")
    if not env_path.exists():
        print("警告: .env 文件还不存在。" if is_zh(lang) else "WARN: .env file does not exist yet.")
    configured_count = 0
    for entry in registry:
        found_name, found_value = entry_configured(entry, values)
        if is_zh(lang):
            status = "已配置" if found_value else "缺失"
        else:
            status = "OK" if found_value else "MISSING"
        if found_value:
            configured_count += 1
        label = f"{entry['key']} ({entry.get('service', '-')})"
        if found_value:
            suffix = f" 通过 {found_name} = {mask(found_value)}" if is_zh(lang) else f" via {found_name} = {mask(found_value)}"
        else:
            suffix = ""
        print(f"{status:7} {label}{suffix}")
    if is_zh(lang):
        print(f"\n已配置 {configured_count}/{len(registry)} 组可选 Key。")
    else:
        print(f"\nConfigured {configured_count} of {len(registry)} optional key groups.")
    return 0


def prompt_value(prompt: str) -> str:
    try:
        return getpass.getpass(prompt)
    except (EOFError, getpass.GetPassWarning):
        return input(prompt)


def interactive_configure(registry: list[dict], env_path: Path, configure_all: bool, lang: str = "en") -> int:
    values = parse_env(env_path)
    selected = registry if configure_all else [entry for entry in registry if entry["key"] in COMMON_KEYS]
    if is_zh(lang):
        mode = "全部已知可选 Key" if configure_all else "常用 Key"
        print(f"InkyPi API Key 设置（{mode}）")
        print(f"目标文件: {env_path}")
        print("每个 Key 都可以直接按回车跳过。已有值会保留，除非你输入新值。")
        print("之后可再次运行本脚本并加 --all 来配置所有可选服务。\n")
    else:
        mode = "all known optional keys" if configure_all else "common keys only"
        print(f"InkyPi API key setup ({mode})")
        print(f"Target file: {env_path}")
        print("Press Enter to skip any key. Existing values are kept unless you type a replacement.")
        print("Run this script again with --all to configure every optional provider.\n")

    for entry in selected:
        primary = entry["key"]
        found_name, found_value = entry_configured(entry, values)
        print(f"{primary} - {entry.get('service', primary)}")
        print(f"  {'启用功能' if is_zh(lang) else 'Enables'}: {', '.join(entry.get('features', []))}")
        print(f"  {'获取 Key' if is_zh(lang) else 'Get key'}: {entry.get('signup_url', '-')}")
        aliases = entry.get("aliases", [])
        if aliases:
            print(f"  {'兼容别名' if is_zh(lang) else 'Accepted aliases'}: {', '.join(aliases)}")
        note = entry.get("notes", "")
        if note:
            print(f"  {'说明' if is_zh(lang) else 'Note'}: {note}")
        if found_value:
            print(f"  {'当前' if is_zh(lang) else 'Current'}: {found_name} = {mask(found_value)}")
        prompt = f"  粘贴 {primary}（留空表示保留/跳过）: " if is_zh(lang) else f"  Paste {primary} (blank to keep/skip): "
        new_value = prompt_value(prompt).strip()
        if new_value:
            values[primary] = new_value
        print()

    write_env(env_path, values, registry)
    print(f"{'已保存' if is_zh(lang) else 'Saved'} {env_path}")
    if is_zh(lang):
        print("修改 Key 后请重启 InkyPi: sudo systemctl restart inkypi")
    else:
        print("Restart InkyPi after changing keys: sudo systemctl restart inkypi")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Configure optional InkyPi API keys. Supports --lang zh-CN.")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH, help="Path to the .env file to read/write.")
    parser.add_argument("--all", action="store_true", help="Prompt for every optional API key instead of common keys only.")
    parser.add_argument("--list", action="store_true", help="List known API key names and registration URLs.")
    parser.add_argument("--common", action="store_true", help="With --list, show only common first-install keys.")
    parser.add_argument("--check", action="store_true", help="Show which optional key groups are configured.")
    parser.add_argument("--write-example", type=Path, help="Write an example .env file to this path.")
    parser.add_argument("--lang", choices=("en", "zh-CN"), help="Output language. Defaults to INKYPI_LANG or system locale.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    registry = load_registry()
    lang = detect_language(args.lang)

    if args.list:
        print_registry(registry, common_only=args.common, lang=lang)
        return 0

    if args.check:
        return print_check(registry, args.env_file, lang=lang)

    if args.write_example:
        write_example(args.write_example, registry)
        print(f"{'已写入' if is_zh(lang) else 'Wrote'} {args.write_example}")
        return 0

    return interactive_configure(registry, args.env_file, configure_all=args.all, lang=lang)


if __name__ == "__main__":
    raise SystemExit(main())
