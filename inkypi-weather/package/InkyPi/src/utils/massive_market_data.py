from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

MASSIVE_BASE_URL = "https://api.massive.com"
MASSIVE_ENV_KEYS = (
    "MASSIVE_API_KEY",
    "MASSIVE_ECONOMIC_KEY",
    "MASSIVE_ECNOMIC_KEY",
    "Massive_Ecnomic_Key",
)

MASSIVE_SYMBOL_ALIASES = {
    "^GSPC": ("I:SPX", "SPY"),
    "^IXIC": ("I:COMP", "I:NDX", "QQQ"),
    "^DJI": ("I:DJI", "DIA"),
}


class MassiveMarketDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class MassiveBar:
    ticker: str
    date: str
    timestamp_ms: int | None
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None


def load_massive_api_key(device_config=None) -> str:
    for key_name in MASSIVE_ENV_KEYS:
        value = ""
        if device_config is not None and hasattr(device_config, "load_env_key"):
            try:
                value = device_config.load_env_key(key_name) or ""
            except Exception as exc:
                logger.warning("Could not read Massive env key %s: %s", key_name, exc)
                value = ""
        if not value:
            value = os.getenv(key_name, "")
        value = str(value or "").strip()
        if value:
            return value
    return ""


def massive_ticker_candidates(symbol: str) -> list[str]:
    raw = str(symbol or "").strip().upper()
    if not raw:
        return []
    if raw in MASSIVE_SYMBOL_ALIASES:
        return list(MASSIVE_SYMBOL_ALIASES[raw])
    if raw.startswith("^"):
        return []
    if re.search(r"\.(SS|SZ|HK|L|TO|AX|PA|DE|F)$", raw):
        return []
    if re.match(r"^[A-Z0-9][A-Z0-9.\-:]{0,15}$", raw):
        return [raw]
    return []


def period_date_range(period: str, today: date | None = None) -> tuple[str, str]:
    end = today or date.today()
    period_key = str(period or "1mo").lower()
    if period_key == "ytd":
        start = date(end.year, 1, 1)
    else:
        days_by_period = {
            "1d": 7,
            "5d": 14,
            "1mo": 45,
            "3mo": 110,
            "6mo": 200,
            "1y": 370,
        }
        start = end - timedelta(days=days_by_period.get(period_key, 45))
    return start.isoformat(), end.isoformat()


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _date_from_timestamp_ms(value: Any) -> str:
    try:
        timestamp_ms = int(value)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).date().isoformat()


def _bar_from_api(ticker: str, payload: dict[str, Any]) -> MassiveBar | None:
    close = _finite_float(payload.get("c"))
    if close is None:
        return None
    timestamp_ms = None
    try:
        timestamp_ms = int(payload["t"]) if payload.get("t") is not None else None
    except (TypeError, ValueError):
        timestamp_ms = None
    return MassiveBar(
        ticker=ticker,
        date=_date_from_timestamp_ms(timestamp_ms),
        timestamp_ms=timestamp_ms,
        open=_finite_float(payload.get("o")),
        high=_finite_float(payload.get("h")),
        low=_finite_float(payload.get("l")),
        close=close,
        volume=_finite_float(payload.get("v")),
    )


class MassiveMarketData:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = MASSIVE_BASE_URL,
        timeout: int = 8,
        session=None,
    ):
        self.api_key = str(api_key or "").strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session

    def available(self) -> bool:
        return bool(self.api_key)

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.api_key:
            raise MassiveMarketDataError("Massive API key is not configured")
        request_params = dict(params or {})
        request_params["apiKey"] = self.api_key
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            if self.session is None:
                self.session = self._requests_session()
            session = self.session
            response = session.get(
                url,
                params=request_params,
                timeout=self.timeout,
                headers={"User-Agent": "InkyPi MassiveMarketData/1.0"},
            )
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise MassiveMarketDataError(f"{type(exc).__name__}: request failed") from exc
        if not isinstance(payload, dict):
            raise MassiveMarketDataError("Massive response was not a JSON object")
        return payload

    @staticmethod
    def _requests_session():
        import requests

        return requests.Session()

    def fetch_daily_bars(
        self,
        ticker: str,
        *,
        period: str = "1mo",
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 5000,
    ) -> list[MassiveBar]:
        start, end = period_date_range(period)
        start = from_date or start
        end = to_date or end
        encoded_ticker = quote(str(ticker or "").strip(), safe=":.-")
        payload = self.get_json(
            f"/v2/aggs/ticker/{encoded_ticker}/range/1/day/{start}/{end}",
            {
                "adjusted": "true",
                "sort": "asc",
                "limit": int(limit),
            },
        )
        rows = payload.get("results") or []
        if not isinstance(rows, list):
            return []
        bars = [
            bar
            for bar in (_bar_from_api(str(ticker), row) for row in rows if isinstance(row, dict))
            if bar is not None
        ]
        return bars

    def fetch_quote(self, symbol: str, name: str = "") -> dict[str, Any] | None:
        for ticker in massive_ticker_candidates(symbol):
            try:
                bars = self.fetch_daily_bars(ticker, period="5d")
            except MassiveMarketDataError as exc:
                logger.warning("Massive quote fetch failed for %s: %s", ticker, exc)
                continue
            if not bars:
                continue
            latest = bars[-1]
            previous = bars[-2].close if len(bars) >= 2 else None
            change = latest.close - previous if previous else None
            change_pct = change / previous * 100 if previous and change is not None else None
            return {
                "symbol": symbol,
                "name": name or symbol,
                "price": round(float(latest.close), 2),
                "change": round(float(change), 2) if change is not None else None,
                "change_pct": round(float(change_pct), 2) if change_pct is not None else None,
                "as_of": latest.date,
                "currency": "USD",
                "exchange": "Massive",
                "source": "massive",
                "massive_symbol": ticker,
            }
        return None

    def fetch_ticker_details(self, ticker: str) -> dict[str, Any]:
        encoded_ticker = quote(str(ticker or "").strip(), safe=":.-")
        payload = self.get_json(f"/v3/reference/tickers/{encoded_ticker}", {})
        result = payload.get("results")
        return result if isinstance(result, dict) else {}

    def fetch_treasury_yields(self, *, limit: int = 1) -> list[dict[str, Any]]:
        payload = self.get_json(
            "/fed/v1/treasury-yields",
            {
                "limit": int(limit),
                "sort": "date.desc",
            },
        )
        rows = payload.get("results") or []
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]
