#!/usr/bin/env python3
"""Convert a Robinhood holdings snapshot into StockTracker's CSV format.

The Money instance of StockTracker already accepts a simple CSV with:

    symbol,shares

This tool intentionally expects a current positions/holdings snapshot, not an
account activity export. Activity rows can be incomplete for current holdings.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT = Path(".tmp") / "money_robinhood_holdings.csv"

POSITION_LIST_KEYS = (
    "positions",
    "holdings",
    "equity_positions",
    "stock_positions",
    "results",
    "items",
)
NESTED_CONTAINER_KEYS = ("data", "account", "portfolio", "brokerage")
WRAPPER_KEYS = ("result", "structuredContent", "structured_content")
SYMBOL_KEYS = (
    "symbol",
    "ticker",
    "ticker_symbol",
    "security_symbol",
    "instrument_symbol",
)
QUANTITY_KEYS = (
    "shares",
    "share_count",
    "quantity",
    "qty",
    "current_quantity",
    "total_quantity",
    "position",
)
CASH_SYMBOLS = {"", "CASH", "USD", "US DOLLAR", "US_DOLLAR"}
SKIP_ASSET_TYPE_HINTS = ("option", "crypto", "cash")


@dataclass(frozen=True)
class Holding:
    symbol: str
    shares: Decimal


def _field_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _normalized_map(raw: dict[str, Any]) -> dict[str, Any]:
    return {_field_key(key): value for key, value in raw.items()}


def _first_present(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    normalized = _normalized_map(row)
    for key in keys:
        value = normalized.get(_field_key(key))
        if value not in (None, ""):
            return value
    return None


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return -parsed if negative else parsed


def _clean_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    if symbol in CASH_SYMBOLS:
        return ""
    return symbol


def _nested_symbol(value: Any) -> str:
    if isinstance(value, dict):
        symbol = _first_present(value, SYMBOL_KEYS)
        if symbol:
            return _clean_symbol(symbol)
    return ""


def _extract_symbol(row: dict[str, Any]) -> str:
    symbol = _first_present(row, SYMBOL_KEYS)
    if symbol:
        return _clean_symbol(symbol)

    for nested_key in ("instrument", "security", "asset", "equity", "stock"):
        nested = row.get(nested_key)
        symbol = _nested_symbol(nested)
        if symbol:
            return symbol

    return ""


def _extract_quantity(row: dict[str, Any]) -> Decimal | None:
    return _parse_decimal(_first_present(row, QUANTITY_KEYS))


def _asset_type_text(row: dict[str, Any]) -> str:
    values = []
    for key in ("type", "asset_type", "instrument_type", "position_type", "security_type"):
        value = _first_present(row, (key,))
        if value:
            values.append(str(value).lower())
    return " ".join(values)


def _looks_like_activity_row(row: dict[str, Any]) -> bool:
    normalized = _normalized_map(row)
    activity_keys = {"activitydate", "transcode", "transactiontype", "activitytype"}
    return any(key in normalized for key in activity_keys)


def _extract_position_lists(payload: Any) -> list[list[dict[str, Any]]]:
    if isinstance(payload, list):
        return [[item for item in payload if isinstance(item, dict)]]

    if not isinstance(payload, dict):
        return []

    results: list[list[dict[str, Any]]] = []
    for key in WRAPPER_KEYS:
        results.extend(_extract_position_lists(payload.get(key)))

    content = payload.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            for key in ("json", "data", "structuredContent", "structured_content"):
                results.extend(_extract_position_lists(item.get(key)))
            text = item.get("text")
            if isinstance(text, str):
                try:
                    results.extend(_extract_position_lists(json.loads(text)))
                except json.JSONDecodeError:
                    pass

    for key in POSITION_LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            rows = [item for item in value if isinstance(item, dict)]
            if rows:
                results.append(rows)

    for key in NESTED_CONTAINER_KEYS:
        nested = payload.get(key)
        results.extend(_extract_position_lists(nested))

    return results


def normalize_holdings(payload: Any) -> list[Holding]:
    """Return positive equity holdings from a Robinhood snapshot-like payload."""

    candidate_lists = _extract_position_lists(payload)
    activity_rows_seen = False

    for rows in candidate_lists:
        holdings: dict[str, Decimal] = {}
        order: list[str] = []
        usable_rows = 0

        for row in rows:
            if _looks_like_activity_row(row):
                activity_rows_seen = True
                continue

            asset_text = _asset_type_text(row)
            if any(hint in asset_text for hint in SKIP_ASSET_TYPE_HINTS):
                continue

            symbol = _extract_symbol(row)
            shares = _extract_quantity(row)
            if not symbol or shares is None:
                continue

            usable_rows += 1
            if symbol not in holdings:
                holdings[symbol] = Decimal("0")
                order.append(symbol)
            holdings[symbol] += shares

        result = [Holding(symbol, holdings[symbol]) for symbol in order if holdings[symbol] > 0]
        if result:
            return result
        if usable_rows:
            break

    if activity_rows_seen:
        raise ValueError(
            "Robinhood activity rows were found, but current holdings were not. "
            "Use a positions/holdings snapshot, not an activity export."
        )

    raise ValueError("No positive equity holdings found in the Robinhood snapshot")


def write_stocktracker_csv(holdings: list[Holding], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["symbol", "shares"])
        for holding in holdings:
            writer.writerow([holding.symbol, str(holding.shares.normalize())])


def load_payload(input_path: str) -> Any:
    if input_path == "-":
        return json.load(sys.stdin)
    with Path(input_path).open("r", encoding="utf-8-sig") as input_file:
        return json.load(input_file)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Normalize a Robinhood current holdings snapshot for the Money StockTracker instance."
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="-",
        help="Path to a Robinhood MCP JSON response, or '-' for stdin.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"CSV output path. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--print-symbols",
        action="store_true",
        help="Print only normalized symbols after writing the CSV.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = load_payload(args.input)
    holdings = normalize_holdings(payload)
    write_stocktracker_csv(holdings, Path(args.output))

    print(f"Wrote {len(holdings)} holdings to {args.output}")
    if args.print_symbols:
        print(",".join(holding.symbol for holding in holdings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
