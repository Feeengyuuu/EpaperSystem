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
The six-game Steam deals page must preserve complete cover art without enlargement, cropping or added black borders; road shields should use standard transparent source assets.

### Details
The user requested more games and rejected the dark padding around cover thumbnails. Interpreting this as permission to zoom and centre-crop was wrong: the user then explicitly requested the complete image without forced enlargement. Preserve the source aspect ratio and every edge, only shrink oversized images, and let surrounding space use the page theme background. Adapt the layout to the artwork instead of cropping artwork to fill a fixed frame. For Bay Commute the user rejected hand-drawn road shields; public-domain standard-design SVGs and transparent PNGs for I-680, I-880 and California 84 are bundled with provenance. Map locations use actual Caltrans coordinates over a single USGS export, with source extent retained for projection and no runtime basemap requests.

### Suggested Action
Keep these visual requirements when adjusting the two plugins. Removing image padding does not authorize cropping. Preview complete real covers before deployment. Prefer traceable standard road symbols. Avoid using the standard OpenStreetMap tile service for offline bundled maps; check the actual provider's policy before choosing a static-map source.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/game_deals/render.py, inkypi-weather/package/InkyPi/src/plugins/bay_commute/assets/credits.json, inkypi-weather/package/InkyPi/src/plugins/bay_commute/map_view.py
- Tags: epaper, covers, standard-shields, static-map, source-attribution
- Pattern-Key: epaper.complete_artwork_and_standard_source_graphics

---

## [LRN-20260929-DB3] best_practice

**Logged**: 2026-09-30T07:10:00Z
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
Use the device's actual regular and bold font files for local e-paper acceptance previews.

### Details
Local previews fell back to bundled Noto Sans SC, which supports U+00B7. The device prefers its durable Microsoft YaHei files, and the retained YaHei Bold file renders U+00B7 as a missing-glyph box although its regular face supports it. U+2027 is present in both faces. The two new pages use that supported separator in fixed interface text; provider titles and shared font selection remain unchanged.

### Suggested Action
Resolve and record the actual regular/bold font paths when producing day/night acceptance previews. Check glyph coverage with those files, especially symbols and separators, rather than treating a visually successful fallback-font preview as device proof. Keep font and data identity separate from layout correctness.

### Metadata
- Source: runtime_verification
- Related Files: inkypi-weather/package/InkyPi/src/plugins/game_deals/render.py, inkypi-weather/package/InkyPi/src/plugins/bay_commute/render.py, inkypi-weather/package/InkyPi/src/plugins/bay_commute/map_view.py
- Tags: epaper, fonts, glyph-coverage, acceptance-preview
- Pattern-Key: epaper.preview_with_device_font_files

---
