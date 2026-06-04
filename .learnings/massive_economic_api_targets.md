# Massive economic API integration targets

## [LRN-20260603-003] integration-targets

**Logged**: 2026-06-03
**Priority**: medium
**Status**: proposed
**Area**: epaper, market-data

### Summary
The live env contains `Massive_Ecnomic_Key`, but current code does not consume any Massive API key. Massive is useful for existing economic and market-information plugins, not for comic-cover retrieval.

### Useful Existing Targets
- `daily_ai_news`: best first target. Its market snapshot currently calls Yahoo Finance chart URLs for A-share and US index summaries. Massive can provide authenticated market data and macro indicators, improving reliability for the market block while keeping news generation separate.
- `stocktracker`: second target. It currently depends on `yfinance` for quotes, history, and metadata. Massive can be an `auto` provider for supported US stocks, ETFs, indices, forex, and crypto, with `yfinance` kept as fallback for unsupported symbols or plan limits.

### Not A Direct Fit
- `gcd_comic_covers`, `magazine_covers`, `newspaper`, `box_office_top_movies`, and `sports_dashboard` do not directly benefit from Massive economic data.

### Implementation Notes
- Prefer canonical env key `MASSIVE_API_KEY`, but accept the currently deployed typo `Massive_Ecnomic_Key` as fallback so the live key works without re-entry.
- Do not log or commit the key value.
- Keep short timeouts, cache results, and fail open to existing Yahoo/yfinance paths so display rotation is not blocked.
- Confirm the user's Massive subscription coverage before removing Yahoo/yfinance behavior, especially for non-US or A-share symbols.

### References
- Massive REST docs: `https://massive.com/docs/rest`
- Massive Economy docs: `https://massive.com/docs/rest/economy/overview`
- Massive Indices docs: `https://massive.com/docs/rest/indices/overview`
