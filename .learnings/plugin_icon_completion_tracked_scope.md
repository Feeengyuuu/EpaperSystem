# Plugin icon completion uses tracked plugin metadata scope

## [LRN-20260605-004] icon audit scope

**Logged**: 2026-06-05T00:00:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper-plugin-assets

### Summary
When filling missing web settings icons for EpaperSystem plugins, use tracked `plugin-info.json` files as the formal plugin inventory unless the user explicitly asks to include local prototypes.

### Details
During icon completion, an abandoned local `starlink_radar` prototype existed as an untracked plugin directory. A raw directory scan made it look like another missing icon, but the useful scope was the committed/formal plugin inventory. `git ls-files 'inkypi-weather/package/InkyPi/src/plugins/*/plugin-info.json'` gave the right audit boundary.

### Suggested Action
- Audit missing icons from tracked plugin metadata first.
- Generate 512x512 `icon.png` assets for formal plugins only.
- Keep abandoned local prototypes out of asset-completion passes unless the user explicitly resumes them.

### Metadata
- Source: observed_workflow
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/*/plugin-info.json`
- Tags: icons, imagegen, tracked-scope, plugin-assets
