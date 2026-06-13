import csv
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from tools.robinhood_money_sync import normalize_holdings, write_stocktracker_csv  # noqa: E402


def test_robinhood_snapshot_writes_stocktracker_holdings_csv(tmp_path):
    fixture = Path(__file__).resolve().parent / "fixtures" / "robinhood_positions_snapshot.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    output_path = tmp_path / "money_robinhood_holdings.csv"

    holdings = normalize_holdings(payload)
    write_stocktracker_csv(holdings, output_path)

    with output_path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))

    assert rows == [
        {"symbol": "AAPL", "shares": "4.75"},
        {"symbol": "NVDA", "shares": "2.25"},
    ]


def test_robinhood_snapshot_can_be_loaded_from_mcp_text_wrapper():
    fixture = Path(__file__).resolve().parent / "fixtures" / "robinhood_positions_snapshot.json"
    wrapped_payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": fixture.read_text(encoding="utf-8"),
                }
            ]
        },
    }

    holdings = normalize_holdings(wrapped_payload)

    assert [(holding.symbol, str(holding.shares)) for holding in holdings] == [
        ("AAPL", "4.75"),
        ("NVDA", "2.25"),
    ]


def test_robinhood_activity_export_is_rejected_as_snapshot_source():
    fixture = Path(__file__).resolve().parent / "fixtures" / "robinhood_activity.csv"
    rows = list(csv.DictReader(fixture.open(newline="", encoding="utf-8")))

    with pytest.raises(ValueError, match="activity"):
        normalize_holdings({"positions": rows})
