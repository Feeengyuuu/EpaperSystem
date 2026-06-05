# Ergou Daily Mac automation must use img-2

## [LRN-20260604-001] user_preference

**Logged**: 2026-06-04T17:35:41-07:00
**Priority**: high
**Status**: active
**Area**: automation

### Summary
For the Mac automation of `二狗新闻早报`, the final image must be generated through `img-2` / `gpt-image-2`.

### Details
During the Mac automation setup, a deterministic Pillow-rendered PNG path was proposed for text stability. The user explicitly corrected this with: "你必须用 img-2 生成". Future work on this workflow should keep content gathering and prompt assembly separate from final image generation, and the final output should call the Codex imagegen CLI or equivalent `img-2` path.

### Suggested Action
Do not replace the final image step with HTML, SVG, Pillow, or other deterministic renderers unless the user explicitly changes this requirement. Keep `image_model` locked to `gpt-image-2` and fail fast if another model is configured.

### Metadata
- Source: Ergou Daily Mac automation setup
- Related Files: tools/ergou_daily_mac/ergou_daily.py
- Tags: ergou_daily, img-2, gpt-image-2, macos, automation
