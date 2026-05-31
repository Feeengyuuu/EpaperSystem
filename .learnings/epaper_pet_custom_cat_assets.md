## [LRN-20260530-003] best_practice

**Logged**: 2026-05-30
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For custom pet-state art with fur, generate on flat chroma green and remove the key locally; do not rely on checkerboard "transparent preview" output.

### Details
The first generated 12-state cat sprite sheet looked transparent but was actually RGB with a checkerboard background. Splitting it by grid caused background artifacts, white-fur holes, and neighboring pose fragments. Regenerating the sheet on a strict, uniform `#00ff00` background allowed clean local key removal while preserving the user's white/silver tabby cat identity.

The user's cat also has short front legs and a low compact body. Future pet-state prompts should explicitly preserve "short front legs / stubby forelegs, compact paws, low-to-ground silhouette" and should require complete visibility of the head, ears, paws, body, and tail with generous padding. Belly-up poses are especially prone to the head being cut at the left edge, so generate or replace that state as a single asset if the grid sheet crops it.

### Suggested Action
When replacing `epaper_pet` ASCII faces with real pet artwork, prompt for:

```text
perfectly flat solid #00ff00 chroma-key background, no checkerboard, no shadows,
each pose fully inside its own grid cell, no crossing/cropping,
short front legs / stubby forelegs, compact paws, low body,
complete head, ears, paws, body, and tail visible with generous padding
```

Then split into `src/plugins/epaper_pet/assets/cat_states/*.png`, validate RGBA alpha corners, and map secondary moods to the nearest available state image in `PET_STATE_IMAGE_MAP`.

If a fixed 4x3 crop leaves non-transparent pixels on any output edge, re-extract from the full green sheet by connected components instead of grid coordinates. Each final PNG should have `edge alpha` counts of zero on all four sides before rendering.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py`, `inkypi-weather/package/InkyPi/src/plugins/epaper_pet/assets/cat_states/`
- Tags: epaper, epaper_pet, imagegen, transparent-assets, chroma-key, custom-pet, short-legs

## [LRN-20260530-004] best_practice

**Logged**: 2026-05-30
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For e-paper pet "animation", use activity-specific pose variants plus printed motion marks instead of real animated image formats.

### Details
The InkyPi display pipeline renders a single PIL image per refresh, so GIF-style animation is not useful on the physical screen. A better fit is to generate multiple transparent full-body cat poses and resolve them by current activity first, then by mood. This lets the pet feel alive across refreshes: `tiny zoomies` can show a low running pose, `shadow pounce` can show a crouched pounce, `snacking` can show a paw-held snack, and `bedtime curl` can show a curled sleeping pose.

Generated activity pose sheets can still pick up small neighboring fragments or green spill. Split 4x2 sheets by cell, remove only border-connected chroma-key pixels, drop small isolated alpha components, and despill edge pixels near transparent areas. Validate every final PNG has fully transparent edge pixels before rendering.

### Suggested Action
When adding motion to `epaper_pet`, keep the asset resolution as transparent PNGs in `assets/cat_states/`, add exact activity mappings in `PET_ACTIVITY_IMAGE_MAP`, and draw restrained panel-native motion accents behind the pet image. Do not use animated GIFs for the e-paper output unless a web-only preview path is explicitly requested.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py`, `inkypi-weather/package/InkyPi/src/plugins/epaper_pet/assets/cat_states/`
- Tags: epaper, epaper_pet, imagegen, activity-mapping, transparent-assets, motion-poses

## [LRN-20260530-005] best_practice

**Logged**: 2026-05-30
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
When adding new pet visual systems, also migrate the same-day AI `daily_life` context so dialogue changes take effect immediately.

### Details
`epaper_pet` keeps a stable `daily_life` object per date. If new AI context fields are only added when a new day is created, devices that already generated today's state will not expose the new information to AI dialogue until tomorrow. For visual pet upgrades, the prompt should receive structured fields such as `visual_state`, `pose_library`, `motion_theme`, `body_focus`, `visual_motif`, and `pose_focus`.

### Suggested Action
When extending `daily_life`, add a helper that backfills missing fields even when `current.date == today`. Include tests that build `_life_context` from an existing same-day daily state and assert the new visual fields are present.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py`, `inkypi-weather/package/InkyPi/tests/test_epaper_pet_context.py`
- Tags: epaper, epaper_pet, ai-dialogue, daily-life, context-migration
