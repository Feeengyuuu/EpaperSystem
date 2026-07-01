from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SESSION = PROJECT_ROOT / "src" / "plugins" / "telegram_digest" / "cache" / "telegram_account"
VENDOR_DIR = PROJECT_ROOT / "src" / "plugins" / "telegram_digest" / "vendor"


def first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return ""


def session_file_for(session_path: Path) -> Path:
    if session_path.suffix == ".session":
        return session_path
    return Path(str(session_path) + ".session")


def load_env_files(explicit: str = "") -> None:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend([
        Path.cwd() / ".env",
        PROJECT_ROOT / ".env",
        PROJECT_ROOT / "src" / ".env",
    ])
    seen = set()
    for path in candidates:
        path = path.expanduser().resolve()
        if path in seen:
            continue
        seen.add(path)
        if path.is_file():
            load_dotenv(path, override=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or refresh a local Telegram account session for Telegram Digest.")
    parser.add_argument("--api-id", default="", help="Telegram API ID from https://my.telegram.org/apps")
    parser.add_argument("--api-hash", default="", help="Telegram API hash from https://my.telegram.org/apps")
    parser.add_argument("--session", default="", help="Session path without the .session suffix, unless you include it")
    parser.add_argument("--phone", default="", help="Optional phone number; otherwise Telethon prompts interactively")
    parser.add_argument("--env-file", default="", help="Optional .env file to load before reading TELEGRAM_* values")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_files(args.env_file)

    api_id = args.api_id or first_env("TELEGRAM_API_ID", "TG_API_ID", "TELEGRAM_APP_ID", "TELEGRAM_DIGEST_API_ID")
    api_hash = args.api_hash or first_env("TELEGRAM_API_HASH", "TG_API_HASH", "TELEGRAM_APP_HASH", "TELEGRAM_DIGEST_API_HASH")
    session_value = args.session or first_env("TELEGRAM_SESSION_PATH", "TG_SESSION_PATH", "TELEGRAM_ACCOUNT_SESSION", "TELEGRAM_DIGEST_SESSION_PATH")
    session_path = Path(session_value).expanduser() if session_value else DEFAULT_SESSION

    if not api_id or not api_hash:
        print("Missing TELEGRAM_API_ID or TELEGRAM_API_HASH. Create them at https://my.telegram.org/apps.", file=sys.stderr)
        return 2

    try:
        api_id_int = int(api_id)
    except ValueError:
        print("TELEGRAM_API_ID must be an integer.", file=sys.stderr)
        return 2

    if VENDOR_DIR.is_dir() and str(VENDOR_DIR) not in sys.path:
        sys.path.insert(0, str(VENDOR_DIR))
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        print("Telethon is not installed. Install package requirements or the plugin vendor directory first.", file=sys.stderr)
        return 2

    phone = args.phone.strip()
    if not phone:
        phone = input("Phone number in international format, for example +15551234567: ").strip()
    if not phone:
        print("Phone number is required for Telegram account login.", file=sys.stderr)
        return 2

    session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(session_path), api_id_int, api_hash)
    try:
        client.start(phone=phone)
        me = client.get_me()
        username = f"@{me.username}" if getattr(me, "username", None) else str(getattr(me, "id", "unknown"))
        print(f"Authorized Telegram account: {username}")
        print(f"Session file: {session_file_for(session_path)}")
        print("Use this path as TELEGRAM_SESSION_PATH or telegramSessionPath.")
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())