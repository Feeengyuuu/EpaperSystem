from __future__ import annotations

import argparse
import json
from pathlib import Path


LOCAL_EVENTS_PLUGIN_ID = "ticketmaster_events"
LOCAL_EVENTS_INSTANCE_NAME = "DailyShow"
DEFAULT_REFRESH_INTERVAL_SECONDS = 3 * 60 * 60


def update_local_events_refresh_schedule(
    config: dict,
    interval_seconds: int = DEFAULT_REFRESH_INTERVAL_SECONDS,
) -> list[str]:
    """Keep the saved Local Events instance aligned with its three-hour policy."""
    interval_seconds = int(interval_seconds)
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    playlists = (config.get("playlist_config") or {}).get("playlists") or []
    updated: list[str] = []
    found = False

    for playlist in playlists:
        playlist_name = playlist.get("name") or "<unnamed>"
        for plugin in playlist.get("plugins") or []:
            if (
                plugin.get("plugin_id") != LOCAL_EVENTS_PLUGIN_ID
                or plugin.get("name") != LOCAL_EVENTS_INSTANCE_NAME
            ):
                continue
            found = True
            old_refresh = dict(plugin.get("refresh") or {})
            new_refresh = {"interval": interval_seconds}
            if old_refresh != new_refresh:
                plugin["refresh"] = new_refresh
                updated.append(
                    f"{playlist_name}/{LOCAL_EVENTS_PLUGIN_ID}/"
                    f"{LOCAL_EVENTS_INSTANCE_NAME}: {old_refresh} -> {new_refresh}"
                )

    if not found:
        raise ValueError(
            f"No {LOCAL_EVENTS_INSTANCE_NAME}/{LOCAL_EVENTS_PLUGIN_ID} playlist instance found."
        )
    return updated or [
        f"{LOCAL_EVENTS_PLUGIN_ID}/{LOCAL_EVENTS_INSTANCE_NAME} already "
        f"refreshes every {interval_seconds} seconds"
    ]


def update_device_config_file(
    path: Path,
    interval_seconds: int = DEFAULT_REFRESH_INTERVAL_SECONDS,
    dry_run: bool = False,
) -> list[str]:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    updated = update_local_events_refresh_schedule(config, interval_seconds)
    if not dry_run:
        path.write_text(
            json.dumps(config, ensure_ascii=False, indent=4) + "\n",
            encoding="utf-8",
        )
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update Local Events to its three-hour refresh policy."
    )
    parser.add_argument("device_json", type=Path)
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=DEFAULT_REFRESH_INTERVAL_SECONDS,
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    for line in update_device_config_file(
        args.device_json,
        args.interval_seconds,
        dry_run=args.dry_run,
    ):
        print(line)


if __name__ == "__main__":
    main()
