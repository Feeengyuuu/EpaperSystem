# Steam profile gilded avatar frame

Date: 2026-10-07. Status: approved by the user (direction "A 鎏金宫廷" chosen from
rendered mockups in `outputs/steam-avatar-frame-20261007/frame-options.png`).

## Goal

Replace the 2 px navy outline around the Steam profile avatar in the console
rail with a refined, luxurious gilded frame that reads well both in the
full-colour web preview and on the Spectra 6 panel (`epd7in3e`: black, white,
yellow, red, blue, green with Floyd–Steinberg dithering).

## Visual design

- The avatar stays a 146×146 square at (16, 16); the photo is never cropped.
- Frame band (about 10 px) around the avatar with a six-stop metallic gold
  gradient. Bevel: light top-left edge, dark bottom-right edge. Bronze hairlines
  close the outer and inner edges; a deep-gold fillet sits just inside.
- Corners: three-layer diamond rosettes (bronze → gold → bright gold) with a
  ruby centre and a small highlight.
- Mid-edge gold studs on all four sides.
- A small five-point crown with a ruby crests the top edge.
- E-paper legibility: strokes are at least 2 px at final size, gold is built from
  yellow-dominant tones with bronze outlines, gems use the native red ink.
- Bounds: the overlay is 180×178 at the rail origin; opaque pixels stay at
  x < 180 and y < 178 (corner rosette tips reach y 177), clear of the rail
  divider at x 180–182 and of the persona name glyphs, which start near y 180.
- The console is intentionally identical in day and night modes, so the frame is
  designed for the dark canvas only.

## Implementation

- New module `plugins/steam_profile_dashboard/avatar_frame.py` draws the frame
  procedurally at 4× supersampling and returns a cached RGBA overlay whose
  avatar window is fully transparent (`functools.lru_cache`, one draw per
  process). No new binary assets.
- `console_renderer._Console.rail()` pastes the avatar (or the existing
  placeholder) and then alpha-composites the overlay at the rail origin instead
  of drawing the navy rounded outline.
- Friend avatars (32 px) are unchanged.
- `STEAM_DASHBOARD_STYLE_VERSION` moves to `v40`; `v39` joins the compatible
  styles so cached data still serves theme transitions without Steam requests.

## Testing

- Overlay: expected size and mode; the avatar window is fully transparent; band,
  corner gems and crown pixels fall in the expected colour families; repeated
  calls return the cached image.
- Rail render: avatar centre pixels are unchanged by the frame; no frame pixels
  beyond x 181 or below y 174; the name area is untouched.
- Spectra 6 simulation: after quantising with the device palette the band is
  yellow-dominant and the corner gems are red.
