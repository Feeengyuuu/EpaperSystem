import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.massive_market_data import (  # noqa: E402
    MassiveMarketData,
    MassiveMarketDataError,
    load_massive_api_key,
    massive_ticker_candidates,
    period_date_range,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payloads.pop(0))


class FakeDeviceConfig:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def load_env_key(self, name):
        self.calls.append(name)
        return self.values.get(name)


def test_load_massive_api_key_accepts_existing_typo_name(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    device_config = FakeDeviceConfig({"Massive_Ecnomic_Key": "live-key"})

    assert load_massive_api_key(device_config) == "live-key"
    assert "MASSIVE_API_KEY" in device_config.calls
    assert "Massive_Ecnomic_Key" in device_config.calls


def test_massive_ticker_candidates_skip_non_us_yahoo_suffixes():
    assert massive_ticker_candidates("AAPL") == ["AAPL"]
    assert massive_ticker_candidates("^GSPC")[0] == "I:SPX"
    assert massive_ticker_candidates("000001.SS") == []
    assert massive_ticker_candidates("0P0001RU1X.L") == []


def test_period_date_range_maps_stocktracker_periods():
    assert period_date_range("5d", date(2026, 6, 3)) == ("2026-05-20", "2026-06-03")
    assert period_date_range("ytd", date(2026, 6, 3)) == ("2026-01-01", "2026-06-03")


def test_fetch_quote_returns_normalized_market_row_without_key_leakage():
    session = FakeSession([
        {
            "status": "OK",
            "results": [
                {"t": 1780185600000, "o": 100, "h": 105, "l": 99, "c": 100, "v": 10},
                {"t": 1780272000000, "o": 100, "h": 112, "l": 100, "c": 110, "v": 20},
            ],
        }
    ])
    client = MassiveMarketData("secret-key", session=session)

    row = client.fetch_quote("AAPL", "Apple")

    assert row == {
        "symbol": "AAPL",
        "name": "Apple",
        "price": 110.0,
        "change": 10.0,
        "change_pct": 10.0,
        "as_of": "2026-06-01",
        "currency": "USD",
        "exchange": "Massive",
        "source": "massive",
        "massive_symbol": "AAPL",
    }
    assert session.calls[0][1]["params"]["apiKey"] == "secret-key"
    assert "secret-key" not in row.values()


def test_fetch_treasury_yields_uses_economy_endpoint():
    session = FakeSession([
        {
            "status": "OK",
            "results": [
                {
                    "date": "2026-06-02",
                    "yield_2_year": 4.1,
                    "yield_10_year": 4.3,
                }
            ],
        }
    ])
    client = MassiveMarketData("secret-key", session=session)

    rows = client.fetch_treasury_yields()

    assert rows == [{"date": "2026-06-02", "yield_2_year": 4.1, "yield_10_year": 4.3}]
    assert session.calls[0][0].endswith("/fed/v1/treasury-yields")
    assert session.calls[0][1]["params"]["sort"] == "date.desc"


def test_massive_errors_do_not_expose_api_key():
    class FailingSession:
        def get(self, url, **kwargs):
            raise RuntimeError(f"failed url={url}?apiKey={kwargs['params']['apiKey']}")

    client = MassiveMarketData("secret-key", session=FailingSession())

    try:
        client.fetch_treasury_yields()
    except MassiveMarketDataError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected MassiveMarketDataError")

    assert "secret-key" not in message
    assert "apiKey" not in message
