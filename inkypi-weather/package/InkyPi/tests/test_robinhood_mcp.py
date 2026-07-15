import hashlib
import json
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.robinhood_mcp import (  # noqa: E402
    RobinhoodMCPClient,
    RobinhoodMCPError,
)


def _account_hash(account_number):
    return hashlib.sha256(account_number.encode("utf-8")).hexdigest()[:12]


class FakeRobinhoodClient(RobinhoodMCPClient):
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call_tool(self, name, arguments=None):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        response = self.responses[name]
        if callable(response):
            return response(arguments)
        return response


def test_robinhood_snapshot_selects_hashed_account_paginates_and_uses_newest_trade():
    account_number = "fake-account-123"

    def positions(arguments):
        if arguments.get("cursor") == "cursor-2":
            return {
                "data": {
                    "results": [
                        {"symbol": "AAPL", "quantity": "1.25", "type": "stock"},
                    ]
                }
            }
        return {
            "data": {
                "results": [
                    {"symbol": "SPCX", "quantity": "2.5", "type": "stock"},
                ],
                "next": "cursor-2",
            }
        }

    client = FakeRobinhoodClient(
        {
            "get_accounts": {"data": [{"account_number": account_number, "status": "active"}]},
            "get_equity_positions": positions,
            "get_equity_quotes": {
                "data": {
                    "results": [
                        {
                            "symbol": "SPCX",
                            "last_trade_price": "135.00",
                            "venue_last_trade_time": "2026-07-14T20:00:00Z",
                            "last_non_reg_trade_price": "136.12",
                            "venue_last_non_reg_trade_time": "2026-07-14T21:00:00Z",
                            "adjusted_previous_close": "130.00",
                        },
                        {
                            "symbol": "AAPL",
                            "last_trade_price": "220.00",
                            "venue_last_trade_time": "2026-07-14T20:00:01Z",
                            "previous_close": "215.00",
                        },
                    ]
                }
            },
            "get_portfolio": {
                "data": {
                    "total_value": "1000.50",
                    "cash": "25.50",
                    "pending_deposits": "5.00",
                    "currency": "USD",
                    "buying_power": {"buying_power": "50.25", "display_currency": "USD"},
                }
            },
        }
    )

    snapshot = client.fetch_snapshot(_account_hash(account_number))

    assert snapshot["account_hash"] == _account_hash(account_number)
    assert snapshot["positions"] == [
        {"symbol": "SPCX", "quantity": 2.5},
        {"symbol": "AAPL", "quantity": 1.25},
    ]
    assert snapshot["quotes"]["SPCX"] == {
        "symbol": "SPCX",
        "price": 136.12,
        "previous_close": 130.0,
        "timestamp": "2026-07-14T21:00:00Z",
        "extended_hours": True,
    }
    assert snapshot["portfolio_meta"] == {
        "account_value": 1000.5,
        "cash_balance": 25.5,
        "buying_power": 50.25,
        "pending_deposits": 5.0,
        "currency": "USD",
    }
    position_calls = [arguments for name, arguments in client.calls if name == "get_equity_positions"]
    assert position_calls == [
        {"account_number": account_number},
        {"account_number": account_number, "cursor": "cursor-2"},
    ]


def test_robinhood_snapshot_fails_closed_when_any_held_symbol_has_no_quote():
    account_number = "fake-account-456"
    client = FakeRobinhoodClient(
        {
            "get_accounts": {"data": [{"account_number": account_number}]},
            "get_equity_positions": {
                "data": {"results": [{"symbol": "SPCX", "quantity": "1"}]}
            },
            "get_equity_quotes": {"data": {"results": []}},
            "get_portfolio": {"data": {}},
        }
    )

    with pytest.raises(RobinhoodMCPError, match="missing an official quote.*SPCX"):
        client.fetch_snapshot(_account_hash(account_number))


def test_robinhood_client_refuses_every_trading_tool():
    client = RobinhoodMCPClient(token_path="unused.json")

    with pytest.raises(RobinhoodMCPError, match="not allowlisted"):
        client.call_tool("place_equity_order", {"symbol": "SPCX"})


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = json.dumps(payload).encode("utf-8")
        self.headers = {"Content-Type": "application/json"}
        self.text = json.dumps(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeHTTP:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse({"access_token": "new-access", "expires_in": 3600})


def test_robinhood_refresh_preserves_rotating_refresh_token_and_persists_atomically(tmp_path):
    token_path = tmp_path / "robinhood.json"
    token_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mcp_url": "https://agent.robinhood.com/mcp/trading",
                "token_url": "https://api.robinhood.com/oauth2/token/",
                "registration": {"client_id": "client-123"},
                "token": {"access_token": "old-access", "refresh_token": "refresh-123"},
            }
        ),
        encoding="utf-8",
    )
    http = FakeHTTP()
    client = RobinhoodMCPClient(token_path=token_path, http=http)

    token = client.refresh_access_token()

    saved = json.loads(token_path.read_text(encoding="utf-8"))
    assert token == "new-access"
    assert saved["token"]["access_token"] == "new-access"
    assert saved["token"]["refresh_token"] == "refresh-123"
    assert saved["token"]["expires_at"] > saved["token"]["obtained_at"]
    assert not token_path.with_suffix(".json.tmp").exists()
    assert http.calls[0][1]["data"] == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh-123",
        "client_id": "client-123",
        "resource": "https://agent.robinhood.com/mcp/trading",
    }
