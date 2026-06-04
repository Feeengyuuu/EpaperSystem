# Massive market data integration

## [LRN-20260603-005] implementation

**Logged**: 2026-06-03
**Priority**: high
**Status**: active
**Area**: epaper, market-data, api-keys

### Summary
Massive market data is now a shared integration target for InkyPi plugins. Use `utils.massive_market_data` for future Massive REST calls instead of adding plugin-local key parsing or request code.

### Implementation Pattern
- Load keys through `load_massive_api_key(device_config)`.
- Prefer `MASSIVE_API_KEY`, but keep fallback support for the live typo `Massive_Ecnomic_Key`.
- Do not log raw Massive exceptions from `requests`, because `raise_for_status()` can include URLs with `apiKey` query parameters. Raise/log redacted `MassiveMarketDataError` messages instead.
- Keep existing upstreams as fallback: Yahoo Finance for `daily_ai_news`, and yfinance for `stocktracker`.
- Skip known unsupported Yahoo-style non-US suffixes such as `.SS`, `.SZ`, and `.L` instead of forcing Massive calls.

### Live Capability Probe
A value-redacted probe from `ColoredEpaperFrame` using the live key succeeded:
- `GET /v2/aggs/ticker/AAPL/range/1/day/2025-05-01/2025-05-15`: HTTP 200, API status `OK`, returned OHLCV fields.
- `GET /fed/v1/treasury-yields`: HTTP 200, API status `OK`, returned Treasury yield fields including `yield_2_year`, `yield_10_year`, and `yield_30_year`.

### Deployment
The Massive client plus `daily_ai_news` and `stocktracker` integrations were deployed to the active package path `/home/feeengyuuu/inkypi-weather-pi-package-zero2w-20260531/InkyPi`. Remote `py_compile` passed, the deployed client fetched AAPL bars and Treasury yields, `inkypi` was restarted, and `/playlist` returned HTTP 200 after Waveshare initialization.

### Connected Plugins
- `daily_ai_news`: Massive can provide US index rows and macro Treasury yields in `market_snapshot`, with Yahoo fallback.
- `stocktracker`: Massive can provide daily OHLC history and ticker details when provider is `auto` or `massive`, with yfinance fallback in `auto`.

### References
- Shared client: `inkypi-weather/package/InkyPi/src/utils/massive_market_data.py`
- Daily AI News: `inkypi-weather/package/InkyPi/src/plugins/daily_ai_news/daily_ai_news.py`
- Stock Tracker: `inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py`
- Massive REST docs: `https://massive.com/docs/rest/quickstart`
- Massive custom bars docs: `https://massive.com/docs/rest/stocks/aggregates/custom-bars`
- Massive Treasury yields docs: `https://massive.com/docs/rest/economy/treasury-yields`
