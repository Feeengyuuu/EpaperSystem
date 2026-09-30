# Game Deals

Displays up to six currently discounted US Steam offers, with USD prices provided
by CheapShark. This is a dynamic deal-rating feed, not a personalised wishlist.
Prices and availability can change before the next scheduled fetch.

- Source: `https://www.cheapshark.com/api/1.0/deals` with `storeID=1`, `onSale=1`,
  `pageSize=20`, `sortBy=Deal Rating`.
- Official API documentation: <https://apidocs.cheapshark.com/>.
- User agent identifies EpaperSystem and its repository. Shared HTTP transport
  applies bounded retries; final HTTP 429 failures additionally impose an hour
  of local backoff. No store redirect is followed by the device.
- Official destination URLs use CheapShark's required `/redirect?dealID=...`
  form, retained in the data cache. There is no QR dependency on the display.
- No historical-low claim is made: the list response does not prove a Steam
  historical low, and the cross-store history endpoint is not requested.

Register the playlist instance with `refresh.interval=7200`,
`settings.storeScope="steam"`, `settings.themeMode="auto"`, and
`settings.refreshOnDisplay=false`. The manifest recommends the same interval;
the repository also enforces a 7200-second successful-data TTL.

Runtime state is under `INKYPI_CACHE_DIR/plugins/game_deals/data/`:
`deals-steam.json` stores the original successful `fetched_at`, currency and six
normalised offers. Failure adds `error` and `retry_at` without renewing source
time. Genuine empty responses clear old offers. Data older than two hours is
visibly stale; offers older than 24 hours are hidden.

The manifest opts into provider-free cached-display redraw so expiry labels and
day/night themes are accurate when the page is shown. Cached display and theme
redraw neither fetch providers nor write data/cover caches. Failed DATA renders
carry stale/local-fallback provenance and cannot replace the last-good image or
claim a successful provider refresh.

Covers use approved HTTPS Steam/CheapShark hosts, no redirects, a 512 KiB byte
limit and at most 1024 by 1024 decoded pixels. Saved thumbnails are at most
320 by 180. The display proportionally center-crops each cover to fill its frame
without padding or stretching. Versioned cover keys replace earlier smaller
thumbnails on the next data refresh. The media namespace retains at most
4 MiB/48 files for 14 days.
Rendering is Pillow-only at the installed screen's 800 by 480 resolution.
