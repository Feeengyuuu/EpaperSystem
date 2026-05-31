# Flight radar observed trails

- `SkyRadar` trails should be subtle overlays: semi-transparent arrival/departure colors without a black outline.
- Treat the path line as observed ADS-B history rather than a guaranteed full planned route. Keep a short tail near the aircraft and cap long jumps so stale points do not draw lines across the whole map.
- After changing SkyRadar rendering, regenerate `/tmp/flight_radar_preview_terrain.png`, replace `static/images/plugins/flight_radar_SkyRadar.png` through a `.new` file plus `mv -f`, restart `inkypi`, and verify `/playlist` returns `200`.
