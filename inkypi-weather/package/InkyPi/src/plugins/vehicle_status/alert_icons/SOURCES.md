# Vehicle Status alert icon sources

The twenty glyphs in this directory are the status lamps' icons: connectivity, sleep,
stale data, low battery, charging plug, charge port, padlock shut and open, open door,
open window, open tailgate, open front trunk, tyre-pressure warning, hot cabin, software
update, sentry eye, service wrench, pet mode, all clear and warning.

They were generated on 2026-10-26 as one sheet with GPT Image 2.5 (Creative Claw
`image/gpt-image-2.5-sunburst`, job `cfa2bdee-c3a4-4641-898f-346b43788a3e`) so the set
shares one style: solid black pictograms in a 5x4 grid on a transparent 1920x1536
background, with no text, brands or gradients. The sheet had SHA-256
`0429b1b6a29c65e29d4c13c244426580c8c0e1c4b4f553028f6a15b1e8474e42`; the tyre-pressure
glyph is the generic ISO tell-tale shape, and none reproduces a Tesla logo or app icon.

Each 384x384 cell's alpha was cleared of specks below 24, cropped to its visible
bounds, fitted inside 48x48 with Lanczos resampling and centred on a 64x64 transparent
canvas. The working sheet is not part of the release.

At runtime the plugin uses only each glyph's alpha channel and recolours it: muted on a
quiet tile, knocked out of a lit red, amber, blue or green tile.

`pin.png`, the map pin before the location's address, was generated on 2026-10-29 on its own
with GPT Image 2.5 (job `01d725c5-3651-4fff-b760-b961b7e75963`): a solid black location pin on a
transparent 1232x1232 background, source SHA-256
`f5e7bfae7c7f102431879e185af02230d204a2770da65eb3eb4e71146d560b62`. It went through the same
clean-up and fitting as the sheet's cells and is drawn in the page's alert red.
