2026-05-31

For FlightRadar/SkyRadar route city labels, prefer a city-specific font path:
Microsoft YaHei when it is legally installed and available, then bundled
NotoSansSC-VF as the Pi-safe simplified Chinese fallback. Do not rely on the
generic `_font()` stack for Chinese route labels because DejaVu may be selected
first and render missing glyph boxes.

Keep route localization display-only. Preserve the raw English city/airport
fields for route-flow logic, and localize in `_format_route_line()` so arrival
and departure sorting continues to match SFO aliases.
