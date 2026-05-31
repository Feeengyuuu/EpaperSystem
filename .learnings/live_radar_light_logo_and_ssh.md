# LiveRadar light logo masking and SSH quoting

## [LRN-20260527-055] environment

**Logged**: 2026-05-27T22:34:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
LiveRadar title logos with dark source backgrounds need a light-theme foreground mask, and Windows PowerShell SSH deployment commands must protect remote shell variables from local expansion.

### Details
The LiveRadar logo source contains a black background that blends into night mode but appears as a black square in day mode. Render light mode by treating near-black logo pixels as transparent and drawing the remaining foreground in dark grayscale.

PowerShell expands `$PKG` and `$(date ...)` before `ssh` unless the remote command is quoted safely. This caused an attempted unzip into `/src/plugins` and a local `Get-Date` parse error. Use a PowerShell single-quoted remote command or otherwise escape remote `$` expressions.

### Suggested Action
When polishing InkyPi plugins with day/night variants, visually QA both themes and add image-level regression tests for theme-specific assets. For Pi deployments from PowerShell, keep remote shell commands in a single-quoted argument and verify the remote path with `grep` before restart.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py
- Tags: inkypi, epaperpod, live-radar, theme, logo, powershell, ssh
