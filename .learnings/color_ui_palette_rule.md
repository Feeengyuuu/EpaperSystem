## [LRN-20260528-004] best_practice

**Logged**: 2026-05-28T18:59:32-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Future formal color EpaperSystem UI must use the user-provided vintage comic process/Pantone chart codes as the governing palette rule.

### Details
The user provided a DC Comics 1982 process/Pantone-style color chart and stated that, when the product moves to a color e-paper formal version, the chart should define the UI and host/device color-matching rules. The user then clarified that the printed color codes are the most important part because they can precisely locate specific colors. On 2026-05-29 the user further clarified that the color law should be old comic palette compliance, including functional pages such as Stock Tracker. Treat the image as a palette and visual-system reference only; do not reuse copyrighted character artwork or brand marks from it.

### Suggested Action
When asked to improve the UI for color e-paper, start from `docs/color-ui-guidelines.md`: use black linework, paper/warm-white grounds, and limited flat CMYK-like accents from the chart. Create named color tokens that store the exact `source_label` such as `100Y-25R PANTONE 123`, then calibrate final values against the actual color e-paper panel before production use. Do not replace the code reference with sampled image RGB. Use external digital conversion references only as derivatives; preserve source IDs such as discontinued `PANTONE 833`. Do not introduce neutral product-dashboard palettes unless the user explicitly overrides the old comic palette rule.

### Metadata
- Source: user_feedback
- Related Files: `docs/color-ui-guidelines.md`, `https://www.trucolor.net/portfolio/dc-comics-1982-color-palette/`
- Tags: epaper, color-ui, palette, pantone, cmyk, design-system

---
