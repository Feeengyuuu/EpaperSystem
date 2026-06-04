# GitHub plugin enriched dashboard

## [LRN-20260531-002] project_quirk

**Logged**: 2026-05-31T17:12:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
The `github` plugin token can support a richer Contributions dashboard on `ColoredEpaperFrame` without using Chromium.

### Details
For username `Feeengyuuu`, the configured `GITHUB_SECRET` successfully allowed GraphQL access to profile counts, starred repos, public repository totals, top public repositories, and contribution breakdown fields. The production render path should stay Pillow-based and show profile stats, heatmap, contribution mix, and top repositories in one 800x480 image.

### Suggested Action
When changing this plugin, validate the GraphQL shape on the Pi without printing the token, then deploy only `src/plugins/github/github_contributions.py`, run remote `PYTHONPYCACHEPREFIX=/tmp/inkypi-pycache python3 -m py_compile`, restart `inkypi`, and verify `/api/current_image` plus the `Updating display` log.

### Metadata
- Source: live debug
- Related Files: inkypi-weather/package/InkyPi/src/plugins/github/github_contributions.py
- Tags: github, graphql, dashboard, pillow, colored-epaper-frame
