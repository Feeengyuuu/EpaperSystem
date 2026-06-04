from __future__ import annotations

import argparse
import json

import requests


DEFAULT_BASE = "http://192.168.1.188"
PLAYLIST = "DailyDoseOfDay"
PLUGIN_ID = "lol_info"
INSTANCE_NAME = "LoLInfo"


def _settings() -> dict[str, str]:
    return {
        "plugin_id": PLUGIN_ID,
        "gameName": "无敌杀人王",
        "tagLine": "pog",
        "platformRoute": "na1",
        "regionalRoute": "americas",
        "refreshMinutes": "120",
        "recentLimit": "5",
        "masteryLimit": "5",
        "includeChallenges": "true",
        "includeActiveGame": "true",
        "refreshOnDisplay": "true",
        "useMockData": "false",
        "forceRefresh": "false",
        "refresh_settings": json.dumps(
            {"refreshType": "interval", "unit": "hour", "interval": "2"},
            ensure_ascii=False,
        ),
    }


def update_or_add(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    settings = _settings()

    update = requests.put(f"{base_url}/update_plugin_instance/{INSTANCE_NAME}", data=settings, timeout=30)
    if update.ok:
        return f"updated existing {INSTANCE_NAME}"

    add_settings = dict(settings)
    add_settings["refresh_settings"] = json.dumps(
        {
            "playlist": PLAYLIST,
            "instance_name": INSTANCE_NAME,
            "refreshType": "interval",
            "unit": "hour",
            "interval": "2",
        },
        ensure_ascii=False,
    )
    add = requests.post(f"{base_url}/add_plugin", data=add_settings, timeout=30)
    add.raise_for_status()
    return f"added {INSTANCE_NAME} to {PLAYLIST}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Add or update LoLInfo in the DailyDoseOfDay random playlist.")
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    args = parser.parse_args()
    print(update_or_add(args.base_url))


if __name__ == "__main__":
    main()
