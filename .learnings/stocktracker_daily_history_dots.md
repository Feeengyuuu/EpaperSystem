# Stocktracker daily history dots

- `stocktracker/Money` portfolio trend markers should represent persisted daily portfolio snapshots.
- Keep one snapshot per local date; same-day refreshes replace that date's point, and later days append new points.
- Do not use transient Yahoo intraday/history samples as the user-facing historical progress markers.
- Sanitize Yahoo numeric gaps before writing history or summary text; `NaN` must not appear in JSON or rendered dashboard copy.
