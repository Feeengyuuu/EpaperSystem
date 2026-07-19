# Orbital Signal source research

## Information scope

- Global launches: Launch Library 2 v2.3.0 `launches/upcoming` provides the next launch time, status, provider, rocket, mission, orbit, pad, location, webcast state, and later launches. Anonymous production access is limited to 15 requests per hour, so the plugin caches results for 60 minutes by default.
- Prediction markets: Polymarket Gamma API exposes public events and their markets without authentication. The plugin requests active, open events ordered by 24-hour volume, then derives one leading outcome, current implied probability, 24-hour move, liquidity, and a display-only heat score.
- Heat is an editorial attention signal, not a recommendation. It combines log-scaled 24-hour volume and absolute 24-hour probability movement, capped at 100.

## Display boundary

- The left panel shows one launch hero and two following launches.
- The right panel shows three distinct high-volume events, not three contracts from the same event.
- Expired events, invalid probabilities, and launches with no usable NET are rejected.
- Live data falls back independently to fresh cache, stale cache, then clearly marked fixtures.
- The plugin performs no internal polling or animated refresh. Data is considered only when InkyPi renders the plugin.

## Primary documentation

- https://ll.thespacedevs.com/docs
- https://ll.thespacedevs.com/2.3.0/launches/upcoming/
- https://docs.polymarket.com/api-reference/introduction
- https://docs.polymarket.com/market-data/fetching-markets
