# Dota Profile Hero Icons Need White Pixel Normalization

## Learning
OpenDota hero `icon` images can contain opaque white or near-white background pixels inside the downloaded 32x32 image. A black container alone does not remove the visible white frame on the e-paper dashboard.

## Context
- Plugin: `inkypi-weather/package/InkyPi/src/plugins/dota_profile_dashboard/dota_profile_dashboard.py`
- Source images: OpenDota `heroStats.icon` paths served from `https://cdn.cloudflare.steamstatic.com`
- Symptom: every hero avatar looked like it had a white border or misalignment even after the outer container background was changed to black.

## Recommended Pattern
- Do not draw a separate outline around Dota hero icons in the compact dashboard rows.
- Convert source icons to RGBA and replace near-white pixels, for example RGB components >= 215, with black before resizing.
- Run the same near-white cleanup after resizing, because interpolation can reintroduce pale edge pixels.

## Verification
Local preview `tools/preview_dota_profile_dashboard.py` rendered `.tmp/dota_profile_dashboard_preview.png` with black icon backgrounds and no visible white frame. The local `black icon smoke ok` check verified white pixels are normalized while non-white hero pixels remain visible.
