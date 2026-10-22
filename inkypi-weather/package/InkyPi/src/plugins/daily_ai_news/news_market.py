"""Market strip data for 整点新闻: A-share and US index quotes from Yahoo, with Massive
when a key is configured."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
from urllib.parse import quote

import pytz

from utils.http_client import get_http_client
from utils.massive_market_data import MassiveMarketData, MassiveMarketDataError, load_massive_api_key

logger = logging.getLogger(__name__)

MAX_PROVIDER_IO_WORKERS = 4
MARKET_GROUPS = {
    "a_share": [
        ("000001.SS", "上证指数"),
        ("399001.SZ", "深证成指"),
        ("399006.SZ", "创业板指"),
    ],
    "us_stock": [
        ("^GSPC", "标普500"),
        ("^IXIC", "纳斯达克"),
        ("^DJI", "道琼斯"),
    ],
}
MASSIVE_INDEX_TICKERS = {
    "^GSPC": {"I:SPX"},
    "^IXIC": {"I:COMP", "I:NDX"},
    "^DJI": {"I:DJI"},
}
MARKET_SHORT_NAMES = {
    "上证指数": "上证",
    "深证成指": "深成",
    "创业板指": "创业板",
    "标普500": "标普",
    "纳斯达克": "纳指",
    "道琼斯": "道指",
}
# The exchange or index mark beside each tile's name, from market_logos/.
MARKET_LOGOS = {
    "上证指数": "sse",
    "深证成指": "szse",
    "创业板指": "szse",
    "标普500": "sp500",
    "纳斯达克": "nasdaq",
    "道琼斯": "dowjones",
}
# Daily closes kept per index for the market tile sparkline.
MARKET_CLOSES_KEPT = 22


class NewsMarketMixin:
    """Index quotes and daily closes for the market strip."""

    def _fetch_market_snapshot(self, now: datetime, device_config=None) -> dict[str, Any]:
        snapshot: dict[str, Any] = {"generated_at": now.isoformat(), "groups": {}}
        massive_client = None
        massive_key = load_massive_api_key(device_config)
        if massive_key:
            massive_client = MassiveMarketData(massive_key)

        quote_tasks = [
            (group, symbol, name)
            for group, symbols in MARKET_GROUPS.items()
            for symbol, name in symbols
        ]
        rows_by_group: dict[str, list[dict[str, Any]]] = {
            group: [] for group in MARKET_GROUPS
        }
        worker_count = max(
            1,
            min(
                MAX_PROVIDER_IO_WORKERS,
                len(quote_tasks) + int(massive_client is not None),
            ),
        )
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            macro_future = (
                executor.submit(self._fetch_massive_macro, massive_client)
                if massive_client is not None
                else None
            )
            quote_futures = [
                (
                    group,
                    symbol,
                    executor.submit(
                        self._fetch_market_quote,
                        massive_client,
                        symbol,
                        name,
                    ),
                )
                for group, symbol, name in quote_tasks
            ]
            for group, symbol, future in quote_futures:
                try:
                    row = future.result()
                except Exception as exc:
                    logger.warning(
                        "Market quote worker failed for %s: %s",
                        symbol,
                        type(exc).__name__,
                    )
                    row = None
                if row:
                    rows_by_group[group].append(row)
            if macro_future is not None:
                try:
                    snapshot["macro"] = macro_future.result()
                except Exception as exc:
                    logger.warning(
                        "Market macro worker failed: %s",
                        type(exc).__name__,
                    )

        snapshot["groups"].update(rows_by_group)
        return snapshot

    def _fetch_market_quote(self, massive_client, symbol: str, name: str):
        row = self._fetch_massive_quote(massive_client, symbol, name)
        if self._is_massive_index_proxy_quote(symbol, row):
            logger.warning(
                "Discarding Massive ETF proxy %s for index %s; using Yahoo fallback",
                row.get("massive_symbol"),
                symbol,
            )
            row = None
        if not row:
            return self._fetch_yahoo_quote(symbol, name)
        if not row.get("closes"):
            history = self._fetch_yahoo_quote(symbol, name)
            if history and str(history.get("as_of") or "")[:10] > str(row.get("as_of") or "")[:10]:
                # Massive can lag a session behind; mixing dates in one strip misleads.
                return history
            if history and history.get("closes"):
                # Otherwise Massive gives the quote and Yahoo's daily closes feed the sparkline.
                row = {**row, "closes": history["closes"]}
        return row

    def _fetch_massive_quote(self, massive_client, symbol: str, name: str) -> dict[str, Any] | None:
        if massive_client is None:
            return None
        try:
            return massive_client.fetch_quote(symbol, name)
        except MassiveMarketDataError as exc:
            logger.warning("Massive market quote failed for %s: %s", symbol, exc)
            return None

    @staticmethod
    def _is_massive_index_proxy_quote(symbol: str, row: dict[str, Any] | None) -> bool:
        accepted_tickers = MASSIVE_INDEX_TICKERS.get(symbol)
        if not accepted_tickers or not row or row.get("source") != "massive":
            return False
        massive_symbol = str(row.get("massive_symbol") or "").upper()
        return massive_symbol not in accepted_tickers

    def _fetch_massive_macro(self, massive_client) -> dict[str, Any]:
        try:
            treasury_yields = massive_client.fetch_treasury_yields(limit=1)
        except MassiveMarketDataError as exc:
            logger.warning("Massive macro fetch failed: %s", exc)
            treasury_yields = []
        return {
            "source": "massive",
            "treasury_yields": treasury_yields[:1],
        }

    def _yahoo_chart(self, symbol: str, chart_range: str, interval: str) -> dict[str, Any] | None:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}"
        try:
            resp = get_http_client().request_json(
                "GET",
                url,
                params={"range": chart_range, "interval": interval},
                timeout=10,
                headers={"User-Agent": "InkyPi Daily AI News/1.0"},
            )
            return resp.data["chart"]["result"][0]
        except Exception as exc:
            logger.warning("Market quote fetch failed for %s (%s): %s", symbol, interval, exc)
            return None

    @staticmethod
    def _yahoo_series(result: dict[str, Any]) -> list[tuple[float, float]]:
        quote_data = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        return [
            (stamp, float(close))
            for stamp, close in zip(result.get("timestamp") or [], quote_data.get("close") or [])
            if isinstance(stamp, (int, float)) and isinstance(close, (int, float))
        ]

    @staticmethod
    def _daily_closes(series, gmtoffset: Any) -> list[tuple[float, float]]:
        """The last bar of each exchange-local day, from intraday bars."""
        offset = gmtoffset if isinstance(gmtoffset, (int, float)) else 0
        days: dict[Any, tuple[float, float]] = {}
        for stamp, close in series:
            days[datetime.fromtimestamp(stamp + offset, tz=pytz.UTC).date()] = (stamp, close)
        return [days[day] for day in sorted(days)]

    def _fetch_yahoo_quote(self, symbol: str, name: str) -> dict[str, Any] | None:
        result = self._yahoo_chart(symbol, "1mo", "1d")
        if result is None:
            return None

        meta = result.get("meta") or {}
        series = self._yahoo_series(result)
        price = meta.get("regularMarketPrice")
        if not isinstance(price, (int, float)) and series:
            price = series[-1][1]
        if not isinstance(price, (int, float)):
            return None

        previous = None
        if len(series) < 2:
            # Some indexes (创业板指 399006.SZ) have no daily history on Yahoo; hourly bars,
            # grouped by exchange day, give their recent closes. The hourly chart's own
            # previousClose can be a session behind, so it is only a last resort.
            hourly = self._yahoo_chart(symbol, "5d", "60m")
            if hourly is not None:
                hourly_meta = hourly.get("meta") or {}
                daily = self._daily_closes(self._yahoo_series(hourly), hourly_meta.get("gmtoffset"))
                series = daily or series
                previous_close = hourly_meta.get("previousClose")
                if len(daily) < 2 and isinstance(previous_close, (int, float)) and previous_close:
                    previous = float(previous_close)
        if previous is None:
            previous = self._previous_session_close(series, meta.get("regularMarketTime"), meta.get("gmtoffset"))
        change = change_pct = None
        if previous:
            change = price - previous
            change_pct = change / previous * 100

        as_of = ""
        stamps = [series[-1][0]] if series else []
        market_time = meta.get("regularMarketTime")
        if isinstance(market_time, (int, float)):
            # The price can come from a session the daily bars do not show yet.
            stamps.append(market_time)
        if stamps:
            try:
                as_of = datetime.fromtimestamp(max(stamps), tz=pytz.UTC).isoformat()
            except Exception:
                as_of = ""

        return {
            "symbol": symbol,
            "name": name,
            "price": round(float(price), 2),
            "change": round(float(change), 2) if isinstance(change, (int, float)) else None,
            "change_pct": round(float(change_pct), 2) if isinstance(change_pct, (int, float)) else None,
            "as_of": as_of,
            "currency": meta.get("currency") or "",
            "exchange": meta.get("exchangeName") or meta.get("fullExchangeName") or "",
            "source": "yahoo",
            "closes": [round(close, 2) for _stamp, close in series[-MARKET_CLOSES_KEPT:]],
        }

    @staticmethod
    def _previous_session_close(series, market_time: Any, gmtoffset: Any) -> float | None:
        """The close the latest price changed from: the bar before today's, or the last bar.

        Yahoo stamps each daily bar at its session open; when the last bar is the
        current price's session, the previous session is the bar before it. The
        chart's chartPreviousClose precedes the whole range, so it is never used.
        """
        if not series:
            return None
        offset = gmtoffset if isinstance(gmtoffset, (int, float)) else 0

        def session_day(stamp):
            return datetime.fromtimestamp(stamp + offset, tz=pytz.UTC).date()

        last_is_current = not isinstance(market_time, (int, float)) or session_day(series[-1][0]) == session_day(market_time)
        if last_is_current:
            return series[-2][1] if len(series) >= 2 else None
        return series[-1][1]
