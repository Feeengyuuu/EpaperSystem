from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


MONEY_PLUGIN_ID = "stocktracker"
MONEY_INSTANCE_NAME = "Money"
DEFAULT_MARKET_CLOSE_REFRESH = "13:10"


def _validate_hhmm(value: str) -> str:
    text = str(value or "").strip()
    datetime.strptime(text, "%H:%M")
    return text


def update_money_refresh_schedule(config: dict, scheduled_time: str = DEFAULT_MARKET_CLOSE_REFRESH) -> list[str]:
    """Set the Money plugin internet refresh to the post-close scheduled time."""
    scheduled_time = _validate_hhmm(scheduled_time)
    playlist_config = config.get("playlist_config") or {}
    playlists = playlist_config.get("playlists") or []
    updated: list[str] = []
    found = False

    for playlist in playlists:
        playlist_name = playlist.get("name") or "<unnamed>"
        for plugin in playlist.get("plugins") or []:
            if plugin.get("plugin_id") != MONEY_PLUGIN_ID or plugin.get("name") != MONEY_INSTANCE_NAME:
                continue
            found = True
            old_refresh = dict(plugin.get("refresh") or {})
            new_refresh = {"scheduled": scheduled_time}
            if old_refresh != new_refresh:
                plugin["refresh"] = new_refresh
                updated.append(f"{playlist_name}/{MONEY_PLUGIN_ID}/{MONEY_INSTANCE_NAME}: {old_refresh} -> {new_refresh}")

    if not found:
        raise ValueError(f"No {MONEY_INSTANCE_NAME}/{MONEY_PLUGIN_ID} playlist instance found.")
    return updated or [f"{MONEY_PLUGIN_ID}/{MONEY_INSTANCE_NAME} already scheduled at {scheduled_time}"]


def update_device_config_file(path: Path, scheduled_time: str = DEFAULT_MARKET_CLOSE_REFRESH, dry_run: bool = False) -> list[str]:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    updated = update_money_refresh_schedule(config, scheduled_time)
    if not dry_run:
        path.write_text(json.dumps(config, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update the Money stocktracker playlist instance to refresh shortly after US market close."
    )
    parser.add_argument("device_json", type=Path, help="Path to an InkyPi device.json file.")
    parser.add_argument(
        "--time",
        default=DEFAULT_MARKET_CLOSE_REFRESH,
        help="Scheduled refresh time in the device timezone, default: 13:10.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate and print changes without writing.")
    args = parser.parse_args()

    for line in update_device_config_file(args.device_json, args.time, dry_run=args.dry_run):
        print(line)


if __name__ == "__main__":
    main()
