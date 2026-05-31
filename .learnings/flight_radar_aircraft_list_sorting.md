# Flight radar aircraft list sorting

## [LRN-20260530-001] implementation pattern

**Logged**: 2026-05-30
**Priority**: medium
**Status**: active
**Area**: plugin-rendering

### Summary
For `SkyRadar`, keep the map aircraft order independent from the right-side list order.

### Details
The fetched aircraft list is distance-ranked for map/context use. The right-side list should call `_ordered_aircraft_for_list(snapshot)` so non-arrival aircraft stay in the upper section and arrival/landing aircraft are pushed lower while preserving distance order inside each group.

### Suggested Action
When changing SkyRadar list behavior, add or update tests around `_ordered_aircraft_for_list`. For live validation, inspect the latest snapshot cache and print each aircraft's `_aircraft_list_flow` so ordering can be verified without relying only on the rendered preview.

### Metadata
- Source: SkyRadar right-side list ordering refinement
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/flight_radar/flight_radar.py`, `inkypi-weather/package/InkyPi/tests/test_flight_radar.py`
- Tags: flight-radar, skyradar, sorting, aircraft-list

---
