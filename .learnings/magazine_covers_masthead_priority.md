# Magazine Covers Masthead Priority

- Date: 2026-05-28
- When `MagazineCovers` crops a portrait cover into the 800x480 e-paper canvas, preserve the masthead, title, or logo area first.
- If a source image does not contain a recognizable masthead after crop, render a compact source label so the screen still communicates which publication is being shown.
- User feedback: images that only show body photography or isolated cover text are confusing because the viewer cannot tell what the screen is displaying.
- Current crop rule: keep a Pi-safe original cover image, detect the strongest high-contrast title/logo band in the upper portion of the cover, and crop so that detected band lands near the upper display area. Fall back to top masthead crop when detection is weak.
