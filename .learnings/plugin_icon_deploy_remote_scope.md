# Plugin icon deploy follows live runtime inventory

## [LRN-20260605-005] remote icon deploy scope

**Logged**: 2026-06-05T00:00:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper-plugin-assets

### Summary
When deploying generated plugin icons to the EpaperPod, verify the live runtime plugin directories before copying every local icon.

### Details
The local checkout had a generated `wow_profile_dashboard/icon.png`, but the live `ColoredEpaperFrame` runtime did not have a `wow_profile_dashboard` plugin directory because that prototype had previously been paused without deployment. The deploy should not create a partial remote plugin directory just to place an icon.

### Suggested Action
- Upload icons only for plugin directories that exist under `/usr/local/inkypi/src/plugins`.
- Keep mirror copies in `/home/feeengyuuu/inkypi-weather-pi-package-zero2w-20260531/InkyPi/src/plugins` only for the same existing plugin set.
- Verify `/images/<plugin_id>/icon.png` over HTTP after copying.
- Treat local paused prototypes as local-only unless the user explicitly resumes them.

### Metadata
- Source: observed_deploy
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/*/icon.png`
- Tags: icons, deploy, live-runtime, wow-profile-dashboard, ColoredEpaperFrame
