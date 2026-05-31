# Flight radar route labels

- In `flight_radar`, the right-side aircraft list should prefer human city routes over airport-code routes.
- AirPing route data can include `_airports[].location`; use that to render labels such as `San Francisco -> Burbank`, and fall back to `SFO -> BUR` only when city data is unavailable.
- Changing route-label shape should bump the route cache schema/file so old airport-code-only cache entries do not keep showing.
- Remote validation steps are dependent: upload files first, then run compile/preview scripts. Do not start the preview script before the updated files are on `ColoredEpaperFrame`.
