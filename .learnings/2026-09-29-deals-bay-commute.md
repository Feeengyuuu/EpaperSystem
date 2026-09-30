## [LRN-20260929-DB1] best_practice

**Logged**: 2026-09-30T06:10:00Z
**Priority**: medium
**Status**: resolved
**Area**: tests

### Summary
Verify actual Python 3.11 extension imports, and isolate the task PYTHONPATH before using a shared test environment.

### Details
Several older virtual environments existed but did not contain a usable project dependency set. An inherited .pc-packages path, and an old environment's offline-dependencies .pth, could make find_spec('PIL') succeed while importing PIL.Image failed on _imaging. The existing .tmp/venvs/inkypi-release-311-secure-20260904 environment successfully imported Pillow and the test dependencies after the task process set PYTHONPATH to its own worktree src directory. No shared environment mutation or new installation was needed.

### Suggested Action
Check sys.version and actual imports of PIL.Image, pytest, requests and Flask. Set PYTHONPATH and temporary-directory variables for each task process; keep outputs and test temporary files inside the project. Do not infer interpreter compatibility from folder names or find_spec alone.

### Metadata
- Source: error
- Related Files: tools/run_inkypi_tests.ps1
- Tags: windows, python311, pillow, test-environment
- Pattern-Key: tests.verify_native_imports_and_isolate_pythonpath

---

## [LRN-20260929-DB2] correction

**Logged**: 2026-09-30T06:10:00Z
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
The six-game Steam deals page should use filled, undistorted cover crops; road shields should use standard transparent source assets.

### Details
The user requested more games and then explicitly rejected the dark padding around cover thumbnails. The final six-card layout uses aspect-preserving centre crops and real cover art. For Bay Commute the user rejected hand-drawn road shields; public-domain standard-design SVGs and transparent PNGs for I-680, I-880 and California 84 are now bundled with provenance. Map locations use actual Caltrans coordinates over a single USGS export, with source extent retained for projection and no runtime basemap requests.

### Suggested Action
Keep these visual requirements when adjusting the two plugins. Prefer traceable standard road symbols. Avoid using the standard OpenStreetMap tile service for offline bundled maps; check the actual provider's policy before choosing a static-map source.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/game_deals/render.py, inkypi-weather/package/InkyPi/src/plugins/bay_commute/assets/credits.json, inkypi-weather/package/InkyPi/src/plugins/bay_commute/map_view.py
- Tags: epaper, covers, standard-shields, static-map, source-attribution
- Pattern-Key: epaper.source_graphics_and_filled_cover_slots

---
