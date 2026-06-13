from __future__ import annotations

import argparse
import json
from pathlib import Path


MONEY_PLUGIN_ID = "stocktracker"
MONEY_INSTANCE_NAME = "Money"


def update_money_portfolio_csv(
    config: dict,
    csv_path: str,
    period: str | None = None,
    extra_settings: dict[str, str] | None = None,
) -> list[str]:
    """Point the Money stocktracker instance at a holdings CSV."""

    csv_path = str(csv_path or "").strip()
    if not csv_path:
        raise ValueError("csv_path is required")

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
            settings = plugin.setdefault("plugin_settings", {})
            old_path = settings.get("portfolio_csv_path")
            if old_path != csv_path:
                settings["portfolio_csv_path"] = csv_path
                updated.append(
                    f"{playlist_name}/{MONEY_PLUGIN_ID}/{MONEY_INSTANCE_NAME}: "
                    f"portfolio_csv_path {old_path!r} -> {csv_path!r}"
                )

            if period:
                old_period = settings.get("period")
                if old_period != period:
                    settings["period"] = period
                    updated.append(
                        f"{playlist_name}/{MONEY_PLUGIN_ID}/{MONEY_INSTANCE_NAME}: "
                        f"period {old_period!r} -> {period!r}"
                    )

            for key, value in (extra_settings or {}).items():
                if value is None:
                    continue
                value = str(value).strip()
                if not value:
                    continue
                old_value = settings.get(key)
                if old_value != value:
                    settings[key] = value
                    updated.append(
                        f"{playlist_name}/{MONEY_PLUGIN_ID}/{MONEY_INSTANCE_NAME}: "
                        f"{key} {old_value!r} -> {value!r}"
                    )

    if not found:
        raise ValueError(f"No {MONEY_INSTANCE_NAME}/{MONEY_PLUGIN_ID} playlist instance found.")
    return updated or [f"{MONEY_PLUGIN_ID}/{MONEY_INSTANCE_NAME} already uses {csv_path}"]


def update_device_config_file(
    path: Path,
    csv_path: str,
    period: str | None = None,
    extra_settings: dict[str, str] | None = None,
    dry_run: bool = False,
) -> list[str]:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    updated = update_money_portfolio_csv(config, csv_path, period=period, extra_settings=extra_settings)
    if not dry_run:
        path.write_text(json.dumps(config, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update the Money stocktracker playlist instance to read holdings from a CSV."
    )
    parser.add_argument("device_json", type=Path, help="Path to an InkyPi device.json file.")
    parser.add_argument("csv_path", help="Path that the running InkyPi service can read.")
    parser.add_argument("--period", help="Optional StockTracker history window, for example 1mo.")
    parser.add_argument("--cash-balance", help="Optional Robinhood cash balance.")
    parser.add_argument("--buying-power", help="Optional Robinhood buying power.")
    parser.add_argument("--pending-deposits", help="Optional Robinhood pending deposits.")
    parser.add_argument("--account-value", help="Optional Robinhood account value override.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print changes without writing.")
    args = parser.parse_args()

    extra_settings = {
        "cash_balance": args.cash_balance,
        "buying_power": args.buying_power,
        "pending_deposits": args.pending_deposits,
        "account_value": args.account_value,
    }
    for line in update_device_config_file(
        args.device_json,
        args.csv_path,
        period=args.period,
        extra_settings=extra_settings,
        dry_run=args.dry_run,
    ):
        print(line)


if __name__ == "__main__":
    main()
