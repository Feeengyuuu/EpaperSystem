import sys
from copy import deepcopy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from tools.update_money_portfolio_csv import update_money_portfolio_csv  # noqa: E402


def _device_config():
    return {
        "playlist_config": {
            "playlists": [
                {
                    "name": "DailyDoseOfDay",
                    "plugins": [
                        {
                            "plugin_id": "stocktracker",
                            "name": "Money",
                            "plugin_settings": {
                                "tickers": "AAPL,NVDA",
                                "shares": "1,2",
                                "period": "5d",
                            },
                            "refresh": {"scheduled": "13:10"},
                        }
                    ],
                }
            ]
        }
    }


def test_update_money_portfolio_csv_sets_csv_path_and_period():
    config = _device_config()

    updated = update_money_portfolio_csv(
        config,
        "/usr/local/inkypi/data/money_robinhood_holdings.csv",
        period="1mo",
        extra_settings={
            "cash_balance": "123.45",
            "buying_power": "123.45",
            "pending_deposits": "50.00",
            "account_value": "1000.00",
        },
    )

    settings = config["playlist_config"]["playlists"][0]["plugins"][0]["plugin_settings"]
    assert settings["portfolio_csv_path"] == "/usr/local/inkypi/data/money_robinhood_holdings.csv"
    assert settings["period"] == "1mo"
    assert settings["cash_balance"] == "123.45"
    assert settings["buying_power"] == "123.45"
    assert settings["pending_deposits"] == "50.00"
    assert settings["account_value"] == "1000.00"
    assert settings["tickers"] == "AAPL,NVDA"
    assert settings["shares"] == "1,2"
    assert len(updated) == 6


def test_update_money_portfolio_csv_is_idempotent():
    config = _device_config()
    update_money_portfolio_csv(config, "/tmp/holdings.csv")
    before = deepcopy(config)

    updated = update_money_portfolio_csv(config, "/tmp/holdings.csv")

    assert config == before
    assert updated == ["stocktracker/Money already uses /tmp/holdings.csv"]
