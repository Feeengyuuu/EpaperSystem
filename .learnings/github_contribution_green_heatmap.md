# GitHub contribution heatmap color exception

## [LRN-20260531-003] user_preference

**Logged**: 2026-05-31T17:45:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
The GitHub contribution heatmap should keep the original GitHub green series even when the surrounding page uses the EpaperSystem comic-process color rules.

### Details
During the GitHub dashboard color pass, the user clarified that the original green series in the GitHub graphic should not be changed. Keep the contribution grid colors as `#ebedf0`, `#9be9a8`, `#40c463`, `#30a14e`, and `#216e39`. Apply the vintage comic/Pantone-inspired palette only to the page structure: warm paper background, black linework, section headers, metric cards, and supporting panels.

### Suggested Action
When recoloring `src/plugins/github/github_contributions.py`, preserve the GitHub heatmap colors unless the user explicitly asks to change the contribution grid itself. Improve readability around the grid with layout, linework, labels, and background contrast instead of replacing the green scale.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/github/github_contributions.py
- Tags: github, contributions, heatmap, color-ui, user-preference
