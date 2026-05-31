# Mini Weather background install and fill rule

## [LRN-20260529-116] correction

**Logged**: 2026-05-29T17:10:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
When the user approves generated Mini Weather backgrounds, install that exact set and make the rendered background fill the whole 800x480 plugin viewport.

### Details
The user correction was that the latest generated mythic comic backgrounds were already visually good and should not be regenerated. The remaining requirement was placement: the image must fill the background dimensions. For Mini Weather this means keeping the approved images, storing them as exact `800x480` plugin assets, selecting them by live weather slug, and rendering the background layer with viewport-cover behavior so the base plugin body padding/margins do not leave a plain edge.

### Suggested Action
For future Mini Weather background sets, do not treat "background must fill" as a prompt change if the art is already approved. Treat it as integration/layout work: resize/crop assets to `800x480`, use `background-size: cover`, avoid `background-repeat`, and preview the actual plugin frame to check for outer whitespace.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/mini_weather/render/mini_weather.css`, `inkypi-weather/package/InkyPi/src/plugins/mini_weather/backgrounds_color/`
- Tags: inkypi, mini-weather, weather-backgrounds, imagegen, layout
