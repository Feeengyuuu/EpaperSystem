### Summary
For InkyPi magazine cover rotation, prefer pages that expose actual issue cover images over magazine homepages.

### Details
During the live magazine-cover preview, Vanity Fair, WIRED Japan, The Atlantic, and Magazine Shop collection pages produced usable portrait cover images that looked good when rotated 90 degrees and contained inside the 800x480 display. National Geographic's magazine page produced a landscape article hero rather than a cover, Vogue's magazine page produced a subscription/ad image rather than a cover, and The New Yorker cover page did not expose a usable cover image to the simple HTML parser.

### Suggested Action
When adding magazine sources, validate each source with a real 800x480 preview before deploying it. Keep default sources to verified cover-producing pages, and treat generic magazine homepages as candidates only after source-specific validation.

For user-facing magazine rotation, prefer a shuffled queue over plain sequential rotation. Store the random queue in plugin state, remove each source after a successful display, and reshuffle only after the pool is exhausted so refreshes feel random while avoiding immediate repeats.

When deploying this plugin to the Pi from Windows, prefer direct `scp` upload plus remote `unzip` over a temporary local `python -m http.server`; the latter can leave a child server process attached to the tool session. Avoid remote shell snippets containing `$(date ...)` from PowerShell because local PowerShell expands them first; use remote Python for timestamped backups instead.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/magazine_covers/magazine_covers.py`, `tools/preview_magazine_covers.py`
- Tags: inkypi, magazine-covers, epaper, source-selection, preview
