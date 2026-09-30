# Game Deals and Bay Commute title artwork

## [LRN-20260930-ART] correction

**Logged**: 2026-09-30
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
Treat the circled page titles and USD label as three separate artwork assets, following the existing generated wordmark style.

### Details
The user requested the established artistic-title treatment for Game Deals, Bay Commute, and USD. No exact earlier assets for these labels were present. After a focused inventory, the user confirmed creating three new titles in the existing style. Use independent transparent PNGs, retain their original colors for both themes, and keep ordinary text only as an unavailable-asset fallback. Preserve complete game covers and the page body.

### Suggested Action
Record exact prompts and asset paths, inspect real-size day and night previews, and compare the unchanged body pixels against the same input on the previous renderer. Reuse small bounded image-loading logic rather than importing another plugin's entire renderer.

### Metadata
- Source: user_feedback
- Related Files: docs/design/deals-bay-title-artwork-20260930.json; inkypi-weather/package/InkyPi/src/utils/local_wordmark.py
- Tags: title-artwork, transparent-png, scoped-visual-change
- Pattern-Key: epaper.local-title-artwork

---

## [LRN-20260930-ALP] insight

**Logged**: 2026-09-30
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
Near-invisible alpha noise in generated PNG padding can make a correctly fitted title look much smaller than intended.

### Details
The Bay Commute title had alpha 1-2 pixels far above and below its visible letters: the nonzero-alpha bounds were 1856 by 716 pixels, while alpha above 8 identified the actual 1850 by 413 pixel artwork. USD had similar padding noise. For layout only, determine visible bounds with a low alpha threshold and a small antialiasing margin; retain original RGBA pixels and colors inside those bounds. Do not recolor multicolor artwork or threshold away its actual rendered alpha.

### Suggested Action
Keep a regression covering a distant low-alpha speck, verify real-size previews, and cache only the fitted small image instead of a full decoded original.

### Metadata
- Source: error
- Related Files: inkypi-weather/package/InkyPi/src/utils/local_wordmark.py; inkypi-weather/package/InkyPi/tests/test_local_wordmark.py
- Tags: alpha-bounds, imagegen, epaper-memory
- Pattern-Key: epaper.wordmark-visible-alpha-bounds


### Verification
Four day/night previews passed at native 800x480 resolution. Pixels below y=68 match the prior renderer, missing-asset fallback matches the entire original page, and nine helper regressions passed. Cache retains only 69,488 RGBA bytes across the three fitted images. Device release acceptance is recorded separately in the task output directory.
