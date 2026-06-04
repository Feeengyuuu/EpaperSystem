# SportsDashboard uploaded logo assets

## [LRN-20260602-002] asset-workflow

**Logged**: 2026-06-02T23:40:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For SportsDashboard logo changes, preserve user-uploaded source files and use local assets before any generated or remote fallback.

### Details
The user provided `G:/Download/League_of_legends_pro_league_logo.svg` and `G:/Download/9a4a0aa088a138003b4919b808b88cb9.png` for LPL and FIFA/World Cup branding. Pillow on the Pi cannot load SVG directly and neither local nor Pi runtime had CairoSVG/rsvg available, so the SVG source was copied into `assets/logos/lpl.svg` and rasterized once through local Edge headless into `assets/logos/lpl.png`, then cropped/transparentized. Both source and rendered assets were deployed. Do not substitute generated artwork when the user supplies logo files.

### Suggested Action
For future uploaded SVG logos, copy the SVG source into the plugin assets directory and create a derived PNG only for runtime rendering. Keep the runtime loader local-file-first and deploy assets to both `/usr/local/inkypi` and the active `/home/feeengyuuu/.../InkyPi/src` path.

### Metadata
- Source: asset-workflow
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`, `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/assets/logos/`
- Tags: sports-dashboard, logo-assets, svg, png, local-assets, colored-epaper-frame
